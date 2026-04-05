#!/usr/bin/env python3
"""
OpenClaw Message Forwarder v2 - Enhanced message extraction
Monitors OpenClaw logs and forwards actual message content to FastAPI
"""

import re
import time
import httpx
import json
import os
from pathlib import Path
from datetime import datetime

FASTAPI_URL = "http://127.0.0.1:8000/webhooks/openclaw"
LOG_FILE = Path.home() / ".openclaw" / "logs" / "gateway.log"
OPENCLAW_DIR = Path.home() / ".openclaw"

# Try to find message storage locations
MESSAGE_DB = OPENCLAW_DIR / "messages.db"
SESSION_DIR = OPENCLAW_DIR / "sessions"
CHATS_DIR = OPENCLAW_DIR / "chats"

def extract_message_from_db(phone_number, timestamp):
    """Try to extract message from SQLite database if it exists"""
    if not MESSAGE_DB.exists():
        return None
    
    try:
        import sqlite3
        conn = sqlite3.connect(str(MESSAGE_DB))
        cursor = conn.cursor()
        
        # Try common table structures
        queries = [
            f"SELECT body FROM messages WHERE from_number LIKE '%{phone_number[-10:]}%' ORDER BY timestamp DESC LIMIT 1",
            f"SELECT text FROM messages WHERE sender LIKE '%{phone_number[-10:]}%' ORDER BY created_at DESC LIMIT 1",
            f"SELECT content FROM chats WHERE phone LIKE '%{phone_number[-10:]}%' ORDER BY time DESC LIMIT 1",
        ]
        
        for query in queries:
            try:
                cursor.execute(query)
                result = cursor.fetchone()
                if result:
                    conn.close()
                    return result[0]
            except:
                continue
        
        conn.close()
    except Exception as e:
        print(f"  DB extraction failed: {e}")
    
    return None

def extract_message_from_json(phone_number):
    """Try to extract message from JSON session files"""
    # Check sessions directory
    if SESSION_DIR.exists():
        for json_file in SESSION_DIR.glob("*.json"):
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    # Look for recent messages from this number
                    if isinstance(data, dict) and 'messages' in data:
                        for msg in reversed(data['messages']):
                            if phone_number in str(msg.get('from', '')):
                                return msg.get('body') or msg.get('text') or msg.get('content')
            except:
                continue
    
    # Check chats directory
    if CHATS_DIR.exists():
        for json_file in CHATS_DIR.glob("*.json"):
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    if phone_number in str(json_file) or phone_number in str(data):
                        if isinstance(data, list):
                            for msg in reversed(data):
                                if 'body' in msg or 'text' in msg:
                                    return msg.get('body') or msg.get('text')
            except:
                continue
    
    return None

def extract_message_from_log_context(phone_number, log_lines):
    """Try to extract message from surrounding log lines"""
    # Look for message content in lines near the inbound message notification
    for i, line in enumerate(log_lines):
        if f"Inbound message {phone_number}" in line:
            # Check next 5 lines for message content
            for j in range(i+1, min(i+6, len(log_lines))):
                next_line = log_lines[j]
                # Look for patterns like: "Message: ...", "Body: ...", "Text: ..."
                content_match = re.search(r'(?:Message|Body|Text|Content):\s*(.+)', next_line)
                if content_match:
                    return content_match.group(1).strip()
                
                # Look for quoted text
                quote_match = re.search(r'"([^"]{10,})"', next_line)
                if quote_match:
                    return quote_match.group(1)
    
    return None

async def forward_to_fastapi(phone_number, message_text):
    """Forward message to FastAPI webhook"""
    payload = {
        "text": message_text,
        "from": phone_number
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(FASTAPI_URL, json=payload, timeout=10)
            if response.status_code == 200:
                print(f"  ✓ Forwarded to FastAPI successfully")
                result = response.json()
                if 'reply' in result:
                    print(f"  Reply: {result['reply'][:100]}")
            else:
                print(f"  ✗ FastAPI returned {response.status_code}")
    except Exception as e:
        print(f"  ✗ Failed to forward: {e}")

def monitor_logs():
    """Monitor OpenClaw logs for incoming messages"""
    print("OpenClaw Message Forwarder v2 started...")
    print(f"Monitoring: {LOG_FILE}")
    print(f"Forwarding to: {FASTAPI_URL}")
    print()
    
    # Check for message storage locations
    if MESSAGE_DB.exists():
        print(f"✓ Found message database: {MESSAGE_DB}")
    if SESSION_DIR.exists():
        print(f"✓ Found sessions directory: {SESSION_DIR}")
    if CHATS_DIR.exists():
        print(f"✓ Found chats directory: {CHATS_DIR}")
    print()
    
    if not LOG_FILE.exists():
        print(f"Error: Log file not found at {LOG_FILE}")
        return
    
    # Pattern to match inbound messages
    pattern = re.compile(r'Inbound message (\+\d+) -> \+\d+ \(direct, (\d+) chars\)')
    
    # Keep track of last position in log file
    with open(LOG_FILE, 'r') as f:
        f.seek(0, 2)  # Go to end of file
        
        print("Monitoring for incoming WhatsApp messages...")
        print()
        
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            
            match = pattern.search(line)
            if match:
                phone_number = match.group(1)
                char_count = match.group(2)
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                print(f"[{timestamp}] Detected message from {phone_number} ({char_count} chars)")
                
                # Try multiple extraction methods
                message_text = None
                
                # Method 1: Check database
                message_text = extract_message_from_db(phone_number, timestamp)
                if message_text:
                    print(f"  ✓ Extracted from database")
                
                # Method 2: Check JSON files
                if not message_text:
                    message_text = extract_message_from_json(phone_number)
                    if message_text:
                        print(f"  ✓ Extracted from JSON files")
                
                # Method 3: Check log context
                if not message_text:
                    # Read last 20 lines for context
                    current_pos = f.tell()
                    f.seek(max(0, current_pos - 5000))
                    recent_lines = f.readlines()
                    f.seek(current_pos)
                    
                    message_text = extract_message_from_log_context(phone_number, recent_lines)
                    if message_text:
                        print(f"  ✓ Extracted from log context")
                
                # Fallback: Use placeholder
                if not message_text:
                    message_text = f"Message detected ({char_count} chars)"
                    print(f"  ⚠ Using placeholder (extraction failed)")
                
                # Forward to FastAPI
                import asyncio
                asyncio.run(forward_to_fastapi(phone_number, message_text))
                print()

if __name__ == "__main__":
    try:
        monitor_logs()
    except KeyboardInterrupt:
        print("\nForwarder stopped.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
