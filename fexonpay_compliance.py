"""
Fexonpay - Tax & Compliance Agent
Tracks SST thresholds and payroll deadlines for Malaysian SMEs.
"""

import os
from datetime import datetime, date
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
db = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])

def get_compliance_report(customer_id: str):
    # 1. SST Threshold Check (RM 500,000 for most services)
    # Calculate total revenue for the current calendar year
    year_start = f"{date.today().year}-01-01"
    
    # We sum up all self_invoices that are 'ready' or 'paid'
    rev_result = db.table('self_invoices').select('grand_total').eq('customer_id', customer_id).gte('created_at', year_start).execute()
    total_revenue = sum([float(i['grand_total']) for i in rev_result.data])
    
    sst_threshold = 500000
    sst_status = "✅ Safe (Below Threshold)"
    if total_revenue > (sst_threshold * 0.8):
        sst_status = "⚠️ *Caution: Approaching SST Limit (80%)*"
    if total_revenue >= sst_threshold:
        sst_status = "🚨 *ACTION REQUIRED: Register for SST now (Limit Exceeded)*"
        
    # 2. Statutory Deadlines (EPF/SOCSO/EIS)
    today = date.today()
    deadline = date(today.year, today.month, 15)
    days_to_deadline = (deadline - today).days
    
    deadline_status = f"🗓️ Statutory Payments (EPF/SOCSO) due in *{days_to_deadline} days* (April 15th)."
    if days_to_deadline < 0:
        deadline_status = "🚨 *OVERDUE: Statutory Payments (EPF/SOCSO) were due on April 15th.*"
    elif days_to_deadline <= 5:
        deadline_status = f"🔥 *URGENT: Statutory Payments due in {days_to_deadline} days!*"

    # 3. Format Report
    report = "🇲🇾 *Fexonpay Compliance Agent*\n\n"
    report += f"📊 *SST Monitor (Jan {today.year} - Now)*\n"
    report += f"Total Revenue: RM{total_revenue:,.2f}\n"
    report += f"Status: {sst_status}\n\n"
    report += f"🕒 *Deadlines Check*\n"
    report += f"{deadline_status}\n\n"
    report += "Type *TAX SUMMARY* for detailed breakdown."
    
    return report

if __name__ == "__main__":
    print("Compliance Agent Initialized.")
