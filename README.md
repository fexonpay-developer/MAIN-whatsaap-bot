# Fexonpay Production

## Server
- Host: srv1449947 (Hostinger VPS)
- Path: /home/fexonpay-prod/
- Port: 8001
- PM2 process: fexonpay-prod (id 9)
- Python: /home/fexonpay-prod/venv/bin/python3

## Start / Restart
```bash
pm2 restart fexonpay-prod
pm2 logs fexonpay-prod
```

## Stack
- FastAPI on port 8001
- Together AI (LLM): Meta-Llama-3-8B (fast) + Llama-3.3-70B (smart)
- Supabase (database)
- OpenClaw (WhatsApp gateway) — account: prod
- Gateway config: /root/.openclaw/config.json

## WhatsApp
- OpenClaw accounts: default (dev), prod (production)
- Replies sent via: --account prod

## Key Files
- main.py — FastAPI webhook handler
- fexonpay_brain.py — AI action router
- fexonpay_cash.py — Cash recording
- fexonpay_quotation.py — Quotes & invoices
- fexonpay_finance.py — Financial reports
- fexonpay_memory.py — Customer memory
- fexonpay_accounts.py — Payroll, SST, P&L
- email_poller.py — Vendor bill email processor

## Environment Variables (.env — not committed)
- SUPABASE_URL
- SUPABASE_KEY
- TOGETHER_API_KEY
- OPENCLAW_API_KEY
