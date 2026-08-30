#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose up -d --build
printf '\nOSDU Well 360 local server is starting...\n'
for i in {1..30}; do
  if curl -fsS http://localhost:8089/health >/dev/null 2>&1; then
    echo "Ready: http://localhost:8089"
    echo "Partition: company-prod"
    echo "Bearer token: demo-token"
    echo
    echo "Run: ./smoke-test.sh"
    exit 0
  fi
  sleep 1
done
echo "Service did not become healthy. Check: docker compose logs" >&2
exit 1
