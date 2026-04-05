"""
fexonpay_cash.py
Fexonpay — Cash Confirm System

Handles physical cash transactions:
1. Record cash received from a party
2. Record cash paid out to a party
3. Auto-send WhatsApp confirmation to other party
4. Handle disputes (if party replies NO)
5. Cash summary and audit trail

Owner types naturally:
  "Ahmad paid me RM5000 cash"
  "Received RM500 cash from Ali"
  "Paid Reza RM800 cash for materials"
  "Cash masuk RM2000 daripada Ahmad 0123456789"
"""

import os
import re
import json
import subprocess
from datetime import datetime
from supabase import create_client
from together import Together
from dotenv import load_dotenv

load_dotenv('/home/fexonpay-prod/.env')


class CashManager:

    def __init__(self):
        self.db  = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
        self.ai  = Together(api_key=os.environ['TOGETHER_API_KEY'])

    # ── Receipt number generator ───────────────────────────────────────────────

    def _next_receipt_number(self, customer_id: str) -> str:
        try:
            result = self.db.table('cash_receipts') \
                .select('receipt_number') \
                .eq('customer_id', customer_id) \
                .order('created_at', desc=True) \
                .limit(1).execute()
            if result.data:
                last = result.data[0]['receipt_number']
                num  = int(last.split('-')[-1]) + 1
            else:
                num = 1
            return f"RCP-{num:05d}"
        except:
            import time
            return f"RCP-{int(time.time())}"

    # ── Get customer info ──────────────────────────────────────────────────────

    def _get_customer(self, customer_id: str) -> dict:
        try:
            r = self.db.table('customers').select('*').eq('customer_id', customer_id).execute()
            return r.data[0] if r.data else {}
        except:
            return {}

    # ── Look up vendor phone number ────────────────────────────────────────────

    def _lookup_vendor_phone(self, party_name: str, customer_id: str) -> str:
        """Try to find phone number from vendors table."""
        try:
            result = self.db.table('vendors') \
                .select('phone_number') \
                .eq('customer_id', customer_id) \
                .ilike('vendor_name', f'%{party_name}%') \
                .limit(1).execute()
            if result.data and result.data[0].get('phone_number'):
                return result.data[0]['phone_number']
        except:
            pass
        return ''

    # ── AI extraction ──────────────────────────────────────────────────────────

    def _extract_cash_details(self, text: str) -> dict:
        """Extract cash transaction details from natural language."""
        try:
            prompt = f"""Extract cash transaction details from this text and return ONLY valid JSON.

Text: "{text}"

Return this exact JSON:
{{
  "direction": "in or out",
  "amount": 0.0,
  "party_name": "name of person or company",
  "party_phone": "phone number with country code or empty string",
  "notes": "any additional notes or empty string"
}}

Rules:
- direction "in" = owner RECEIVED cash (keywords: received, paid me, masuk, dapat, collect)
- direction "out" = owner PAID cash (keywords: paid, bayar, keluar, hantar)
- amount is always a positive number
- party_name is who paid or who was paid
- party_phone: if a phone number is mentioned extract it, add 60 prefix if Malaysian number starts with 0
- Example: "Ahmad paid me RM5000" → direction=in, amount=5000, party_name=Ahmad
- Example: "Paid Ali RM800 cash" → direction=out, amount=800, party_name=Ali
- Example: "Cash masuk RM2000 daripada Reza 0123456789" → direction=in, amount=2000, party_name=Reza, party_phone=60123456789
- Return ONLY the JSON, no other text"""

            response = self.ai.chat.completions.create(
                model='meta-llama/Llama-3.3-70B-Instruct-Turbo',
                messages=[{'role': 'user', 'content': prompt}],
                max_tokens=200,
                temperature=0.1
            )
            raw = response.choices[0].message.content.strip()
            start = raw.find('{')
            end   = raw.rfind('}')
            if start != -1 and end != -1:
                return json.loads(raw[start:end+1])
        except Exception as e:
            print(f"[Cash] AI extraction error: {e}")
        return {}

    # ── Send WhatsApp confirmation ─────────────────────────────────────────────

    def _send_confirmation(self, phone: str, message: str) -> bool:
        try:
            result = subprocess.run(
                ['openclaw', 'message', 'send', '--target', phone, '--message', message],
                check=True, capture_output=True, text=True
            )
            print(f"[Cash] Confirmation sent to {phone}")
            return True
        except Exception as e:
            print(f"[Cash] WhatsApp error: {e}")
            return False

    # ── Save to database ───────────────────────────────────────────────────────

    def _save_receipt(self, customer_id: str, data: dict) -> dict:
        try:
            record = {
                'customer_id':          customer_id,
                'business_name':        data.get('business_name', ''),
                'receipt_number':       data['receipt_number'],
                'party_name':           data['party_name'],
                'party_phone':          data.get('party_phone', ''),
                'amount':               data['amount'],
                'direction':            data['direction'],
                'notes':                data.get('notes', ''),
                'confirmation_sent':    data.get('confirmation_sent', False),
                'confirmation_status':  'pending',
                'recorded_at':          datetime.now().isoformat()
            }
            result = self.db.table('cash_receipts').insert(record).execute()
            return result.data[0] if result.data else record
        except Exception as e:
            print(f"[Cash] Save error: {e}")
            return data

    # ── PUBLIC: Record cash transaction ───────────────────────────────────────

    def _auto_categorise(self, vendor_name: str) -> str:
        vendor = vendor_name.lower()
        if any(k in vendor for k in ['tnb','tenaga','electricity','syabas','water','indah water','unifi','streamyx']):
            return 'utilities'
        if any(k in vendor for k in ['celcom','maxis','digi','u mobile','yes ','time ','telekom','tm ']):
            return 'telecommunications'
        if any(k in vendor for k in ['pos malaysia','poslaju','gdex','dhl','fedex','citylink','ninjavan','j&t']):
            return 'courier'
        if any(k in vendor for k in ['petronas','shell','petron','caltex','fuel','petrol']):
            return 'fuel'
        if any(k in vendor for k in ['hardware','bahan','material','supply','supplies','trading','builder']):
            return 'materials'
        if any(k in vendor for k in ['insurance','takaful','allianz','prudential','aia ','great eastern']):
            return 'insurance'
        if any(k in vendor for k in ['hotel','airbnb','flight','airasia','malindo','mas ','travel']):
            return 'travel'
        if any(k in vendor for k in ['food','catering','restoran','cafe','canteen','makan']):
            return 'meals'
        if any(k in vendor for k in ['office','stationery','paper','printer','toner']):
            return 'office supplies'
        if any(k in vendor for k in ['rent','sewa','property']):
            return 'rent'
        if any(k in vendor for k in ['salary','gaji','wages','staff','employee']):
            return 'salary'
        return 'general expenses'

    def record_cash(self, customer_id: str, text: str, business_name: str = '') -> dict:
        """
        Main entry point. Returns dict with:
        - reply: message to send owner
        - needs_phone: True if we need phone number
        - pending_cash: data to store in pending state
        """
        try:
            customer = self._get_customer(customer_id)
            business = customer.get('business_name', 'Fexonpay')

            # Extract details
            details = self._extract_cash_details(text)
            if not details or not details.get('amount') or not details.get('party_name'):
                return {
                    'reply': ("❌ Could not understand the cash transaction.\n\n"
                              "Try:\n"
                              "• \"Ahmad paid me RM5000 cash\"\n"
                              "• \"Paid Ali RM800 cash for materials\"\n"
                              "• \"Cash masuk RM2000 daripada Reza\""),
                    'needs_phone': False
                }

            amount      = float(details['amount'])
            party_name  = details['party_name']
            direction   = details.get('direction', 'in')
            party_phone = details.get('party_phone', '')
            notes       = details.get('notes', '')

            # Try to look up phone from vendors if not provided
            if not party_phone:
                party_phone = self._lookup_vendor_phone(party_name, customer_id)

            receipt_number = self._next_receipt_number(customer_id)
            now_str        = datetime.now(__import__('pytz').timezone('Asia/Kuala_Lumpur')).strftime('%d %b %Y at %I:%M %p')
            direction_text = 'received from' if direction == 'in' else 'paid to'
            direction_icon = '💰' if direction == 'in' else '💸'

            # Save to database
            self._save_receipt(customer_id, {
                'business_name': business_name,
                'receipt_number':    receipt_number,
                'party_name':        party_name,
                'party_phone':       party_phone,
                'amount':            amount,
                'direction':         direction,
                'notes':             notes,
                'confirmation_sent': bool(party_phone)
            })

            # Send confirmation to other party if phone available
            confirmation_sent = False
            if party_phone:
                confirm_msg = (
                    f"Hi {party_name},\n\n"
                    f"{business} has recorded a cash payment of "
                    f"RM{amount:,.2f} {direction_text} you on {now_str}.\n\n"
                    f"Reference: {receipt_number}\n\n"
                    f"If this is incorrect, reply *NO* within 24 hours.\n"
                    f"Otherwise no action needed."
                )
                confirmation_sent = self._send_confirmation(party_phone, confirm_msg)

                # Update confirmation status
                if confirmation_sent:
                    self.db.table('cash_receipts') \
                        .update({'confirmation_sent': True}) \
                        .eq('receipt_number', receipt_number).execute()

            # Build reply to owner
            reply  = f"{direction_icon} *Cash {'Received' if direction == 'in' else 'Paid'} — Recorded*\n\n"
            reply += f"📋 Reference: {receipt_number}\n"
            reply += f"👤 {'From' if direction == 'in' else 'To'}: {party_name}\n"
            reply += f"💰 Amount: RM{amount:,.2f}\n"
            reply += f"🕐 Time: {now_str}\n"
            if notes:
                reply += f"📝 Notes: {notes}\n"

            if direction == 'out':
                category = self._auto_categorise(party_name)
                known_vendors = ['tnb','tenaga','celcom','maxis','digi','unifi','tm ','time ',
                                'pos malaysia','poslaju','gdex','dhl','petronas','shell','petron']
                is_known = any(k in party_name.lower() for k in known_vendors)
                if is_known:
                    reply += f"\n📂 Category: *{category}* (auto-tagged)\n"
                else:
                    reply += f"\n📂 Category: *{category}*\n"
                    reply += "Reply with number to change:\n"
                    reply += "1. Materials  2. Utilities  3. Salary\n"
                    reply += "4. Transport  5. Meals  6. Office Supplies\n"
                    reply += "7. Professional Fees  8. Other\n"
            if confirmation_sent:
                reply += f"\n✅ Confirmation sent to {party_name}'s WhatsApp\n"
                reply += f"They have 24 hours to dispute.\n"
                reply += f"Silence = accepted."
            elif party_phone:
                reply += f"\n⚠️ Could not send confirmation to {party_name}"
            else:
                reply += f"\n💡 No phone number for {party_name}.\n"
                reply += f"Reply with their number to send confirmation,\n"
                reply += f"or type *SKIP* to save without confirmation."

            result = {
                'reply':        reply,
                'needs_phone':  not party_phone and direction == 'in',
                'party_name':   party_name,
                'receipt_number': receipt_number,
                'amount':       amount,
                'direction':    direction
            }
            # For unknown cash outflows, signal main.py to set category pending
            if direction == 'out':
                known_vendors = ['tnb','tenaga','celcom','maxis','digi','unifi','tm ',
                                'pos malaysia','poslaju','gdex','dhl','petronas','shell']
                if not any(k in party_name.lower() for k in known_vendors):
                    result['needs_category'] = True
                    result['vendor_name'] = party_name
            return result

        except Exception as e:
            print(f"[Cash] record_cash error: {e}")
            return {'reply': "❌ Error recording cash. Please try again.", 'needs_phone': False}

    # ── PUBLIC: Add phone to pending receipt ──────────────────────────────────

    def send_confirmation_for_receipt(self, customer_id: str, receipt_number: str, phone: str) -> str:
        """Send confirmation after owner provides phone number."""
        try:
            customer = self._get_customer(customer_id)
            business = customer.get('business_name', 'Fexonpay')

            result = self.db.table('cash_receipts') \
                .select('*') \
                .eq('receipt_number', receipt_number).execute()

            if not result.data:
                return "❌ Receipt not found."

            receipt  = result.data[0]
            now_str  = datetime.now(__import__('pytz').timezone('Asia/Kuala_Lumpur')).strftime('%d %b %Y at %I:%M %p')
            direction_text = 'received from' if receipt['direction'] == 'in' else 'paid to'

            confirm_msg = (
                f"Hi {receipt['party_name']},\n\n"
                f"{business} has recorded a cash payment of "
                f"RM{float(receipt['amount']):,.2f} {direction_text} you on {now_str}.\n\n"
                f"Reference: {receipt['receipt_number']}\n\n"
                f"If this is incorrect, reply *NO* within 24 hours.\n"
                f"Otherwise no action needed."
            )

            sent = self._send_confirmation(phone, confirm_msg)

            if sent:
                self.db.table('cash_receipts') \
                    .update({'party_phone': phone, 'confirmation_sent': True}) \
                    .eq('receipt_number', receipt_number).execute()
                return f"✅ Confirmation sent to {phone}\nReference: {receipt_number}"
            else:
                return f"❌ Could not send to {phone}. Receipt {receipt_number} saved without confirmation."

        except Exception as e:
            print(f"[Cash] send_confirmation error: {e}")
            return "❌ Error sending confirmation."

    # ── PUBLIC: Handle dispute (party replies NO) ──────────────────────────────

    def handle_dispute(self, party_phone: str, receipt_number: str) -> str:
        """Called when a party disputes a cash receipt."""
        try:
            result = self.db.table('cash_receipts') \
                .select('*, customer_id') \
                .eq('receipt_number', receipt_number).execute()

            if not result.data:
                return "Receipt not found."

            receipt = result.data[0]

            # Update status
            self.db.table('cash_receipts') \
                .update({'confirmation_status': 'disputed'}) \
                .eq('receipt_number', receipt_number).execute()

            # Get owner's WhatsApp number
            customer = self.db.table('customers') \
                .select('whatsapp_number') \
                .eq('customer_id', receipt['customer_id']).execute()

            if customer.data:
                owner_phone = customer.data[0]['whatsapp_number']
                alert = (
                    f"⚠️ *Cash Receipt Disputed*\n\n"
                    f"Reference: {receipt_number}\n"
                    f"Party: {receipt['party_name']} ({party_phone})\n"
                    f"Amount: RM{float(receipt['amount']):,.2f}\n\n"
                    f"They have disputed this transaction.\n"
                    f"Please contact them to resolve."
                )
                self._send_confirmation(owner_phone, alert)

            return "Dispute recorded. The business owner has been notified."

        except Exception as e:
            print(f"[Cash] dispute error: {e}")
            return "Error processing dispute."

    # ── PUBLIC: Cash summary ───────────────────────────────────────────────────

    def get_cash_summary(self, customer_id: str, days: int = 30) -> str:
        """Get cash in/out summary for the period."""
        try:
            from datetime import timedelta
            since = (datetime.now() - timedelta(days=days)).isoformat()

            result = self.db.table('cash_receipts') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .gte('created_at', since) \
                .order('created_at', desc=True).execute()

            receipts = result.data or []

            if not receipts:
                return (f"No cash transactions recorded in the last {days} days.\n\n"
                        f"Record one with: \"Ahmad paid me RM500 cash\"")

            cash_in  = [r for r in receipts if r['direction'] == 'in']
            cash_out = [r for r in receipts if r['direction'] == 'out']
            total_in  = sum(float(r['amount']) for r in cash_in)
            total_out = sum(float(r['amount']) for r in cash_out)
            net       = total_in - total_out

            reply  = f"💵 *Cash Summary — Last {days} days*\n\n"
            reply += f"📈 Cash In:  RM{total_in:,.2f} ({len(cash_in)} transactions)\n"
            reply += f"📉 Cash Out: RM{total_out:,.2f} ({len(cash_out)} transactions)\n"
            reply += f"💰 Net:      RM{net:,.2f}\n\n"

            if cash_in:
                reply += f"*Recent Cash Received:*\n"
                for r in cash_in[:5]:
                    date = str(r['created_at'])[:10]
                    status = '✅' if r['confirmation_status'] == 'confirmed' else '🕐'
                    reply += f"  {status} {r['receipt_number']} | {r['party_name']} | RM{float(r['amount']):,.2f} | {date}\n"

            if cash_out:
                reply += f"\n*Recent Cash Paid:*\n"
                for r in cash_out[:5]:
                    date = str(r['created_at'])[:10]
                    reply += f"  💸 {r['receipt_number']} | {r['party_name']} | RM{float(r['amount']):,.2f} | {date}\n"

            disputed = [r for r in receipts if r['confirmation_status'] == 'disputed']
            if disputed:
                reply += f"\n⚠️ *{len(disputed)} Disputed Transaction(s) — Needs Attention*\n"
                for r in disputed:
                    reply += f"  • {r['receipt_number']} | {r['party_name']} | RM{float(r['amount']):,.2f}\n"

            return reply

        except Exception as e:
            print(f"[Cash] summary error: {e}")
            return "Could not load cash summary."
