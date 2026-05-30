"""
Fexonpay - WhatsApp Payment & Invoice Bot
Clean version: fixes double responses, removes dead code, single confirm handler
"""

import os, json, subprocess, time, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from fastapi import FastAPI, Request
from together import Together
from supabase import create_client
import requests as http_requests
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pdfkit
from fexonpay_memory import MemorySystem
from fexonpay_brain import Brain
from fexonpay_finance import FinanceManager
from fexonpay_cash import CashManager
from fexonpay_quotation import QuotationManager
from fexonpay_accounts import AccountsManager
memory = MemorySystem()
brain = Brain()
finance = FinanceManager()
cash_manager = CashManager()
quotation = QuotationManager()
accounts = AccountsManager()

load_dotenv()

app = FastAPI()

together_client = Together(api_key=os.getenv("TOGETHER_API_KEY"))
db = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])

# In-memory state
pending_payments = {}       # waiting for PAY/SKIP

def get_pending(number):
    try:
        r = db.table("customers").select("pending_state").eq("whatsapp_number", number).execute()
        if r.data and r.data[0].get("pending_state"):
            return r.data[0]["pending_state"]
    except Exception as e:
        print(f"get_pending error: {e}")
    return {}

def set_pending(number, state):
    try:
        db.table("customers").update({"pending_state": state}).eq("whatsapp_number", number).execute()
    except Exception as e:
        print(f"set_pending error: {e}")

def clear_pending(number):
    set_pending(number, None)

awaiting_bank_details = {}  # waiting for bank input
pending_invoices = {}       # waiting for CONFIRM/CANCEL

# Malaysian Bank Codes
BANK_CODES = {
    'maybank': 'MBBEMYKL', 'cimb': 'CIBBMYKL', 'public bank': 'PBBEMYKL',
    'public': 'PBBEMYKL', 'rhb': 'RHBBMYKL', 'hong leong': 'HLBBMYKL',
    'ambank': 'ARBKMYKL', 'alliance': 'MFBBMYKL', 'bank islam': 'BIMBMYKL',
    'bsn': 'BSNAMYK1', 'hsbc': 'HBMBMYKL', 'ocbc': 'OCBCMYKL',
    'dummybank': 'DUMMYBANKVERIFIED'
}

# ── Helpers ──────────────────────────────────────────────────

def send_whatsapp_reply(phone: str, message: str):
    try:
        result = subprocess.run(
            ['openclaw', 'message', 'send', '--account', 'prod', '--target', phone, '--message', message],
            check=True, capture_output=True, text=True
        )
        print(f"WhatsApp sent: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"WhatsApp error (CMD): {e.stderr}")
        return False
    except Exception as e:
        print(f"WhatsApp error (EXC): {e}")
        return False

def get_or_create_customer(whatsapp_number: str):
    """Returns customer_id string (backward compatible)."""
    try:
        result = db.table('customers').select('*').eq('whatsapp_number', whatsapp_number).execute()
        if result.data:
            return result.data[0]['customer_id']
        new = db.table('customers').insert({
            'whatsapp_number': whatsapp_number,
            'business_name': f'Customer {whatsapp_number[-4:]}',
            'subscription_status': 'trial'
        }).execute()
        return new.data[0]['customer_id']
    except Exception as e:
        print(f"Customer error: {e}")
        return None

def get_customer(whatsapp_number: str) -> dict | None:
    """Returns full customer record including Billplz keys."""
    try:
        result = db.table('customers').select('*').eq('whatsapp_number', whatsapp_number).execute()
        if result.data:
            return result.data[0]
        # Auto-create if not found
        new = db.table('customers').insert({
            'whatsapp_number': whatsapp_number,
            'business_name': f'Customer {whatsapp_number[-4:]}',
            'subscription_status': 'trial'
        }).execute()
        return new.data[0]
    except Exception as e:
        print(f"Customer fetch error: {e}")
        return None

def save_customer_billplz(customer_id: str, api_key: str, collection_id: str, x_signature: str, sandbox: bool = False):
    """Save Billplz credentials for a customer."""
    try:
        db.table('customers').update({
            'billplz_api_key': api_key,
            'billplz_collection_id': collection_id,
            'billplz_x_signature': x_signature,
            'billplz_sandbox': sandbox
        }).eq('customer_id', customer_id).execute()
        return True
    except Exception as e:
        print(f"Save Billplz error: {e}")
        return False

def get_or_create_vendor(vendor_name: str):
    try:
        result = db.table('vendors').select('*').ilike('vendor_name', vendor_name).execute()
        if result.data:
            vendor_id = result.data[0]['vendor_id']
            db.table('vendors').update({
                'total_transactions': result.data[0]['total_transactions'] + 1,
                'last_updated': datetime.now().isoformat()
            }).eq('vendor_id', vendor_id).execute()
            return vendor_id
        new = db.table('vendors').insert({
            'vendor_name': vendor_name,
            'verified_status': False,
            'total_transactions': 1
        }).execute()
        return new.data[0]['vendor_id']
    except Exception as e:
        print(f"Vendor error: {e}")
        return None

def get_vendor_bank(customer_id: str, vendor_id: str):
    try:
        result = db.table('vendor_banks').select('*').eq('customer_id', customer_id).eq('vendor_id', vendor_id).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"Vendor bank error: {e}")
        return None

def save_vendor_bank(customer_id: str, vendor_id: str, bank_code: str, bank_account: str, bank_name: str):
    try:
        db.table('vendor_banks').upsert({
            'customer_id': customer_id, 'vendor_id': vendor_id,
            'bank_code': bank_code, 'bank_account': bank_account,
            'bank_name': bank_name, 'updated_at': datetime.now().isoformat()
        }, on_conflict='customer_id,vendor_id').execute()
        return True
    except Exception as e:
        print(f"Save bank error: {e}")
        return False

def parse_bank_details(text: str):
    parts = text.strip().split()
    if len(parts) < 2:
        return None, None, None
    bank_name = parts[0].lower()
    account = ''.join(parts[1:]).replace('-', '').replace(' ', '')
    bank_code = BANK_CODES.get(bank_name)
    return bank_code, account, parts[0].title()

def calculate_due_date(payment_term: str):
    today = datetime.now()
    days = {'NET7': 7, 'NET14': 14, 'NET30': 30, 'NET60': 60}
    return (today + timedelta(days=days.get(payment_term, 30))).strftime('%Y-%m-%d')

def generate_invoice_number(customer_id=None):
    try:
        query = db.table('self_invoices').select('invoice_number').order('invoice_number', desc=True).limit(1)
        if customer_id:
            query = query.eq('customer_id', customer_id)
        result = query.execute()
        if result.data:
            last_num = int(result.data[0]['invoice_number'].replace('IV-', ''))
            return f"IV-{last_num + 1:05d}"
        return "IV-00001"
    except Exception as e:
        print(f"Invoice number error: {e}")
        return f"IV-{int(time.time()) % 100000:05d}"

def create_billplz_payment(vendor: str, amount: float, ref: str, customer: dict = None):
    """Create a Billplz bill using customer's own keys, falling back to .env keys."""
    if customer and customer.get('billplz_api_key') and customer.get('billplz_collection_id'):
        api_key       = customer['billplz_api_key']
        collection_id = customer['billplz_collection_id']
        sandbox       = customer.get('billplz_sandbox', False)
    else:
        api_key       = os.environ.get('BILLPLZ_API_KEY', '')
        collection_id = os.environ.get('BILLPLZ_COLLECTION_ID', '')
        sandbox       = os.environ.get('BILLPLZ_SANDBOX', 'false') == 'true'
    if not api_key or not collection_id or api_key.startswith('your_'):
        print("Billplz: No valid API keys configured for this customer.")
        return None
    base_url = 'https://www.billplz-sandbox.com' if sandbox else 'https://www.billplz.com'
    try:
        resp = http_requests.post(
            f'{base_url}/api/v3/bills',
            auth=(api_key, ''),
            data={
                'collection_id': collection_id,
                'email': 'pay@fexonpay.com', 'name': vendor,
                'amount': str(int(amount * 100)),
                'description': f'Fexonpay payment: {vendor}',
                'reference_1_label': 'Reference', 'reference_1': ref,
                'callback_url': 'https://api.fexonpay.com/webhooks/billplz/callback'
            }
        )
        return resp.json()
    except Exception as e:
        print(f'Billplz error: {e}')
        return None

# ── AI Extraction ─────────────────────────────────────────────

async def extract_payment_with_ai(text: str):
    """Extract payment fields from free text (for incoming invoices/messages)."""
    prompt = (
        'You are a Malaysian payment extraction engine. '
        'Extract payment fields from the text below. '
        'Return ONLY valid JSON. No explanation. No markdown. '
        'Fields: vendor_name, amount, currency (default MYR), '
        'due_date (YYYY-MM-DD or null), payment_method, '
        'confidence_score (0.0 to 1.0). '
        'Invoice text: ' + text
    )
    try:
        response = together_client.chat.completions.create(
            model='meta-llama/Llama-3.3-70B-Instruct-Turbo',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=300, temperature=0.1
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"AI payment extraction error: {e}")
        return None

async def extract_invoice_details_for_self(text: str):
    """Extract full invoice details from INVOICE command."""
    prompt = (
        'You are a Malaysian invoice creation assistant. '
        'From the user message, extract ALL fields to create a self-made invoice. '
        'Return ONLY valid JSON. No explanation. No markdown. No code blocks. '
        'Fields: '
        'vendor_name (string), '
        'items (array of {description, quantity, unit_price} — if quantity not mentioned assume 1, '
        'if only total given then quantity=1 and unit_price=total), '
        'payment_mode ("cash" or "credit", default "cash"), '
        'payment_term ("NET30" default unless mentioned), '
        'description (brief summary), '
        'currency ("MYR" default). '
        'User message: ' + text
    )
    try:
        response = together_client.chat.completions.create(
            model='meta-llama/Llama-3.3-70B-Instruct-Turbo',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=300, temperature=0.1
        )
        result_text = response.choices[0].message.content.strip()
        print(f"DEBUG: Raw AI response: {result_text}")

        # Strip markdown if present
        if result_text.startswith('```'):
            lines = [l for l in result_text.split('\n') if not l.strip().startswith('```')]
            result_text = '\n'.join(lines).strip()

        start = result_text.find('{')
        end = result_text.rfind('}')
        if start != -1 and end != -1:
            result_text = result_text[start:end+1]

        return json.loads(result_text)
    except Exception as e:
        print(f"Invoice AI extraction error: {e}")
        return None

# ── Invoice Save & Email ──────────────────────────────────────

def save_self_invoice(customer_id, vendor_id, invoice_data):
    try:
        invoice_number = generate_invoice_number(customer_id)
        payment_term = invoice_data.get('payment_term', 'NET30')
        due_date = calculate_due_date(payment_term)
        items = invoice_data.get('items', [])
        subtotal = sum(item.get('quantity', 1) * item.get('unit_price', 0) for item in items)

        invoice_record = db.table('self_invoices').insert({
            'invoice_number': invoice_number,
            'customer_id': customer_id,
            'vendor_id': vendor_id,
            'vendor_name': invoice_data.get('vendor_name', 'Unknown'),
            'payment_mode': invoice_data.get('payment_mode', 'cash'),
            'invoice_date': datetime.now().strftime('%Y-%m-%d'),
            'due_date': due_date,
            'payment_term': payment_term,
            'currency': invoice_data.get('currency', 'MYR'),
            'subtotal': subtotal,
            'discount_total': 0,
            'tax_total': 0,
            'grand_total': subtotal,
            'description': invoice_data.get('description', ''),
            'status': 'ready',
            'created_at': datetime.now().isoformat()
        }).execute()

        invoice_id = invoice_record.data[0]['invoice_id']

        for i, item in enumerate(items, 1):
            qty = item.get('quantity', 1)
            unit_price = item.get('unit_price', 0)
            db.table('self_invoice_items').insert({
                'invoice_id': invoice_id,
                'item_no': i,
                'description': item.get('description', 'Item'),
                'quantity': qty,
                'unit_price': unit_price,
                'amount': qty * unit_price,
                'discount': 0,
                'tax_amount': 0
            }).execute()

        return {
            'invoice_id': invoice_id,
            'invoice_number': invoice_number,
            'vendor_name': invoice_data.get('vendor_name'),
            'items': items,
            'subtotal': subtotal,
            'grand_total': subtotal,
            'payment_term': payment_term,
            'due_date': due_date,
            'invoice_date': datetime.now().strftime('%d/%m/%Y'),
            'description': invoice_data.get('description', ''),
            'customer_id': customer_id
        }
    except Exception as e:
        print(f"Error saving self-invoice: {e}")
        return None

def generate_and_email_invoice(saved_invoice):
    """Generate PDF and email to customer."""
    company_email = os.getenv('COMPANY_EMAIL')
    app_password = os.getenv('EMAIL_APP_PASSWORD')

    if not company_email or not app_password:
        print("Skipping PDF email: COMPANY_EMAIL or EMAIL_APP_PASSWORD not set")
        return False

    # Get customer email
    customer_query = db.table('customers').select('email, unique_email_alias').eq('customer_id', saved_invoice['customer_id']).execute()
    if not customer_query.data:
        print("Customer not found for emailing.")
        return False

    customer = customer_query.data[0]
    target_email = customer.get('email') or company_email
    cc_email = customer.get('unique_email_alias')

    # Build items HTML
    items_html = ""
    for idx, item in enumerate(saved_invoice.get('items', []), 1):
        qty = item.get('quantity', 1)
        price = item.get('unit_price', 0)
        amt = qty * price
        items_html += f"""
        <tr>
            <td>{item.get('item_no', idx)}</td>
            <td>{item.get('description', 'Item')}</td>
            <td class="text-right">{qty}</td>
            <td class="text-right">{price:,.2f}</td>
            <td class="text-right">{amt:,.2f}</td>
            <td class="text-right">0.00</td>
            <td class="text-right">0.00</td>
        </tr>"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: Arial, sans-serif; color: #111; margin: 0; padding: 50px; font-size: 12px; line-height: 1.4; }}
.header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }}
.company-name {{ font-size: 20px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }}
.invoice-title {{ font-size: 28px; font-weight: 700; text-align: right; text-transform: uppercase; letter-spacing: 2px; }}
.info-section {{ display: flex; justify-content: space-between; margin-bottom: 40px; }}
.bill-to-label {{ color: #666; margin-bottom: 5px; font-size: 11px; }}
.bill-to-name {{ font-weight: 700; font-size: 14px; margin-bottom: 2px; }}
.meta-table {{ width: 250px; border-collapse: collapse; }}
.meta-table td {{ padding: 3px 0; font-size: 11px; }}
.meta-label {{ color: #555; width: 100px; }}
.meta-value {{ text-align: right; }}
.items-table {{ width: 100%; border-collapse: collapse; margin-bottom: 30px; }}
.items-table th {{ background: #f2f2f2; padding: 10px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; border-bottom: 1px solid #ddd; }}
.items-table td {{ padding: 12px 10px; border-bottom: 1px solid #eee; font-size: 11px; }}
.text-right {{ text-align: right; }}
.totals-wrapper {{ float: right; width: 280px; }}
.totals-table {{ width: 100%; border-collapse: collapse; }}
.totals-table td {{ padding: 8px 10px; font-size: 12px; }}
.grand-total-row {{ font-weight: 700; }}
.grand-total-row td {{ border-top: 1px solid #333; border-bottom: 3px double #333; padding-top: 10px; padding-bottom: 10px; }}
.clearfix::after {{ content: ""; clear: both; display: table; }}
</style>
</head>
<body>
<div class="header">
    <div class="company-name">FEXONPAY</div>
    <div class="invoice-title">INVOICE</div>
</div>
<div class="info-section">
    <div class="bill-to">
        <div class="bill-to-label">Bill To</div>
        <div class="bill-to-name">{saved_invoice['vendor_name']}</div>
        <div>{saved_invoice.get('description', '')}</div>
    </div>
    <table class="meta-table">
        <tr><td class="meta-label">Invoice No.</td><td class="meta-value">{saved_invoice['invoice_number']}</td></tr>
        <tr><td class="meta-label">Date</td><td class="meta-value">{saved_invoice.get('invoice_date', '')}</td></tr>
        <tr><td class="meta-label">Payment Term</td><td class="meta-value">{saved_invoice.get('payment_term', 'NET30')}</td></tr>
        <tr><td class="meta-label">Due Date</td><td class="meta-value">{saved_invoice.get('due_date', '')}</td></tr>
    </table>
</div>
<table class="items-table">
    <thead>
        <tr>
            <th width="40">No.</th><th>Description</th>
            <th width="60" class="text-right">Qty</th>
            <th width="80" class="text-right">Unit Price</th>
            <th width="100" class="text-right">Amount</th>
            <th width="60" class="text-right">Disc</th>
            <th width="80" class="text-right">Tax</th>
        </tr>
    </thead>
    <tbody>{items_html}</tbody>
</table>
<div class="clearfix">
    <div class="totals-wrapper">
        <table class="totals-table">
            <tr><td>Subtotal</td><td class="text-right">RM {saved_invoice['subtotal']:,.2f}</td></tr>
            <tr class="grand-total-row"><td>Total</td><td class="text-right">RM {saved_invoice['grand_total']:,.2f}</td></tr>
        </table>
    </div>
</div>
</body>
</html>"""

    filename = f"Invoice_{saved_invoice['invoice_number']}.pdf"
    import pathlib
    pathlib.Path('/home/fexonpay-prod/pdfs/invoices').mkdir(parents=True, exist_ok=True)
    filepath = f"/home/fexonpay-prod/pdfs/invoices/{filename}"

    try:
        pdfkit.from_string(html_content, filepath, options={
            'page-size': 'A4', 'encoding': 'UTF-8',
            'margin-top': '10mm', 'margin-right': '10mm',
            'margin-bottom': '10mm', 'margin-left': '10mm'
        })

        msg = MIMEMultipart()
        msg['From'] = company_email
        msg['To'] = target_email
        if cc_email:
            msg['Cc'] = cc_email
        msg['Subject'] = f"Invoice {saved_invoice['invoice_number']} — {saved_invoice['vendor_name']}"
        msg.attach(MIMEText(f"Please find attached invoice {saved_invoice['invoice_number']} for {saved_invoice['vendor_name']}.", 'plain'))

        with open(filepath, "rb") as f:
            attach = MIMEApplication(f.read(), _subtype="pdf")
            attach.add_header('Content-Disposition', 'attachment', filename=filename)
            msg.attach(attach)

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(company_email, app_password)
        destinations = [target_email] + ([cc_email] if cc_email else [])
        server.sendmail(company_email, destinations, msg.as_string())
        server.quit()

        print(f"DEBUG: Emailed PDF invoice {filename} to {target_email} (CC: {cc_email})")
        return True

    except Exception as e:
        print(f"Error generating/emailing PDF: {e}")
        return False

# ── WhatsApp Message Formatters ───────────────────────────────

def format_invoice_preview(invoice_data):
    items = invoice_data.get('items', [])
    vendor = invoice_data.get('vendor_name', 'Unknown')
    payment_term = invoice_data.get('payment_term', 'NET30')
    due_date = calculate_due_date(payment_term)
    today = datetime.now().strftime('%d/%m/%Y')
    subtotal = sum(item.get('quantity', 1) * item.get('unit_price', 0) for item in items)

    msg = "📄 *Invoice Auto-Generated*\n\n"
    msg += "┌─────────────────────────\n"
    msg += f"│ *INVOICE* (Preview)\n"
    msg += f"│ Date: {today}\n"
    msg += f"│ Term: {payment_term}\n"
    msg += f"│ Due:  {due_date}\n│\n"
    msg += f"│ Bill To: *{vendor}*\n"
    msg += f"│ {invoice_data.get('payment_mode', 'Cash').title()} Sales\n│\n"

    for i, item in enumerate(items, 1):
        qty = item.get('quantity', 1)
        unit_price = item.get('unit_price', 0)
        desc = item.get('description', 'Item')
        msg += f"│ {i}. {desc}\n"
        msg += f"│    {qty} × RM{unit_price:.2f} = RM{qty * unit_price:.2f}\n"

    msg += f"│\n│ *Total: RM{subtotal:.2f}*\n"
    msg += "└─────────────────────────\n\n"
    msg += "Reply *CONFIRM* to save\nReply *CANCEL* to discard"
    return msg

def format_invoice_saved_message(saved, email_sent=False):
    status = "Saved & Emailed! 📧" if email_sent else "Saved!"
    msg = f"✅ *Invoice #{saved['invoice_number']} {status}*\n\n"
    msg += f"Vendor: {saved['vendor_name']}\n"
    msg += f"Description: {saved.get('description', '-')}\n"
    msg += f"Total: RM{saved['grand_total']:.2f}\n"
    msg += f"Due: {saved['due_date']}\n"
    msg += f"Term: {saved['payment_term']}\n\n"
    msg += f"Reply *PAY {saved['invoice_number']}* when {saved['vendor_name']} settles this invoice."
    inv_num = saved.get('invoice_number', '')
    pdf_link = f"https://api.fexonpay.com/pdf/invoice/Invoice_{inv_num}.pdf"
    msg += f"\n\n📄 View Invoice PDF:\n{pdf_link}"
    return msg

def format_payment_confirmation(vendor_name, amount, due_date, bank_info=None):
    msg = f"💰 *Confirm Payment*\n\n"
    msg += f"Vendor: {vendor_name}\n"
    msg += f"Amount: RM{amount:.2f}\n"
    msg += f"Due: {due_date or 'Not specified'}\n"
    if bank_info:
        msg += f"Bank: {bank_info.get('bank_name', 'Unknown')} ...{bank_info['bank_account'][-4:]}\n"
    msg += f"\nReply *PAY* to proceed\nReply *SKIP* to cancel"
    return msg

# ── Webhook ───────────────────────────────────────────────────

@app.get('/')
def health_check():
    return {'status': 'Fexonpay is running', 'version': '4.0'}

@app.post('/webhooks/openclaw')
async def openclaw_webhook(request: Request):
    body = await request.json()
    text = body.get('text', '').strip()
    from_number = body.get('from', '')

    print(f'DEBUG: Received from {from_number}: {text}')

    if not text or not from_number:
        return {'status': 'ok'}
    # Drop openclaw agent echoes (reformatted copies of user messages)
    import time as _t, unicodedata as _u
    def _n(s): return _u.normalize('NFKD',s).lower().replace(' ','').replace('rm','')
    if not hasattr(openclaw_webhook,'_seen'): openclaw_webhook._seen={}
    _k=f"{from_number}:{_n(text)}"
    _now=_t.time()
    _prev=openclaw_webhook._seen.get(from_number,('',0))
    if _n(text)==_n(_prev[0]) and _now-_prev[1]<30:
        print(f'DEBUG: Dropped echo: {text}')
        return {'status':'ok'}
    openclaw_webhook._seen[from_number]=(text,_now)

    customer_id = get_or_create_customer(from_number)
    try:
        _cust_rec = db.table("customers").select("business_name").eq("customer_id", customer_id).execute().data
        business_name = _cust_rec[0].get("business_name", "") if _cust_rec else ""
    except:
        business_name = ""

    # Build current state for brain context
    state = {
        'has_pending_invoice':  get_pending(from_number).get('type') == 'invoice',
        'pending_invoice':      get_pending(from_number).get('invoice', {}),
        'has_pending_payment':  get_pending(from_number).get('type') == 'payment',
        'pending_payment':      get_pending(from_number).get('payment', {}),
        'awaiting_bank_details': from_number in awaiting_bank_details,
        'bank_details_payment': awaiting_bank_details.get(from_number, {}),
    }

    # Fast keyword pre-filter — no AI needed for simple commands
    t = text.strip().upper()
    t_raw = text.strip().lower()

    fast_result = None

    # Pending state shortcuts
    if t in ['CONFIRM', 'YES', 'OK', 'SETUJU', 'YA', 'OK BOLEH', 'CONFIRMED']:
        _ps = get_pending(from_number)
        if _ps.get('type') == 'quote':
            fast_result = {'action': 'quote_to_invoice', 'quote_number': _ps.get('quote_number'), 'reply': 'Converting quote to invoice...'}
        else:
            fast_result = {'action': 'confirm', 'reply': 'Confirming...'}
    elif t in ['CANCEL', 'BATAL', 'NO', 'TAK', 'TAK NAK', 'CANCELLED']:
        fast_result = {'action': 'cancel', 'reply': 'Cancelled.'}
    elif t.startswith('PAY IV-') or t.startswith('BAYAR IV-'):
        invoice_ref = t.split()[1]
        fast_result = {'action': 'mark_paid', 'invoice_ref': invoice_ref, 'reply': 'Marking invoice as paid...'}
    elif t in ['PAY', 'BAYAR']:
        fast_result = {'action': 'reply', 'reply': 'To mark an invoice as paid, reply:\n*PAY IV-XXXXX*\n\nExample: PAY IV-00026'}
    elif t in ['SKIP', 'LATER', 'LEPAS NI']:
        fast_result = {'action': 'skip', 'reply': 'Skipped.'}
    # Cash category selection (1-8)
    elif t in ['1','2','3','4','5','6','7','8']:
        pending = get_pending(from_number)
        if pending and pending.get('type') == 'cash_category':
            cats = {
                '1': 'materials', '2': 'utilities', '3': 'salary',
                '4': 'transport', '5': 'meals', '6': 'office supplies',
                '7': 'professional fees', '8': 'other'
            }
            chosen = cats[t]
            receipt_num = pending.get('receipt_number')
            db.table('cash_receipts').update({'category': chosen}).eq('receipt_number', receipt_num).execute()
            clear_pending(from_number)
            fast_result = {'action': 'reply', 'reply': f'✅ Category updated to *{chosen.title()}* for {receipt_num}'}
        else:
            pass  # not a category selection, let brain handle

    elif t in ['HELP', 'TOLONG', 'MENU', '?']:
        fast_result = {'action': 'help', 'reply': 'Loading menu...'}
    # Dashboard / cashflow
    elif t in ['P&L', 'PL', 'PNL', 'P AND L', 'PROFIT', 'PROFIT AND LOSS', 'UNTUNG RUGI']:
        fast_result = {'action': 'pl_statement', 'period': 'this_month', 'reply': 'Loading P&L...'}
    elif t in ['DASHBOARD', 'STATUS', 'SUMMARY', 'CASHFLOW', 'CASH FLOW', 'RINGKASAN']:
        fast_result = {'action': 'dashboard', 'reply': 'Loading dashboard...'}

    # Vendors list
    elif t.startswith('SET EMAIL ') or t.startswith('MY EMAIL '):
        new_email = text.strip().split()[-1].lower()
        if '@' in new_email:
            db.table('customers').update({'email': new_email}).eq('customer_id', customer_id).execute()
            fast_result = {'action': 'reply', 'reply': f'✅ Email updated to *{new_email}*'}
        else:
            fast_result = {'action': 'reply', 'reply': 'Invalid email. Example: *set email yourname@gmail.com*'}
    elif t in ['VENDORS', 'VENDOR LIST', 'SENARAI VENDOR']:
        fast_result = {'action': 'vendors', 'reply': 'Loading vendors...'}

    # Spend
    elif t in ['SPEND', 'SPENDING', 'BELANJA']:
        fast_result = {'action': 'spend', 'days': 30, 'reply': 'Loading spend summary...'}

    # Budget check
    elif t in ['BUDGET', 'BAJET']:
        fast_result = {'action': 'budget_check', 'reply': 'Checking budgets...'}

    # Memory
    elif t in ['MEMORY', 'INGATAN', 'PREFERENCES']:
        fast_result = {'action': 'memory', 'reply': 'Loading preferences...'}

    # Debt report
    elif t in ['FORECAST', 'RAMALAN', 'PROJECTION']:
        fast_result = {'action': 'forecast', 'reply': 'Loading forecast...'}
    elif t in ['HEALTH', 'HEALTH SCORE', 'SKOR KESIHATAN']:
        fast_result = {'action': 'health_score', 'reply': 'Loading health score...'}
    elif t in ['SST', 'TAX', 'CUKAI']:
        fast_result = {'action': 'sst_check', 'reply': 'Checking SST...'}
    elif t in ['CASHFLOW STATEMENT', 'CASH FLOW STATEMENT', 'CF']:
        fast_result = {'action': 'cashflow_statement', 'period': 'this_month', 'reply': 'Loading cashflow...'}
    elif t in ['BALANCE SHEET', 'BS', 'LEMBARAN IMBANGAN']:
        fast_result = {'action': 'balance_sheet', 'reply': 'Loading balance sheet...'}
    elif t in ['AUDIT', 'AUDIT TRAIL']:
        fast_result = {'action': 'audit_trail', 'reply': 'Loading audit trail...'}
    elif t in ['BUDGET VS ACTUAL', 'BUDGET ACTUAL']:
        fast_result = {'action': 'budget_vs_actual', 'reply': 'Loading budget vs actual...'}
    elif t in ['PAYROLL', 'GAJI', 'RUN PAYROLL']:
        fast_result = {'action': 'run_payroll', 'reply': 'Running payroll...'}
    elif t in ['QUOTES', 'QUOTATIONS', 'SEBUTHARGA']:
        fast_result = {'action': 'quote_list', 'reply': 'Loading quotes...'}
    elif t in ['CASH SUMMARY', 'CASH REPORT']:
        fast_result = {'action': 'cash_summary', 'reply': 'Loading cash summary...'}
    elif t in ['OWE', 'HUTANG', 'DEBT', 'SIAPA HUTANG']:
        fast_result = {'action': 'owe', 'reply': 'Loading debt report...'}
    # Invoice command -- starts with INVOICE or INVOIS
    elif t.startswith('INVOICE ') or t.startswith('INVOIS '):
        fast_result = None  # Let brain extract structured data

    # Quote command
    elif t.startswith('QUOTE ') or t.startswith('QUOTATION '):
        fast_result = {'action': 'quote', 'raw': text.strip(), 'reply': 'Creating quotation...'}

    # Quote to invoice
    elif t.startswith('QUOTE TO INVOICE') or t.startswith('CONVERT QUOTE'):
        parts = text.strip().split()
        qnum = parts[-1] if parts else ''
        fast_result = {'action': 'quote_to_invoice', 'quote_number': qnum, 'reply': f'Converting {qnum}...'}

    if fast_result:
        result = fast_result
    else:
        # Brain decides — only for real questions and invoice creation
        result = await brain.handle(from_number, text, state, customer_id)
    action = result.get('action', 'reply')
    reply  = result.get('reply', '')

    print(f'DEBUG: Brain action={action}')

    # ── Execute actions ───────────────────────────────────────

    if action == 'create_invoice':
        invoice_data = {
            'vendor_name':   result.get('vendor_name', 'Unknown'),
            'amount':        float(result.get('amount', 0)),
            'description':   result.get('description', ''),
            'due_date':      result.get('due_date', calculate_due_date('NET30')),
            'payment_term':  result.get('payment_term', 'NET30'),
            'currency':      result.get('currency', 'MYR'),
            'items': [{
                'description': result.get('description', 'Service'),
                'quantity':    1,
                'unit_price':  float(result.get('amount', 0))
            }]
        }
        # Apply memory preferences
        invoice_data = memory.apply_preferences_to_invoice(customer_id, invoice_data)
        set_pending(from_number, {"type": "invoice", "invoice": invoice_data})
        reply = format_invoice_preview(invoice_data)

    elif action == 'confirm':
        _pstate = get_pending(from_number)
        if _pstate.get('type') != 'invoice':
            reply = "No pending invoice to confirm."
        else:
            invoice_data = _pstate.get('invoice', {})
            clear_pending(from_number)
            vendor_id = get_or_create_vendor(invoice_data['vendor_name'])
            if not customer_id or not vendor_id:
                reply = "❌ Database error. Please try again."
            else:
                saved = save_self_invoice(customer_id, vendor_id, invoice_data)
                if not saved:
                    reply = "❌ Failed to save invoice. Please try again."
                else:
                    pending_payments[from_number] = {
                        'vendor_name':    saved['vendor_name'],
                        'vendor_id':      vendor_id,
                        'amount':         saved['grand_total'],
                        'due_date':       saved['due_date'],
                        'invoice_number': saved['invoice_number'],
                        'expires_at':     (datetime.now() + timedelta(minutes=30)).isoformat()
                    }
                    memory.learn_from_invoice(customer_id, invoice_data)
                    email_sent = generate_and_email_invoice(saved)
                    reply = format_invoice_saved_message(saved, email_sent)

    elif action == 'cancel':
        _pstate = get_pending(from_number)
        if _pstate.get('type') == 'invoice':
            vendor = _pstate.get('invoice', {}).get('vendor_name', 'Unknown')
            clear_pending(from_number)
            reply = f"❌ Invoice for {vendor} discarded."
        else:
            reply = "No pending invoice to cancel."

    elif action == 'mark_paid':
        invoice_ref = fast_result.get('invoice_ref', '')
        result = db.table('self_invoices').select('*').eq('customer_id', customer_id).eq('invoice_number', invoice_ref).execute()
        if not result.data:
            reply = f"❌ Invoice {invoice_ref} not found."
        elif result.data[0]['status'] == 'paid':
            reply = f"✅ {invoice_ref} is already marked as paid."
        else:
            inv = result.data[0]
            db.table('self_invoices').update({'status': 'paid'}).eq('customer_id', customer_id).eq('invoice_number', invoice_ref).execute()
            reply = (f"✅ *{invoice_ref} Marked as Paid*\n\n"
                     f"Client: {inv['vendor_name']}\n"
                     f"Amount: RM{float(inv['grand_total']):.2f}\n"
                     f"Date: {datetime.now().strftime('%d/%m/%Y')}")
    elif action == 'pay':
        if from_number not in pending_payments:
            reply = "No pending payment. Create an invoice first."
        else:
            payment  = pending_payments[from_number]
            customer = get_customer(from_number)
            if not customer or not customer.get('billplz_api_key'):
                reply = ("To mark an invoice as paid, reply:\n"
                         "*PAY IV-XXXXX*\n\n"
                         "Example: PAY IV-00026")
            else:
                bank_info = get_vendor_bank(customer_id, payment['vendor_id'])
                if not bank_info:
                    awaiting_bank_details[from_number] = payment
                    reply = (f"What's {payment['vendor_name']}'s bank details?\n\n"
                             f"Reply: BANK_NAME ACCOUNT_NUMBER\n"
                             f"Example: Maybank 1234567890")
                else:
                    ref  = f"FEXON-{int(time.time())}"
                    bill = create_billplz_payment(payment['vendor_name'], payment['amount'], ref, customer)
                    if bill and bill.get('url'):
                        db.table('transactions').insert({
                            'customer_id':       customer_id,
                            'vendor_id':         payment['vendor_id'],
                            'raw_message':       f"{payment['vendor_name']} RM{payment['amount']}",
                            'amount':            payment['amount'],
                            'currency':          'MYR',
                            'due_date':          payment.get('due_date'),
                            'billplz_reference': bill.get('id'),
                            'payment_status':    'pending',
                            'source_channel':    'whatsapp'
                        }).execute()
                        masked = f"...{bank_info['bank_account'][-4:]}"
                        reply = (f"✅ *Payment Link Created*\n\n"
                                 f"RM{payment['amount']:.2f} → {payment['vendor_name']}\n"
                                 f"Bank: {bank_info.get('bank_name','Unknown')} {masked}\n\n"
                                 f"{bill['url']}")
                        pending_payments.pop(from_number)
                    else:
                        reply = "❌ Payment failed. Check your Billplz settings."

    elif action == 'skip':
        if from_number in pending_payments:
            payment = pending_payments.pop(from_number)
            reply = f"Payment skipped. {payment['vendor_name']} RM{payment['amount']:.2f} not processed."
        else:
            reply = "No pending payment to skip."

    elif action == 'bank_details':
        if from_number not in awaiting_bank_details:
            reply = "No pending payment requiring bank details."
        else:
            payment   = awaiting_bank_details[from_number]
            bank_name = result.get('bank_name', '')
            account   = result.get('account_number', '')
            bank_code, account, bank_name = parse_bank_details(f"{bank_name} {account}")
            if not bank_code or not account:
                reply = "Invalid bank details. Example: Maybank 1234567890"
            else:
                save_vendor_bank(customer_id, payment['vendor_id'], bank_code, account, bank_name)
                customer = get_customer(from_number)
                ref  = f"FEXON-{int(time.time())}"
                bill = create_billplz_payment(payment['vendor_name'], payment['amount'], ref, customer)
                if bill and bill.get('url'):
                    db.table('transactions').insert({
                        'customer_id':       customer_id,
                        'vendor_id':         payment['vendor_id'],
                        'raw_message':       f"{payment['vendor_name']} RM{payment['amount']}",
                        'amount':            payment['amount'],
                        'currency':          'MYR',
                        'billplz_reference': bill.get('id'),
                        'payment_status':    'pending',
                        'source_channel':    'whatsapp'
                    }).execute()
                    reply = (f"✅ Bank saved!\n\n"
                             f"RM{payment['amount']:.2f} → {payment['vendor_name']}\n"
                             f"Bank: {bank_name} ...{account[-4:]}\n\n"
                             f"{bill['url']}\n\n"
                             f"Future payments to {payment['vendor_name']} won't ask for bank details again.")
                    awaiting_bank_details.pop(from_number)
                else:
                    reply = "❌ Payment failed. Please try again."

    elif action == 'help':
        biz = db.table("customers").select("business_name").eq("customer_id", customer_id).execute().data[0].get("business_name", "there") or "there"
        try:
            _alias_r = db.table('customer_preferences')                 .select('preference_value')                 .eq('customer_id', customer_id)                 .eq('preference_key', 'email_alias').execute()
            _alias = _alias_r.data[0]['preference_value'] if _alias_r.data else None
        except:
            _alias = None
        if not _alias:
            try:
                _cr = db.table('customers').select('unique_email_alias')                     .eq('customer_id', customer_id).execute()
                _alias = _cr.data[0].get('unique_email_alias') if _cr.data else None
            except:
                _alias = None
        email_section = (
            f"📧 *VENDOR BILLS VIA EMAIL*\n"
            f"  Forward any vendor bill (PDF/CSV/Excel) to:\n"
            f"  *{_alias}*\n"
            f"  It will be auto-extracted and saved to your records.\n\n"
        ) if _alias else (
            f"📧 *VENDOR BILLS VIA EMAIL*\n"
            f"  Ask your admin to set up your bill forwarding email.\n\n"
        )
        reply = (
            f"👋 Welcome to *Fexonpay*, {biz.split()[0]}!\n"
            f"Your AI business assistant. Here's what I can do:\n\n"
            f"📄 *INVOICING*\n"
            f"  • Invoice Ahmad RM500 cleaning\n"
            f"  • Quote Reza RM2000 rewiring\n"
            f"  • QUOTES — view all quotations\n\n"
            f"💰 *CASH TRACKING*\n"
            f"  • Received RM500 cash from Ali\n"
            f"  • Paid TNB RM150 cash\n\n"
            f"📊 *FINANCIAL REPORTS*\n"
            f"  • DASHBOARD — business snapshot\n"
            f"  • P&L — profit & loss statement\n"
            f"  • BALANCE SHEET — assets & liabilities\n"
            f"  • CASHFLOW STATEMENT\n"
            f"  • SST — tax compliance status\n"
            f"  • FORECAST — 3-month projection\n"
            f"  • HEALTH — business health score\n\n"
            f"👥 *PAYROLL*\n"
            f"  • Add employee Ali IC:901231012345 SALARY:3500\n"
            f"  • PAYROLL — run monthly payroll\n\n"
            f"🏷️ *CATEGORIES & BUDGETS*\n"
            f"  • TNB is utilities\n"
            f"  • Set cleaning budget RM2000\n"
            f"  • BUDGET CHECK\n\n"
            f"{email_section}"
            f"💡 *ADVISORY*\n"
            f"  • Ask me anything about your finances!\n"
            f"  • What is EPF?\n"
            f"  • Am I making money?\n\n"
            f"Type *HELP* anytime to see this menu."
        )
    elif action == 'quote':
        raw = result.get('raw', text)
        reply = quotation.create_quote(customer_id, raw, business_name=business_name)
        import re
        qt_match = re.search(r'QT-\d+', reply)
        if qt_match:
            set_pending(from_number, {'type': 'quote', 'quote_number': qt_match.group()})

    elif action == 'quote_to_invoice':
        quote_number = result.get('quote_number', '')
        if not quote_number:
            reply = "Please provide a quote number. Example: CONVERT QT-00001"
        else:
            inv_data = quotation.quote_to_invoice(customer_id, quote_number)
            if 'error' in inv_data:
                reply = f"❌ {inv_data['error']}"
            else:
                set_pending(from_number, {'type': 'invoice', 'invoice': inv_data})
                reply = (f"✅ Quote {quote_number} ready to convert.\n\n"
                         f"Client: {inv_data['vendor_name']}\n"
                         f"Amount: RM{inv_data['amount']:,.2f}\n"
                         f"Description: {inv_data['description']}\n\n"
                         f"Reply *CONFIRM* to create invoice or *CANCEL* to abort.")

    elif action == 'quote_list':
        reply = quotation.list_quotes(customer_id)

    elif action == 'cash_summary':
        reply = cash_manager.get_cash_summary(customer_id)

    elif action == 'cash_skip':
        _pstate = get_pending(from_number)
        if _pstate.get('type') == 'cash_pending_phone':
            clear_pending(from_number)
            reply = "✅ Cash transaction saved without confirmation."
        else:
            reply = "Nothing to skip."

    elif action == 'pl_statement':
        period = result.get('period', 'this_month')
        reply  = accounts.generate_pl(customer_id, period)

    elif action == 'balance_sheet':
        reply = accounts.generate_balance_sheet(customer_id)

    elif action == 'cashflow_statement':
        period = result.get('period', 'this_month')
        reply  = accounts.generate_cashflow(customer_id, period)

    elif action == 'sst_check':
        reply = accounts.check_sst(customer_id)

    elif action == 'remove_employee':
        reply = accounts.remove_employee(customer_id, result.get('raw', text))
    elif action == 'run_payroll':
        reply = accounts.run_payroll(customer_id)

    elif action == 'add_employee':
        reply = accounts.add_employee(customer_id, result.get('raw', text))

    elif action == 'forecast':
        reply = accounts.forecast(customer_id)

    elif action == 'health_score':
        reply = accounts.health_score(customer_id)

    elif action == 'set_category':
        reply = accounts.set_category(customer_id, result.get('raw', text))

    elif action == 'dashboard':
        reply = finance.get_dashboard(customer_id)

    elif action == 'spend':
        days = int(result.get('days', 30))
        reply = finance.get_spend_summary(customer_id, days)

    elif action == 'vendors':
        reply = finance.get_vendors_summary(customer_id)
    elif action == 'cash_record':
        result_cash = cash_manager.record_cash(customer_id, text)
        reply = result_cash.get('reply', '✅ Cash recorded.')
        if result_cash.get('needs_category'):
            set_pending(from_number, {'type': 'cash_category', 'receipt_number': result_cash.get('receipt_number')})
        # Email notification to owner
        if result_cash.get('receipt_number'):
            try:
                company_email = os.getenv('COMPANY_EMAIL')
                app_password  = os.getenv('EMAIL_APP_PASSWORD')
                cust          = get_customer(from_number)
                target_email  = (cust.get('email') if cust else None) or company_email
                if company_email and app_password and target_email:
                    direction  = 'in' if 'Received' in reply else 'out'
                    subj       = f"Cash {'Received' if direction == 'in' else 'Paid'} — {result_cash.get('receipt_number')}"
                    body       = reply.replace('*', '')
                    msg        = MIMEMultipart()
                    msg['From']    = company_email
                    msg['To']      = target_email
                    msg['Subject'] = subj
                    msg.attach(MIMEText(body, 'plain'))
                    srv = smtplib.SMTP('smtp.gmail.com', 587)
                    srv.starttls()
                    srv.login(company_email, app_password)
                    srv.sendmail(company_email, [target_email], msg.as_string())
                    srv.quit()
                    print(f"DEBUG: Cash receipt email sent to {target_email}")
            except Exception as e:
                print(f"DEBUG: Cash email error: {e}")
    elif action == 'owe':
        ar = db.table('self_invoices').select('*').eq('customer_id', customer_id).neq('status', 'paid').execute()
        ap = db.table('transactions').select('*').eq('customer_id', customer_id).eq('payment_status', 'pending').execute()
        reply = 'Debt Report\n\nClients Owe You:\n'
        if not ar.data: reply += 'None\n'
        else:
            total = 0
            for i in ar.data:
                amt = float(i.get('grand_total', 0)); total += amt
                reply += f'- {i.get("vendor_name")}: RM{amt:,.2f}\n'
            reply += f'   Total: RM{total:,.2f}\n'
        reply += '\nYou Owe Vendors:\n'
        if not ap.data: reply += 'None\n'
        else:
            total = 0
            for i in ap.data:
                amt = float(i.get('amount', 0)); total += amt
                reply += f'- {i.get("vendor_name", "Vendor")}: RM{amt:,.2f}\n'
            reply += f'   Total: RM{total:,.2f}\n'

    elif action == 'budget_set':
        category = result.get('category', '')
        amount   = float(result.get('amount', 0))
        if category and amount:
            pref_key = f"budget_{category.lower()}"
            db.table('customer_preferences').upsert({
                'customer_id':    customer_id,
                'preference_key': pref_key,
                'preference_value': str(amount),
                'confidence':     10,
                'last_seen':      datetime.now().isoformat()
            }, on_conflict='customer_id,preference_key').execute()
            reply = f"✅ Budget set: {category.title()} → RM{amount:.2f}/month"
        else:
            reply = "Please specify a category and amount. Example: set cleaning budget RM2000"

    elif action == 'budget_check':
        reply = finance.get_budget_check(customer_id)

    elif action == 'budget_vs_actual':
        reply = accounts.budget_vs_actual(customer_id)

    elif action == 'audit_trail':
        reply = accounts.get_audit_trail(customer_id)

    elif action == 'setup_billplz':
        api_key       = result.get('api_key', '')
        collection_id = result.get('collection_id', '')
        x_signature   = result.get('x_signature', '')
        if api_key and collection_id:
            if save_customer_billplz(customer_id, api_key, collection_id, x_signature):
                reply = (f"✅ *Billplz configured!*\n\n"
                         f"API Key: ...{api_key[-4:]}\n"
                         f"Collection: {collection_id}\n\n"
                         "Payments are now enabled.")
            else:
                reply = "❌ Failed to save Billplz settings. Please try again."
        else:
            reply = "Please provide both API key and collection ID."

    elif action == 'memory':
        reply = memory.handle_memory_command(customer_id)

    elif action == 'wrong':
        description = result.get('description', text)
        reply = memory.handle_wrong_command(customer_id, f"WRONG {description}")

    elif action == 'yes_remember':
        reply = memory.handle_yes_remember(customer_id)

    elif action == 'forget':
        topic = result.get('topic', '')
        reply = memory.handle_forget_command(customer_id, f"FORGET {topic}")

    elif action == 'vendor_history':
        vendor_name = result.get('vendor_name', '')
        reply = finance.get_vendor_history(customer_id, vendor_name)

    # 'reply' action — brain already set reply above

    send_whatsapp_reply(from_number, reply)
    return {'status': 'ok'}

# ── Billplz Callback ──────────────────────────────────────────

@app.post('/webhooks/billplz/callback')
async def billplz_callback(request: Request):
    try:
        body = await request.json()
        bill_id = body.get('id')
        if body.get('paid', False):
            db.table('transactions').update({'payment_status': 'paid'}).eq('billplz_reference', bill_id).execute()
            print(f'Payment confirmed: {bill_id}')
        return {'status': 'ok'}
    except Exception as e:
        print(f'Billplz callback error: {e}')
        return {'status': 'error', 'message': str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
