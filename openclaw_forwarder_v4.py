#!/usr/bin/env python3
"""
OpenClaw Message Forwarder v4 - Reads from .jsonl session files
"""

import re
import time
import httpx
import json
from pathlib import Path
from datetime import datetime

FASTAPI_URL = "http://127.0.0.1:8000/webhooks/openclaw"
SESSIONS_DIR = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
SESSIONS_FILE = SESSIONS_DIR / "sessions.json"

# Track processed messages
processed_messages = set()

def find_session_file(phone_number):
    """Find the .jsonl file for a phone number"""
    try:
        # Search all .jsonl files for this phone number
        for jsonl_file in SESSIONS_DIR.glob("*.jsonl"):
            with open(jsonl_file, 'r') as f:
                content = f.read()
                if phone_number in content:
                    return jsonl_file
        return None
    except Exception as e:
        print(f"  Error finding session file: {e}")
        return None

def get_latest_user_message(jsonl_file):
    """Extract the latest user message from a .jsonl file"""
    try:
        with open(jsonl_file, 'r') as f:
            lines = f.readlines()
        
        # Read from end to find latest user message
        for line in reversed(lines):
            try:
                data = json.loads(line.strip())
                
                # Check different possible formats
                if data.get('role') == 'user':
                    content = data.get('content', '')
                    if isinstance(content, str) and content:
                        return content
                    elif isinstance(content, list):
                        # Handle array format
                        for item in content:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                return item.get('text', '')
                
                # Check for message field
                if 'message' in data:
                    msg = data['message']
                    if isinstance(msg, dict) and msg.get('role') == 'user':
                        return msg.get('content', '')
                
            except json.JSONDecodeError:
                continue
        
        return None
    except Exception as e:
        print(f"  Error reading message: {e}")
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
                print(f"  ✓ Forwarded to FastAPI")
                result = response.json()
                if 'reply' in result:
                    print(f"  Reply: {result['reply'][:100]}")
            else:
                print(f"  ✗ FastAPI returned {response.status_code}")
    except Exception as e:
        print(f"  ✗ Failed to forward: {e}")

def monitor_sessions():
    """Monitor .jsonl files for new messages"""
    print("OpenClaw Message Forwarder v4 started...")
    print(f"Monitoring: {SESSIONS_DIR}")
    print(f"Forwarding to: {FASTAPI_URL}")
    print()
    
    # Track file modification times
    file_mtimes = {}
    
    print("Monitoring for incoming WhatsApp messages...")
    print()
    
    while True:
        try:
            # Check all .jsonl files
            for jsonl_file in SESSIONS_DIR.glob("*.jsonl"):
                current_mtime = jsonl_file.stat().st_mtime
                
                # Check if file was modified
                if jsonl_file not in file_mtimes or file_mtimes[jsonl_file] != current_mtime:
                    file_mtimes[jsonl_file] = current_mtime
                    
                    # Read the file to check for phone numbers
                    with open(jsonl_file, 'r') as f:
                        content = f.read()
                    
                    # Look for phone numbers in the file
                    phone_matches = re.findall(r'\+60\d{9,10}', content)
                    
                    if phone_matches:
                        # Get the most common phone number (likely the user)
                        phone_number = max(set(phone_matches), key=phone_matches.count)
                        
                        # Skip if it's the bot's number
                        if phone_number == '+60105089527':
                            continue
                        
                        # Get latest message
                        message_text = get_latest_user_message(jsonl_file)
                        
                        if message_text:
                            # Create unique ID
                            msg_id = f"{phone_number}:{message_text[:50]}:{current_mtime}"
                            
                            if msg_id not in processed_messages:
                                processed_messages.add(msg_id)
                                timestamp = datetime.now().strftime("%H:%M:%S")
                                
                                print(f"[{timestamp}] New message from {phone_number}")
                                print(f"  Text: {message_text[:100]}")
                                
                                # Forward to FastAPI
                                import asyncio
                                asyncio.run(forward_to_fastapi(phone_number, message_text))
                                print()
            
            time.sleep(1)
            
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(5)

if __name__ == "__main__":
    try:
        monitor_sessions()
    except KeyboardInterrupt:
        print("\nForwarder stopped.")
