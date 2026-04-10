"""
fexonpay_finance.py
Fexonpay — Financial Dashboard, Expense Tracker & Budget Manager

Provides:
1. DASHBOARD — full financial snapshot
2. Enhanced STATUS — spending summaries
3. Natural language spending queries (via brain)
4. BUDGET SET [category] RM[amount] — set budgets
5. BUDGET CHECK — check budget vs actual spend

Usage in main.py:
    from fexonpay_finance import FinanceManager
    finance = FinanceManager()
"""

import os
from datetime import datetime, timedelta
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/home/fexonpay-prod/.env')


class FinanceManager:

    def __init__(self, db=None):
        self.db = db or create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_customer_id(self, whatsapp_number: str) -> str | None:
        try:
            result = self.db.table('customers').select('customer_id') \
                .eq('whatsapp_number', whatsapp_number).execute()
            return result.data[0]['customer_id'] if result.data else None
        except Exception as e:
            print(f"[Finance] customer lookup error: {e}")
            return None

    def _get_transactions(self, customer_id: str, days: int = 30) -> list:
        try:
            since = (datetime.now() - timedelta(days=days)).isoformat()
            result = self.db.table('cash_receipts') \
                .select('amount, direction, created_at, party_name') \
                .eq('customer_id', customer_id) \
                .gte('created_at', since) \
                .execute()
            return result.data or []
        except Exception as e:
            print(f"[Finance] transactions error: {e}")
            return []

    def _get_invoices(self, customer_id: str, days: int = 30) -> list:
        try:
            since = (datetime.now() - timedelta(days=days)).isoformat()
            result = self.db.table('self_invoices') \
                .select('invoice_number, vendor_name, grand_total, status, created_at, due_date') \
                .eq('customer_id', customer_id) \
                .gte('created_at', since) \
                .execute()
            return result.data or []
        except Exception as e:
            print(f"[Finance] invoices error: {e}")
            return []

    def _get_budgets(self, customer_id: str) -> dict:
        try:
            result = self.db.table('customer_preferences') \
                .select('preference_key, preference_value') \
                .eq('customer_id', customer_id) \
                .ilike('preference_key', 'budget_%') \
                .execute()
            budgets = {}
            for row in (result.data or []):
                key = row['preference_key'].replace('budget_', '')
                budgets[key] = float(row['preference_value'])
            return budgets
        except Exception as e:
            print(f"[Finance] budgets error: {e}")
            return {}

    def _get_vendor_name(self, vendor_id: str) -> str:
        try:
            result = self.db.table('vendors').select('vendor_name') \
                .eq('vendor_id', vendor_id).execute()
            return result.data[0]['vendor_name'] if result.data else 'Unknown'
        except:
            return 'Unknown'

    def _spend_by_vendor(self, customer_id: str, days: int = 30) -> dict:
        txns = self._get_transactions(customer_id, days)
        spend = {}
        for t in txns:
            if t.get('direction') == 'out':
                vendor = t.get('party_name', 'Unknown')
                spend[vendor] = spend.get(vendor, 0) + float(t.get('amount', 0))
        return dict(sorted(spend.items(), key=lambda x: x[1], reverse=True))

    # ── 1. DASHBOARD ──────────────────────────────────────────────────────────

    def get_dashboard(self, customer_id: str) -> str:
        try:
            import pytz; MY_TZ = pytz.timezone('Asia/Kuala_Lumpur'); now = datetime.now(MY_TZ)

            # This month
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc).isoformat()
            week_start = (now - timedelta(days=7)).astimezone(pytz.utc).isoformat()

            # Invoices
            all_invoices = self.db.table('self_invoices') \
                .select('invoice_number, vendor_name, grand_total, status, due_date, created_at') \
                .eq('customer_id', customer_id) \
                .order('created_at', desc=True) \
                .execute()
            invoices = all_invoices.data or []

            outstanding = [i for i in invoices if i['status'] in ['ready', 'sent']]
            today_str = now.date().isoformat()
            overdue     = [i for i in outstanding if i.get('due_date') and str(i['due_date']) < today_str]
            month_inv   = [i for i in invoices if i['created_at'] >= month_start]

            outstanding_amt = sum(float(i['grand_total']) for i in outstanding)
            overdue_amt     = sum(float(i['grand_total']) for i in overdue)
            month_income    = sum(float(i['grand_total']) for i in month_inv)
            all_txns = self.db.table('cash_receipts') \
                .select('amount, direction, created_at, party_name') \
                .eq('customer_id', customer_id) \
                .order('created_at', desc=True) \
                .execute()
            txns = all_txns.data or []
            month_txns   = [t for t in txns if t['created_at'] >= month_start]
            week_txns    = [t for t in txns if t['created_at'] >= week_start]
            out_month    = [t for t in month_txns if t.get('direction') == 'out']
            out_week     = [t for t in week_txns if t.get('direction') == 'out']
            in_month     = [t for t in month_txns if t.get('direction') == 'in']

            month_expense = sum(float(t['amount']) for t in out_month)
            week_expense  = sum(float(t['amount']) for t in out_week)
            month_paid    = sum(float(t['amount']) for t in in_month)
            pending_amt   = 0.0
            pending_txns  = []

            # Recent invoices
            recent = invoices[:3]
            recent_lines = []
            for inv in recent:
                status_icon = '✅' if inv['status'] == 'paid' else '🕐'
                recent_lines.append(f"  {status_icon} {inv['invoice_number']} | {inv['vendor_name']} | RM{float(inv['grand_total']):.2f}")

            # Top vendors this month
            vendor_spend = self._spend_by_vendor(customer_id, 30)
            top_vendors = list(vendor_spend.items())[:3]
            vendor_lines = [f"  • {v}: RM{a:.2f}" for v, a in top_vendors]

            net = month_paid - month_expense
            net_sign = '+' if net >= 0 else '-'
            lines = [
                '📊 *Fexonpay Dashboard*',
                f'_{now.strftime("%d %b %Y, %I:%M %p")}_',
                '',
                '💰 *Money Owed to You*',
                f'  Outstanding: RM{outstanding_amt:.2f} ({len(outstanding)} invoices)',
                f'  Overdue: RM{overdue_amt:.2f} ({len(overdue)} invoices)',
                f'  Invoiced this month: RM{month_income:.2f} ({len(month_inv)} invoices)',
                '',
                '💸 *Cash Flow (This Month)*',
                f'  Received from clients: RM{month_paid:.2f}',
                f'  Paid to vendors: RM{month_expense:.2f}',
                f'  Net: {net_sign}RM{abs(net):.2f}',
                '',
            ]

            if recent_lines:
                lines += ['📋 *Recent Invoices*'] + recent_lines + ['']

            if vendor_lines:
                lines += ['🏆 *Top Vendors (30 days)*'] + vendor_lines + ['']

            lines += [
                'Commands: BUDGET CHECK | SPEND | VENDORS',
            ]

            return '\n'.join(lines)

        except Exception as e:
            print(f"[Finance] dashboard error: {e}")
            return "❌ Could not load dashboard. Please try again."

    # ── 2. SPEND summary ──────────────────────────────────────────────────────

    def get_spend_summary(self, customer_id: str, days: int = 30) -> str:
        try:
            import pytz
            MY_TZ = pytz.timezone('Asia/Kuala_Lumpur')
            now = datetime.now(MY_TZ)

            if days == 1:
                since = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                period_label = "today"
            elif days == 7:
                since = (now - timedelta(days=7)).isoformat()
                period_label = "last 7 days"
            else:
                since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
                period_label = "this month"

            r = self.db.table('cash_receipts') \
                .select('party_name, amount, direction, created_at') \
                .eq('customer_id', customer_id) \
                .eq('direction', 'out') \
                .gte('created_at', since) \
                .execute()
            txns = r.data or []

            if not txns:
                return f"No spending recorded {period_label}."

            total = sum(float(t['amount']) for t in txns)
            vendor_totals = {}
            for t in txns:
                v = t.get('party_name', 'Unknown')
                vendor_totals[v] = vendor_totals.get(v, 0) + float(t['amount'])

            lines = [
                f'💸 *Spending Summary ({period_label})*',
                '',
                f'Total spent: RM{total:.2f}',
                f'Transactions: {len(txns)}',
                '',
                '*By Vendor:*',
            ]
            for vendor, amount in sorted(vendor_totals.items(), key=lambda x: -x[1])[:5]:
                pct = (amount / total * 100) if total > 0 else 0
                lines.append(f'  • {vendor}: RM{amount:.2f} ({pct:.0f}%)')
            return '\n'.join(lines)
        except Exception as e:
            print(f"[Finance] spend error: {e}")
            return "❌ Could not load spending summary."

    def get_vendors_summary(self, customer_id: str) -> str:
        try:
            vendor_spend = self._spend_by_vendor(customer_id, 365)
            if not vendor_spend:
                return "No vendors recorded yet."
            lines = ['🏢 *Your Vendors*', '']
            for name, spend in list(vendor_spend.items())[:10]:
                lines.append(f'  • {name}: RM{spend:.2f} total paid')
            return '\n'.join(lines)

        except Exception as e:
            print(f"[Finance] vendors error: {e}")
            return "❌ Could not load vendors."

    # ── 4. BUDGET SET ─────────────────────────────────────────────────────────

    def set_budget(self, customer_id: str, text: str) -> str:
        """
        Parse: BUDGET SET [category] RM[amount]
        e.g.  BUDGET SET cleaning RM2000
        """
        try:
            parts = text.strip().split()
            # Expected: BUDGET SET [category...] RM[amount]
            if len(parts) < 4:
                return ("To set a budget:\n"
                        "BUDGET SET [category] RM[amount]\n\n"
                        "Example:\n"
                        "BUDGET SET cleaning RM2000\n"
                        "BUDGET SET utilities RM500")

            # Find RM amount
            amount_str = None
            category_parts = []
            # Rebuild text after "BUDGET SET" to handle "rm 2000" with space
            remainder = ' '.join(parts[2:]).upper()
            import re
            rm_match = re.search(r'RM\s*([\d,]+\.?\d*)', remainder)
            if rm_match:
                try:
                    amount_str = float(rm_match.group(1).replace(',', ''))
                except:
                    pass
                # Category is everything before RM
                cat_text = remainder[:rm_match.start()].strip().lower()
                category_parts = cat_text.split() if cat_text else []
            else:
                for part in parts[2:]:
                    if part.upper().startswith('RM'):
                        try:
                            amount_str = float(part[2:].replace(',', ''))
                        except:
                            pass
                    else:
                        category_parts.append(part.lower())

            if not amount_str or not category_parts:
                return ("Invalid format.\nUse: BUDGET SET [category] RM[amount]\n"
                        "Example: BUDGET SET cleaning RM2000")

            category = ' '.join(category_parts).lower()
            pref_key = f"budget_{category}"

            self.db.table('customer_preferences').upsert({
                'customer_id': customer_id,
                'preference_key': pref_key,
                'preference_value': str(amount_str),
                'confidence': 10,
                'last_seen': datetime.now().isoformat()
            }, on_conflict='customer_id,preference_key').execute()

            return (f"✅ *Budget Set*\n\n"
                    f"Category: {category.title()}\n"
                    f"Monthly budget: RM{amount_str:.2f}\n\n"
                    f"Use BUDGET CHECK to see your progress.")

        except Exception as e:
            print(f"[Finance] set_budget error: {e}")
            return "❌ Could not set budget. Try: BUDGET SET cleaning RM2000"

    # ── 5. BUDGET CHECK ───────────────────────────────────────────────────────

    def get_budget_check(self, customer_id: str) -> str:
        try:
            budgets = self._get_budgets(customer_id)

            if not budgets:
                return ("No budgets set yet.\n\n"
                        "Set one with:\n"
                        "BUDGET SET [category] RM[amount]\n"
                        "Example: BUDGET SET cleaning RM2000")

            # Get this month's spend by vendor (use vendor name as category proxy)
            import pytz; MY_TZ = pytz.timezone('Asia/Kuala_Lumpur'); now = datetime.now(MY_TZ)
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc).isoformat()
            try:
                invoices_result = self.db.table('self_invoices') \
                    .select('vendor_name, grand_total') \
                    .eq('customer_id', customer_id) \
                    .gte('created_at', month_start) \
                    .execute()
                invoices_data = invoices_result.data or []
            except Exception as qe:
                print(f"[Finance] budget invoices query error: {qe}")
                invoices_data = []

            # Group spend by keyword matching budget categories
            spend_by_category = {}
            for inv in invoices_data:
                vendor_lower = inv['vendor_name'].lower()
                amount = float(inv['grand_total'])
                for category in budgets:
                    if category in vendor_lower:
                        spend_by_category[category] = spend_by_category.get(category, 0) + amount

            lines = [
                f'📊 *Budget Check — {now.strftime("%B %Y")}*',
                '',
            ]

            for category, budget_amt in budgets.items():
                spent = spend_by_category.get(category, 0)
                pct   = (spent / budget_amt * 100) if budget_amt > 0 else 0
                remaining = budget_amt - spent

                if pct >= 100:
                    icon = '🔴'
                    status = 'OVER BUDGET'
                elif pct >= 80:
                    icon = '🟡'
                    status = 'Near limit'
                else:
                    icon = '🟢'
                    status = 'On track'

                lines += [
                    f'{icon} *{category.title()}*',
                    f'  Budget: RM{budget_amt:.2f}',
                    f'  Spent:  RM{spent:.2f} ({pct:.0f}%)',
                    f'  Left:   RM{remaining:.2f} — {status}',
                    '',
                ]

            return '\n'.join(lines)

        except Exception as e:
            print(f"[Finance] budget_check error: {e}")
            return "❌ Could not load budget check."

    # ── Vendor history lookup ─────────────────────────────────────────────────

    def get_vendor_history(self, customer_id: str, vendor_name: str) -> str:
        try:
            invoices = self.db.table('self_invoices') \
                .select('invoice_number, grand_total, status, created_at, due_date') \
                .eq('customer_id', customer_id) \
                .ilike('vendor_name', f'%{vendor_name}%') \
                .order('created_at', desc=True) \
                .limit(10) \
                .execute()

            if not invoices.data:
                return f"No invoices found for {vendor_name}."

            total = sum(float(i['grand_total']) for i in invoices.data)
            paid  = sum(float(i['grand_total']) for i in invoices.data if i['status'] == 'paid')

            lines = [
                f'📋 *{vendor_name} — Payment History*',
                '',
                f'Total invoiced: RM{total:.2f}',
                f'Total paid: RM{paid:.2f}',
                f'Invoices: {len(invoices.data)}',
                '',
            ]
            for inv in invoices.data[:5]:
                icon = '✅' if inv['status'] == 'paid' else '🕐'
                date = inv['created_at'][:10]
                lines.append(f'{icon} {inv["invoice_number"]} | RM{float(inv["grand_total"]):.2f} | {date}')

            return '\n'.join(lines)

        except Exception as e:
            print(f"[Finance] vendor history error: {e}")
            return f"❌ Could not load history for {vendor_name}."
