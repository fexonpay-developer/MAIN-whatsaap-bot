#!/bin/bash
# Script to investigate how OpenClaw stores messages

echo "=== Checking OpenClaw message-related commands ==="
openclaw --help | grep -i message

echo -e "\n=== Checking OpenClaw directory structure ==="
ls -la ~/.openclaw/

echo -e "\n=== Looking for message databases or storage ==="
find ~/.openclaw/ -type f -name "*.db" -o -name "*.sqlite" -o -name "*message*" 2>/dev/null

echo -e "\n=== Checking if OpenClaw has a messages API ==="
openclaw message --help 2>/dev/null || echo "No 'message' command found"

echo -e "\n=== Checking OpenClaw session/chat storage ==="
find ~/.openclaw/ -type f -name "*session*" -o -name "*chat*" 2>/dev/null

echo -e "\n=== Checking OpenClaw log files for message patterns ==="
tail -50 ~/.openclaw/logs/gateway.log 2>/dev/null | grep -A 2 "Inbound message"

echo -e "\n=== Checking if OpenClaw stores messages in JSON format ==="
find ~/.openclaw/ -type f -name "*.json" -exec echo "File: {}" \; -exec head -20 {} \; 2>/dev/null
