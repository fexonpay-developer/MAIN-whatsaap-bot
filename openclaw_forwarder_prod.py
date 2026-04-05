#!/usr/bin/env python3
"""
Fexonpay Production Forwarder
Monitors ~/.openclaw/agents/main/sessions/ and filters for accountId=prod only
Dev messages (accountId=default) are ignored completely
"""
import re, time, httpx, json, asyncio
from pathlib import Path
from datetime import datetime

FASTAPI_URL    = "http://127.0.0.1:8001/webhooks/openclaw"
SESSIONS_DIR   = Path("/root/.openclaw/agents/main/sessions")
SESSIONS_JSON  = SESSIONS_DIR / "sessions.json"
PROCESSED_FILE = Path("/home/fexonpay-prod/processed_messages_prod.txt")

def load_processed():
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE) as f:
            return set(l.strip() for l in f)
    return set()

def save_processed(mid):
    with open(PROCESSED_FILE, 'a') as f:
        f.write(f"{mid}\n")

def get_prod_session_ids():
    """Return set of sessionIds that belong to accountId=prod."""
    try:
        with open(SESSIONS_JSON) as f:
            sessions = json.load(f)
        return {
            v["sessionId"]
            for v in sessions.values()
            if v.get("lastAccountId") == "prod" and v.get("sessionId")
        }
    except:
        return set()

def extract_message(content):
    try:
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    text  = item.get('text', '')
                    lines = text.split('\n')
                    last  = max((i for i,l in enumerate(lines) if '```' in l), default=-1)
                    if last >= 0 and last < len(lines)-1:
                        actual = '\n'.join(lines[last+1:]).strip()
                        if actual: return actual
                    return text
        return str(content)
    except:
        return str(content)

def get_latest(jsonl_file):
    try:
        lines = open(jsonl_file).readlines()
        for line in reversed(lines):
            try:
                d = json.loads(line)
                if d.get('type') == 'message' and d.get('message',{}).get('role') == 'user':
                    uid = f"{d.get('id','')}:{d.get('timestamp','')}"
                    txt = extract_message(d['message'].get('content',[]))
                    return uid, txt
            except: continue
    except: pass
    return None, None

def get_phone(jsonl_file):
    try:
        content = open(jsonl_file).read()
        phones  = re.findall(r'\+\d{10,13}', content)
        if phones:
            return max(set(phones), key=phones.count)
    except: pass
    return None

async def forward(phone, text):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(FASTAPI_URL, json={"text": text, "from": phone}, timeout=60)
            print(f"  {'OK' if r.status_code==200 else 'FAIL'} {r.status_code}")
    except Exception as e:
        print(f"  ERR {e}")

def monitor():
    print(f"Fexonpay PROD Forwarder")
    print(f"Sessions: {SESSIONS_DIR}")
    print(f"API: {FASTAPI_URL}")
    print(f"Filter: accountId=prod only")
    processed   = load_processed()
    file_mtimes = {}
    print(f"Ready. {len(processed)} previously processed.")
    while True:
        try:
            prod_ids = get_prod_session_ids()
            for f in SESSIONS_DIR.glob("*.jsonl"):
                # Skip if not a prod session
                if f.stem not in prod_ids:
                    continue
                mtime = f.stat().st_mtime
                if f not in file_mtimes or file_mtimes[f] != mtime:
                    file_mtimes[f] = mtime
                    phone = get_phone(f)
                    if not phone:
                        continue
                    mid, text = get_latest(f)
                    if mid and text and mid not in processed:
                        processed.add(mid)
                        save_processed(mid)
                        ts = datetime.now().strftime('%H:%M:%S')
                        print(f"[{ts}] {phone}: {text[:80]}")
                        asyncio.run(forward(phone, text))
            time.sleep(1)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    try: monitor()
    except KeyboardInterrupt: print("Stopped.")
