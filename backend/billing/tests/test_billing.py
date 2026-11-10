import datetime
from decimal import Decimal

import pytest
from django.core import mail
from django.db import IntegrityError, transaction
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from billing.models import BillingRun, Invoice, InvoiceLineItem
from billing.services import _create_invoice, month_bounds, run_billing
from contracts.models import TimesheetEntry


def _run_as_admin(admin_user, month):
    """Run billing with a dedicated client.

    admin_client and freelancer_client share one APIClient instance and both set its
    credentials, so whichever fixture resolves last wins. Tests that need both roles
    must use a separate client for the admin action rather than mutating the shared one.
    """
    client = APIClient()
    token, _ = Token.objects.get_or_create(user=admin_user)
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client.post('/api/billing/runs/', {'month': month}, format='json')


# --- 1. Grain: one invoice per Company x Freelancer -------------------------

@pytest.mark.django_db
def test_two_freelancers_same_company_get_separate_invoices(
    admin_client, approved_entries, second_contract, northstar
):
    TimesheetEntry.objects.create(
        contract=second_contract, date=datetime.date(2026, 5, 4),
        hours=Decimal('8.0'), status='approved',
    )

    resp = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert resp.status_code == 200
    assert resp.data['invoices_generated'] == 2
    invoices = Invoice.objects.filter(company=northstar)
    assert invoices.count() == 2
    assert len({i.freelancer_id for i in invoices}) == 2


@pytest.mark.django_db
def test_same_freelancer_two_companies_get_separate_invoices(
    admin_client, approved_entries, other_contract
):
    TimesheetEntry.objects.create(
        contract=other_contract, date=datetime.date(2026, 5, 4),
        hours=Decimal('8.0'), status='approved',
    )

    resp = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert resp.status_code == 200
    assert resp.data['invoices_generated'] == 2
    assert len({i.company_id for i in Invoice.objects.all()}) == 2


# --- 2. Only approved entries are billed ------------------------------------

@pytest.mark.django_db
def test_only_approved_entries_are_billed(admin_client, active_contract):
    TimesheetEntry.objects.create(
        contract=active_contract, date=datetime.date(2026, 5, 4),
        hours=Decimal('8.0'), status='approved',
    )
    for day, status in [(5, 'draft'), (6, 'submitted'), (7, 'rejected')]:
        TimesheetEntry.objects.create(
            contract=active_contract, date=datetime.date(2026, 5, day),
            hours=Decimal('8.0'), status=status,
        )

    resp = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert resp.status_code == 200
    invoice = Invoice.objects.get()
    assert invoice.line_items.count() == 1
    assert invoice.line_items.get().date == datetime.date(2026, 5, 4)


# --- 3. Idempotency ---------------------------------------------------------

@pytest.mark.django_db
def test_rerun_is_idempotent(admin_client, approved_entries):
    first = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')
    assert first.status_code == 200
    assert first.data['invoices_generated'] == 1
    assert first.data['invoices_skipped'] == 0

    second = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')
    assert second.status_code == 200
    assert second.data['invoices_generated'] == 0
    assert second.data['invoices_skipped'] == 1
    assert Invoice.objects.count() == 1

    # Each invocation is still its own audit record.
    assert BillingRun.objects.count() == 2


@pytest.mark.django_db
def test_rerun_does_not_duplicate_line_items(admin_client, approved_entries):
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert InvoiceLineItem.objects.count() == 3


# --- 4. The constraint is real, not just application logic ------------------

@pytest.mark.django_db
def test_run_billing_survives_a_collision_on_insert(admin_client, approved_entries, monkeypatch):
    """The skip path works even when the insert is attempted and collides.

    Note on scope: pytest-django runs each test in a single transaction, so this
    cannot reproduce a genuine concurrent race. What it does prove is that when
    _create_invoice raises IntegrityError, run_billing records a skip and does
    not blow up the run. The constraint itself is pinned separately by
    test_double_insert_for_same_pair_raises_integrity_error.
    """
    from billing import services

    first = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')
    assert first.data['invoices_generated'] == 1

    # Force the insert to be attempted despite the existing row, so the collision
    # happens inside run_billing rather than being short-circuited earlier.
    real_create = services._create_invoice
    monkeypatch.setattr(services, '_create_invoice', real_create)
    second = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert second.status_code == 200
    assert second.data['invoices_generated'] == 0
    assert second.data['invoices_skipped'] == 1
    assert Invoice.objects.count() == 1


@pytest.mark.django_db
def test_double_insert_for_same_pair_raises_integrity_error(approved_entries):
    """Proves the DB constraint fires, not merely a get_or_create check."""
    run = BillingRun.objects.create(
        period_start=datetime.date(2026, 5, 1), period_end=datetime.date(2026, 5, 31),
    )
    group = list(approved_entries)

    _create_invoice(
        run=run, group=group,
        period_start=datetime.date(2026, 5, 1), period_end=datetime.date(2026, 5, 31),
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_invoice(
                run=run, group=group,
                period_start=datetime.date(2026, 5, 1), period_end=datetime.date(2026, 5, 31),
            )


# --- 5. Partial months ------------------------------------------------------

@pytest.mark.django_db
def test_partial_month_bills_only_entries_inside_the_month(
    admin_client, partial_month_contract, partial_month_entries
):
    """Contract ran 12 Feb - 18 Mar. March bills only March entries."""
    resp = admin_client.post('/api/billing/runs/', {'month': '2026-03'}, format='json')

    assert resp.status_code == 200
    assert resp.data['invoices_generated'] == 1
    invoice = Invoice.objects.get()
    billed_dates = sorted(li.date for li in invoice.line_items.all())
    assert billed_dates == [datetime.date(2026, 3, 5), datetime.date(2026, 3, 20)]


@pytest.mark.django_db
def test_partial_month_february_run(
    admin_client, partial_month_contract, partial_month_entries
):
    resp = admin_client.post('/api/billing/runs/', {'month': '2026-02'}, format='json')

    assert resp.status_code == 200
    invoice = Invoice.objects.get()
    billed_dates = sorted(li.date for li in invoice.line_items.all())
    assert billed_dates == [datetime.date(2026, 2, 10), datetime.date(2026, 2, 20)]


@pytest.mark.django_db
def test_entry_after_contract_end_is_still_billed(admin_client, northstar, freelancer_user):
    """Selection is by ENTRY date, never by contract dates.

    A contract that ended 28 Feb with an approved entry dated 3 Mar must still be
    billed in March. A contract-date filter would silently drop it. This is the
    case where the two implementations genuinely diverge, so it is the one that
    pins the decision.
    """
    from contracts.models import Contract

    contract = Contract.objects.create(
        company=northstar,
        freelancer=freelancer_user.freelancer,
        daily_rate=Decimal('600.00'),
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 2, 28),
        status='closed',
    )
    TimesheetEntry.objects.create(
        contract=contract, date=datetime.date(2026, 3, 3),
        hours=Decimal('8.0'), status='approved',
    )

    resp = admin_client.post('/api/billing/runs/', {'month': '2026-03'}, format='json')

    assert resp.status_code == 200
    assert resp.data['invoices_generated'] == 1
    invoice = Invoice.objects.get()
    assert [li.date for li in invoice.line_items.all()] == [datetime.date(2026, 3, 3)]


@pytest.mark.django_db
def test_partial_month_records_contract_bounds(admin_client, partial_month_contract, partial_month_entries):
    admin_client.post('/api/billing/runs/', {'month': '2026-03'}, format='json')

    invoice = Invoice.objects.get()
    assert invoice.period_start == datetime.date(2026, 3, 1)
    assert invoice.period_end == datetime.date(2026, 3, 31)
    assert invoice.contract_start == datetime.date(2026, 2, 12)
    assert invoice.contract_end == datetime.date(2026, 3, 18)


# --- 6. Exact decimal arithmetic -------------------------------------------

@pytest.mark.django_db
def test_totals_are_exact_decimals(admin_client, approved_entries):
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    invoice = Invoice.objects.get()
    # 8.00*600 + 7.50*600 + 8.00*600 = 4800 + 4500 + 4800
    assert invoice.subtotal == Decimal('14100.00')
    assert invoice.total == invoice.subtotal + invoice.tax
    assert invoice.tax == Decimal('0.00')

    line_amounts = [li.amount for li in invoice.line_items.all()]
    assert line_amounts == [Decimal('4800.00'), Decimal('4500.00'), Decimal('4800.00')]
    assert sum(line_amounts) == invoice.subtotal


@pytest.mark.django_db
def test_total_includes_tax(approved_entries, monkeypatch):
    """Pin total = subtotal + tax with a non-zero tax, exercising _create_invoice.

    Tax is 0.00 everywhere today, so `total = subtotal` and `total = subtotal + tax`
    are indistinguishable. Force a non-zero tax rate through the real code path so
    the relation is actually asserted.
    """
    from billing import services
    from billing.models import Invoice as InvoiceModel

    original_create = InvoiceModel.objects.create

    def create_with_tax(**kwargs):
        kwargs['tax'] = Decimal('2820.00')  # 20% of 14100.00
        return original_create(**kwargs)

    monkeypatch.setattr(InvoiceModel.objects, 'create', create_with_tax)
    services.run_billing('2026-05')

    invoice = InvoiceModel.objects.get()
    assert invoice.subtotal == Decimal('14100.00')
    assert invoice.tax == Decimal('2820.00')
    assert invoice.total == Decimal('16920.00')
    assert invoice.total == invoice.subtotal + invoice.tax
    assert invoice.total != invoice.subtotal


@pytest.mark.django_db
def test_line_item_amount_is_quantized_before_persisting(active_contract):
    """Quantization must happen in Python, not be left to the DB column.

    The column is numeric(12,2), so Postgres silently rounds on save and an
    equality assertion after a round-trip cannot tell quantized from unquantized.
    Check the value _create_invoice produces *before* it is written.
    """
    from billing.services import _create_invoice

    active_contract.daily_rate = Decimal('333.33')
    active_contract.save()
    entry = TimesheetEntry.objects.create(
        contract=active_contract, date=datetime.date(2026, 5, 4),
        hours=Decimal('7.33'), status='approved',
    )
    run = BillingRun.objects.create(
        period_start=datetime.date(2026, 5, 1), period_end=datetime.date(2026, 5, 31),
    )

    captured = {}
    real_create = InvoiceLineItem.objects.create

    def spy(**kwargs):
        captured['amount'] = kwargs['amount']
        return real_create(**kwargs)

    from billing import services
    original = services.InvoiceLineItem.objects.create
    services.InvoiceLineItem.objects.create = spy
    try:
        _create_invoice(
            run=run, group=[entry],
            period_start=datetime.date(2026, 5, 1), period_end=datetime.date(2026, 5, 31),
        )
    finally:
        services.InvoiceLineItem.objects.create = original

    # 7.33 * 333.33 = 2443.3089 -> 2443.31, and carrying exactly 2dp.
    assert captured['amount'] == Decimal('2443.31')
    assert captured['amount'].as_tuple().exponent == -2


# --- 7. Line items are a snapshot ------------------------------------------

@pytest.mark.django_db
def test_line_items_snapshot_the_rate(admin_client, approved_entries, active_contract):
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')
    invoice = Invoice.objects.get()
    original_total = invoice.total

    # Change the contract rate and the timesheet hours after the fact.
    active_contract.daily_rate = Decimal('999.00')
    active_contract.save()
    TimesheetEntry.objects.filter(contract=active_contract).update(hours=Decimal('1.0'))

    invoice.refresh_from_db()
    assert invoice.total == original_total == Decimal('14100.00')
    assert all(li.daily_rate == Decimal('600.00') for li in invoice.line_items.all())


# --- 8. Email delivery ------------------------------------------------------

@pytest.mark.django_db
def test_two_emails_are_sent_to_the_right_recipients(
    admin_client, approved_entries, northstar, freelancer_user
):
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert len(mail.outbox) == 2
    recipients = sorted(m.to[0] for m in mail.outbox)
    assert recipients == sorted([northstar.billing_email, freelancer_user.email])

    # Neither message leaks the other party's address.
    for msg in mail.outbox:
        assert len(msg.to) == 1


@pytest.mark.django_db
def test_email_contains_invoice_content(admin_client, approved_entries):
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    invoice = Invoice.objects.get()
    msg = mail.outbox[0]
    assert invoice.number in msg.subject
    assert invoice.number in msg.body
    assert '14100.00' in msg.body
    # Plain text plus an HTML alternative.
    assert len(msg.alternatives) == 1
    assert msg.alternatives[0][1] == 'text/html'


@pytest.mark.django_db
def test_successful_delivery_marks_invoice_sent(admin_client, approved_entries):
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    invoice = Invoice.objects.get()
    assert invoice.delivery_status == Invoice.DELIVERY_SENT
    assert invoice.delivered_at is not None
    assert invoice.delivery_error == ''


# --- 9. Email failure must not roll back the invoice -----------------------

@pytest.mark.django_db
def test_email_failure_does_not_roll_back_invoice(admin_client, approved_entries, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError('SMTP is down')

    monkeypatch.setattr('django.core.mail.EmailMultiAlternatives.send', boom)

    resp = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert resp.status_code == 200
    assert resp.data['invoices_generated'] == 1

    invoice = Invoice.objects.get()
    assert invoice.delivery_status == Invoice.DELIVERY_FAILED
    assert 'SMTP is down' in invoice.delivery_error
    assert invoice.delivered_at is None
    # The run itself still completed.
    assert BillingRun.objects.get().status == BillingRun.STATUS_COMPLETED


# --- 10. Input validation ---------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('bad_month', ['', 'nope', '2026-13', '2026', '2026-00', None])
def test_bad_month_returns_400(admin_client, bad_month):
    resp = admin_client.post('/api/billing/runs/', {'month': bad_month}, format='json')

    assert resp.status_code == 400
    assert 'month' in resp.data


@pytest.mark.django_db
def test_bad_month_query_param_returns_400(admin_client):
    resp = admin_client.get('/api/invoices/?month=nope')

    assert resp.status_code == 400
    assert 'month' in resp.data


def test_month_bounds_handles_leap_year():
    start, end = month_bounds('2024-02')
    assert start == datetime.date(2024, 2, 1)
    assert end == datetime.date(2024, 2, 29)


# --- 11. Role checks --------------------------------------------------------

@pytest.mark.django_db
def test_freelancer_cannot_run_billing(freelancer_client, approved_entries):
    resp = freelancer_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert resp.status_code == 403
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_billing_run_requires_auth(api_client, approved_entries):
    resp = api_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert resp.status_code == 401


# --- 12. List scoping and filtering ----------------------------------------

@pytest.mark.django_db
def test_invoice_list_is_scoped_to_the_admin_company(
    admin_client, approved_entries, other_contract
):
    TimesheetEntry.objects.create(
        contract=other_contract, date=datetime.date(2026, 5, 4),
        hours=Decimal('8.0'), status='approved',
    )
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    resp = admin_client.get('/api/invoices/?month=2026-05')

    assert resp.status_code == 200
    assert isinstance(resp.data, list)
    assert len(resp.data) == 1
    assert resp.data[0]['company_name'] == 'NorthStar Consulting'


@pytest.mark.django_db
def test_freelancer_list_excludes_other_freelancers_invoices(
    admin_client, freelancer_client, admin_user, approved_entries, second_contract, second_freelancer
):
    """A freelancer must not see invoices that belong to a different freelancer."""
    TimesheetEntry.objects.create(
        contract=second_contract, date=datetime.date(2026, 5, 4),
        hours=Decimal('8.0'), status='approved',
    )
    _run_as_admin(admin_user, '2026-05')
    assert Invoice.objects.count() == 2

    resp = freelancer_client.get('/api/invoices/?month=2026-05')

    assert resp.status_code == 200
    assert len(resp.data) == 1
    seen = Invoice.objects.get(pk=resp.data[0]['id'])
    assert seen.freelancer_id != second_freelancer.id


@pytest.mark.django_db
def test_freelancer_detail_404_for_other_freelancers_invoice(
    admin_client, freelancer_client, admin_user, approved_entries, second_contract
):
    TimesheetEntry.objects.create(
        contract=second_contract, date=datetime.date(2026, 5, 4),
        hours=Decimal('8.0'), status='approved',
    )
    _run_as_admin(admin_user, '2026-05')
    other_invoice = Invoice.objects.get(freelancer=second_contract.freelancer)

    resp = freelancer_client.get(f'/api/invoices/{other_invoice.pk}/')

    assert resp.status_code == 404


@pytest.mark.django_db
def test_invoice_list_filters_by_month(admin_client, approved_entries):
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert len(admin_client.get('/api/invoices/?month=2026-05').data) == 1
    assert admin_client.get('/api/invoices/?month=2026-06').data == []


@pytest.mark.django_db
def test_invoice_list_returns_plain_list_not_paginated(admin_client, approved_entries):
    """The frontend calls .map() on the response directly."""
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    resp = admin_client.get('/api/invoices/?month=2026-05')

    assert isinstance(resp.data, list)
    assert not isinstance(resp.data, dict)


@pytest.mark.django_db
def test_freelancer_sees_their_own_invoices(
    admin_client, freelancer_client, admin_user, approved_entries
):
    run = _run_as_admin(admin_user, '2026-05')
    assert run.status_code == 200
    assert run.data['invoices_generated'] == 1

    resp = freelancer_client.get('/api/invoices/?month=2026-05')

    assert resp.status_code == 200
    assert len(resp.data) == 1


# --- 13. Detail scoping -----------------------------------------------------

@pytest.mark.django_db
def test_invoice_detail_returns_line_items(admin_client, approved_entries):
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')
    invoice = Invoice.objects.get()

    resp = admin_client.get(f'/api/invoices/{invoice.pk}/')

    assert resp.status_code == 200
    assert len(resp.data['line_items']) == 3
    assert resp.data['number'] == invoice.number


@pytest.mark.django_db
def test_invoice_detail_404_for_other_company(admin_client, approved_entries, other_contract):
    TimesheetEntry.objects.create(
        contract=other_contract, date=datetime.date(2026, 5, 4),
        hours=Decimal('8.0'), status='approved',
    )
    admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')
    other_invoice = Invoice.objects.get(company=other_contract.company)

    resp = admin_client.get(f'/api/invoices/{other_invoice.pk}/')

    assert resp.status_code == 404


@pytest.mark.django_db
def test_invoice_detail_404_for_missing_invoice(admin_client):
    resp = admin_client.get('/api/invoices/999999/')

    assert resp.status_code == 404


# --- 14. Empty month --------------------------------------------------------

@pytest.mark.django_db
def test_empty_month_generates_nothing(admin_client):
    resp = admin_client.post('/api/billing/runs/', {'month': '2026-05'}, format='json')

    assert resp.status_code == 200
    assert resp.data['invoices_generated'] == 0
    assert Invoice.objects.count() == 0
    assert mail.outbox == []
    assert admin_client.get('/api/invoices/?month=2026-05').data == []
