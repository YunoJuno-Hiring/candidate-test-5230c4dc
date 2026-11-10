from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Invoice
from .serializers import InvoiceDetailSerializer, InvoiceSerializer
from .services import month_bounds, run_billing


def invoices_for_user(user):
    """Same role-branching pattern as contracts/views.py."""
    if hasattr(user, 'company_admin'):
        return Invoice.objects.filter(company=user.company_admin.company)
    if hasattr(user, 'freelancer'):
        return Invoice.objects.filter(freelancer=user.freelancer)
    return Invoice.objects.none()


class BillingRunView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not hasattr(request.user, 'company_admin'):
            return Response(
                {'detail': 'Only company admins can run billing.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        month = request.data.get('month', '')
        try:
            run = run_billing(month, triggered_by=request.user)
        except ValueError as exc:
            return Response({'month': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'run_id': str(run.id),
            'invoices_generated': run.invoices_generated,
            'invoices_skipped': run.invoices_skipped,
            'status': run.status,
        })


class InvoiceListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = invoices_for_user(request.user).select_related('company', 'freelancer')
        month = request.query_params.get('month')
        if month:
            try:
                start, end = month_bounds(month)
            except ValueError as exc:
                return Response({'month': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(period_start=start)
        # Plain list — the frontend calls .map() on the response directly.
        return Response(InvoiceSerializer(qs, many=True).data)


class InvoiceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            invoice = (
                invoices_for_user(request.user)
                .select_related('company', 'freelancer')
                .prefetch_related('line_items')
                .get(pk=pk)
            )
        except Invoice.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(InvoiceDetailSerializer(invoice).data)
