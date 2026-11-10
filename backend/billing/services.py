import calendar
import datetime
from collections import defaultdict
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from contracts.models import TimesheetEntry

from .models import BillingRun, Invoice, InvoiceLineItem

MONEY = Decimal('0.01')


def month_bounds(month: str) -> tuple[datetime.date, datetime.date]:
    """'2026-05' -> (date(2026,5,1), date(2026,5,31)). Raises ValueError on bad input."""
    try:
        year_s, month_s = month.split('-')
        year, mon = int(year_s), int(month_s)
        start = datetime.date(year, mon, 1)
    except (ValueError, AttributeError):
        raise ValueError('month must be in YYYY-MM format')
    last_day = calendar.monthrange(year, mon)[1]
    return start, datetime.date(year, mon, last_day)


def run_billing(month: str, *, triggered_by=None) -> BillingRun:
    """Create one invoice per (company, freelancer) with approved entries in `month`.

    Idempotent: any (company, freelancer) that already has an invoice for this
    period is skipped, so re-running is safe and returns 0 new invoices.
    """
    period_start, period_end = month_bounds(month)

    run = BillingRun.objects.create(
        period_start=period_start, period_end=period_end, triggered_by=triggered_by,
    )

    # One query, filtered on ENTRY date. This is what makes partial months correct.
    entries = (
        TimesheetEntry.objects
        .filter(
            status=TimesheetEntry.STATUS_APPROVED,
            date__gte=period_start,
            date__lte=period_end,
        )
        .select_related('contract', 'contract__company', 'contract__freelancer')
        .order_by('date', 'id')
    )

    grouped = defaultdict(list)
    for entry in entries:
        grouped[(entry.contract.company_id, entry.contract.freelancer_id)].append(entry)

    created, skipped = [], 0
    for (company_id, freelancer_id), group in grouped.items():
        try:
            with transaction.atomic():
                invoice = _create_invoice(
                    run=run, group=group,
                    period_start=period_start, period_end=period_end,
                )
        except IntegrityError:
            # Unique constraint fired: another run already billed this pair.
            skipped += 1
            continue
        created.append(invoice)

    run.invoices_generated = len(created)
    run.invoices_skipped = skipped
    run.status = BillingRun.STATUS_COMPLETED
    run.completed_at = timezone.now()
    run.save(update_fields=['invoices_generated', 'invoices_skipped', 'status', 'completed_at'])

    # Delivery happens AFTER the rows are committed — see step 6.
    from .emails import deliver_invoice
    for invoice in created:
        deliver_invoice(invoice)

    return run


def _create_invoice(*, run, group, period_start, period_end) -> Invoice:
    first = group[0]
    contract = first.contract
    company, freelancer = contract.company, contract.freelancer

    invoice = Invoice.objects.create(
        number='',  # set below, needs the PK
        company=company,
        freelancer=freelancer,
        billing_run=run,
        period_start=period_start,
        period_end=period_end,
        contract_start=contract.start_date,
        contract_end=contract.end_date,
    )

    subtotal = Decimal('0.00')
    for entry in group:
        amount = (entry.hours * entry.contract.daily_rate).quantize(MONEY)
        subtotal += amount
        InvoiceLineItem.objects.create(
            invoice=invoice,
            timesheet_entry=entry,
            contract=entry.contract,
            date=entry.date,
            description=f'Professional services — {entry.date:%d %b %Y}',
            hours=entry.hours,
            daily_rate=entry.contract.daily_rate,
            amount=amount,
        )

    invoice.subtotal = subtotal
    invoice.total = subtotal + invoice.tax
    invoice.number = f'INV-{period_start:%Y-%m}-{invoice.pk:05d}'
    invoice.save(update_fields=['subtotal', 'total', 'number'])
    return invoice
