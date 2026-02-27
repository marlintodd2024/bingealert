#!/bin/bash
# Rebuild portal data from Seerr/Sonarr sources

echo "🔄 Rebuilding portal data from sources..."
echo ""

API_URL="http://localhost:8000"

echo "⏳ Checking portal is running..."
if ! curl -s "$API_URL/health" > /dev/null 2>&1; then
    echo "❌ Portal is not responding at $API_URL"
    exit 1
fi

echo "👥 Syncing users from Seerr..."
curl -s -X POST "$API_URL/admin/sync/users" | python3 -m json.tool 2>/dev/null
sleep 2

echo ""
echo "📋 Syncing requests from Seerr..."
curl -s -X POST "$API_URL/admin/sync/requests" | python3 -m json.tool 2>/dev/null
sleep 2

echo ""
echo "📥 Importing episodes from Sonarr..."
curl -s -X POST "$API_URL/admin/import-all-existing-episodes" | python3 -m json.tool 2>/dev/null
sleep 5

echo ""
echo "🔍 Running reconciliation..."
curl -s -X POST "$API_URL/admin/reconcile" | python3 -m json.tool 2>/dev/null
sleep 2

echo ""
echo "✅ Data rebuild complete!"
echo "📊 Check the admin dashboard to verify"
