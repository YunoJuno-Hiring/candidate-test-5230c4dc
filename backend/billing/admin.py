from django.contrib import admin

from .models import BillingRun, Invoice, InvoiceLineItem


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0
    readonly_fields = ['date', 'description', 'hours', 'daily_rate', 'amount']
    can_delete = False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['number', 'company', 'freelancer', 'period_start', 'total', 'delivery_status']
    list_filter = ['period_start', 'delivery_status', 'status']
    search_fields = ['number', 'company__name', 'freelancer__name']
    inlines = [InvoiceLineItemInline]


@admin.register(BillingRun)
class BillingRunAdmin(admin.ModelAdmin):
    """Runs are an audit trail: inspectable, never editable."""
    list_display = ['id', 'period_start', 'status', 'invoices_generated', 'invoices_skipped', 'created_at']
    list_filter = ['status', 'period_start']
    readonly_fields = [f.name for f in BillingRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
