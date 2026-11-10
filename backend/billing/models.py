import uuid
from decimal import Decimal

from django.db import models

from accounts.models import Company, Freelancer
from contracts.models import Contract, TimesheetEntry


class BillingRun(models.Model):
    """One invocation of the monthly billing run. Audit trail + run_id."""
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Running'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    invoices_generated = models.PositiveIntegerField(default=0)
    invoices_skipped = models.PositiveIntegerField(default=0)
    triggered_by = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='billing_runs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Billing run {self.id} for {self.period_start:%Y-%m} ({self.status})'


class Invoice(models.Model):
    """One invoice per Company x Freelancer x month."""
    STATUS_ISSUED = 'issued'
    STATUS_VOID = 'void'
    STATUS_CHOICES = [
        (STATUS_ISSUED, 'Issued'),
        (STATUS_VOID, 'Void'),
    ]

    DELIVERY_PENDING = 'pending'
    DELIVERY_SENT = 'sent'
    DELIVERY_FAILED = 'failed'
    DELIVERY_CHOICES = [
        (DELIVERY_PENDING, 'Pending'),
        (DELIVERY_SENT, 'Sent'),
        (DELIVERY_FAILED, 'Failed'),
    ]

    number = models.CharField(max_length=32, unique=True)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='invoices')
    freelancer = models.ForeignKey(Freelancer, on_delete=models.PROTECT, related_name='invoices')
    billing_run = models.ForeignKey(
        BillingRun, null=True, on_delete=models.SET_NULL, related_name='invoices',
    )

    # Period bounds — period_start is always the 1st. This is the idempotency key.
    period_start = models.DateField()
    period_end = models.DateField()

    # Narrative snapshot: contract dates as they were at generation time.
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='GBP')

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED)
    delivery_status = models.CharField(max_length=10, choices=DELIVERY_CHOICES, default=DELIVERY_PENDING)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_error = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period_start', 'freelancer__name']
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'freelancer', 'period_start'],
                name='uniq_invoice_company_freelancer_period',
            ),
        ]
        indexes = [models.Index(fields=['company', 'period_start'])]

    def __str__(self):
        return f'{self.number} — {self.company.name} / {self.freelancer.name}'


class InvoiceLineItem(models.Model):
    """A snapshot of one approved timesheet entry as billed."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='line_items')
    # SET_NULL: invoice history must survive timesheet deletion.
    timesheet_entry = models.ForeignKey(
        TimesheetEntry, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='invoice_line_items',
    )
    contract = models.ForeignKey(Contract, null=True, on_delete=models.SET_NULL)

    date = models.DateField()
    description = models.CharField(max_length=255)
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    daily_rate = models.DecimalField(max_digits=8, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f'{self.date} — {self.hours}h @ {self.daily_rate}'
