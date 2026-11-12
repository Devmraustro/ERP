#!/bin/bash
# Source-tree deployment smoke test (B2 review): start the documented V1 entry
# point against a throwaway database and verify the security boundary end to end.
set -u
REPO="${ERP_ROOT:-/tmp/gate}"
VENV="${ERP_VENV:-/tmp/py312venv}"
D=$(mktemp -d); PORT=${PORT:-8124}
export LD_LIBRARY_PATH=/tmp/py312/lib:/tmp/py312deps/lib
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
export PYTHONPATH="$REPO" PYTHONDONTWRITEBYTECODE=1

echo "== bootstrap admin (documented first-run step) =="
printf 'Deploy#2026x\nDeploy#2026x\n' | ERP_DB_PATH="$D/erp.sqlite3" \
  "$VENV/bin/python" -m dz_erp.serve --bootstrap-admin admin --company-name "Deploy Co" 2>&1 | tail -2

echo "== start server from source (python -m dz_erp.serve) =="
ERP_DB_PATH="$D/erp.sqlite3" ERP_VAULT_PATH="$D/vault" \
  "$VENV/bin/python" -m dz_erp.serve --host 127.0.0.1 --port "$PORT" > "$D/server.log" 2>&1 &
SRV=$!
for _ in $(seq 1 40); do curl -s -m 2 -o /dev/null "http://127.0.0.1:$PORT/healthz" && break; sleep 0.5; done

code() { curl -s -m 5 -o /dev/null -w "%{http_code}" "$@"; }
echo "healthz                    : $(code "http://127.0.0.1:$PORT/healthz")"
echo "openapi.json               : $(code "http://127.0.0.1:$PORT/openapi.json")"
echo "users without token        : $(code "http://127.0.0.1:$PORT/api/v1/users")"
TOK=$(curl -s -m 5 -X POST "http://127.0.0.1:$PORT/api/v1/auth/token" -H 'content-type: application/json' \
      -d '{"username":"admin","password":"Deploy#2026x"}' | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
echo "token issued               : $([ -n "$TOK" ] && echo yes || echo no)"
echo "session with token         : $(code -H "Authorization: Bearer $TOK" "http://127.0.0.1:$PORT/api/v1/auth/session")"
echo "Host: evil.example (SEC-15): $(code -H 'Host: evil.example' "http://127.0.0.1:$PORT/healthz")"
echo "wrong password             : $(code -X POST "http://127.0.0.1:$PORT/api/v1/auth/token" -H 'content-type: application/json' -d '{"username":"admin","password":"nope"}')"
echo "short new password (SEC-13): $(code -X POST -H "Authorization: Bearer $TOK" -H 'content-type: application/json' -d '{"current_password":"Deploy#2026x","new_password":"short"}' "http://127.0.0.1:$PORT/api/v1/auth/password")"
echo "== server log tail =="; tail -4 "$D/server.log"
kill $SRV 2>/dev/null; wait $SRV 2>/dev/null
rm -rf "$D"
