"""
fexonpay_morning_briefing.py
Fexonpay — Daily Morning Briefing

Runs every day at 8:00 AM Malaysia time (UTC+8)
Sends every active customer a personalised WhatsApp briefing

Run with PM2:
    pm2 start fexonpay_morning_briefing.py --name fexonpay-briefing --interpreter python3
"""

import os
import time
import subprocess
from datetime import datetime, timedelta
import pytz
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/home/fexonpay-prod/.env', override=True)

db = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
MY_TZ = pytz.timezone('Asia/Kuala_Lumpur')


# ── WhatsApp sender ────────────────────────────────────────────────────────────

def send_whatsapp(phone: str, message: str):
    try:
        result = subprocess.run(
            ['openclaw', 'message', 'send', '--channel', 'whatsapp', '--account', 'prod', '--target', phone, '--message', message],
            env={**__import__('os').environ, 'OPENCLAW_GATEWAY_URL': 'ws://127.0.0.1:18789', 'OPENCLAW_GATEWAY_TOKEN': 'c4cb0fc562700ac37cc60bb9983a1fb752a61801af15b748'},
            check=True, capture_output=True, text=True
        )
        print(f"[Briefing] Sent to {phone}: {result.stdout.strip()}")
        return True
    except Exception as e:
        print(f"[Briefing] Failed to send to {phone}: {e}")
        return False


# ── Data fetcher ───────────────────────────────────────────────────────────────

def get_briefing_data(customer_id: str) -> dict:
    """Pull all data needed for the morning briefing."""
    try:
        now        = datetime.now(MY_TZ)
        today_str  = now.date().isoformat()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        week_ahead  = (now + timedelta(days=7)).date().isoformat()

        # ── Invoices ──────────────────────────────────────────────────────────
        all_inv = db.table('self_invoices') \
            .select('invoice_number, vendor_name, grand_total, status, due_date, created_at') \
            .eq('customer_id', customer_id) \
            .execute()
        invoices = all_inv.data or []

        outstanding = [i for i in invoices if i['status'] in ['ready', 'sent', 'pending']]
        overdue     = [i for i in outstanding if i.get('due_date') and str(i['due_date'])[:10] < today_str]
        due_this_week = [i for i in outstanding
                         if i.get('due_date')
                         and today_str <= str(i['due_date'])[:10] <= week_ahead
                         and i not in overdue]
        month_inv   = [i for i in invoices if i.get('created_at', '') >= month_start]
        month_invoiced = sum(float(i['grand_total']) for i in month_inv)

        # ── Quotations ────────────────────────────────────────────────────────

        # ── Transactions this month ───────────────────────────────────────────
        all_txns = db.table('cash_receipts') \
            .select('amount, direction, created_at') \
            .eq('customer_id', customer_id) \
            .gte('created_at', month_start) \
            .execute()
        txns = all_txns.data or []
        month_expense = sum(float(t['amount']) for t in txns if t.get('direction') == 'out')

        # ── Determine business health ─────────────────────────────────────────
        overdue_amt   = sum(float(i['grand_total']) for i in overdue)
        due_week_amt  = sum(float(i['grand_total']) for i in due_this_week)
        net_position  = month_invoiced - month_expense

        if len(overdue) == 0 and net_position >= 0:
            health = '🟢 GOOD'
        elif len(overdue) <= 2 and net_position >= 0:
            health = '🟡 ATTENTION NEEDED'
        else:
            health = '🔴 ACTION REQUIRED'

        # ── Determine top action for today ────────────────────────────────────
        if overdue:
            top_action = f"Chase {len(overdue)} overdue invoice{'s' if len(overdue) > 1 else ''} worth RM{overdue_amt:,.2f}"
        elif due_this_week:
            top_action = f"Prepare for {len(due_this_week)} payment{'s' if len(due_this_week) > 1 else ''} due this week — RM{due_week_amt:,.2f}"
        else:
            top_action = "All clear — great time to follow up with clients"

        return {
            'health':          health,
            'month_invoiced':  month_invoiced,
            'month_expense':   month_expense,
            'net_position':    net_position,
            'overdue':         overdue,
            'overdue_amt':     overdue_amt,
            'due_this_week':   due_this_week,
            'due_week_amt':    due_week_amt,
            'top_action':      top_action,
            'date_str':        now.strftime('%A, %d %b %Y'),
        }

    except Exception as e:
        print(f"[Briefing] Data error for {customer_id}: {e}")
        return {}


# ── Message builder ────────────────────────────────────────────────────────────

def build_briefing_message(customer: dict, data: dict) -> str:
    """Build the personalised morning briefing message."""
    if not data:
        return ""

    name = customer.get('business_name', 'there').split()[0]

    msg = f"☀️ *Good morning, {name}!*\n"
    msg += f"_{data['date_str']}_\n\n"

    # Business health
    msg += f"*Business Health: {data['health']}*\n\n"

    # Financial snapshot
    msg += f"📈 *This Month So Far*\n"
    msg += f"  Invoiced: RM{data['month_invoiced']:,.2f}\n"
    msg += f"  Expenses: RM{data['month_expense']:,.2f}\n"
    net = data['net_position']
    net_icon = '📈' if net >= 0 else '📉'
    msg += f"  Net: {net_icon} RM{abs(net):,.2f} {'profit' if net >= 0 else 'loss'}\n\n"

    # Overdue invoices
    if data['overdue']:
        msg += f"⚠️ *Overdue ({len(data['overdue'])} invoices — RM{data['overdue_amt']:,.2f})*\n"
        for inv in data['overdue'][:3]:
            due = str(inv.get('due_date', ''))[:10]
            msg += f"  • {inv['vendor_name']} — RM{float(inv['grand_total']):,.2f} (due {due})\n"
        if len(data['overdue']) > 3:
            msg += f"  • ...and {len(data['overdue']) - 3} more\n"
        msg += "\n"

    # Due this week
    if data['due_this_week']:
        msg += f"📅 *Due This Week — RM{data['due_week_amt']:,.2f}*\n"
        for inv in data['due_this_week'][:3]:
            due = str(inv.get('due_date', ''))[:10]
            msg += f"  • {inv['vendor_name']} — RM{float(inv['grand_total']):,.2f} (due {due})\n"
        msg += "\n"

    # Pending quotes
    # Top action
    msg += f"💡 *Today's Priority:*\n"
    msg += f"  {data['top_action']}\n\n"

    msg += "Reply *DASHBOARD* for full details or ask me anything about your finances."

    return msg


# ── Main briefing sender ───────────────────────────────────────────────────────

def send_morning_briefings():
    """Send briefing to all active customers."""
    print(f"[Briefing] Starting morning briefings — {datetime.now(MY_TZ).strftime('%d %b %Y %H:%M')}")

    try:
        customers = db.table('customers') \
            .select('customer_id, business_name, whatsapp_number, subscription_status') \
            .execute()

        if not customers.data:
            print("[Briefing] No customers found")
            return

        sent = 0
        for customer in customers.data:
            phone = customer.get('whatsapp_number', '')
            if not phone:
                continue

            data = get_briefing_data(customer['customer_id'])
            if not data:
                continue

            message = build_briefing_message(customer, data)
            if not message:
                continue

            if send_whatsapp(phone, message):
                sent += 1

            # Small delay between messages
            time.sleep(2)

        print(f"[Briefing] Done — sent to {sent}/{len(customers.data)} customers")

    except Exception as e:
        print(f"[Briefing] Error: {e}")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def run_scheduler():
    """Run forever, sending briefings at 8:00 AM Malaysia time daily."""
    print("[Briefing] Scheduler started — will send at 8:00 AM MYT daily")

    last_sent_date = None

    while True:
        now_myt = datetime.now(MY_TZ)
        today   = now_myt.date()

        # Send at 8:00 AM MYT
        if now_myt.hour == 8 and now_myt.minute == 0 and last_sent_date != today:
            last_sent_date = today
            send_morning_briefings()

        # Sleep 50 seconds then check again
        time.sleep(50)


if __name__ == '__main__':
    run_scheduler()
