"""
fexonpay_quotation.py
Fexonpay — Quotation System

Handles:
1. Create quotation from natural language
2. Generate professional PDF
3. Email PDF to client
4. Convert quote to invoice
5. List quotes

Usage in main.py:
    from fexonpay_quotation import QuotationManager
    quotation = QuotationManager()
"""

import os
import re
import json
import smtplib
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timedelta
from supabase import create_client
from together import Together
from dotenv import load_dotenv
import pdfkit

load_dotenv('/home/fexonpay-prod/.env')


class QuotationManager:

    def __init__(self):
        self.db = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
        self.ai = Together(api_key=os.environ['TOGETHER_API_KEY'])

    # ── Quote number generator ─────────────────────────────────────────────────

    def _next_quote_number(self, customer_id: str) -> str:
        try:
            # Scoped to customer to avoid cross-customer duplicates
            result = self.db.table('quotations') \
                .select('quote_number') \
                .eq('customer_id', customer_id) \
                .order('created_at', desc=True) \
                .limit(1).execute()
            if result.data:
                last = result.data[0]['quote_number']
                num = int(last.split('-')[-1]) + 1
            else:
                num = 1
            return f"QT-{num:05d}"
        except:
            import time
            return f"QT-{int(time.time())}"

    # ── Get customer info ──────────────────────────────────────────────────────

    def _get_customer(self, customer_id: str) -> dict:
        try:
            r = self.db.table('customers').select('*').eq('customer_id', customer_id).execute()
            return r.data[0] if r.data else {}
        except:
            return {}

    # ── AI extraction ──────────────────────────────────────────────────────────

    def _extract_quote_details(self, raw_text: str) -> dict:
        """Use AI to extract quote details from natural language."""
        try:
            prompt = f"""Extract quotation details from this text and return ONLY valid JSON.

Text: "{raw_text}"

Return this exact JSON structure:
{{
  "client_name": "client name or empty string",
  "client_email": "email or empty string",
  "client_phone": "phone or empty string",
  "client_company": "company name or empty string",
  "items": [
    {{"description": "item description", "qty": 1, "unit_price": 0.0, "taxed": false}}
  ],
  "notes": "any special notes or empty string",
  "valid_days": 30
}}

Rules:
- client_name is the PERSON'S NAME only — never an email address
- Email addresses go in client_email field only
- The name comes BEFORE the email in the text — extract it correctly
- Example: "Quote Ahmad ahmad@gmail.com RM500 cleaning" → client_name="Ahmad", client_email="ahmad@gmail.com"
- Example: "Quote Sharvin sharvn034@gmail.com RM5600 supply" → client_name="Sharvin", client_email="sharvn034@gmail.com"
- Extract all line items separately
- If one price mentioned with description, treat as single item
- qty defaults to 1
- taxed defaults to false
- valid_days defaults to 30
- Return ONLY the JSON, no other text"""

            response = self.ai.chat.completions.create(
                model='meta-llama/Llama-3.3-70B-Instruct-Turbo',
                messages=[{'role': 'user', 'content': prompt}],
                max_tokens=500,
                temperature=0.1
            )
            raw = response.choices[0].message.content.strip()
            # Clean JSON
            start = raw.find('{')
            end = raw.rfind('}')
            if start != -1 and end != -1:
                return json.loads(raw[start:end+1])
        except Exception as e:
            print(f"[Quotation] AI extraction error: {e}")
        return {}

    # ── PDF generation ─────────────────────────────────────────────────────────

    def _generate_pdf(self, quote_data: dict, customer: dict) -> bytes:
        """Generate professional quote PDF matching the reference design."""

        business_name = customer.get('business_name', 'Your Business')
        quote_number  = quote_data['quote_number']
        date_str      = datetime.now().strftime('%d/%m/%Y')
        valid_until   = (datetime.now() + timedelta(days=quote_data.get('valid_days', 30))).strftime('%d/%m/%Y')
        client_name   = quote_data.get('client_name', '')
        client_email  = quote_data.get('client_email', '')
        client_phone  = quote_data.get('client_phone', '')
        client_company= quote_data.get('client_company', '')
        items         = quote_data.get('items', [])
        notes         = quote_data.get('notes', '')

        # Calculate totals
        subtotal = 0.0
        taxable  = 0.0
        tax_rate = 0.06  # 6% SST
        rows_html = ''

        for item in items:
            qty        = float(item.get('qty', 1))
            unit_price = float(item.get('unit_price', 0))
            amount     = qty * unit_price
            taxed      = item.get('taxed', False)
            subtotal  += amount
            if taxed:
                taxable += amount

            rows_html += f"""
            <tr>
                <td>{item.get('description', '')}</td>
                <td class="num">{unit_price:,.2f}</td>
                <td class="num">{int(qty)}</td>
                <td class="center">{'X' if taxed else ''}</td>
                <td class="num">{amount:,.2f}</td>
            </tr>"""

        # Add empty rows to fill table
        for _ in range(max(0, 8 - len(items))):
            rows_html += """
            <tr class="empty-row">
                <td>&nbsp;</td><td></td><td></td><td></td><td>-</td>
            </tr>"""

        tax_due   = taxable * tax_rate
        total     = subtotal + tax_due

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: Arial, sans-serif; font-size: 11px; color: #333; padding: 30px; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }}
  .company-info {{ flex: 1; }}
  .company-name {{ font-size: 20px; font-weight: bold; color: #333; margin-bottom: 5px; }}
  .quote-title {{ font-size: 32px; font-weight: bold; color: #2563eb; text-align: right; }}
  .quote-meta {{ text-align: right; margin-top: 10px; }}
  .quote-meta table {{ margin-left: auto; border-collapse: collapse; }}
  .quote-meta td {{ padding: 2px 5px; }}
  .quote-meta td:first-child {{ text-align: right; font-weight: bold; color: #555; }}
  .quote-meta td:last-child {{ border: 1px solid #ccc; min-width: 100px; padding: 2px 8px; }}
  .section-header {{ background: #2563eb; color: white; font-weight: bold; padding: 5px 8px; margin: 15px 0 5px 0; }}
  .client-info {{ padding: 5px 0; line-height: 1.8; }}
  table.items {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
  table.items th {{ background: #2563eb; color: white; padding: 6px 8px; text-align: left; }}
  table.items th.num {{ text-align: right; }}
  table.items th.center {{ text-align: center; }}
  table.items td {{ padding: 5px 8px; border-bottom: 1px solid #eee; }}
  table.items td.num {{ text-align: right; }}
  table.items td.center {{ text-align: center; }}
  table.items tr.empty-row td {{ color: #ccc; }}
  table.items tr:nth-child(even) {{ background: #f9f9f9; }}
  .totals {{ display: flex; justify-content: flex-end; margin-top: 10px; }}
  .totals table {{ border-collapse: collapse; min-width: 250px; }}
  .totals td {{ padding: 4px 8px; }}
  .totals td:first-child {{ text-align: right; color: #555; }}
  .totals td:last-child {{ border: 1px solid #ccc; text-align: right; min-width: 100px; }}
  .totals tr.total-row td {{ font-weight: bold; background: #f0f0f0; }}
  .terms {{ margin-top: 20px; }}
  .terms-header {{ background: #2563eb; color: white; font-weight: bold; padding: 5px 8px; }}
  .terms-content {{ padding: 8px; line-height: 1.8; font-size: 10px; }}
  .signature {{ margin-top: 15px; font-size: 10px; }}
  .footer {{ margin-top: 20px; text-align: center; font-size: 10px; color: #555; border-top: 1px solid #eee; padding-top: 10px; }}
</style>
</head>
<body>

<div class="header">
  <div class="company-info">
    <div class="company-name">{business_name}</div>
    <div>Powered by Fexonpay</div>
    <div>Email: {customer.get('email', '')}</div>
    <br>
    <div>Prepared by: {business_name}</div>
  </div>
  <div>
    <div class="quote-title">QUOTE</div>
    <div class="quote-meta">
      <table>
        <tr><td>DATE</td><td>{date_str}</td></tr>
        <tr><td>QUOTE #</td><td>{quote_number}</td></tr>
        <tr><td>CUSTOMER ID</td><td>{client_name[:10] if client_name else '-'}</td></tr>
        <tr><td>VALID UNTIL</td><td>{valid_until}</td></tr>
      </table>
    </div>
  </div>
</div>

<div class="section-header">CUSTOMER</div>
<div class="client-info">
  <div>{client_name}</div>
  <div>{client_company}</div>
  <div>{client_phone}</div>
  <div>{client_email}</div>
</div>

<table class="items">
  <thead>
    <tr>
      <th style="width:45%">DESCRIPTION</th>
      <th class="num" style="width:15%">UNIT PRICE</th>
      <th class="num" style="width:8%">QTY</th>
      <th class="center" style="width:8%">TAXED</th>
      <th class="num" style="width:15%">AMOUNT</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<div class="totals">
  <table>
    <tr><td>Subtotal</td><td>RM {subtotal:,.2f}</td></tr>
    <tr><td>Taxable</td><td>RM {taxable:,.2f}</td></tr>
    <tr><td>Tax rate</td><td>6.000%</td></tr>
    <tr><td>Tax due</td><td>RM {tax_due:,.2f}</td></tr>
    <tr><td>Other</td><td>RM -</td></tr>
    <tr class="total-row"><td>TOTAL</td><td>RM {total:,.2f}</td></tr>
  </table>
</div>

<div class="terms">
  <div class="terms-header">TERMS AND CONDITIONS</div>
  <div class="terms-content">
    1. Customer will be billed after indicating acceptance of this quote<br>
    2. Payment will be due prior to delivery of service and goods<br>
    3. Please contact us to confirm acceptance of this quotation<br>
    {'4. Notes: ' + notes if notes else ''}
    <br><br>
    <div class="signature">
      x ___________________________________<br>
      Print Name:
    </div>
  </div>
</div>

<div class="footer">
  If you have any questions about this price quote, please contact {business_name}<br>
  <strong>Thank You For Your Business!</strong>
</div>

</body>
</html>"""

        import pathlib
        pathlib.Path('/home/fexonpay-prod/pdfs/quote').mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w') as f:
            f.write(html)
            html_path = f.name

        # Save PDF permanently for WhatsApp link
        quote_num = quote_data.get('quote_number', 'QT-00001')
        pdf_path = f'/home/fexonpay-prod/pdfs/quote/Quote_{quote_num}.pdf'

        try:
            pdfkit.from_file(html_path, pdf_path, options={
                'page-size': 'A4',
                'margin-top': '10mm',
                'margin-bottom': '10mm',
                'margin-left': '10mm',
                'margin-right': '10mm',
                'encoding': 'UTF-8',
                'quiet': ''
            })
            print(f"[Quotation] PDF saved to {pdf_path}")
        except Exception as pdf_err:
            print(f"[Quotation] PDF generation error: {pdf_err}")
            os.unlink(html_path)
            return b""

        if not os.path.exists(pdf_path):
            print(f"[Quotation] PDF file not found after generation: {pdf_path}")
            os.unlink(html_path)
            return b""

        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        os.unlink(html_path)
        return pdf_bytes

    # ── Email PDF ──────────────────────────────────────────────────────────────

    def _email_quote(self, pdf_bytes: bytes, quote_data: dict, customer: dict):
        """Email the quote PDF to the client."""
        company_email = os.getenv('COMPANY_EMAIL')
        app_password  = os.getenv('EMAIL_APP_PASSWORD')
        if not company_email or not app_password:
            print("[Quotation] Email not configured")
            return False

        client_email = quote_data.get('client_email', '')
        if not client_email:
            print("[Quotation] No client email provided")
            return False

        try:
            business_name = customer.get('business_name', 'Fexonpay')
            quote_number  = quote_data['quote_number']

            msg = MIMEMultipart()
            msg['From']    = company_email
            msg['To']      = client_email
            msg['Subject'] = f"Quotation {quote_number} from {business_name}"

            body = f"""Dear {quote_data.get('client_name', 'Client')},

Please find attached your quotation {quote_number} from {business_name}.

This quotation is valid for {quote_data.get('valid_days', 30)} days.

To accept this quotation, please reply to this email or contact us directly.

Thank you for your business.

Best regards,
{business_name}
Powered by Fexonpay"""

            msg.attach(MIMEText(body, 'plain'))

            pdf_part = MIMEApplication(pdf_bytes, _subtype='pdf')
            pdf_part.add_header('Content-Disposition', 'attachment',
                                filename=f'{quote_number}.pdf')
            msg.attach(pdf_part)

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(company_email, app_password)
                server.send_message(msg)

            print(f"[Quotation] Email sent to {client_email}")
            return True

        except Exception as e:
            print(f"[Quotation] Email error: {e}")
            return False

    # ── Save to Supabase ───────────────────────────────────────────────────────

    def _save_quote(self, customer_id: str, quote_data: dict, business_name: str = '') -> dict:
        try:
            items   = quote_data.get('items', [])
            subtotal = sum(float(i.get('qty', 1)) * float(i.get('unit_price', 0)) for i in items)
            taxable  = sum(float(i.get('qty', 1)) * float(i.get('unit_price', 0)) for i in items if i.get('taxed'))
            tax_due  = taxable * 0.06
            total    = subtotal + tax_due

            record = {
                'customer_id':    customer_id,
                'business_name':  business_name,
                'quote_number':   quote_data['quote_number'],
                'client_name':    quote_data.get('client_name', ''),
                'client_email':   quote_data.get('client_email', ''),
                'client_phone':   quote_data.get('client_phone', ''),
                'items':          items,
                'subtotal':       subtotal,
                'tax_amount':     tax_due,
                'total_amount':   total,
                'notes':          quote_data.get('notes', ''),
                'valid_days':     quote_data.get('valid_days', 30),
                'status':         'sent',
                'created_at':     datetime.now().isoformat()
            }

            result = self.db.table('quotations').insert(record).execute()
            return result.data[0] if result.data else record

        except Exception as e:
            print(f"[Quotation] Save error: {e}")
            return quote_data

    # ── PUBLIC: Create quote ───────────────────────────────────────────────────

    def create_quote(self, customer_id: str, raw_text: str, business_name: str = '') -> str:
        """Main entry point. Parse text, generate PDF, email, save."""
        try:
            customer = self._get_customer(customer_id)
            if not customer:
                return "❌ Could not find your account. Please try again."

            # Extract details via AI
            details = self._extract_quote_details(raw_text)
            if not details or not details.get('items'):
                return ("❌ Could not understand the quotation details.\n\n"
                        "Try: QUOTE [client name] [email] RM[amount] [description]\n"
                        "Example: QUOTE Ahmad ahmad@gmail.com RM3500 Electrical rewiring job")

            # Generate quote number
            details['quote_number'] = self._next_quote_number(customer_id)

            # Generate PDF
            pdf_bytes = self._generate_pdf(details, customer)

            # Save to database
            self._save_quote(customer_id, details, business_name=customer.get('business_name', ''))

            # Email if client email provided
            emailed = False
            if details.get('client_email'):
                emailed = self._email_quote(pdf_bytes, details, customer)

            # Build response
            items    = details.get('items', [])
            subtotal = sum(float(i.get('qty', 1)) * float(i.get('unit_price', 0)) for i in items)
            taxable  = sum(float(i.get('qty', 1)) * float(i.get('unit_price', 0)) for i in items if i.get('taxed'))
            total    = subtotal + (taxable * 0.06)

            reply = f"✅ *Quotation Created*\n\n"
            reply += f"📋 Quote #: {details['quote_number']}\n"
            reply += f"👤 Client: {details.get('client_name', 'N/A')}\n"
            if details.get('client_company'):
                reply += f"🏢 Company: {details['client_company']}\n"
            reply += f"\n*Items:*\n"
            for item in items:
                qty   = float(item.get('qty', 1))
                price = float(item.get('unit_price', 0))
                reply += f"• {item['description']} x{int(qty)} @ RM{price:,.2f} = RM{qty*price:,.2f}\n"
            reply += f"\n💰 *Total: RM{total:,.2f}*\n"
            reply += f"📅 Valid until: {(datetime.now() + timedelta(days=details.get('valid_days', 30))).strftime('%d %b %Y')}\n"

            quote_num = details['quote_number']
            pdf_link = f"https://api.fexonpay.com/pdf/quote/Quote_{quote_num}.pdf"
            reply += f"\n\n📄 View PDF:\n{pdf_link}"
            reply += f"\n\n💡 To convert, reply: *confirm*"

            if emailed:
                reply += f"\n📧 Also sent to {details['client_email']}"
            elif details.get('client_email'):
                reply += f"\n⚠️ Could not email to {details['client_email']}"
            else:
                reply += f"\n💡 Add client email to also send via email"

            return reply

        except Exception as e:
            print(f"[Quotation] create_quote error: {e}")
            return "❌ Error creating quotation. Please try again."

    # ── PUBLIC: List quotes ────────────────────────────────────────────────────

    def list_quotes(self, customer_id: str) -> str:
        try:
            result = self.db.table('quotations') \
                .select('quote_number, client_name, total_amount, status, created_at') \
                .eq('customer_id', customer_id) \
                .order('created_at', desc=True) \
                .limit(10).execute()

            if not result.data:
                return "No quotations found. Create one with: QUOTE [client] [email] RM[amount] [description]"

            reply = "📋 *Recent Quotations*\n\n"
            for q in result.data:
                date = q['created_at'][:10]
                reply += f"• {q['quote_number']} | {q['client_name']} | RM{float(q['total_amount']):,.2f} | {q['status']} | {date}\n"
            return reply

        except Exception as e:
            print(f"[Quotation] list error: {e}")
            return "Could not load quotations."

    # ── PUBLIC: Convert quote to invoice ──────────────────────────────────────

    def quote_to_invoice(self, customer_id: str, quote_number: str) -> dict:
        """Convert a quotation to an invoice. Returns invoice data dict."""
        try:
            result = self.db.table('quotations') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .eq('quote_number', quote_number).execute()

            if not result.data:
                return {'error': f"Quote {quote_number} not found."}

            quote = result.data[0]
            items = quote.get('items', [])

            # Build invoice data compatible with existing invoice flow
            invoice_data = {
                'vendor_name':   quote['client_name'],
                'amount':        float(quote['total_amount']),
                'description':   f"From quotation {quote_number}",
                'payment_term':  'NET30',
                'currency':      'MYR',
                'items':         items,
                'quote_number':  quote_number
            }

            # Mark quote as converted
            self.db.table('quotations') \
                .update({'status': 'converted'}) \
                .eq('quote_number', quote_number).execute()

            return invoice_data

        except Exception as e:
            print(f"[Quotation] quote_to_invoice error: {e}")
            return {'error': str(e)}

    def get_latest_open_quote(self, customer_id: str) -> dict:
        """Returns the most recent unconverted quote, or error if none/multiple."""
        try:
            result = self.db.table('quotations') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .eq('status', 'sent') \
                .order('created_at', desc=True) \
                .execute()
            if not result.data:
                return {'error': 'No open quotes to convert.'}
            if len(result.data) > 1:
                nums = ', '.join(q['quote_number'] for q in result.data)
                return {'error': f"Multiple open quotes: {nums}. Use: confirm QT-XXXXX"}
            return result.data[0]
        except Exception as e:
            print(f"[Quotation] get_latest_open_quote error: {e}")
            return {'error': str(e)}
