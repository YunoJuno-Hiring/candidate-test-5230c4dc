from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.utils.html import escape

from .models import Invoice


def invoice_recipients(invoice):
    """Company billing address + the freelancer's own address.

    FreelancerSerializer deliberately does not expose the email, so read the
    User relation directly rather than going through the serializer.
    """
    return [invoice.company.billing_email, invoice.freelancer.user.email]


def render_invoice_text(invoice) -> str:
    lines = [
        f'Invoice {invoice.number}',
        f'Period: {invoice.period_start:%d %b %Y} – {invoice.period_end:%d %b %Y}',
        f'Billed to: {invoice.company.name}',
        f'Freelancer: {invoice.freelancer.name}',
        '',
    ]
    for li in invoice.line_items.all():
        lines.append(
            f'{li.date:%d %b %Y}  {li.hours}h x {li.daily_rate} = {li.amount}'
        )
    lines += [
        '',
        f'Subtotal: {invoice.subtotal}',
        f'Total:    {invoice.total} {invoice.currency}',
    ]
    return '\n'.join(lines)


def render_invoice_html(invoice) -> str:
    """HTML alternative. Same content as the text renderer, escaped for safety."""
    rows = ''.join(
        '<tr>'
        f'<td>{li.date:%d %b %Y}</td>'
        f'<td class="num">{li.hours}</td>'
        f'<td class="num">{li.daily_rate}</td>'
        f'<td class="num">{li.amount}</td>'
        '</tr>'
        for li in invoice.line_items.all()
    )
    return f"""<!DOCTYPE html>
<html>
  <body style="font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <h1 style="font-size: 20px; margin: 0 0 4px;">Invoice {escape(invoice.number)}</h1>
    <p style="color: #64748b; margin: 0 0 16px;">
      Period: {invoice.period_start:%d %b %Y} – {invoice.period_end:%d %b %Y}
    </p>
    <p style="margin: 0 0 4px;"><strong>Billed to:</strong> {escape(invoice.company.name)}</p>
    <p style="margin: 0 0 16px;"><strong>Freelancer:</strong> {escape(invoice.freelancer.name)}</p>
    <table style="border-collapse: collapse; width: 100%; font-size: 14px;">
      <thead>
        <tr style="background: #f8fafc; text-align: left;">
          <th style="padding: 8px; border-bottom: 1px solid #e2e8f0;">Date</th>
          <th style="padding: 8px; border-bottom: 1px solid #e2e8f0;">Hours</th>
          <th style="padding: 8px; border-bottom: 1px solid #e2e8f0;">Rate</th>
          <th style="padding: 8px; border-bottom: 1px solid #e2e8f0;">Amount</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <p style="margin: 16px 0 0;"><strong>Subtotal:</strong> {invoice.subtotal}</p>
    <p style="margin: 4px 0 0;"><strong>Total:</strong> {invoice.total} {escape(invoice.currency)}</p>
  </body>
</html>"""


def deliver_invoice(invoice) -> None:
    """Send the invoice to both parties. Never raises — records the outcome.

    Called AFTER the invoice transaction has committed, so a failure here can
    never roll back an issued invoice.
    """
    try:
        for recipient in invoice_recipients(invoice):
            msg = EmailMultiAlternatives(
                subject=f'Invoice {invoice.number} — {invoice.period_start:%B %Y}',
                body=render_invoice_text(invoice),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
            )
            msg.attach_alternative(render_invoice_html(invoice), 'text/html')
            msg.send()
    except Exception as exc:  # SMTP is a network boundary
        invoice.delivery_status = Invoice.DELIVERY_FAILED
        invoice.delivery_error = str(exc)[:2000]
        invoice.save(update_fields=['delivery_status', 'delivery_error'])
        return
    invoice.delivery_status = Invoice.DELIVERY_SENT
    invoice.delivered_at = timezone.now()
    invoice.delivery_error = ''
    invoice.save(update_fields=['delivery_status', 'delivered_at', 'delivery_error'])
