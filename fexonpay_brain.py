"""
fexonpay_brain.py - Fexonpay AI Brain (Two-Tier Model)

Tier 1: Llama-3.1-8B  - fast, cheap, action extraction only
Tier 2: Llama-3.3-70B - smart, full Malaysian accounting knowledge + advisory

Owner never needs to know commands. Just talk naturally.
"""

import os
import json
from together import Together
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv('/home/fexonpay/.env')

# -- Tier 1: Fast action extraction (Gemma 4 4B) ------------------------------
MODEL_FAST = "meta-llama/Meta-Llama-3-8B-Instruct-Lite"

# -- Tier 2: Smart accountant brain (Gemma 4 31B) -----------------------------
MODEL_SMART = "meta-llama/Llama-3.3-70B-Instruct-Turbo"

ACTION_PROMPT = """
Reply ONLY with valid JSON. No prose.

## Actions you can extract:

Invoice creation (owner billing a client):
{"action":"create_invoice","vendor_name":"Ahmad","amount":500.00,"description":"cleaning","due_date":"NET30","currency":"MYR","reply":"Creating invoice..."}

Quotation (sending a price quote to a client):
{"action":"quote","raw":"[full original text]","reply":"Creating quotation..."}

Cash received (owner received physical cash):
{"action":"cash_record","reply":"Recording cash..."}

Cash paid out (owner paid someone in cash):
{"action":"cash_record","reply":"Recording cash..."}

Category setting (classifying a vendor/expense):
{"action":"set_category","raw":"[full original text]","reply":"Updating category..."}

Budget setting:
{"action":"budget_set","category":"cleaning","amount":2000.00,"reply":"Setting budget..."}

Multiple budgets:
{"action":"budget_set","bulk":true,"items":[{"category":"cleaning","amount":2000},{"category":"transport","amount":500}],"reply":"Setting budgets..."}

Multiple categories:
{"action":"set_category","bulk":true,"items":[{"vendor":"Ali Hardware","category":"materials"},{"vendor":"TNB","category":"utilities"}],"reply":"Updating categories..."}

Add employee:
{"action":"add_employee","raw":"[full original text]","reply":"Adding employee..."}
{"action":"remove_employee","raw":"[full original text]","reply":"Removing employee..."}

Convert quote to invoice:
{"action":"quote_to_invoice","quote_number":"QT-00001","reply":"Converting..."}
Vendor history:
{"action":"vendor_history","vendor_name":"Ali","reply":"Loading vendor history..."}

If the message is a question, advisory request, or you cannot clearly extract an action:
{"action":"needs_advisor","reply":""}

## Examples:
"buat invoice untuk Ahmad RM500 kerja cleaning" -> create_invoice
"Ahmad bayar RM500 cash tadi" -> cash_record
"quote Reza RM3000 rewiring" -> quote
"Ali Hardware tu masuk category materials" -> set_category
"TNB is utilities" -> set_category
"vendor history ali" -> {"action":"vendor_history","vendor_name":"ali","reply":"Loading vendor history..."}
"history with TNB" -> {"action":"vendor_history","vendor_name":"TNB","reply":"Loading vendor history..."}
"Petronas is transport" -> set_category
"[vendor] is [category]" -> set_category
"set budget cleaning RM2000 dan transport RM500" -> budget_set bulk
"what is EPF?" -> needs_advisor
"am I making money?" -> needs_advisor
"macam mana nak handle SST?" -> needs_advisor
"I don't know what to do for tax" -> needs_advisor
"how much did I spend" -> {"action":"spend","days":30,"reply":"Loading spend summary..."}
"berapa I spend this week" -> {"action":"spend","days":7,"reply":"Loading spend summary..."}
"spending last 7 days" -> {"action":"spend","days":7,"reply":"Loading spend summary..."}
"""

# -- Tier 2: Full Malaysian accounting advisor prompt (70B model) --------------
ADVISOR_PROMPT = """You are Fexonpay, a friendly AI financial advisor and accountant for Malaysian SME owners.

You speak like a knowledgeable friend - clear, warm, practical. No jargon unless you explain it.
You reply in the same language the owner uses (English, Malay, or mixed).
You always back answers with the owner's REAL data when available.
You ALWAYS reply with valid JSON only.

## Your Personality
- Patient teacher - owners may know nothing about accounting, explain from basics
- Proactive - don't just answer, suggest the next step
- Malaysian context - know local laws, rates, deadlines
- Data-driven - use their actual numbers, not generic advice

## Full Malaysian Accounting Knowledge

### EPF (Employees Provident Fund)
- Mandatory for all employees under Employment Act
- Employee contributes 11% of monthly salary
- Employer contributes 13% (salary <=RM5,000) or 12% (salary >RM5,000)
- Payment due by 15th of following month to KWSP
- Penalty: 6% per annum for late payment
- Register at KWSP website: kwsp.gov.my
- Both employee and employer contributions are tax deductible

### SOCSO (Social Security Organisation / PERKESO)
- Mandatory for employees earning <=RM4,000/month
- Covers employment injury and invalidity
- Rates follow contribution table (approx 1.75% employee + 1.75% employer)
- Payment due by 15th of following month
- Register at perkeso.gov.my

### EIS (Employment Insurance System)
- Mandatory since 2018 under EIS Act 2017
- 0.2% employee + 0.2% employer (capped at RM4,000 salary)
- Provides financial assistance if retrenched
- Payment together with SOCSO

### SST (Sales and Service Tax)
- Service tax: 6% on taxable services
- Threshold: RM500,000 annual taxable turnover
- Must register within 30 days of exceeding threshold
- Filing: bi-monthly (every 2 months)
- Due date: last day of month following taxable period
- Register at MySST: mysst.customs.gov.my

### Income Tax for SMEs
- Sole proprietor/partnership: personal income tax rates (0-30%)
- Sdn Bhd: 17% on first RM600,000 profit, 24% above
- CP204: estimated tax payment, due by 30th of month before assessment year
- CP204 instalments: monthly, 12 months
- Tax return filing: Form B (individual) by 30 June, Form C (company) by 7 months after year end
- File at: mytax.hasil.gov.my

### LHDN e-Invoice (MyInvois)
- Mandatory for all businesses progressively from 2024
- Self-billed invoice for informal vendor payments
- Submit via MyInvois API or portal
- UUID assigned upon validation
- Required for tax deduction claims

### SSM (Companies Commission / Suruhanjaya Syarikat Malaysia)
- Annual return filing required for Sdn Bhd
- Due within 30 days of AGM
- Beneficial ownership register (RORC) required
- Failure to file: fines up to RM50,000

### Accounting Basics for SMEs
- Bookkeeping: record every transaction when it happens
- Reconciliation: match bank statement to records monthly
- Accounts receivable: money clients owe you
- Accounts payable: money you owe vendors
- Gross profit: revenue minus direct costs
- Net profit: gross profit minus all expenses
- Cashflow: actual cash in and out (different from profit)
- Working capital: current assets minus current liabilities

### Common Malaysian SME Tax Deductions
- Business expenses (rent, utilities, salary, marketing)
- Capital allowance on equipment (10-20% per year)
- EPF and SOCSO employer contributions
- Professional fees (accountant, lawyer)
- Training expenses (HRD Corp claimable)
- Vehicle expenses (business use portion only)

### Financial Statement Basics
- P&L: shows if business made profit or loss over a period
- Balance Sheet: snapshot of what business owns and owes at one point
- Cashflow Statement: shows actual cash movement
- All three needed for bank loans, investors, and LHDN

## Response Format
Always reply with JSON:

For educational/advisory responses:
{"action":"reply","reply":"[your response - warm, clear, use owner's real data if available]"}

For actions you detect while advising:
{"action":"create_invoice","vendor_name":"...","amount":0.00,"description":"...","due_date":"NET30","currency":"MYR","reply":"[conversational confirmation]"}

## Rules
- Never make up numbers - use owner's real data or say you don't have it
- Always suggest a next step after answering
- If owner seems confused, offer to walk them through step by step
- Flag urgent compliance issues (overdue tax, SST threshold approaching)
- Respond in the same language as the owner
- Keep replies concise - max 200 words unless explaining a complex topic
- Use bullet points for lists, not walls of text
- Always end advisory responses with one actionable suggestion
"""


class Brain:

    def __init__(self):
        self.client = Together(api_key=os.environ['TOGETHER_API_KEY'])
        self.db     = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])

    # -- Tier 1: Fast routing with 8B model -----------------------------------

    async def _fast_route(self, text: str, state: dict) -> dict:
        """Use cheap 8B model to detect simple actions."""
        try:
            pending = ''
            if state.get('has_pending_invoice'):
                inv = state.get('pending_invoice', {})
                pending = f"\nPENDING INVOICE awaiting CONFIRM/CANCEL: {inv.get('vendor_name')} RM{inv.get('amount')}"

            response = self.client.chat.completions.create(
                model=MODEL_FAST,
                messages=[
                    {'role': 'system', 'content': ACTION_PROMPT + pending},
                    {'role': 'user',   'content': text}
                ],
                max_tokens=250,
                temperature=0.1
            )
            raw = response.choices[0].message.content.strip()
            result = self._parse_json(raw)
            print(f"[Brain-8B] action={result.get('action')} | text={text[:60]}")
            return result
        except Exception as e:
            print(f"[Brain-8B] error: {e}")
            return {'action': 'needs_advisor', 'reply': ''}

    # -- Tier 2: Full advisor with 70B model -----------------------------------

    async def _advisor(self, text: str, state: dict, customer_id: str) -> dict:
        """Use smart 70B model for advisory, educational, and complex queries."""
        try:
            financial_ctx = self._get_financial_context(customer_id) if customer_id else ''
            state_ctx     = self._get_state_context(state)

            context = f"""## Current Date
{datetime.now().strftime('%d %B %Y, %A')}

## Bot State
{state_ctx}

## Owner's Live Financial Data
{financial_ctx}"""

            response = self.client.chat.completions.create(
                model=MODEL_SMART,
                messages=[
                    {'role': 'system', 'content': ADVISOR_PROMPT + f"\n\n{context}"},
                    {'role': 'user',   'content': text}
                ],
                max_tokens=700,
                temperature=0.4
            )
            raw = response.choices[0].message.content.strip()
            result = self._parse_json(raw)
            print(f"[Brain-70B] action={result.get('action')} | text={text[:60]}")
            return result
        except Exception as e:
            print(f"[Brain-70B] error: {e}")
            return {
                'action': 'reply',
                'reply':  'Sorry, I had trouble processing that. Please try again.'
            }

    # -- Main entry point ------------------------------------------------------

    async def handle(self, from_number: str, text: str, state: dict, customer_id: str = None) -> dict:
        """
        Two-tier routing:
        1. Try cheap 8B model first for simple actions
        2. If 8B returns needs_advisor -> escalate to 70B
        """
        try:
            # Tier 1 - fast route
            result = await self._fast_route(text, state)

            # Tier 2 - escalate to advisor if needed
            if result.get('action') == 'needs_advisor':
                result = await self._advisor(text, state, customer_id)

            return result

        except Exception as e:
            print(f"[Brain] handle error: {e}")
            return {
                'action': 'reply',
                'reply':  'Something went wrong. Please try again.'
            }

    # -- Financial context loader ----------------------------------------------

    def _get_financial_context(self, customer_id: str) -> str:
        try:
            now        = datetime.now()
            month_start = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
            yr_start    = now.replace(month=1, day=1, hour=0, minute=0, second=0).isoformat()

            # Invoices this month
            inv = self.db.table('self_invoices') \
                .select('invoice_number,vendor_name,grand_total,status,due_date,created_at') \
                .eq('customer_id', customer_id) \
                .gte('created_at', month_start).execute().data or []

            paid_inv    = [i for i in inv if i.get('status') == 'paid']
            unpaid_inv  = [i for i in inv if i.get('status') in ['sent','ready']]
            overdue_inv = [i for i in unpaid_inv
                          if i.get('due_date','9999') < now.date().isoformat()]

            total_invoiced = sum(float(i['grand_total']) for i in inv)
            total_paid     = sum(float(i['grand_total']) for i in paid_inv)
            total_unpaid   = sum(float(i['grand_total']) for i in unpaid_inv)

            # Cash this month
            cash = self.db.table('cash_receipts') \
                .select('amount,direction,party_name,created_at') \
                .eq('customer_id', customer_id) \
                .gte('created_at', month_start).execute().data or []

            cash_in  = sum(float(r['amount']) for r in cash if r.get('direction') == 'in')
            cash_out = sum(float(r['amount']) for r in cash if r.get('direction') == 'out')

            # YTD revenue
            inv_ytd = self.db.table('self_invoices') \
                .select('grand_total') \
                .eq('customer_id', customer_id) \
                .gte('created_at', yr_start).execute().data or []
            ytd_revenue = sum(float(i['grand_total']) for i in inv_ytd)

            # Employees
            emps = self.db.table('employees') \
                .select('name,monthly_salary') \
                .eq('customer_id', customer_id) \
                .eq('status', 'active').execute().data or []

            # Quotations
            quotes = self.db.table('quotations') \
                .select('quote_number,client_name,total_amount,status') \
                .eq('customer_id', customer_id) \
                .in_('status', ['sent','pending']).execute().data or []

            # Customer info
            cust = self.db.table('customers') \
                .select('business_name,subscription_status') \
                .eq('customer_id', customer_id).execute().data or [{}]
            biz_name = cust[0].get('business_name', 'Your Business') if cust else 'Your Business'

            lines = [
                f"Business: {biz_name}",
                f"Month: {now.strftime('%B %Y')}",
                f"",
                f"THIS MONTH:",
                f"  Invoiced: RM{total_invoiced:,.2f} ({len(inv)} invoices)",
                f"  Collected: RM{total_paid:,.2f}",
                f"  Outstanding: RM{total_unpaid:,.2f} ({len(unpaid_inv)} invoices)",
                f"  Overdue: RM{sum(float(i['grand_total']) for i in overdue_inv):,.2f} ({len(overdue_inv)} invoices)",
                f"  Cash received: RM{cash_in:,.2f}",
                f"  Cash paid out: RM{cash_out:,.2f}",
                f"  Net cash: RM{cash_in - cash_out:,.2f}",
                f"",
                f"YEAR TO DATE:",
                f"  Total revenue: RM{ytd_revenue:,.2f}",
                f"  SST threshold: RM500,000 ({ytd_revenue/500000*100:.1f}% reached)",
                f"",
                f"STAFF: {len(emps)} active employee(s)",
            ]

            for e in emps:
                lines.append(f"  - {e['name']}: RM{float(e['monthly_salary']):,.2f}/month")

            if quotes:
                lines.append(f"")
                lines.append(f"PENDING QUOTES: {len(quotes)}")
                for q in quotes[:3]:
                    lines.append(f"  - {q['quote_number']} | {q.get('client_name','')} | RM{float(q.get('total_amount',0)):,.2f}")

            if overdue_inv:
                lines.append(f"")
                lines.append(f"OVERDUE INVOICES (ACTION NEEDED):")
                for i in overdue_inv[:3]:
                    lines.append(f"  - {i['invoice_number']} | {i['vendor_name']} | RM{float(i['grand_total']):,.2f} | due {i.get('due_date','')}")

            return '\n'.join(lines)

        except Exception as e:
            print(f"[Brain] financial context error: {e}")
            return "Financial data temporarily unavailable."

    def _get_state_context(self, state: dict) -> str:
        parts = []
        if state.get('has_pending_invoice'):
            inv = state.get('pending_invoice', {})
            parts.append(f"PENDING INVOICE awaiting CONFIRM/CANCEL: {inv.get('vendor_name')} RM{inv.get('amount')}")
        return '\n'.join(parts) if parts else 'No pending actions.'

    def _parse_json(self, text: str) -> dict:
        try:
            text = text.strip()
            if text.startswith('```'):
                lines = [l for l in text.split('\n') if not l.strip().startswith('```')]
                text  = '\n'.join(lines).strip()
            start = text.find('{')
            end   = text.rfind('}')
            if start != -1 and end != -1:
                return json.loads(text[start:end+1])
        except Exception as e:
            print(f"[Brain] parse error: {e} | raw: {text[:200]}")
        return {
            'action': 'reply',
            'reply':  "I didn't quite catch that. Could you rephrase? You can ask me anything about your business finances."
        }

    # Keep backward compatibility
    def _is_financial_question(self, text: str) -> bool:
        return True

    def _parse_response(self, text: str) -> dict:
        return self._parse_json(text)

