"""
Fexonpay - Cash Flow Prediction Agent
Uses Gemma 4 to forecast financial health and warn about shortfalls.
"""

import os
from datetime import datetime, timedelta
from supabase import create_client
from dotenv import load_dotenv
from together import Together

load_dotenv()
db = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
together_client = Together(api_key=os.environ['TOGETHER_API_KEY'])

def get_cash_forecast(customer_id: str):
    # 1. Get current cash balance (from latest cash_receipts or summary)
    # 2. Get upcoming income (Accounts Receivable)
    ar_result = db.table('self_invoices').select('grand_total, due_date').eq('customer_id', customer_id).neq('status', 'paid').execute()
    total_ar = sum([float(i['grand_total']) for i in ar_result.data])
    
    # 3. Get upcoming bills (Accounts Payable/Transactions)
    ap_result = db.table('transactions').select('amount, due_date').eq('customer_id', customer_id).eq('payment_status', 'pending').execute()
    total_ap = sum([float(i['amount']) for i in ap_result.data])
    
    # 4. Use Gemma 4 to analyze the risk
    prompt = f"""You are a senior financial forecaster for a Malaysian SME.
Analyze this data:
- Accounts Receivable (Money coming in): RM{total_ar}
- Accounts Payable (Bills to pay): RM{total_ap}
- Current Date: {datetime.now().strftime('%Y-%m-%d')}

Identify:
1. Is there a shortfall risk in the next 7 days?
2. What specific action should the owner take? (e.g. follow up on a specific invoice)
3. Give a 1-sentence "Cash Flow Health" score.

Format the output for WhatsApp (use bold, emojis). Keep it concise.
"""

    response = together_client.chat.completions.create(
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    
    return response.choices[0].message.content

if __name__ == "__main__":
    # Test for Sharvin (replace with actual ID if testing live)
    # print(get_cash_forecast('CUST-ID-HERE'))
    print("Prediction Agent Initialized.")
