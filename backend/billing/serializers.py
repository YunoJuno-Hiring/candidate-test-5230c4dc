from rest_framework import serializers

from .models import Invoice, InvoiceLineItem


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = ['id', 'date', 'description', 'hours', 'daily_rate', 'amount']


class InvoiceSerializer(serializers.ModelSerializer):
    """List shape: enough to render a row and a total, no line items."""
    company_name = serializers.CharField(source='company.name', read_only=True)
    freelancer_name = serializers.CharField(source='freelancer.name', read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id', 'number', 'company', 'company_name',
            'freelancer', 'freelancer_name', 'period_start', 'period_end',
            'subtotal', 'tax', 'total', 'currency',
            'status', 'delivery_status', 'created_at',
        ]


class InvoiceDetailSerializer(InvoiceSerializer):
    line_items = InvoiceLineItemSerializer(many=True, read_only=True)

    class Meta(InvoiceSerializer.Meta):
        fields = InvoiceSerializer.Meta.fields + [
            'line_items', 'contract_start', 'contract_end',
            'delivered_at', 'delivery_error',
        ]
