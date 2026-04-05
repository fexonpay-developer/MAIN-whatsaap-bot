#!/bin/bash
# Complete End-to-End Payment Flow Test

echo "=========================================="
echo "Fexonpay End-to-End Payment Flow Test"
echo "=========================================="
echo ""

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PHONE="+60122591813"
API_URL="http://127.0.0.1:8000"

echo "Testing phone: $PHONE"
echo "API endpoint: $API_URL"
echo ""

# Test 1: Invoice Submission
echo "=========================================="
echo "TEST 1: Invoice Submission"
echo "=========================================="
echo "Sending: 'TNB bill RM200 due tomorrow'"
echo ""

RESPONSE=$(curl -s -X POST $API_URL/webhooks/openclaw \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"TNB bill RM200 due tomorrow\", \"from\": \"$PHONE\"}")

echo "Response: $RESPONSE"
echo ""

if echo "$RESPONSE" | grep -q "TNB"; then
    echo -e "${GREEN}✓ Invoice extraction successful${NC}"
else
    echo -e "${RED}✗ Invoice extraction failed${NC}"
fi
echo ""
sleep 2

# Test 2: Add Another Invoice
echo "=========================================="
echo "TEST 2: Add Another Invoice"
echo "=========================================="
echo "Sending: 'Astro bill RM150 due next week'"
echo ""

RESPONSE=$(curl -s -X POST $API_URL/webhooks/openclaw \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"Astro bill RM150 due next week\", \"from\": \"$PHONE\"}")

echo "Response: $RESPONSE"
echo ""

if echo "$RESPONSE" | grep -q "Astro"; then
    echo -e "${GREEN}✓ Second invoice added${NC}"
else
    echo -e "${RED}✗ Second invoice failed${NC}"
fi
echo ""
sleep 2

# Test 3: Status Check
echo "=========================================="
echo "TEST 3: Status Check"
echo "=========================================="
echo "Sending: 'status'"
echo ""

RESPONSE=$(curl -s -X POST $API_URL/webhooks/openclaw \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"status\", \"from\": \"$PHONE\"}")

echo "Response: $RESPONSE"
echo ""

if echo "$RESPONSE" | grep -q "pending"; then
    echo -e "${GREEN}✓ Status check successful${NC}"
    PENDING=$(echo "$RESPONSE" | grep -oP '\d+(?= pending)')
    if [ ! -z "$PENDING" ]; then
        echo "  Found $PENDING pending payment(s)"
    fi
else
    echo -e "${RED}✗ Status check failed${NC}"
fi
echo ""
sleep 2

# Test 4: Payment Generation
echo "=========================================="
echo "TEST 4: Payment Link Generation"
echo "=========================================="
echo "Sending: 'PAY ALL'"
echo ""

RESPONSE=$(curl -s -X POST $API_URL/webhooks/openclaw \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"PAY ALL\", \"from\": \"$PHONE\"}")

echo "Response: $RESPONSE"
echo ""

if echo "$RESPONSE" | grep -q "billplz"; then
    echo -e "${GREEN}✓ Payment links generated${NC}"
    LINKS=$(echo "$RESPONSE" | grep -oP 'https://[^\s"]+billplz[^\s"]+')
    if [ ! -z "$LINKS" ]; then
        echo ""
        echo "Payment Links:"
        echo "$LINKS" | while read -r link; do
            echo "  → $link"
        done
    fi
else
    echo -e "${RED}✗ Payment generation failed${NC}"
fi
echo ""

echo "=========================================="
echo "Test Complete!"
echo "=========================================="
