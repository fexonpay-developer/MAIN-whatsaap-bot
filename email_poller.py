"""
Fexonpay - Email Invoice Poller v2
Polls fexonpay@gmail.com every 3 minutes.
Routes emails to correct customer via Gmail alias (fexonpay+ALIAS@gmail.com).
Extracts invoice details via AI, saves to Supabase, notifies customer via WhatsApp.
Supports PDF, CSV, Excel attachments and multiple invoices per email.
"""

import imaplib
import email
import os
import re
import json
import time
import subprocess
import tempfile
import pdfplumber
from email.header import decode_header
from together import Together
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime

load_dotenv("/home/fexonpay-prod/.env", override=True)

GMAIL_USER    = os.getenv("COMPANY_EMAIL", "fexonpay@gmail.com")
GMAIL_PASSWORD= os.getenv("EMAIL_APP_PASSWORD")
TOGETHER_KEY  = os.getenv("TOGETHER_API_KEY")
ADMIN_PHONE   = "+60193875342"
POLL_INTERVAL = 180

together_client = Together(api_key=TOGETHER_KEY)
db = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

def send_whatsapp(phone, message):
    try:
        subprocess.run(
            ["openclaw", "message", "send", "--channel", "whatsapp", "--account", "prod", "--target", phone, "--message", message],
            env={**__import__("os").environ, "OPENCLAW_GATEWAY_URL": "ws://127.0.0.1:18789", "OPENCLAW_GATEWAY_TOKEN": "c4cb0fc562700ac37cc60bb9983a1fb752a61801af15b748"},
            check=True, capture_output=True, text=True
        )
        return True
    except Exception as e:
        print(f"WhatsApp error: {e}")
        return False

def extract_text_from_pdf(pdf_bytes):
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
        text = ""
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        os.unlink(tmp_path)
        return text.strip()
    except Exception as e:
        print(f"PDF error: {e}")
        return ""

def extract_text_from_csv(csv_bytes):
    try:
        import csv, io
        text = csv_bytes.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        return "\n".join(" | ".join(str(c) for c in row if c) for row in reader)
    except Exception as e:
        print(f"CSV error: {e}")
        return ""

def extract_text_from_excel(excel_bytes):
    try:
        import openpyxl, io
        wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), data_only=True)
        rows = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    rows.append(" | ".join(cells))
        return "\n".join(rows)
    except Exception as e:
        print(f"Excel error: {e}")
        return ""

def extract_invoices(text, sender_email):
    prompt = (
        "You are a Malaysian invoice parsing assistant. "
        "Extract invoice details from this text and return ONLY valid JSON. "
        "No explanation. No markdown. No code blocks. "
        "If there are MULTIPLE invoices return a JSON array. If ONE invoice return a single object. "
        "Fields per invoice: vendor_name, invoice_number, invoice_date, due_date, "
        "items (array of {description, quantity, unit_price}), total_amount, "
        "currency (MYR default), payment_term (NET30 default). "
        f"Sender: {sender_email}.\nText:\n{text[:3000]}"
    )
    try:
        resp = together_client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024, temperature=0.1
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.split("\n") if not l.strip().startswith("```")).strip()
        s, e = raw.find("{"), raw.rfind("}")
        a_s, a_e = raw.find("["), raw.rfind("]")
        if a_s != -1 and (s == -1 or a_s < s):
            parsed = json.loads(raw[a_s:a_e+1])
        elif s != -1:
            parsed = json.loads(raw[s:e+1])
        else:
            return None
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception as e:
        print(f"AI error: {e}")
        return None

def save_expense(customer_id, business_name, inv, sender_email, category=None):
    try:
        vendor = inv.get("vendor_name", sender_email.split("@")[0])
        amount = float(inv.get("total_amount", 0))
        if amount <= 0:
            return False
        r = db.table("cash_receipts").select("receipt_number").eq("customer_id", customer_id).order("created_at", desc=True).limit(1).execute()
        last = 0
        if r.data:
            try: last = int(r.data[0]["receipt_number"].split("-")[-1])
            except: pass
        rnum = f"EXP-{str(last+1).zfill(5)}"
        category = category or auto_categorise(vendor)
        db.table("cash_receipts").insert({
            "customer_id": customer_id, "business_name": business_name,
            "receipt_number": rnum, "party_name": vendor, "party_phone": "",
            "amount": amount, "direction": "out",
            "notes": f"Auto-imported: {inv.get('invoice_number','')} | category:{category}",
            "confirmation_sent": False, "confirmation_status": "auto",
            "recorded_at": datetime.now().isoformat()
        }).execute()

        # Also save to expense_categories so P&L picks it up correctly
        try:
            db.table("expense_categories").upsert({
                "customer_id": customer_id,
                "vendor_name":  vendor,
                "category":     category,
                "confidence":   8
            }, on_conflict="customer_id,vendor_name").execute()
        except:
            pass
        db.table("audit_trail").insert({
            "customer_id": customer_id, "business_name": business_name,
            "action_type": "EMAIL_INVOICE_IMPORTED", "entity_type": "expense",
            "entity_id": rnum, "amount": amount, "performed_by": "email_poller",
            "description": f"Email invoice from {vendor} RM{amount:.2f} auto-imported"
        }).execute()
        print(f"[Poller] Saved {rnum}: {vendor} RM{amount:.2f}")
        return True
    except Exception as e:
        print(f"[Poller] Save error: {e}")
        return False

def auto_categorise(vendor_name: str) -> str:
    vendor = vendor_name.lower()
    if any(k in vendor for k in ['tnb', 'tenaga', 'electricity', 'electric']):
        return 'utilities'
    if any(k in vendor for k in ['celcom', 'maxis', 'digi', 'unifi', 'telekom', 'tm ', 'time ', 'yes ', 'u mobile']):
        return 'telecommunications'
    if any(k in vendor for k in ['pos malaysia', 'poslaju', 'gdex', 'dhl', 'fedex', 'citylink', 'ninjavan']):
        return 'courier'
    if any(k in vendor for k in ['petronas', 'shell', 'petron', 'caltex', 'bph', 'fuel', 'petrol']):
        return 'fuel'
    if any(k in vendor for k in ['hardware', 'bahan', 'material', 'supply', 'supplies', 'trading', 'builder']):
        return 'materials'
    if any(k in vendor for k in ['insurance', 'takaful', 'allianz', 'prudential', 'aia ', 'great eastern']):
        return 'insurance'
    if any(k in vendor for k in ['hotel', 'airbnb', 'flight', 'airasia', 'malindo', 'mas ', 'travel']):
        return 'travel'
    if any(k in vendor for k in ['food', 'catering', 'restoran', 'cafe', 'canteen', 'makan']):
        return 'meals'
    if any(k in vendor for k in ['office', 'stationery', 'paper', 'printer', 'toner']):
        return 'office supplies'
    if any(k in vendor for k in ['rent', 'sewa', 'property', 'real estate']):
        return 'rent'
    return 'general expenses'

def get_customer_by_alias(alias):
    try:
        r = db.table("customers").select("*").ilike("unique_email_alias", f"%{alias}%").execute()
        if r.data:
            return r.data[0]
        slug = alias.lower().replace(" ","").replace("-","").replace("_","")
        all_c = db.table("customers").select("*").execute().data or []
        for c in all_c:
            biz = c.get("business_name","").lower().replace(" ","").replace("-","").replace("_","")
            if slug in biz or biz in slug:
                return c
    except Exception as e:
        print(f"[Poller] Alias lookup error: {e}")
    return None

def get_to_alias(msg):
    to_header = msg.get("To","") + " " + msg.get("Delivered-To","")
    m = re.search(r"fexonpay\+([a-zA-Z0-9._-]+)@gmail\.com", to_header, re.IGNORECASE)
    return m.group(1) if m else ""

def format_preview(inv, sender, category):
    vendor = inv.get("vendor_name", sender)
    total  = float(inv.get("total_amount", 0))
    due    = inv.get("due_date", "N/A")
    lines  = [
        "📧 *Bill Received via Email*",
        "┌─────────────────────────",
        f"│ From:     {vendor}",
        f"│ Due:      {due}",
        f"│ Total:    RM{total:.2f}",
        f"│ Category: {category} ✨",
        "└─────────────────────────",
        "",
        "✅ Will auto-save in 10 minutes.",
        "Reply *SAVE AS [category]* to change.",
        "Reply *SKIP* to discard.",
        "",
        "Example: SAVE AS materials",
    ]
    return "\n".join(lines)

def process_auto_saves():
    try:
        now = datetime.now().timestamp()
        all_custs = db.table("customers").select("customer_id, business_name, whatsapp_number, pending_state").execute().data or []
        for cust in all_custs:
            ps = cust.get("pending_state")
            if not ps or not isinstance(ps, dict) or ps.get("type") != "email_bill":
                continue
            if now < ps.get("auto_save_at", 0):
                continue
            cid      = cust["customer_id"]
            biz      = cust.get("business_name", "")
            phone    = cust.get("whatsapp_number", ADMIN_PHONE)
            inv      = ps.get("invoice", {})
            sender   = ps.get("sender", "")
            category = ps.get("category", "general expenses")
            saved    = save_expense(cid, biz, inv, sender, category)
            db.table("customers").update({"pending_state": None}).eq("customer_id", cid).execute()
            if saved:
                vendor = inv.get("vendor_name", sender)
                amount = float(inv.get("total_amount", 0))
                send_whatsapp(phone,
                    f"✅ *Bill Auto-Saved*\n"
                    f"  Vendor:   {vendor}\n"
                    f"  Amount:   RM{amount:.2f}\n"
                    f"  Category: {category}\n\n"
                    f"Added to your P&L and cashflow.")
                print(f"[Poller] Auto-saved: {vendor} RM{amount:.2f} for {cid}")
    except Exception as e:
        print(f"[Poller] Auto-save check error: {e}")

def poll_emails():
    print("[Poller] Connecting to Gmail IMAP...")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_PASSWORD)
        mail.select("inbox")
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            mail.logout()
            return
        email_ids = messages[0].split()
        print(f"[Poller] Found {len(email_ids)} unread email(s).")
        for eid in email_ids:
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject_raw = msg.get("Subject","No Subject")
            subject = ""
            for part, enc in decode_header(subject_raw):
                subject += part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
            sender = msg.get("From","")
            sender_email = sender.split("<")[1].rstrip(">") if "<" in sender else sender
            if sender_email.lower() in [GMAIL_USER.lower(), "fexonpay@gmail.com"]:
                mail.store(eid, "+FLAGS", "\\Seen")
                continue
            kws = ["invoice","invois","bil","receipt","resit","payment","bayaran","quotation"]
            if not any(k in subject.lower() for k in kws):
                print(f"[Poller] Skipping: {subject}")
                mail.store(eid, "+FLAGS", "\\Seen")
                continue
            alias    = get_to_alias(msg)
            customer = get_customer_by_alias(alias) if alias else None
            if customer:
                notify_phone  = customer.get("whatsapp_number", ADMIN_PHONE)
                customer_id   = customer.get("customer_id")
                business_name = customer.get("business_name","")
            else:
                notify_phone  = ADMIN_PHONE
                customer_id   = None
                business_name = ""
            print(f"[Poller] Processing: {subject} from {sender_email}")
            found = False
            for part in msg.walk():
                content_type  = part.get_content_type()
                filename      = part.get_filename() or ""
                filename_lower= filename.lower()
                is_pdf   = content_type == "application/pdf" or filename_lower.endswith(".pdf")
                is_csv   = filename_lower.endswith(".csv")
                is_excel = filename_lower.endswith((".xlsx",".xls"))
                is_zip   = filename_lower.endswith(".zip")
                if is_zip:
                    import zipfile, io
                    zip_bytes = part.get_payload(decode=True)
                    all_invoices = []
                    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                        for zname in zf.namelist():
                            zdata = zf.read(zname)
                            if zname.lower().endswith(".pdf"):
                                ztxt = extract_text_from_pdf(zdata)
                            elif zname.lower().endswith(".csv"):
                                ztxt = extract_text_from_csv(zdata)
                            elif zname.lower().endswith((".xlsx",".xls")):
                                ztxt = extract_text_from_excel(zdata)
                            else:
                                continue
                            invs = extract_invoices(ztxt, sender_email) if ztxt else None
                            if invs:
                                all_invoices.extend(invs)
                    if all_invoices:
                        total_saved, total_amt = 0, 0
                        lines = ["📦 *ZIP Bills Received*", f"From: {sender_email}", f"{len(all_invoices)} invoices found", ""]
                        for inv in all_invoices:
                            cat = auto_categorise(inv.get("vendor_name", sender_email))
                            saved = save_expense(customer_id, business_name, inv, sender_email, cat) if customer_id else False
                            if saved: total_saved += 1
                            amt = float(inv.get("total_amount", 0))
                            total_amt += amt
                            lines.append(f"  • {inv.get('vendor_name','?')} RM{amt:.2f} → {cat}")
                        lines += ["", f"✅ {total_saved}/{len(all_invoices)} saved.", f"Total: RM{total_amt:.2f}"]
                        send_whatsapp(notify_phone, "\n".join(lines))
                    else:
                        send_whatsapp(notify_phone, f"📦 ZIP received from {sender_email} but no invoices could be extracted.")
                    found = True
                    continue
                if not (is_pdf or is_csv or is_excel):
                    continue
                attach_bytes = part.get_payload(decode=True)
                if not attach_bytes:
                    continue
                if is_pdf:
                    extracted = extract_text_from_pdf(attach_bytes)
                elif is_csv:
                    extracted = extract_text_from_csv(attach_bytes)
                else:
                    extracted = extract_text_from_excel(attach_bytes)
                invoices = extract_invoices(extracted, sender_email) if extracted else None
                if not invoices:
                    send_whatsapp(notify_phone, f"📧 Bill from {sender_email}\n⚠️ Could not extract details. Check email manually.")
                    found = True
                    continue
                if len(invoices) > 1:
                    # Bulk — auto-save all with category, send summary
                    total_saved = 0
                    total_amt   = 0
                    lines = ["📧 *Bills Received via Email*", f"From: {sender_email}", ""]
                    for inv in invoices:
                        cat   = auto_categorise(inv.get("vendor_name", sender_email))
                        saved = False
                        if customer_id:
                            saved = save_expense(customer_id, business_name, inv, sender_email, cat)
                            if saved: total_saved += 1
                        amt = float(inv.get("total_amount", 0))
                        total_amt += amt
                        lines.append(f"  • {inv.get('vendor_name','?')} RM{amt:.2f} → {cat}")
                    lines += ["", f"✅ {total_saved}/{len(invoices)} saved to your records.",
                              "These will appear in your P&L and cashflow."]
                    send_whatsapp(notify_phone, "\n".join(lines))
                else:
                    # Single invoice — suggest category, auto-save after 10 min if no reply
                    inv      = invoices[0]
                    category = auto_categorise(inv.get("vendor_name", sender_email))
                    if customer_id:
                        try:
                            db.table("customers").update({"pending_state": {
                                "type":          "email_bill",
                                "invoice":       inv,
                                "sender":        sender_email,
                                "category":      category,
                                "customer_id":   customer_id,
                                "business_name": business_name,
                                "auto_save_at":  (datetime.now().timestamp() + 600),
                            }}).eq("customer_id", customer_id).execute()
                            print(f"[Poller] Pending bill stored for {customer_id}, auto-save in 10min")
                        except Exception as e:
                            print(f"[Poller] Pending store error: {e}")
                    send_whatsapp(notify_phone, format_preview(inv, sender_email, category))
                found = True
            if not found:
                print(f"[Poller] No attachments in email from {sender_email}")
            mail.store(eid, "+FLAGS", "\\Seen")
        mail.logout()
        print("[Poller] Done.")
    except Exception as e:
        print(f"[Poller] Error: {e}")

if __name__ == "__main__":
    print(f"[Poller] Started. Monitoring {GMAIL_USER} every {POLL_INTERVAL}s.")
    while True:
        try:
            process_auto_saves()
            poll_emails()
        except Exception as e:
            print(f"[Poller] Loop error: {e}")
        print(f"[Poller] Sleeping {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)
