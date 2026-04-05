#!/usr/bin/env python3
"""
OpenClaw Message Forwarder - Final Version
Only forwards truly NEW messages, no loops
"""

import re
import time
import httpx
import json
from pathlib import Path
from datetime import datetime

FASTAPI_URL = "http://127.0.0.1:8000/webhooks/openclaw"
SESSIONS_DIR = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
PROCESSED_FILE = Path("/home/fexonpay/processed_messages.txt")

def load_processed_messages():
    """Load list of already processed message IDs"""
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, 'r') as f:
            return set(line.strip() for line in f)
    return set()

def save_processed_message(msg_id):
    """Save a processed message ID"""
    with open(PROCESSED_FILE, 'a') as f:
        f.write(f"{msg_id}\n")

def extract_actual_message(content):
    """Extract the actual user message from OpenClaw's content format"""
    try:
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    text = item.get('text', '')
                    lines = text.split('\n')
                    last_code_block = -1
                    for i, line in enumerate(lines):
                        if '```' in line:
                            last_code_block = i
                    if last_code_block >= 0 and last_code_block < len(lines) - 1:
                        actual_message = '\n'.join(lines[last_code_block + 1:]).strip()
                        if actual_message:
                            return actual_message
                    return text
        return str(content)
    except:
        return str(content)

def get_latest_user_message_with_id(jsonl_file):
    """Extract the latest user message with its unique ID"""
    try:
        with open(jsonl_file, 'r') as f:
            lines = f.readlines()
        
        for line in reversed(lines):
            try:
                data = json.loads(line.strip())
                if data.get('type') == 'message':
                    message = data.get('message', {})
                    if message.get('role') == 'user':
                        msg_id = data.get('id', '')
                        timestamp = data.get('timestamp', '')
                        unique_id = f"{msg_id}:{timestamp}"
                        content = message.get('content', [])
                        text = extract_actual_message(content)
                        return unique_id, text
            except json.JSONDecodeError:
                continue
        return None, None
    except Exception as e:
        return None, None

async def forward_to_fastapi(phone_number, message_text):
    """Forward message to FastAPI webhook"""
    payload = {"text": message_text, "from": phone_number}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(FASTAPI_URL, json=payload, timeout=10)
            if response.status_code == 200:
                print(f"  ✓ Forwarded successfully")
                result = response.json()
                if 'reply' in result:
                    print(f"  Reply: {result['reply'][:80]}")
            else:
                print(f"  ✗ FastAPI returned {response.status_code}")
    except Exception as e:
        print(f"  ✗ Failed to forward: {e}")

def monitor_sessions():
    """Monitor .jsonl files for new messages"""
    print("OpenClaw Message Forwarder (Final) started...")
    print(f"Monitoring: {SESSIONS_DIR}")
    print(f"Forwarding to: {FASTAPI_URL}")
    print()
    
    processed_messages = load_processed_messages()
    print(f"Loaded {len(processed_messages)} previously processed messages")
    print()
    
    file_mtimes = {}
    print("Monitoring for NEW incoming WhatsApp messages...")
    print()
    
    while True:
        try:
            for jsonl_file in SESSIONS_DIR.glob("*.jsonl"):
                current_mtime = jsonl_file.stat().st_mtime
                
                if jsonl_file not in file_mtimes or file_mtimes[jsonl_file] != current_mtime:
                    file_mtimes[jsonl_file] = current_mtime
                    
                    with open(jsonl_file, 'r') as f:
                        content = f.read()
                    
                    phone_matches = re.findall(r'\+60\d{9,10}', content)
                    if phone_matches:
                        phone_number = max(set(phone_matches), key=phone_matches.count)
                        if phone_number == '+60105089527':
                            continue
                        
                        msg_id, message_text = get_latest_user_message_with_id(jsonl_file)
                        
                        if msg_id and message_text and msg_id not in processed_messages:
                            processed_messages.add(msg_id)
                            save_processed_message(msg_id)
                            
                            timestamp = datetime.now().strftime("%H:%M:%S")
                            print(f"[{timestamp}] New message from {phone_number}")
                            print(f"  Text: {message_text[:100]}")
                            
                            import asyncio
                            asyncio.run(forward_to_fastapi(phone_number, message_text))
                            print()
            
            time.sleep(1)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    try:
        monitor_sessions()
    except KeyboardInterrupt:
        print("\nForwarder stopped.")
