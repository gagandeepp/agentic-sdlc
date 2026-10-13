#!/bin/sh
# Runs as root just long enough to stash keys where the agent user can't read them, then
# drops to appuser and execs the API as pid 1 (so SIGTERM reaches uvicorn).
set -eu
umask 077
mkdir -p /run/agentic
: > /run/agentic/keys.env
for name in AGENT_ANTHROPIC_KEY AGENT_GITHUB_TOKEN; do
  value=$(printenv "$name" || true)
  if [ -n "$value" ]; then
    printf '%s=%s\n' "$name" "$value" >> /run/agentic/keys.env
  fi
done
chown -R appuser:appuser /run/agentic
chmod 0500 /run/agentic
chmod 0400 /run/agentic/keys.env
unset AGENT_ANTHROPIC_KEY AGENT_GITHUB_TOKEN ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN GH_TOKEN GITHUB_TOKEN
umask 002
exec setpriv --reuid=appuser --regid=appuser --init-groups -- \
  uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" --timeout-graceful-shutdown 5
