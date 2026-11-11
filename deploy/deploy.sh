#!/usr/bin/env bash
# Deploy the Coeos-RAG control plane to the host that runs the LightRAG instances.
#
#   ODYRAG_PORT=<port> ODYRAG_ENGINE_URL=<engine URL as seen from the host> deploy/deploy.sh <user@host>
#
# ODYRAG_BIND (optional, default 0.0.0.0) is the interface the dashboard listens on.
#
# Idempotent. It never overwrites an existing registry or admin token, refuses to run
# while a change is in flight, and fails loudly unless the running service reports the
# version that was just deployed. The inference engine is only ever a client target.
set -euo pipefail

TARGET=${1:?usage: deploy.sh user@host}
: "${ODYRAG_PORT:?set ODYRAG_PORT (listening port of the dashboard)}"
: "${ODYRAG_ENGINE_URL:?set ODYRAG_ENGINE_URL (public API of the inference engine, as seen from the host)}"

HERE=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(sed -nE 's/^__version__ = "([^"]+)".*/\1/p' "$HERE/coeos_rag/__init__.py")
[ -n "$VERSION" ] || { echo "cannot read the version from coeos_rag/__init__.py" >&2; exit 1; }
LABEL=eu.odyssai.odyrag
ODYRAG_BIND=${ODYRAG_BIND:-0.0.0.0}

# 1. never redeploy under a running change (a restart in the middle could strand an instance)
if ssh "$TARGET" "curl -sf --max-time 5 http://localhost:${ODYRAG_PORT}/api/jobs" 2>/dev/null | grep -q '"status": *"running"'; then
  echo "a change is in flight on the host: not deploying" >&2
  exit 1
fi

# 2. code, venv, state
ssh "$TARGET" 'mkdir -p ~/odyrag/app ~/odyrag/logs ~/.odyrag'
rsync -a --delete --exclude .venv --exclude __pycache__ --exclude .git --exclude .pytest_cache --exclude '*.md' \
  --exclude docs --exclude graphify-out --exclude examples \
  "$HERE/" "$TARGET:odyrag/app/"
ssh "$TARGET" 'cd ~/odyrag && { [ -x venv/bin/python ] || /opt/homebrew/bin/python3.11 -m venv venv; } \
  && venv/bin/pip install -q --disable-pip-version-check -r app/requirements.txt'

# 3. admin token (created once, 0600, never printed) and registry (seeded once)
ssh "$TARGET" 'umask 077; [ -f ~/.odyrag/admin-token ] || openssl rand -hex 24 > ~/.odyrag/admin-token'
# deploy/registry.prod.json is local to the operator (not in the repository). Without it a
# first install starts with an empty registry: see AGENTS.md.
ssh "$TARGET" '[ -f ~/.odyrag/registry.json ] || { [ -f ~/odyrag/app/deploy/registry.prod.json ] && cp ~/odyrag/app/deploy/registry.prod.json ~/.odyrag/registry.json; } || echo "no deploy/registry.prod.json: the registry starts empty (see AGENTS.md)"'

# 4. launchd plist (written with plistlib: no hand-made XML)
ssh "$TARGET" "ODYRAG_PORT='$ODYRAG_PORT' ODYRAG_BIND='$ODYRAG_BIND' ODYRAG_ENGINE_URL='$ODYRAG_ENGINE_URL' LABEL='$LABEL' ~/odyrag/venv/bin/python - <<'PY'
import os, plistlib, pathlib
home = pathlib.Path.home()
plist = {
    'Label': os.environ['LABEL'], 'KeepAlive': True, 'RunAtLoad': True,
    'WorkingDirectory': str(home / 'odyrag' / 'app'),
    'ProgramArguments': [str(home / 'odyrag' / 'venv' / 'bin' / 'python'), '-m', 'uvicorn', 'coeos_rag.app:create_app',
                         '--factory', '--host', os.environ['ODYRAG_BIND'], '--port', os.environ['ODYRAG_PORT']],
    'StandardOutPath': str(home / 'odyrag' / 'logs' / 'odyrag.log'),
    'StandardErrorPath': str(home / 'odyrag' / 'logs' / 'odyrag.log'),
    'EnvironmentVariables': {
        'ODYRAG_STATE_DIR': str(home / '.odyrag'),
        'ODYRAG_ENGINE_URL': os.environ['ODYRAG_ENGINE_URL'],
        'ODYRAG_ADMIN_TOKEN': (home / '.odyrag' / 'admin-token').read_text().strip(),
    },
}
target = home / 'Library' / 'LaunchAgents' / (os.environ['LABEL'] + '.plist')
target.write_bytes(plistlib.dumps(plist))
target.chmod(0o600)
PY"

# 5. (re)load and verify
ssh "$TARGET" "launchctl bootout gui/\$(id -u)/$LABEL 2>/dev/null || true; sleep 2; launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/$LABEL.plist"
for _ in $(seq 1 30); do
  live=$(ssh "$TARGET" "curl -sf --max-time 3 http://localhost:${ODYRAG_PORT}/health" 2>/dev/null | sed -nE 's/.*"version": *"([^"]+)".*/\1/p' || true)
  [ -n "$live" ] && break
  sleep 2
done
if [ "$live" != "$VERSION" ]; then
  echo "DEPLOY FAILED: expected version $VERSION, the service reports '${live:-nothing}'" >&2
  exit 1
fi
echo "deployed Coeos-RAG $VERSION to $TARGET (port $ODYRAG_PORT)"
