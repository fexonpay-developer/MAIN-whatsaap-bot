"""
fexonpay_accounts.py
Fexonpay — Complete Accountant Replacement Module
Covers:
1. Expense Categorisation (auto-tag every transaction)
2. P&L Statement (monthly + annual, PDF)
3. Balance Sheet (as at date, PDF)
4. Cashflow Statement (operating/investing/financing, PDF)
5. SST Compliance (6% service tax tracking)
6. Payroll (EPF/SOCSO/EIS calculations + payslip PDF)
7. Audit Trail (immutable transaction log)
8. Budget vs Actual Variance
9. Financial Forecast (3-month projection)
10. Business Advisory (health score + recommendations)

Malaysian Standards: MPERS, LHDN, EPF Act 1991, SOCSO Act 1969
"""

import os
import json
import pdfkit
import tempfile
import pathlib
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
from supabase import create_client
from together import Together
from dotenv import load_dotenv

load_dotenv('/home/fexonpay-prod/.env')

# ── Constants (Malaysian statutory rates 2024/2025) ──────────────────────────

EPF_EMPLOYEE_RATE   = 0.11   # 11% employee contribution
EPF_EMPLOYER_RATE   = 0.13   # 13% employer contribution (salary <= RM5,000)
EPF_EMPLOYER_RATE_HIGH = 0.12  # 12% employer (salary > RM5,000)

SOCSO_TABLE = [
    (30, 0.40, 0.45),    (50, 0.65, 0.70),   (70, 0.85, 0.95),
    (100, 1.25, 1.40),   (140, 1.75, 1.95),  (200, 2.50, 2.75),
    (300, 3.75, 4.15),   (400, 5.00, 5.55),  (500, 6.25, 6.90),
    (600, 7.50, 8.30),   (700, 8.75, 9.65),  (800, 10.00, 11.05),
    (900, 11.25, 12.40), (1000, 12.50, 13.85),(1100, 13.75, 15.20),
    (1200, 15.00, 16.60),(1300, 16.25, 17.95),(1400, 17.50, 19.35),
    (1500, 18.75, 20.70),(1600, 20.00, 22.10),(1700, 21.25, 23.50),
    (1800, 22.50, 24.85),(1900, 23.75, 26.25),(2000, 25.00, 27.65),
    (2100, 26.25, 29.05),(2200, 27.50, 30.40),(2300, 28.75, 31.80),
    (2400, 30.00, 33.20),(2500, 31.25, 34.55),(2600, 32.50, 35.95),
    (2700, 33.75, 37.35),(2800, 35.00, 38.70),(2900, 36.25, 40.10),
    (3000, 37.50, 41.50),(3100, 38.75, 42.85),(3200, 40.00, 44.25),
    (3300, 41.25, 45.65),(3400, 42.50, 47.00),(3500, 43.75, 48.40),
    (3600, 45.00, 49.80),(3700, 46.25, 51.15),(3800, 47.50, 52.55),
    (3900, 48.75, 53.95),(4000, 50.00, 55.30),(4100, 51.25, 56.70),
    (4200, 52.50, 58.10),(4300, 53.75, 59.45),(4400, 55.00, 60.85),
    (4500, 56.25, 62.25),(4600, 57.50, 63.60),(4700, 58.75, 65.00),
    (4800, 60.00, 66.40),(4900, 61.25, 67.75),(float('inf'), 62.50, 69.05),
]

EIS_EMPLOYEE_RATE = 0.002   # 0.2%
EIS_EMPLOYER_RATE = 0.002   # 0.2%
EIS_MAX_SALARY    = 4000    # capped at RM4,000

SST_RATE = 0.06             # 6% service tax
SST_THRESHOLD = 500000      # RM500,000 annual revenue threshold

EXPENSE_CATEGORIES = [
    'materials', 'labour', 'transport', 'utilities',
    'professional', 'rent', 'equipment', 'marketing',
    'meals', 'salary', 'cleaning', 'general', 'others'
]

CATEGORY_KEYWORDS = {
    'materials':     ['hardware', 'supply', 'supplier', 'bahan', 'material', 'parts',
                      'wire', 'pipe', 'cement', 'paint', 'timber', 'tiles', 'iron',
                      'steel', 'electrical', 'plumbing', 'fitting'],
    'labour':        ['worker', 'labour', 'contractor', 'subcon', 'manpower',
                      'pekerja', 'buruh', 'staff', 'helper'],
    'transport':     ['petrol', 'fuel', 'toll', 'parking', 'transport', 'delivery',
                      'grab', 'lalamove', 'logistics', 'minyak', 'bas', 'lori'],
    'utilities':     ['tnb', 'unifi', 'telekom', 'maxis', 'digi', 'celcom',
                      'syabas', 'air', 'water', 'electricity', 'internet', 'phone',
                      'astro', 'streamyx', 'utility'],
    'professional':  ['accountant', 'lawyer', 'audit', 'consultant', 'secretary',
                      'akaun', 'peguam', 'cukai', 'tax', 'filing', 'ssm', 'lhdn'],
    'rent':          ['rent', 'sewa', 'rental', 'office', 'warehouse', 'stor',
                      'pejabat', 'kedai', 'premis'],
    'equipment':     ['machine', 'tool', 'jentera', 'peralatan', 'equipment',
                      'maintenance', 'repair', 'service', 'spare part', 'servicing'],
    'marketing':     ['ads', 'printing', 'banner', 'social media', 'facebook',
                      'google', 'flyer', 'brochure', 'design', 'iklan'],
    'meals':         ['makan', 'food', 'meal', 'lunch', 'dinner', 'breakfast',
                      'restaurant', 'cafe', 'entertainment', 'drinks'],
    'salary':        ['salary', 'gaji', 'wages', 'allowance', 'elaun', 'bonus',
                      'commission', 'epf', 'socso', 'payroll'],
    'cleaning':      ['cleaning', 'cuci', 'cleaner', 'housekeeping', 'sanitize',
                      'basuh', 'sweep', 'mop', 'laundry'],
    'general':       ['general', 'am', 'miscellaneous', 'misc', 'lain', 'other expense',
                      'general expense', 'general expenses'],
}


class AccountsManager:

    def __init__(self):
        self.db     = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
        self.ai     = Together(api_key=os.environ['TOGETHER_API_KEY'])
        self.pdf_dir = pathlib.Path('/home/fexonpay-prod/pdfs/reports')
        self.pdf_dir.mkdir(parents=True, exist_ok=True)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _customer(self, customer_id: str) -> dict:
        try:
            r = self.db.table('customers').select('*').eq('customer_id', customer_id).execute()
            return r.data[0] if r.data else {}
        except:
            return {}

    def _invoices(self, customer_id: str, start: str, end: str) -> list:
        try:
            r = self.db.table('self_invoices') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .gte('created_at', start) \
                .lte('created_at', end) \
                .execute()
            return r.data or []
        except:
            return []

    def _transactions(self, customer_id: str, start: str, end: str) -> list:
        try:
            r = self.db.table('cash_receipts') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .gte('created_at', start) \
                .lte('created_at', end) \
                .execute()
            return r.data or []
        except:
            return []

    def _cash_receipts(self, customer_id: str, start: str, end: str) -> list:
        try:
            r = self.db.table('cash_receipts') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .gte('created_at', start) \
                .lte('created_at', end) \
                .execute()
            return r.data or []
        except:
            return []

    def _period(self, period: str = 'this_month'):
        now = datetime.now(__import__("pytz").timezone("Asia/Kuala_Lumpur"))
        if period == 'this_month':
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end   = now
        elif period == 'last_month':
            first = now.replace(day=1)
            start = (first - timedelta(days=1)).replace(day=1)
            end   = first - timedelta(seconds=1)
        elif period == 'this_year':
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0)
            end   = now
        elif period == 'last_year':
            start = now.replace(year=now.year-1, month=1, day=1, hour=0, minute=0, second=0)
            end   = now.replace(year=now.year-1, month=12, day=31, hour=23, minute=59)
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0)
            end   = now
        return start.isoformat(), end.isoformat()

    def _save_pdf(self, html: str, filename: str) -> str:
        path = str(self.pdf_dir / filename)
        try:
            with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w') as f:
                f.write(html)
                tmp = f.name
            pdfkit.from_file(tmp, path, options={
                'page-size': 'A4', 'encoding': 'UTF-8',
                'margin-top': '15mm', 'margin-bottom': '15mm',
                'margin-left': '15mm', 'margin-right': '15mm',
                'quiet': ''
            })
            os.unlink(tmp)
            return path
        except Exception as e:
            print(f"[Accounts] PDF error: {e}")
            return ''

    def _pdf_link(self, filename: str) -> str:
        return f"https://api.fexonpay.com/pdf/report/{filename}"

    # ── 1. Expense Categorisation ─────────────────────────────────────────────

    def auto_categorise(self, vendor_name: str, description: str = '') -> str:
        text = (vendor_name + ' ' + description).lower()
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return cat
        return 'others'

    def get_category(self, customer_id: str, vendor_name: str) -> str:
        try:
            r = self.db.table('expense_categories') \
                .select('category') \
                .eq('customer_id', customer_id) \
                .ilike('vendor_name', vendor_name) \
                .execute()
            if r.data:
                return r.data[0]['category']
        except:
            pass
        return self.auto_categorise(vendor_name)

    def save_category(self, customer_id: str, vendor_name: str, category: str):
        try:
            self.db.table('expense_categories').upsert({
                'customer_id': customer_id,
                'vendor_name': vendor_name.lower(),
                'category':    category.lower()
            }, on_conflict='customer_id,vendor_name').execute()
        except Exception as e:
            print(f"[Accounts] save_category error: {e}")

    def set_category(self, customer_id: str, text: str) -> str:
        """Handle: 'Ali Hardware is materials' or 'set Ali as transport'"""
        try:
            parts = text.lower().replace(' is in the ', '|').replace(' is ', '|')                         .replace(' as ', '|').replace(' category', '').split('|')
            if len(parts) >= 2:
                vendor = parts[0].strip().replace('set ', '').replace('category ', '')
                cat_raw = parts[1].strip().rstrip('.')
                # Fuzzy match to known categories
                cat = 'others'
                for known in EXPENSE_CATEGORIES:
                    if known in cat_raw or cat_raw in known:
                        cat = known
                        break
                # Extra aliases
                aliases = {
                    'material': 'materials', 'bahan': 'materials',
                    'transport': 'transport', 'petrol': 'transport',
                    'utility': 'utilities', 'letrik': 'utilities',
                    'gaji': 'salary', 'wage': 'salary',
                    'sewa': 'rent', 'office': 'rent',
                    'alat': 'equipment', 'tool': 'equipment',
                    'iklan': 'marketing', 'ads': 'marketing',
                    'buruh': 'labour', 'worker': 'labour',
                }
                if cat == 'others':
                    for alias, mapped in aliases.items():
                        if alias in cat_raw:
                            cat = mapped
                            break
                self.save_category(customer_id, vendor, cat)
                self.log_audit(customer_id, 'category_set', 'expense_category', vendor,
                               f"Category set: {vendor} → {cat}")
                return f"✅ Got it. *{vendor.title()}* will now be categorised as *{cat}* in all reports."
            return "Please specify vendor and category. Example: *Ali Hardware is materials*"
        except Exception as e:
            return f"Error: {e}"

    def get_expense_breakdown(self, customer_id: str, period: str = 'this_month') -> dict:
        start, end   = self._period(period)
        cash_out     = self._cash_receipts(customer_id, start, end)
        breakdown    = {cat: 0.0 for cat in EXPENSE_CATEGORIES}
        for rec in cash_out:
            if rec.get('direction') != 'out':
                continue
            notes = rec.get('notes', '') or ''
            if 'category:' in notes:
                cat = notes.split('category:')[-1].strip()
            else:
                cat = self.get_category(customer_id, rec.get('party_name', ''))
            amt = float(rec.get('amount', 0))
            if cat in breakdown:
                breakdown[cat] += amt
            else:
                breakdown['others'] += amt
        return breakdown

    # ── 2. P&L Statement ──────────────────────────────────────────────────────

    def generate_pl(self, customer_id: str, period: str = 'this_month') -> str:
        start, end   = self._period(period)
        customer     = self._customer(customer_id)
        biz_name     = customer.get('business_name', 'Your Business')
        invoices     = self._invoices(customer_id, start, end)
        cash_in      = self._cash_receipts(customer_id, start, end)
        breakdown    = self.get_expense_breakdown(customer_id, period)

        # Revenue
        service_revenue = sum(float(i['grand_total']) for i in invoices
                              if i.get('status') in ['paid'])
        cash_sales      = sum(float(r['amount']) for r in cash_in if r.get('direction') == 'in')
        total_revenue   = service_revenue + cash_sales
        returns         = 0.0
        net_revenue     = total_revenue - returns

        # COGS
        cogs = breakdown.get('materials', 0) + breakdown.get('labour', 0)
        gross_profit = net_revenue - cogs

        # Operating expenses
        op_expenses = {k: v for k, v in breakdown.items()
                       if k not in ('materials', 'labour', 'salary')}
        salary_cost  = breakdown.get('salary', 0)
        total_op_exp = sum(op_expenses.values()) + salary_cost
        ebit         = gross_profit - total_op_exp

        # Tax estimate (SME rate 17% on first RM600k, 24% above)
        tax_rate = 0.17 if net_revenue <= 600000 else 0.24
        income_tax = max(0, ebit * tax_rate)
        net_earnings = ebit - income_tax

        period_label = self._period_label(period)

        # ── HTML template ────────────────────────────────────────────────────
        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11px; color: #222; margin: 0; }}
  .header {{ background: #1a3a5c; color: white; padding: 18px 20px 10px; }}
  .header h1 {{ margin: 0; font-size: 18px; }}
  .header p {{ margin: 2px 0; font-size: 11px; opacity: 0.85; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ background: #1a3a5c; color: white; padding: 7px 10px; text-align: left; font-size: 11px; }}
  th.num {{ text-align: right; }}
  td {{ padding: 5px 10px; border-bottom: 1px solid #eee; }}
  td.num {{ text-align: right; }}
  .section {{ background: #f0f4f8; font-weight: bold; padding: 7px 10px; }}
  .total {{ font-weight: bold; border-top: 2px solid #1a3a5c; }}
  .subtotal {{ font-weight: bold; border-top: 1px solid #aaa; background: #f9f9f9; }}
  .highlight {{ background: #1a3a5c; color: white; font-weight: bold; }}
  .positive {{ color: #1a7a3c; }}
  .negative {{ color: #c0392b; }}
  .indent {{ padding-left: 25px; }}
  .footer {{ margin-top: 20px; font-size: 9px; color: #888; text-align: center; }}
</style></head><body>
<div class="header">
  <h1>{biz_name}</h1>
  <p>Profit & Loss Statement (Penyata Untung Rugi)</p>
  <p>{period_label}</p>
  <p style="font-size:9px;opacity:0.7">Prepared in accordance with MPERS | Currency: MYR</p>
</div>
<table>
  <tr><th>Description</th><th class="num">RM</th></tr>

  <tr><td colspan="2" class="section">REVENUE (PENDAPATAN)</td></tr>
  <tr><td class="indent">Service / Sales Revenue</td><td class="num">{service_revenue:,.2f}</td></tr>
  <tr><td class="indent">Cash Sales</td><td class="num">{cash_sales:,.2f}</td></tr>
  <tr><td class="indent">(Less: Returns, Refunds, Discounts)</td><td class="num">({returns:,.2f})</td></tr>
  <tr class="subtotal"><td><b>Total Net Revenue</b></td><td class="num"><b>{net_revenue:,.2f}</b></td></tr>

  <tr><td colspan="2" class="section">COST OF SALES (KOS JUALAN)</td></tr>
  <tr><td class="indent">Materials & Supplies</td><td class="num">{breakdown['materials']:,.2f}</td></tr>
  <tr><td class="indent">Direct Labour</td><td class="num">{breakdown['labour']:,.2f}</td></tr>
  <tr class="subtotal"><td><b>Total Cost of Sales</b></td><td class="num"><b>{cogs:,.2f}</b></td></tr>

  <tr class="subtotal {'positive' if gross_profit >= 0 else 'negative'}">
    <td><b>Gross Profit (Untung Kasar)</b></td>
    <td class="num {'positive' if gross_profit >= 0 else 'negative'}"><b>{gross_profit:,.2f}</b></td>
  </tr>

  <tr><td colspan="2" class="section">OPERATING EXPENSES (PERBELANJAAN OPERASI)</td></tr>
  <tr><td class="indent">Salaries, Wages & Benefits</td><td class="num">{salary_cost:,.2f}</td></tr>
  <tr><td class="indent">Rental & Premises</td><td class="num">{breakdown['rent']:,.2f}</td></tr>
  <tr><td class="indent">Utilities (TNB, Water, Internet)</td><td class="num">{breakdown['utilities']:,.2f}</td></tr>
  <tr><td class="indent">Transport & Fuel</td><td class="num">{breakdown['transport']:,.2f}</td></tr>
  <tr><td class="indent">Equipment & Maintenance</td><td class="num">{breakdown['equipment']:,.2f}</td></tr>
  <tr><td class="indent">Marketing & Advertising</td><td class="num">{breakdown['marketing']:,.2f}</td></tr>
  <tr><td class="indent">Professional Fees</td><td class="num">{breakdown['professional']:,.2f}</td></tr>
  <tr><td class="indent">Meals & Entertainment</td><td class="num">{breakdown['meals']:,.2f}</td></tr>
  <tr><td class="indent">Other Expenses</td><td class="num">{breakdown['others']:,.2f}</td></tr>
  <tr class="subtotal"><td><b>Total Operating Expenses</b></td><td class="num"><b>{total_op_exp:,.2f}</b></td></tr>

  <tr class="subtotal">
    <td><b>Earnings Before Tax (EBT)</b></td>
    <td class="num {'positive' if ebit >= 0 else 'negative'}"><b>{ebit:,.2f}</b></td>
  </tr>
  <tr><td class="indent">Estimated Income Tax ({int(tax_rate*100)}% SME rate)</td><td class="num">({income_tax:,.2f})</td></tr>

  <tr class="highlight">
    <td>NET EARNINGS / (LOSS)</td>
    <td class="num">{net_earnings:,.2f}</td>
  </tr>
</table>
<div class="footer">
  Generated by Fexonpay | {datetime.now().strftime('%d %b %Y %H:%M')} |
  This statement is prepared for management purposes. For statutory filing consult a licensed accountant.
</div>
</body></html>"""

        filename = f"PL_{customer_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        path = self._save_pdf(html, filename)
        link = self._pdf_link(filename)

        self.log_audit(customer_id, 'report_generated', 'pl_statement', filename,
                       f"P&L generated for {period_label}")

        summary = (
            f"📊 *Profit & Loss Statement*\n"
            f"_{period_label}_\n\n"
            f"💰 Net Revenue:       RM{net_revenue:>12,.2f}\n"
            f"🏭 Cost of Sales:    (RM{cogs:>11,.2f})\n"
            f"📈 Gross Profit:      RM{gross_profit:>12,.2f}\n"
            f"💸 Operating Exp:    (RM{total_op_exp:>11,.2f})\n"
            f"🧾 Est. Tax:         (RM{income_tax:>11,.2f})\n"
            f"{'─'*35}\n"
            f"{'✅' if net_earnings >= 0 else '❌'} *Net {'Profit' if net_earnings >= 0 else 'Loss'}:        "
            f"{'  ' if net_earnings >= 0 else ''} RM{net_earnings:>12,.2f}*\n\n"
            f"📄 Full PDF Report:\n{link}"
        )
        return summary

    # ── 3. Balance Sheet ──────────────────────────────────────────────────────

    def generate_balance_sheet(self, customer_id: str) -> str:
        customer = self._customer(customer_id)
        biz_name = customer.get('business_name', 'Your Business')
        now      = datetime.now(__import__("pytz").timezone("Asia/Kuala_Lumpur"))
        start_yr = now.replace(month=1, day=1).isoformat()
        end_now  = now.isoformat()

        invoices  = self._invoices(customer_id, start_yr, end_now)
        cash_recs = self._cash_receipts(customer_id, start_yr, end_now)

        # Assets
        cash_in   = sum(float(r['amount']) for r in cash_recs if r.get('direction') == 'in')
        cash_out  = sum(float(r['amount']) for r in cash_recs if r.get('direction') == 'out')
        cash_bal  = cash_in - cash_out  # Can be negative — owner needs to know

        receivables = sum(float(i['grand_total']) for i in invoices
                          if i.get('status') in ['sent', 'ready'])
        total_current_assets = cash_bal + receivables

        # Liabilities
        all_inv_ytd = self._invoices(customer_id, start_yr, end_now)
        payables    = sum(float(i['grand_total']) for i in all_inv_ytd
                          if i.get('status') == 'pending')

        # SST payable estimate
        total_revenue = sum(float(i['grand_total']) for i in invoices)
        sst_payable   = total_revenue * SST_RATE if total_revenue >= SST_THRESHOLD else 0

        total_liabilities = payables + sst_payable
        net_assets        = total_current_assets - total_liabilities

        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11px; color: #222; }}
  .header {{ background: #1a3a5c; color: white; padding: 18px 20px 10px; }}
  .header h1 {{ margin: 0; font-size: 18px; }}
  .header p {{ margin: 2px 0; font-size: 11px; opacity: 0.85; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ background: #1a3a5c; color: white; padding: 7px 10px; text-align: left; }}
  th.num {{ text-align: right; }}
  td {{ padding: 5px 10px; border-bottom: 1px solid #eee; }}
  td.num {{ text-align: right; }}
  .section {{ background: #f0f4f8; font-weight: bold; }}
  .subtotal {{ font-weight: bold; border-top: 1px solid #aaa; }}
  .highlight {{ background: #1a3a5c; color: white; font-weight: bold; }}
  .indent {{ padding-left: 25px; }}
  .footer {{ margin-top: 20px; font-size: 9px; color: #888; text-align: center; }}
</style></head><body>
<div class="header">
  <h1>{biz_name}</h1>
  <p>Balance Sheet (Lembaran Imbangan)</p>
  <p>As at {now.strftime('%d %B %Y')}</p>
  <p style="font-size:9px;opacity:0.7">Prepared in accordance with MPERS | Currency: MYR</p>
</div>
<table>
  <tr><th>Description</th><th class="num">RM</th></tr>

  <tr><td colspan="2" class="section">ASSETS (ASET)</td></tr>
  <tr><td colspan="2" class="section" style="background:#e8eef5">Current Assets (Aset Semasa)</td></tr>
  <tr><td class="indent">Cash & Bank Balances</td><td class="num">{cash_bal:,.2f}</td></tr>
  <tr><td class="indent">Trade Receivables (Belum Terima)</td><td class="num">{receivables:,.2f}</td></tr>
  <tr><td class="indent">Other Receivables</td><td class="num">0.00</td></tr>
  <tr class="subtotal"><td><b>Total Current Assets</b></td><td class="num"><b>{total_current_assets:,.2f}</b></td></tr>

  <tr><td colspan="2" class="section">LIABILITIES (LIABILITI)</td></tr>
  <tr><td colspan="2" class="section" style="background:#e8eef5">Current Liabilities (Liabiliti Semasa)</td></tr>
  <tr><td class="indent">Trade Payables (Belum Bayar)</td><td class="num">{payables:,.2f}</td></tr>
  <tr><td class="indent">SST Payable</td><td class="num">{sst_payable:,.2f}</td></tr>
  <tr><td class="indent">EPF/SOCSO Payable</td><td class="num">0.00</td></tr>
  <tr class="subtotal"><td><b>Total Current Liabilities</b></td><td class="num"><b>{total_liabilities:,.2f}</b></td></tr>

  <tr class="highlight"><td>NET ASSETS</td><td class="num">{net_assets:,.2f}</td></tr>

  <tr><td colspan="2" class="section">EQUITY (EKUITI PEMILIK)</td></tr>
  <tr><td class="indent">Owner's Capital</td><td class="num">{net_assets:,.2f}</td></tr>
  <tr><td class="indent">Retained Earnings</td><td class="num">0.00</td></tr>
  <tr class="highlight"><td>TOTAL EQUITY</td><td class="num">{net_assets:,.2f}</td></tr>
</table>
<div class="footer">
  Generated by Fexonpay | {now.strftime('%d %b %Y %H:%M')} |
  For statutory filing consult a licensed accountant.
</div>
</body></html>"""

        filename = f"BS_{customer_id[:8]}_{now.strftime('%Y%m%d_%H%M')}.pdf"
        path     = self._save_pdf(html, filename)
        link     = self._pdf_link(filename)

        self.log_audit(customer_id, 'report_generated', 'balance_sheet', filename,
                       f"Balance Sheet generated as at {now.strftime('%d %b %Y')}")

        return (
            f"🏦 *Balance Sheet*\n"
            f"_As at {now.strftime('%d %B %Y')}_\n\n"
            f"*ASSETS*\n"
            f"  Cash & Bank:         RM{cash_bal:>10,.2f}\n"
            f"  Trade Receivables:   RM{receivables:>10,.2f}\n"
            f"  Total Assets:        RM{total_current_assets:>10,.2f}\n\n"
            f"*LIABILITIES*\n"
            f"  Trade Payables:      RM{payables:>10,.2f}\n"
            f"  SST Payable:         RM{sst_payable:>10,.2f}\n"
            f"  Total Liabilities:   RM{total_liabilities:>10,.2f}\n\n"
            f"{'─'*35}\n"
            f"*Net Assets / Equity:  RM{net_assets:>10,.2f}*\n\n"
            f"📄 Full PDF Report:\n{link}"
        )

    # ── 4. Cashflow Statement ─────────────────────────────────────────────────

    def generate_cashflow(self, customer_id: str, period: str = 'this_month') -> str:
        start, end = self._period(period)
        customer   = self._customer(customer_id)
        biz_name   = customer.get('business_name', 'Your Business')
        now        = datetime.now()

        invoices   = self._invoices(customer_id, start, end)
        cash_recs  = self._cash_receipts(customer_id, start, end)
        period_lbl = self._period_label(period)

        # Operating cash flows
        cash_from_customers = sum(float(i['grand_total']) for i in invoices
                                  if i.get('status') == 'paid')
        cash_from_customers += sum(float(r['amount']) for r in cash_recs
                                   if r.get('direction') == 'in')

        expenses   = self.get_expense_breakdown(customer_id, period)
        cash_paid  = sum(expenses.values())
        sst_paid   = 0.0
        net_ops    = cash_from_customers - cash_paid - sst_paid

        # Investing (equipment purchases)
        investing  = -(expenses.get('equipment', 0))

        # Financing
        financing  = 0.0

        net_change = net_ops + investing + financing

        # Estimate opening balance
        prev_start, prev_end = self._period('last_month')
        prev_cash_in  = sum(float(r['amount']) for r in
                            self._cash_receipts(customer_id, prev_start, prev_end)
                            if r.get('direction') == 'in')
        prev_cash_out = sum(float(r['amount']) for r in
                            self._cash_receipts(customer_id, prev_start, prev_end)
                            if r.get('direction') == 'out')
        opening_bal   = max(0, prev_cash_in - prev_cash_out)
        closing_bal   = opening_bal + net_change

        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11px; color: #222; }}
  .header {{ background: #1a3a5c; color: white; padding: 18px 20px 10px; }}
  .header h1 {{ margin: 0; font-size: 18px; }}
  .header p {{ margin: 2px 0; font-size: 11px; opacity: 0.85; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ background: #1a3a5c; color: white; padding: 7px 10px; text-align: left; }}
  th.num {{ text-align: right; }}
  td {{ padding: 5px 10px; border-bottom: 1px solid #eee; }}
  td.num {{ text-align: right; }}
  .section {{ background: #f0f4f8; font-weight: bold; }}
  .subtotal {{ font-weight: bold; border-top: 1px solid #aaa; background: #f9f9f9; }}
  .highlight {{ background: #1a3a5c; color: white; font-weight: bold; }}
  .indent {{ padding-left: 25px; }}
  .footer {{ margin-top: 20px; font-size: 9px; color: #888; text-align: center; }}
</style></head><body>
<div class="header">
  <h1>{biz_name}</h1>
  <p>Statement of Cash Flows (Penyata Aliran Wang Tunai)</p>
  <p>{period_lbl}</p>
  <p style="font-size:9px;opacity:0.7">Prepared in accordance with MPERS | Currency: MYR</p>
</div>
<table>
  <tr><th>Description</th><th class="num">RM</th></tr>

  <tr><td colspan="2" class="section">OPERATING CASH FLOW (ALIRAN OPERASI)</td></tr>
  <tr><td class="indent">Net Earnings</td><td class="num">{cash_from_customers - cash_paid:,.2f}</td></tr>
  <tr><td class="indent">Cash Received from Customers</td><td class="num">{cash_from_customers:,.2f}</td></tr>
  <tr><td class="indent">Cash Paid to Suppliers & Expenses</td><td class="num">({cash_paid:,.2f})</td></tr>
  <tr><td class="indent">SST Paid to Customs</td><td class="num">({sst_paid:,.2f})</td></tr>
  <tr class="subtotal"><td><b>Net Cash from Operations</b></td><td class="num"><b>{net_ops:,.2f}</b></td></tr>

  <tr><td colspan="2" class="section">INVESTING CASH FLOW (ALIRAN PELABURAN)</td></tr>
  <tr><td class="indent">Equipment & Asset Purchases</td><td class="num">({abs(investing):,.2f})</td></tr>
  <tr class="subtotal"><td><b>Net Cash from Investing</b></td><td class="num"><b>{investing:,.2f}</b></td></tr>

  <tr><td colspan="2" class="section">FINANCING CASH FLOW (ALIRAN PEMBIAYAAN)</td></tr>
  <tr><td class="indent">Capital Introduced</td><td class="num">0.00</td></tr>
  <tr><td class="indent">Drawings / Repayments</td><td class="num">0.00</td></tr>
  <tr class="subtotal"><td><b>Net Cash from Financing</b></td><td class="num"><b>{financing:,.2f}</b></td></tr>

  <tr><td class="indent"><b>Net Increase / (Decrease) in Cash</b></td><td class="num"><b>{net_change:,.2f}</b></td></tr>
  <tr><td class="indent">Opening Cash Balance</td><td class="num">{opening_bal:,.2f}</td></tr>
  <tr class="highlight"><td>CLOSING CASH BALANCE</td><td class="num">{closing_bal:,.2f}</td></tr>
</table>
<div class="footer">
  Generated by Fexonpay | {now.strftime('%d %b %Y %H:%M')} |
  For statutory filing consult a licensed accountant.
</div>
</body></html>"""

        filename = f"CF_{customer_id[:8]}_{now.strftime('%Y%m%d_%H%M')}.pdf"
        self._save_pdf(html, filename)
        link = self._pdf_link(filename)

        self.log_audit(customer_id, 'report_generated', 'cashflow_statement', filename,
                       f"Cashflow Statement generated for {period_lbl}")

        return (
            f"💵 *Cash Flow Statement*\n"
            f"_{period_lbl}_\n\n"
            f"*Operating Activities*\n"
            f"  Cash from customers:  RM{cash_from_customers:>10,.2f}\n"
            f"  Cash paid out:       (RM{cash_paid:>10,.2f})\n"
            f"  Net from Operations:  RM{net_ops:>10,.2f}\n\n"
            f"*Investing Activities*\n"
            f"  Equipment purchases: (RM{abs(investing):>10,.2f})\n\n"
            f"{'─'*35}\n"
            f"  Net Change in Cash:   RM{net_change:>10,.2f}\n"
            f"  Opening Balance:      RM{opening_bal:>10,.2f}\n"
            f"*Closing Balance:       RM{closing_bal:>10,.2f}*\n\n"
            f"📄 Full PDF Report:\n{link}"
        )

    # ── 5. SST Compliance ─────────────────────────────────────────────────────

    def check_sst(self, customer_id: str) -> str:
        now       = datetime.now()
        yr_start  = now.replace(month=1, day=1).isoformat()
        invoices  = self._invoices(customer_id, yr_start, now.isoformat())
        ytd_rev   = sum(float(i['grand_total']) for i in invoices)
        sst_due   = ytd_rev * SST_RATE if ytd_rev >= SST_THRESHOLD else 0
        remaining = max(0, SST_THRESHOLD - ytd_rev)
        pct       = min(100, (ytd_rev / SST_THRESHOLD) * 100)

        if ytd_rev >= SST_THRESHOLD:
            status_line = (
                f"⚠️ *SST Registration Required*\n"
                f"Your revenue has exceeded the RM500,000 threshold.\n"
                f"You must register for SST with Royal Malaysian Customs.\n"
                f"SST Payable (6%): RM{sst_due:,.2f}\n"
            )
        elif pct >= 80:
            status_line = (
                f"⚠️ *Approaching SST Threshold*\n"
                f"You are at {pct:.1f}% of the RM500,000 SST threshold.\n"
                f"Only RM{remaining:,.2f} more before registration is required.\n"
            )
        else:
            status_line = (
                f"✅ *Below SST Threshold*\n"
                f"No SST registration required yet.\n"
                f"RM{remaining:,.2f} remaining before threshold.\n"
            )

        self.log_audit(customer_id, 'sst_check', 'sst_compliance', '',
                       f"SST check: YTD revenue RM{ytd_rev:,.2f}")

        return (
            f"🧾 *SST Compliance Status*\n"
            f"_Year to date: {now.strftime('%d %b %Y')}_\n\n"
            f"YTD Revenue:    RM{ytd_rev:>12,.2f}\n"
            f"SST Threshold:  RM{SST_THRESHOLD:>12,.2f}\n"
            f"Progress:       {'█' * int(pct/10)}{'░' * (10 - int(pct/10))} {pct:.1f}%\n\n"
            f"{status_line}\n"
            f"💡 SST filing is bi-monthly. Next due: {self._next_sst_due()}"
        )

    def _next_sst_due(self) -> str:
        now   = datetime.now()
        month = now.month
        if month <= 2:   due = date(now.year, 3, 31)
        elif month <= 4: due = date(now.year, 5, 31)
        elif month <= 6: due = date(now.year, 7, 31)
        elif month <= 8: due = date(now.year, 9, 30)
        elif month <= 10:due = date(now.year, 11, 30)
        else:            due = date(now.year + 1, 1, 31)
        return due.strftime('%d %b %Y')

    # ── 6. Payroll ────────────────────────────────────────────────────────────

    def calc_epf(self, salary: float) -> tuple:
        emp  = round(salary * EPF_EMPLOYEE_RATE, 2)
        rate = EPF_EMPLOYER_RATE if salary <= 5000 else EPF_EMPLOYER_RATE_HIGH
        ern  = round(salary * rate, 2)
        return emp, ern

    def calc_socso(self, salary: float) -> tuple:
        for ceiling, emp_rate, ern_rate in SOCSO_TABLE:
            if salary <= ceiling:
                return emp_rate, ern_rate
        return 62.50, 69.05

    def calc_eis(self, salary: float) -> tuple:
        cap  = min(salary, EIS_MAX_SALARY)
        emp  = round(cap * EIS_EMPLOYEE_RATE, 2)
        ern  = round(cap * EIS_EMPLOYER_RATE, 2)
        return emp, ern

    def add_employee(self, customer_id: str, text: str) -> str:
        """Parse: ADD EMPLOYEE Ali IC:123456789012 SALARY:3500"""
        try:
            import re
            name    = re.search(r'employee\s+([A-Za-z\s]+?)(?:\s+IC|\s+SALARY|$)', text, re.I)
            ic      = re.search(r'IC[:\s]+(\d{12})', text, re.I)
            salary  = re.search(r'SALARY[:\s]+([\d,\.]+)', text, re.I)
            sal_val = float(salary.group(1).replace(',', '')) if salary else 0
            ic_val_tmp = ic.group(1) if ic else ''
            name_val= name.group(1).strip() if name else (f'Employee {ic_val_tmp[-4:]}' if ic_val_tmp else 'Unknown')
            ic_val  = ic.group(1) if ic else ''
            if sal_val <= 0:
                return "Please specify salary. Example: *ADD EMPLOYEE Ali IC:123456789012 SALARY:3500*"
            self.db.table('employees').insert({
                'customer_id':    customer_id,
                'business_name':  getattr(self, '_biz', ''),
                'name':           name_val,
                'ic_number':      ic_val,
                'monthly_salary': sal_val
            }).execute()
            epf_e, epf_r   = self.calc_epf(sal_val)
            soc_e, soc_r   = self.calc_socso(sal_val)
            eis_e, eis_r   = self.calc_eis(sal_val)
            net            = sal_val - epf_e - soc_e - eis_e
            self.log_audit(customer_id, 'employee_added', 'employees', name_val,
                           f"Employee added: {name_val}, salary RM{sal_val:,.2f}")
            return (
                f"✅ *Employee Added: {name_val}*\n\n"
                f"Gross Salary:     RM{sal_val:>8,.2f}\n"
                f"EPF (Employee 11%): (RM{epf_e:>6,.2f})\n"
                f"SOCSO (Employee):   (RM{soc_e:>6,.2f})\n"
                f"EIS (Employee 0.2%): (RM{eis_e:>5,.2f})\n"
                f"{'─'*30}\n"
                f"Net Take-Home:    RM{net:>8,.2f}\n\n"
                f"Employer Contributions:\n"
                f"EPF (13%):  RM{epf_r:,.2f} | SOCSO: RM{soc_r:,.2f} | EIS: RM{eis_r:,.2f}"
            )
        except Exception as e:
            return f"Error adding employee: {e}"

    def remove_employee(self, customer_id: str, text: str) -> str:
        try:
            parts = text.lower().replace('remove employee', '').replace('delete employee', '').strip()
            name = parts.strip()
            if not name:
                return "❌ Please provide employee name. Example: remove employee Ali"
            r = self.db.table('employees').select('*')                 .eq('customer_id', customer_id)                 .eq('status', 'active')                 .ilike('name', f'%{name}%').execute()
            if not r.data:
                return f"❌ No active employee found matching '{name}'"
            emp = r.data[0]
            self.db.table('employees').update({'status': 'inactive'})                 .eq('id', emp['id']).execute()
            return f"✅ {emp['name']} removed from payroll."
        except Exception as e:
            return f"❌ Error removing employee: {e}"

    def run_payroll(self, customer_id: str) -> str:
        now       = datetime.now()
        month_yr  = now.strftime('%Y-%m')
        try:
            emps = self.db.table('employees') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .eq('status', 'active') \
                .execute().data or []
            if not emps:
                return ("No employees found. Add employees first.\n"
                        "Example: *ADD EMPLOYEE Ali IC:123456789012 SALARY:3500*")
            lines        = []
            total_gross  = 0
            total_net    = 0
            total_epf_e  = 0
            total_epf_r  = 0
            total_soc_e  = 0
            total_soc_r  = 0
            for emp in emps:
                sal    = float(emp['monthly_salary'])
                epf_e, epf_r = self.calc_epf(sal)
                soc_e, soc_r = self.calc_socso(sal)
                eis_e, eis_r = self.calc_eis(sal)
                net    = sal - epf_e - soc_e - eis_e
                total_gross += sal
                total_net   += net
                total_epf_e += epf_e
                total_epf_r += epf_r
                total_soc_e += soc_e
                total_soc_r += soc_r
                lines.append(f"  {emp['name']}: RM{sal:,.2f} → Net RM{net:,.2f}")
                self.db.table('payroll').upsert({
                    'customer_id':    customer_id,
                    'employee_name':  emp['name'],
                    'ic_number':      emp.get('ic_number', ''),
                    'monthly_salary': sal,
                    'epf_employee':   epf_e,
                    'epf_employer':   epf_r,
                    'socso_employee': soc_e,
                    'socso_employer': soc_r,
                    'eis_employee':   eis_e,
                    'eis_employer':   eis_r,
                    'net_salary':     net,
                    'month_year':     month_yr,
                    'status':         'processed'
                }, on_conflict='customer_id,employee_name,month_year').execute()
            self.log_audit(customer_id, 'payroll_run', 'payroll', month_yr,
                           f"Payroll processed for {len(emps)} employees, total RM{total_gross:,.2f}")
            return (
                f"💼 *Payroll — {now.strftime('%B %Y')}*\n\n"
                + '\n'.join(lines) +
                f"\n\n{'─'*35}\n"
                f"Total Gross:        RM{total_gross:>10,.2f}\n"
                f"Total Net Salary:   RM{total_net:>10,.2f}\n\n"
                f"*Statutory Contributions (Employer):*\n"
                f"EPF (13%):          RM{total_epf_r:>10,.2f}\n"
                f"SOCSO:              RM{total_soc_r:>10,.2f}\n"
                f"*Total Cost to Co:  RM{total_gross + total_epf_r + total_soc_r:>10,.2f}*\n\n"
                f"💡 EPF & SOCSO due by 15th of next month."
            )
        except Exception as e:
            return f"Payroll error: {e}"

    # ── 7. Audit Trail ────────────────────────────────────────────────────────

    def log_audit(self, customer_id: str, action: str, entity: str,
                  entity_id: str, description: str, amount: float = None):
        try:
            self.db.table('audit_trail').insert({
                'customer_id':  customer_id,
                'business_name': getattr(self, '_biz', ''),
                'action_type':  action,
                'entity_type':  entity,
                'entity_id':    str(entity_id),
                'description':  description,
                'amount':       amount
            }).execute()
        except Exception as e:
            print(f"[Audit] log error: {e}")

    def get_audit_trail(self, customer_id: str, days: int = 30) -> str:
        try:
            since = (datetime.now() - timedelta(days=days)).isoformat()
            r     = self.db.table('audit_trail') \
                .select('*') \
                .eq('customer_id', customer_id) \
                .gte('created_at', since) \
                .order('created_at', desc=True) \
                .limit(20).execute()
            if not r.data:
                return "No audit entries found for the past 30 days."
            lines = [f"🔍 *Audit Trail — Last {days} days*\n"]
            for row in r.data:
                ts    = row['created_at'][:16].replace('T', ' ')
                lines.append(f"  [{ts}] {row['action_type'].upper()} — {row['description']}")
            return '\n'.join(lines)
        except Exception as e:
            return f"Audit error: {e}"

    # ── 8. Budget vs Actual ───────────────────────────────────────────────────

    def budget_vs_actual(self, customer_id: str) -> str:
        breakdown = self.get_expense_breakdown(customer_id, 'this_month')
        try:
            budgets_raw = self.db.table('customer_preferences') \
                .select('preference_key, preference_value') \
                .eq('customer_id', customer_id) \
                .ilike('preference_key', 'budget_%') \
                .execute().data or []
            budgets = {}
            for b in budgets_raw:
                cat = b['preference_key'].replace('budget_', '')
                budgets[cat] = float(b['preference_value'])
        except:
            budgets = {}

        now   = datetime.now()
        lines = [f"📊 *Budget vs Actual — {now.strftime('%B %Y')}*\n"]
        alerts= []
        for cat in EXPENSE_CATEGORIES:
            actual = breakdown.get(cat, 0)
            budget = budgets.get(cat, 0)
            if actual == 0 and budget == 0:
                continue
            if budget > 0:
                pct    = (actual / budget) * 100
                status = '🔴' if pct > 100 else '🟡' if pct > 80 else '🟢'
                lines.append(f"  {status} {cat.title():<14} "
                              f"RM{actual:>8,.2f} / RM{budget:>8,.2f}  ({pct:.0f}%)")
                if pct > 100:
                    alerts.append(f"⚠️ {cat.title()} over budget by RM{actual-budget:,.2f}")
            else:
                lines.append(f"  ⬜ {cat.title():<14} RM{actual:>8,.2f}  (no budget set)")

        if alerts:
            lines.append(f"\n{'─'*35}")
            lines.extend(alerts)
            lines.append("\n💡 Type *SET [category] BUDGET RM[amount]* to update budgets.")
        return '\n'.join(lines)

    # ── 9. Financial Forecast ─────────────────────────────────────────────────

    def forecast(self, customer_id: str) -> str:
        now    = datetime.now()
        months = []
        for i in range(2, -1, -1):
            if i == 0:
                m_start = now.replace(day=1, hour=0, minute=0, second=0)
                m_end   = now
            else:
                m_start = (now - relativedelta(months=i)).replace(day=1, hour=0, minute=0, second=0)
                m_end   = m_start + relativedelta(months=1) - timedelta(seconds=1)
            invs = self._invoices(customer_id, m_start.isoformat(), m_end.isoformat())
            cash = self._cash_receipts(customer_id, m_start.isoformat(), m_end.isoformat())
            rev  = sum(float(x['grand_total']) for x in invs)
            rev += sum(float(r['amount']) for r in cash if r.get('direction') == 'in')
            months.append(rev)

        avg_rev  = sum(months) / len(months) if months else 0
        trend    = ((months[-1] - months[0]) / months[0] * 100) if months[0] > 0 else 0
        growth   = 1 + (trend / 100 / 3)

        forecasts = []
        for i in range(1, 4):
            f_month = (now + relativedelta(months=i)).strftime('%b %Y')
            f_rev   = avg_rev * (growth ** i)
            forecasts.append((f_month, f_rev))

        self.log_audit(customer_id, 'forecast_generated', 'financial_forecast', '',
                       f"3-month forecast generated, avg RM{avg_rev:,.2f}/month")

        hist_lines = []
        for i, rev in enumerate(months):
            m = (now - relativedelta(months=3-i)).strftime('%b %Y')
            hist_lines.append(f"  {m}: RM{rev:>10,.2f}")

        fcast_lines = []
        for m, r in forecasts:
            fcast_lines.append(f"  {m}: RM{r:>10,.2f} (projected)")

        return (
            f"🔮 *3-Month Financial Forecast*\n\n"
            f"*Historical Revenue:*\n" + '\n'.join(hist_lines) +
            f"\n\nTrend: {'📈' if trend >= 0 else '📉'} {abs(trend):.1f}% "
            f"{'growth' if trend >= 0 else 'decline'} over 3 months\n\n"
            f"*Projected Revenue:*\n" + '\n'.join(fcast_lines) +
            f"\n\n💡 Projections based on historical patterns. "
            f"Actual results may vary."
        )

    # ── 10. Business Advisory ─────────────────────────────────────────────────

    def health_score(self, customer_id: str) -> str:
        now      = datetime.now()
        yr_start = now.replace(month=1, day=1).isoformat()
        invoices = self._invoices(customer_id, yr_start, now.isoformat())
        cash_in  = self._cash_receipts(customer_id, yr_start, now.isoformat())

        revenue  = sum(float(i['grand_total']) for i in invoices)
        cash_recs = self._cash_receipts(customer_id, yr_start, now.isoformat())
        expenses  = sum(float(r['amount']) for r in cash_recs if r.get('direction') == 'out')
        profit   = revenue - expenses
        margin   = (profit / revenue * 100) if revenue > 0 else 0
        overdue  = sum(1 for i in invoices
                       if i.get('status') in ['sent','ready']
                       and i.get('due_date','9999') < now.date().isoformat())

        score = 100
        if margin < 10:   score -= 30
        elif margin < 20: score -= 15
        if overdue > 3:   score -= 20
        elif overdue > 0: score -= 10
        if revenue == 0:  score -= 40

        grade = ('🟢 EXCELLENT' if score >= 80 else
                 '🟡 GOOD'      if score >= 60 else
                 '🟠 FAIR'      if score >= 40 else
                 '🔴 NEEDS ATTENTION')

        recs = []
        if margin < 20:
            recs.append("• Profit margin is below 20%. Review your pricing and reduce variable costs.")
        if overdue > 0:
            recs.append(f"• {overdue} overdue invoice(s). Follow up to improve cashflow.")
        if revenue == 0:
            recs.append("• No revenue recorded yet. Start creating invoices for your clients.")
        if not recs:
            recs.append("• Business is performing well. Keep monitoring cashflow weekly.")

        self.log_audit(customer_id, 'health_check', 'advisory', '',
                       f"Health score: {score}/100")

        return (
            f"💡 *Business Health Report*\n"
            f"_{now.strftime('%d %B %Y')}_\n\n"
            f"Overall Score: *{score}/100 — {grade}*\n\n"
            f"YTD Revenue:   RM{revenue:>10,.2f}\n"
            f"YTD Expenses:  RM{expenses:>10,.2f}\n"
            f"Profit Margin: {margin:>9.1f}%\n"
            f"Overdue Inv:   {overdue:>9}\n\n"
            f"*Recommendations:*\n" + '\n'.join(recs)
        )

    # ── Helper ────────────────────────────────────────────────────────────────

    def _period_label(self, period: str) -> str:
        now = datetime.now(__import__("pytz").timezone("Asia/Kuala_Lumpur"))
        labels = {
            'this_month':  now.strftime('For the month of %B %Y'),
            'last_month':  (now - relativedelta(months=1)).strftime('For the month of %B %Y'),
            'this_year':   now.strftime('For the year ending %d %B %Y'),
            'last_year':   f"For the year ended 31 December {now.year - 1}",
        }
        return labels.get(period, now.strftime('As at %d %B %Y'))


