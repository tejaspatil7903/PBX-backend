#!/usr/bin/env bash
# ==============================================================================
# sync-ip.sh
# Automatically detects current active macOS Wi-Fi / Ethernet IP address,
# updates FreeSWITCH internal profile, PostgreSQL tenant domains, /etc/hosts,
# and reloads FreeSWITCH without requiring manual config edits.
# ==============================================================================

set -e

# Detect active local IP on macOS
ACTIVE_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -n 1)

if [ -z "$ACTIVE_IP" ]; then
    echo "❌ Error: Could not determine active local IP address."
    exit 1
fi

echo "================================================="
echo "  PBX IP Sync Tool"
echo "  Active Local IP detected: $ACTIVE_IP"
echo "================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PBX_BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INTERNAL_XML="$PBX_BACKEND_DIR/config/freeswitch/sip_profiles/internal.xml"

# 1. Update internal.xml
if [ -f "$INTERNAL_XML" ]; then
    echo "--> Updating FreeSWITCH config: $INTERNAL_XML"
    # Update ext-rtp-ip and ext-sip-ip
    sed -i '' -E "s/<param name=\"ext-rtp-ip\" value=\"[^\"]*\"\/>/<param name=\"ext-rtp-ip\" value=\"$ACTIVE_IP\"\/>/" "$INTERNAL_XML"
    sed -i '' -E "s/<param name=\"ext-sip-ip\" value=\"[^\"]*\"\/>/<param name=\"ext-sip-ip\" value=\"$ACTIVE_IP\"\/>/" "$INTERNAL_XML"
    
    # Add alias if not already present
    if ! grep -q "<alias name=\"$ACTIVE_IP\"/>" "$INTERNAL_XML"; then
        sed -i '' -E "/<aliases>/a\\
    <alias name=\"$ACTIVE_IP\"/>
" "$INTERNAL_XML"
    fi
    echo "    ✓ Updated ext-rtp-ip and ext-sip-ip to $ACTIVE_IP"
else
    echo "⚠️ Warning: internal.xml not found at $INTERNAL_XML"
fi

VARS_XML="$PBX_BACKEND_DIR/config/freeswitch/vars.xml"
if [ -f "$VARS_XML" ]; then
    sed -i '' -E "s/<X-PRE-PROCESS cmd=\"set\" data=\"domain=[^\"]*\"\/>/<X-PRE-PROCESS cmd=\"set\" data=\"domain=$ACTIVE_IP\"\/>/" "$VARS_XML"
    sed -i '' -E "s/<X-PRE-PROCESS cmd=\"set\" data=\"domain_name=[^\"]*\"\/>/<X-PRE-PROCESS cmd=\"set\" data=\"domain_name=$ACTIVE_IP\"\/>/" "$VARS_XML"
    sed -i '' -E "s/<X-PRE-PROCESS cmd=\"set\" data=\"external_sip_ip=[^\"]*\"\/>/<X-PRE-PROCESS cmd=\"set\" data=\"external_sip_ip=$ACTIVE_IP\"\/>/" "$VARS_XML"
    sed -i '' -E "s/<X-PRE-PROCESS cmd=\"set\" data=\"external_rtp_ip=[^\"]*\"\/>/<X-PRE-PROCESS cmd=\"set\" data=\"external_rtp_ip=$ACTIVE_IP\"\/>/" "$VARS_XML"
    echo "    ✓ Updated vars.xml to $ACTIVE_IP"
fi

# 2. Update PostgreSQL tenant sip_domain
echo "--> Updating PostgreSQL tenant domain..."
docker exec pbx_postgres psql -U pbx_user -d pbx_db -c \
  "UPDATE tenants SET sip_domain = '$ACTIVE_IP' WHERE domain = 'pbx.terrixai.com' OR sip_domain LIKE '%';" >/dev/null 2>&1 || true
echo "    ✓ Updated tenant domain to match $ACTIVE_IP"

# 3. Reload FreeSWITCH
echo "--> Reloading FreeSWITCH Sofia profile..."
docker exec pbx_freeswitch fs_cli -x "reloadxml" >/dev/null 2>&1 || true
docker exec pbx_freeswitch fs_cli -x "sofia profile internal restart reloadxml" >/dev/null 2>&1 || true
echo "    ✓ FreeSWITCH internal profile reloaded"

# 4. Update /etc/hosts (needs sudo if not root)
echo "--> Checking /etc/hosts..."
CURRENT_HOSTS_IP=$(grep "pbx.terrixai.com" /etc/hosts | awk '{print $1}' | head -n 1 || true)

if [ "$CURRENT_HOSTS_IP" != "$ACTIVE_IP" ]; then
    echo "    Updating /etc/hosts (requires sudo):"
    sudo sed -i '' '/pbx.terrixai.com/d' /etc/hosts
    echo "$ACTIVE_IP pbx.terrixai.com" | sudo tee -a /etc/hosts >/dev/null
    echo "    ✓ /etc/hosts updated to $ACTIVE_IP pbx.terrixai.com"
else
    echo "    ✓ /etc/hosts is already up-to-date ($ACTIVE_IP)"
fi

echo "================================================="
echo "  ✅ All systems synced to $ACTIVE_IP"
echo "  SIP Domain / Outbound Proxy: $ACTIVE_IP"
echo "  Domain: pbx.terrixai.com (resolving to $ACTIVE_IP)"
echo "================================================="
