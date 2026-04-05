"""
Fexonpay - Collections Agent
Automatically checks for overdue invoices and notifies the owner for approval.
"""

import os
import json
from datetime import datetime, date
from supabase import create_client
from dotenv import load_dotenv
import subprocess

load_dotenv()

db = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])

def send_whatsapp(phone: str, message: str):
    try:
        subprocess.run(['openclaw', 'message', 'send', '--target', phone, '--message', message], check=True)
        return True
    except Exception as e:
        print(f"Error sending WhatsApp: {e}")
        return False

def check_collections():
    print(f"[{datetime.now()}] Running collections check...")
    
    # 1. Fetch all unpaid client invoices
    # We use neq('status', 'paid') as per existing logic
    result = db.table('self_invoices').select('*').neq('status', 'paid').execute()
    
    if not result.data:
        print("No unpaid invoices found.")
        return

    today = date.today()
    
    for inv in result.data:
        customer_id = inv.get('customer_id')
        due_date_str = inv.get('due_date')
        if not due_date_str or due_date_str == 'Not specified':
            continue
            
        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        except ValueError:
            # Try DD/MM/YYYY if extraction used that format
            try:
                due_date = datetime.strptime(due_date_str, '%d/%m/%Y').date()
            except:
                continue
                
        days_overdue = (today - due_date).days
        
        if days_overdue in [7, 14, 30]:
            # Fetch owner's phone number
            cust_result = db.table('customers').select('whatsapp_number').eq('customer_id', customer_id).execute()
            if not cust_result.data:
                continue
                
            owner_phone = cust_result.data[0]['whatsapp_number']
            client_name = inv.get('vendor_name', 'Unknown Client')
            amount = inv.get('grand_total', 0)
            inv_no = inv.get('invoice_number', 'N/A')
            
            tone = "Friendly"
            if days_overdue == 14: tone = "Firm"
            if days_overdue == 30: tone = "Urgent/Legal"
            
            msg = f"🔔 *Payment Reminder Alert ({tone} Tone)*\n\n"
            msg += f"Invoice {inv_no} for *{client_name}* is {days_overdue} days overdue.\n"
            msg += f"Amount: RM{float(amount):.2f}\n\n"
            msg += f"Shall I send a reminder to the client? (Reply *REMIND {inv_no}*)"
            
            send_whatsapp(owner_phone, msg)
            print(f"Sent reminder alert to {owner_phone} for {inv_no}")

if __name__ == "__main__":
    check_collections()
