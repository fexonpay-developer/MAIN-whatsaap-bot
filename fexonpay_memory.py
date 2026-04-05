"""
fexonpay_memory.py
Fexonpay Bot — Memory & Learning System

Handles:
- Corrections (WRONG / NO commands)
- Customer preferences (payment terms, formatting, vendors)
- Self-improving invoice generation
- MEMORY and FORGET commands

Usage in main.py:
    from fexonpay_memory import MemorySystem
    memory = MemorySystem()
"""

import os
from datetime import datetime
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/home/fexonpay-prod/.env')


class MemorySystem:

    def __init__(self):
        self.db = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])

    # ── CORRECTIONS ───────────────────────────────────────────────────────────

    def log_correction(self, customer_id: str, wrong_behaviour: str, correct_behaviour: str = '', trigger_text: str = '') -> dict:
        """
        Log a correction. If same correction seen 3x, flag for confirmation.
        Returns dict with 'needs_confirmation': True if threshold reached.
        """
        try:
            existing = self.db.table('bot_corrections') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .ilike('wrong_behaviour', f'%{wrong_behaviour[:30]}%') \
                .eq('confirmed', False) \
                .execute()

            if existing.data:
                record = existing.data[0]
                new_count = record['occurrences'] + 1
                self.db.table('bot_corrections').update({
                    'occurrences': new_count,
                    'last_seen': datetime.now().isoformat(),
                    'correct_behaviour': correct_behaviour
                }).eq('id', record['id']).execute()
                return {
                    'status': 'updated',
                    'occurrences': new_count,
                    'needs_confirmation': new_count >= 3,
                    'record_id': record['id']
                }
            else:
                result = self.db.table('bot_corrections').insert({
                    'customer_id': customer_id,
                    'trigger_text': trigger_text,
                    'wrong_behaviour': wrong_behaviour,
                    'correct_behaviour': correct_behaviour,
                    'confirmed': False,
                    'occurrences': 1
                }).execute()
                return {
                    'status': 'new',
                    'occurrences': 1,
                    'needs_confirmation': False,
                    'record_id': result.data[0]['id'] if result.data else None
                }
        except Exception as e:
            print(f"[Memory] log_correction error: {e}")
            return {'status': 'error', 'needs_confirmation': False}

    def confirm_correction(self, customer_id: str, record_id: str) -> bool:
        """Mark a correction as permanently confirmed."""
        try:
            self.db.table('bot_corrections').update({
                'confirmed': True
            }).eq('id', record_id).eq('customer_id', customer_id).execute()
            return True
        except Exception as e:
            print(f"[Memory] confirm_correction error: {e}")
            return False

    def get_corrections(self, customer_id: str, confirmed_only: bool = False) -> list:
        """Get all corrections for a customer."""
        try:
            query = self.db.table('bot_corrections').select('*').eq('customer_id', customer_id)
            if confirmed_only:
                query = query.eq('confirmed', True)
            result = query.order('occurrences', desc=True).execute()
            return result.data or []
        except Exception as e:
            print(f"[Memory] get_corrections error: {e}")
            return []

    def forget_correction(self, customer_id: str, keyword: str) -> bool:
        """Remove a correction by keyword match."""
        try:
            self.db.table('bot_corrections') \
                .delete() \
                .eq('customer_id', customer_id) \
                .ilike('wrong_behaviour', f'%{keyword}%') \
                .execute()
            return True
        except Exception as e:
            print(f"[Memory] forget_correction error: {e}")
            return False

    # ── PREFERENCES ───────────────────────────────────────────────────────────

    def set_preference(self, customer_id: str, key: str, value: str) -> bool:
        """Save or update a customer preference. Confidence increases on repeat."""
        try:
            existing = self.db.table('customer_preferences') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .eq('preference_key', key) \
                .execute()

            if existing.data:
                new_confidence = min(existing.data[0]['confidence'] + 1, 10)
                self.db.table('customer_preferences').update({
                    'preference_value': value,
                    'confidence': new_confidence,
                    'last_seen': datetime.now().isoformat()
                }).eq('customer_id', customer_id).eq('preference_key', key).execute()
            else:
                self.db.table('customer_preferences').insert({
                    'customer_id': customer_id,
                    'preference_key': key,
                    'preference_value': value,
                    'confidence': 1
                }).execute()
            return True
        except Exception as e:
            print(f"[Memory] set_preference error: {e}")
            return False

    def get_preferences(self, customer_id: str) -> dict:
        """Get all preferences for a customer as a flat dict."""
        try:
            result = self.db.table('customer_preferences') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .order('confidence', desc=True) \
                .execute()
            prefs = {}
            for row in (result.data or []):
                prefs[row['preference_key']] = {
                    'value': row['preference_value'],
                    'confidence': row['confidence']
                }
            return prefs
        except Exception as e:
            print(f"[Memory] get_preferences error: {e}")
            return {}

    def get_preference(self, customer_id: str, key: str, default=None):
        """Get a single preference value."""
        prefs = self.get_preferences(customer_id)
        if key in prefs:
            return prefs[key]['value']
        return default

    def forget_preference(self, customer_id: str, key: str) -> bool:
        """Remove a specific preference."""
        try:
            self.db.table('customer_preferences') \
                .delete() \
                .eq('customer_id', customer_id) \
                .eq('preference_key', key) \
                .execute()
            return True
        except Exception as e:
            print(f"[Memory] forget_preference error: {e}")
            return False

    # ── AUTO-LEARN FROM INVOICE BEHAVIOUR ─────────────────────────────────────

    def learn_from_invoice(self, customer_id: str, invoice_data: dict):
        """Silently learn preferences from a confirmed invoice."""
        try:
            if invoice_data.get('payment_term'):
                self.set_preference(customer_id, 'payment_term', invoice_data['payment_term'])
            if invoice_data.get('currency'):
                self.set_preference(customer_id, 'currency', invoice_data['currency'])
            if invoice_data.get('payment_mode'):
                self.set_preference(customer_id, 'payment_mode', invoice_data['payment_mode'])
            print(f"[Memory] Learned from invoice confirmation.")
        except Exception as e:
            print(f"[Memory] learn_from_invoice error: {e}")

    def apply_preferences_to_invoice(self, customer_id: str, invoice_data: dict) -> dict:
        """Apply learned preferences to fill in invoice defaults before preview."""
        try:
            prefs = self.get_preferences(customer_id)

            if 'payment_term' in prefs and invoice_data.get('payment_term') == 'NET30':
                if prefs['payment_term']['confidence'] >= 2:
                    invoice_data['payment_term'] = prefs['payment_term']['value']

            if 'currency' in prefs:
                invoice_data['currency'] = prefs['currency']['value']

            if 'payment_mode' in prefs and not invoice_data.get('payment_mode'):
                invoice_data['payment_mode'] = prefs['payment_mode']['value']

            return invoice_data
        except Exception as e:
            print(f"[Memory] apply_preferences error: {e}")
            return invoice_data

    # ── COMMAND HANDLERS ──────────────────────────────────────────────────────

    def handle_wrong_command(self, customer_id: str, text: str) -> str:
        """Handle WRONG or NO command. Logs correction and returns WhatsApp reply."""
        description = text
        for prefix in ['wrong ', 'no ', 'no,', 'wrong,']:
            if text.lower().startswith(prefix):
                description = text[len(prefix):].strip()
                break

        if not description:
            return ("Tell me what was wrong. Example:\n"
                    "WRONG invoice term should be NET60 not NET30\n"
                    "WRONG vendor name should be Ali Baba Cleaning")

        result = self.log_correction(
            customer_id=customer_id,
            wrong_behaviour=description,
            trigger_text=text
        )

        if result['needs_confirmation']:
            return (f"⚠️ I've made this mistake {result['occurrences']} times now.\n\n"
                    f"Should I permanently remember:\n"
                    f"❌ Never do: {description}\n\n"
                    f"Reply *YES REMEMBER* to save permanently\n"
                    f"Reply *NO* to skip")

        return (f"✅ Got it. I've noted that and won't repeat it.\n\n"
                f"*Logged:* {description}\n\n"
                f"If I keep making this mistake, it will be permanently saved after 3 times.")

    def handle_yes_remember(self, customer_id: str) -> str:
        """Confirm the most recent pending correction as permanent."""
        try:
            result = self.db.table('bot_corrections') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .eq('confirmed', False) \
                .order('last_seen', desc=True) \
                .limit(1) \
                .execute()

            if result.data:
                record = result.data[0]
                self.confirm_correction(customer_id, record['id'])
                return (f"🧠 Permanently saved:\n\n"
                        f"❌ I will never: {record['wrong_behaviour']}\n\n"
                        f"Use *MEMORY* to review everything I know about you.")
            else:
                return "No pending corrections to confirm."
        except Exception as e:
            print(f"[Memory] handle_yes_remember error: {e}")
            return "Error saving to memory. Please try again."

    def handle_memory_command(self, customer_id: str) -> str:
        """Show the customer what the bot has learned about them."""
        corrections = self.get_corrections(customer_id)
        prefs = self.get_preferences(customer_id)

        lines = ['🧠 *What I know about you*\n']

        if prefs:
            lines.append('*Your Preferences:*')
            pref_labels = {
                'payment_term': 'Payment Term',
                'currency':     'Currency',
                'payment_mode': 'Payment Mode',
            }
            for key, data in prefs.items():
                label = pref_labels.get(key, key.replace('_', ' ').title())
                dots = '●' * min(data['confidence'], 5) + '○' * (5 - min(data['confidence'], 5))
                lines.append(f"  {label}: {data['value']} [{dots}]")
        else:
            lines.append('*Preferences:* None learned yet')

        lines.append('')

        confirmed = [c for c in corrections if c['confirmed']]
        pending   = [c for c in corrections if not c['confirmed']]

        if confirmed:
            lines.append('*Permanent Rules:*')
            for c in confirmed[:5]:
                lines.append(f"  ❌ Never: {c['wrong_behaviour']}")

        if pending:
            lines.append('*Logged Mistakes (not permanent yet):*')
            for c in pending[:5]:
                lines.append(f"  ⚠️ ({c['occurrences']}x) {c['wrong_behaviour']}")

        if not confirmed and not pending:
            lines.append('*Corrections:* None logged yet')

        lines.append('')
        lines.append('Commands: *FORGET [topic]* to remove anything')

        return '\n'.join(lines)

    def handle_forget_command(self, customer_id: str, text: str) -> str:
        """Handle FORGET command — remove a preference or correction."""
        keyword = text.lower().replace('forget', '').strip()

        if not keyword:
            return ("What should I forget?\n"
                    "Example: FORGET payment term\n"
                    "Example: FORGET vendor name mistake")

        self.forget_correction(customer_id, keyword)

        common_keys = {
            'payment term': 'payment_term',
            'currency':     'currency',
            'payment mode': 'payment_mode',
        }
        for phrase, key in common_keys.items():
            if phrase in keyword:
                self.forget_preference(customer_id, key)
        self.forget_preference(customer_id, keyword)

        return (f"🗑️ Done. Removed anything related to *{keyword}* from my memory.\n\n"
                f"Reply *MEMORY* to see what's left.")
