#!/usr/bin/env python3
"""
OpenClaw Message Forwarder v3 - Reads from session storage
"""

import re
import time
import httpx
import json
from pathlib import Path
from datetime import datetime

FASTAPI_URL = "http://127.0.0.1:8000/webhooks/openclaw"
LOG_FILE = Path.home() / ".openclaw" / "logs" / "gateway.log"
SESSIONS_FILE = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"

def get_latest_message(phone_number):
    """Extract latest message from sessions.json"""
    try:
        if not SESSIONS_FILE.exists():
            return None
        
        with open(SESSIONS_FILE, 'r') as f:
            sessions = json.load(f)
        
        # Look for session with this phone number
        for session_key, session_data in sessions.items():
            if phone_number in session_key:
                # Check if there's message history
                if 'messages' in session_data:
                    messages = session_data['messages']
                    if messages:
                        # Get the last user message
                        for msg in reversed(messages):
                            if msg.get('role') == 'user':
                                return msg.get('content')
                
                # Check other possible fields
                if 'lastMessage' in session_data:
                    return session_data['lastMessage']
                if 'text' in session_data:
                    return session_data['text']
        
        return None
    except Exception as e:
        print(f"  Error reading sessions: {e}")
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
    """Monitor sessions file for changes"""
    print("OpenClaw Message Forwarder v3 started...")
    print(f"Monitoring: {SESSIONS_FILE}")
    print(f"Forwarding to: {FASTAPI_URL}")
    print()
    
    if not SESSIONS_FILE.exists():
        print(f"Error: Sessions file not found at {SESSIONS_FILE}")
        return
    
    last_modified = SESSIONS_FILE.stat().st_mtime
    processed_messages = set()
    
    print("Monitoring for incoming WhatsApp messages...")
    print()
    
    while True:
        try:
            current_modified = SESSIONS_FILE.stat().st_mtime
            
            if current_modified != last_modified:
                last_modified = current_modified
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                # Read sessions file
                with open(SESSIONS_FILE, 'r') as f:
                    sessions = json.load(f)
                
                # Check all sessions for new messages
                for session_key, session_data in sessions.items():
                    if 'whatsapp' in session_key and 'direct' in session_key:
                        # Extract phone number from session key
                        match = re.search(r'\+\d+', session_key)
                        if match:
                            phone_number = match.group(0)
                            
                            # Get latest message
                            message_text = get_latest_message(phone_number)
                            
                            if message_text:
                                # Create unique ID for this message
                                msg_id = f"{phone_number}:{message_text[:50]}"
                                
                                if msg_id not in processed_messages:
                                    processed_messages.add(msg_id)
                                    print(f"[{timestamp}] New message from {phone_number}")
                                    print(f"  Text: {message_text[:100]}")
                                    
                                    # Forward to FastAPI
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
