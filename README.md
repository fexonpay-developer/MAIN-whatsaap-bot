# Fexonpay
### AI-Powered WhatsApp Accounting Bot for Malaysian SMEs

Fexonpay lets small business owners manage their finances entirely through WhatsApp. No app to download, no accounting knowledge needed. Just talk naturally.

---

## Problem
Malaysian SME owners struggle with:
- Manual bookkeeping in Excel or paper
- Expensive accounting software they do not understand
- Hiring full-time bookkeepers for simple tasks
- Delayed financial visibility — they do not know if they are profitable

## Solution
A WhatsApp bot that acts as an AI accountant. Owners just message naturally:

    "Ahmad paid me RM500 cash today"
    "Buat invoice untuk Reza RM2000 electrical works"
    "p&l"
    "how much did I spend this month?"

The bot handles the rest.

---

## Features

### Invoicing
- Create invoices via natural language
- Auto-generate PDF invoices
- Email directly to clients
- Mark invoices as paid with PAY IV-XXXXX

### Cash Management
- Record cash in/out naturally
- Auto-categorise expenses (utilities, materials, salary etc.)
- Cash summary and spending reports

### Financial Reports
- Profit & Loss Statement
- Balance Sheet
- Cash Flow Statement
- Dashboard overview

### Email Invoice Processing
- Forward vendor invoices to your Fexonpay email
- Bot auto-extracts and categorises (PDF, CSV, ZIP supported)

### Payroll
- Add employees with EPF, SOCSO, EIS auto-calculation
- Malaysian statutory compliance built-in

### Morning Briefing
- Daily 8AM summary of outstanding invoices, cash position, top expenses

---

## Tech Stack

| Layer | Technology |
|---|---|
| Interface | WhatsApp (via OpenClaw) |
| Backend | FastAPI (Python) |
| AI Brain | Meta-Llama 3.3 70B + Llama 3.1 8B (Together AI) |
| Database | Supabase (PostgreSQL) |
| PDF Generation | pdfkit + wkhtmltopdf |
| Email | Gmail SMTP |
| Hosting | Ubuntu VPS |
| Process Manager | PM2 |

---

## Architecture

    WhatsApp Message
           |
    OpenClaw Gateway
           |
    FastAPI Webhook (main.py)
           |
    Fast Handler (exact commands) -> Direct response
           |
    AI Brain Tier 1 - Llama 3.1 8B (action extraction)
           |
    AI Brain Tier 2 - Llama 3.3 70B (advisory and complex queries)
           |
    Supabase Database
           |
    WhatsApp Response

---

## Example Commands

    Invoice:      "invoice Ahmad RM500 cleaning"
    Confirm:      "confirm"
    Mark paid:    "PAY IV-00025"
    Cash in:      "Ahmad paid me RM200 cash"
    Cash out:     "Paid TNB RM300"
    Reports:      "p&l" / "balance sheet" / "dashboard"
    Spending:     "how much did I spend today"
    Vendors:      "vendors"
    Set email:    "set email myname@gmail.com"
    Payroll:      "add employee Ahmad IC:901234567890 salary:3500"

---

## Security
- All secrets stored in .env (not committed)
- Customer data isolated by customer_id
- Each WhatsApp number gets its own data silo
- .env excluded via .gitignore

---

## Built for Malaysia
- Bilingual (English + Bahasa Malaysia)
- EPF, SOCSO, EIS calculations
- SST compliance
- MyInvois / e-Invoice ready (coming soon)
- Malaysian business context in AI brain

---

## Roadmap
- Multi-item quotations
- Collections agent (auto-chase unpaid invoices)
- AR aging report (30/60/90 days)
- MyInvois e-Invoice Type 11
- Multi-tenant architecture (private cloud)
- CP204 tax estimation

---

Built for Malaysian SME owners who just want to run their business.
