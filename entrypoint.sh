#!/bin/bash
set -e

export BROWSER=false
DATA_DIR="${C3_DATA_DIR:-/data}"

# ── Pre-configure Claude Code ─────────────────────────────────────────────────
python3 -c "
import json, pathlib, os

data_dir = os.environ.get('C3_DATA_DIR', '/data')
p = pathlib.Path('/home/c3/.claude.json')
cfg = json.loads(p.read_text()) if p.exists() else {}

cfg['theme'] = 'dark'
cfg['hasCompletedOnboarding'] = True

proj = cfg.setdefault('projects', {}).setdefault(data_dir, {})
proj['hasTrustDialogAccepted'] = True
proj['hasCompletedProjectOnboarding'] = True
proj['allowAllMcpjsonServers'] = True
proj.setdefault('enabledMcpjsonServers', [])
if 'whatsapp' not in proj['enabledMcpjsonServers']:
    proj['enabledMcpjsonServers'].append('whatsapp')

cfg.setdefault('cachedGrowthBookFeatures', {})
cfg['cachedGrowthBookFeatures']['tengu_harbor'] = True
cfg['bypassPermissionsModeAccepted'] = True
cfg['preferredNotebookModel'] = 'claude-3-5-haiku-latest'
cfg['smallModelEnabled'] = True

p.write_text(json.dumps(cfg, indent=2))
"

# ── Step 1: Claude auth ──────────────────────────────────────────────────────
if claude auth status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('loggedIn') and d.get('authMethod')=='claude.ai' else 1)" 2>/dev/null; then
    echo "[c3] ✅ Claude authenticated"
else
    echo ""
    echo "┌──────────────────────────────────────────────────────┐"
    echo "│  Step 1/2 — Claude login                             │"
    echo "│                                                      │"
    echo "│  1. Type /login when Claude opens                    │"
    echo "│  2. Open the URL in your browser                     │"
    echo "│  3. Paste the code back                              │"
    echo "│  4. Type /exit                                       │"
    echo "└──────────────────────────────────────────────────────┘"
    echo ""
    claude
    echo ""
    if ! claude auth status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('loggedIn') else 1)" 2>/dev/null; then
        echo "[c3] Auth failed. Container will restart — try again."
        exit 1
    fi
    echo "[c3] ✅ Claude authenticated"
fi

# ── Step 2: WhatsApp auth ────────────────────────────────────────────────────
if [ -f "$DATA_DIR/sessions/creds.json" ] && [ "$(wc -c < "$DATA_DIR/sessions/creds.json")" -gt 10 ]; then
    echo "[c3] ✅ WhatsApp authenticated"
else
    rm -f "$DATA_DIR/sessions/creds.json"

    echo ""
    echo "┌──────────────────────────────────────────────────────┐"
    echo "│  Step 2/2 — WhatsApp login                           │"
    echo "│                                                      │"
    echo "│  Scan the QR code with your phone.                   │"
    echo "│  WhatsApp → Linked Devices → Link a Device           │"
    echo "│                                                      │"
    echo "│  QR refreshes every 20s. Wait for ✅                  │"
    echo "└──────────────────────────────────────────────────────┘"
    echo ""
    mkdir -p "$DATA_DIR/sessions"
    SESSIONS_DIR="$DATA_DIR/sessions" node /app/c3/baileys_bridge.js &
    BRIDGE_PID=$!

    echo "[c3] Waiting for QR scan..."
    while true; do
        if [ -f "$DATA_DIR/sessions/creds.json" ] && [ "$(wc -c < "$DATA_DIR/sessions/creds.json")" -gt 10 ]; then
            break
        fi
        sleep 2
    done

    echo "[c3] QR scanned, saving session..."
    sleep 10

    kill $BRIDGE_PID 2>/dev/null || true
    wait $BRIDGE_PID 2>/dev/null || true

    python3 -c "
import json, pathlib, re, os
data_dir = os.environ.get('C3_DATA_DIR', '/data')
creds = pathlib.Path(f'{data_dir}/sessions/creds.json')
cfg_path = pathlib.Path(f'{data_dir}/config.json')
if creds.exists() and creds.stat().st_size > 10 and not cfg_path.exists():
    try:
        data = json.loads(creds.read_text())
        me = data.get('me', {}).get('id', '')
        if me:
            jid = re.sub(r':\d+@', '@', me)
            cfg_path.write_text(json.dumps({
                'hosts': [{'jid': jid, 'name': 'Host'}]
            }, indent=2))
            print(f'[c3] Auto-created config.json with host JID: {jid}')
    except Exception as e:
        print(f'[c3] Could not auto-create config: {e}')
"
    echo ""
    echo "[c3] ✅ WhatsApp authenticated"
fi

# ── Launch ────────────────────────────────────────────────────────────────────
echo ""
echo "┌──────────────────────────────────────────────────────┐"
echo "│  🚀 c3-py starting                                   │"
echo "│                                                      │"
echo "│  Channels: server:whatsapp                           │"
echo "│  Press Enter to accept the dev channels warning.     │"
echo "│  Then Ctrl+P Ctrl+Q to detach.                       │"
echo "└──────────────────────────────────────────────────────┘"
echo ""
exec c3-py "$DATA_DIR" "$@"
