import os
import sys

path = '/home/fexonpay-prod/main.py'
if not os.path.exists(path):
    print(f"Error: {path} not found.")
    sys.exit(1)

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 1. Remove any previously added priority blocks to avoid duplicates
new_lines = []
skip = False
for line in lines:
    if '# --- PRIORITY COMMANDS ---' in line or '# --- ULTIMATE PRIORITY COMMANDS ---' in line:
        skip = True
    if not skip:
        new_lines.append(line)
    if 'return {\'status\': \'ok\'}' in line and skip:
        skip = False
        continue # skip the separator line if it exists

# 2. Re-insert at the absolute top of the function
for i, line in enumerate(new_lines):
    if 'async def openclaw_webhook(request: Request):' in line:
        # Priority code block
        priority_code = [
            "    # --- ULTIMATE PRIORITY COMMANDS ---\n",
            "    try:\n",
            "        data_raw = await request.json()\n",
            "        text_msg = data_raw.get('text', '').lower().strip()\n",
            "        phone_num = data_raw.get('from', '')\n",
            "        if text_msg in ['owe', 'hutang', 'debt', 'siapa hutang', 'we']:\n",
            "            cid = get_or_create_customer(phone_num)\n",
            "            eng = text_msg in ['owe', 'debt', 'we']\n",
            "            ar_res = db.table('self_invoices').select('*').eq('customer_id', cid).neq('status', 'paid').execute()\n",
            "            ap_res = db.table('transactions').select('*').eq('customer_id', cid).eq('payment_status', 'pending').execute()\n",
            "            report = '[REPORT] Debt Report\\n\\n[AR] Clients Owe You:\\n' if eng else '[LAPORAN] Laporan Hutang Sharvin\\n\\n[AR] Pelanggan Berhutang\\n'\n",
            "            if not ar_res.data: report += '- None\\n' if eng else '- Tiada\\n'\n",
            "            else:\n",
            "                t = 0\n",
            "                for v in ar_res.data:\n",
            "                    a = float(v.get('grand_total', 0))\n",
            "                    t += a\n",
            "                    report += f'- {v.get(\"vendor_name\")}: RM{a:,.2f} ({v.get(\"invoice_number\")})\\n'\n",
            "                report += f'   Total: RM{t:,.2f}\\n'\n",
            "            report += \"\\n[AP] Vendor Obligations:\\n\" if eng else \"\\n[AP] Kewajiban Vendor:\\n\"\n",
            "            if not ap_res.data: report += '- None\\n' if eng else '- Tiada\\n'\n",
            "            else:\n",
            "                t = 0\n",
            "                for v in ap_res.data:\n",
            "                    a = float(v.get(\"amount\", 0))\n",
            "                    t += a\n",
            "                    report += f'- {v.get(\"vendor_name\", \"Vendor\")}: RM{a:,.2f}\\n'\n",
            "                report += f'   Total: RM{t:,.2f}\\n'\n",
            "            subprocess.run(['openclaw', 'message', 'send', '--target', phone_num, '--message', report])\n",
            "            return {'status': 'ok'}\n",
            "    except Exception as e:\n",
            "        print(f'Priority Logic Error: {e}')\n",
            "    # --- END PRIORITY COMMANDS ---\n"
        ]
        # Insert after the function definition line (i+1)
        for j, code_line in enumerate(priority_code):
            new_lines.insert(i + 1 + j, code_line)
        break

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("SUCCESS: Priority commands moved to the absolute top of the function.")
