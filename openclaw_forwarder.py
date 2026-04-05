#!/usr/bin/env python3
import subprocess
import requests
import re
import time
import sys

def monitor_openclaw_logs():
    """Monitor OpenClaw logs and forward messages to FastAPI"""
    print("OpenClaw Message Forwarder started...")
    print("Monitoring for incoming WhatsApp messages...")
    
    # Start tailing OpenClaw logs
    process = subprocess.Popen(
        ['pm2', 'logs', 'openclaw-gateway', '--lines', '0', '--raw'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    
    # Pattern to detect incoming messages
    # Example: "Inbound message +60122591813 -> +60105089527 (direct, 80 chars)"
    pattern = r'Inbound message (\+\d+) -> \+60105089527.*?\(.*?(\d+) chars\)'
    
    message_buffer = {}
    
    try:
        for line in process.stdout:
            # Detect inbound message
            match = re.search(pattern, line)
            if match:
                from_number = match.group(1)
                char_count = match.group(2)
                
                print(f"\n[{time.strftime('%H:%M:%S')}] Detected message from {from_number} ({char_count} chars)")
                
                # Store in buffer (we'll need to extract actual message text)
                message_buffer[from_number] = {
                    'timestamp': time.time(),
                    'char_count': char_count
                }
                
                # Wait a moment for message to be fully processed
                time.sleep(1)
                
                # For now, forward a notification to FastAPI
                # In production, you'd extract the actual message text
                try:
                    response = requests.post(
                        'http://127.0.0.1:8000/webhooks/openclaw',
                        json={
                            'from': from_number,
                            'text': f'Message detected ({char_count} chars)',
                            'forwarded_by': 'openclaw_forwarder'
                        },
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        print(f"✓ Forwarded to FastAPI successfully")
                    else:
                        print(f"✗ FastAPI returned status {response.status_code}")
                        
                except requests.exceptions.RequestException as e:
                    print(f"✗ Error forwarding to FastAPI: {e}")
                    
    except KeyboardInterrupt:
        print("\n\nForwarder stopped by user")
        process.terminate()
        sys.exit(0)
    except Exception as e:
        print(f"\n\nError: {e}")
        process.terminate()
        sys.exit(1)

if __name__ == '__main__':
    monitor_openclaw_logs()
