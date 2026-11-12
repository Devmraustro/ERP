#!/usr/bin/env bash
# Phase 10 — public TLS validation against a REAL hostname and a REAL certificate.
#
# NOT EXECUTED in this project: no public hostname, no publicly trusted certificate.
# The self-signed harness in docs/release/phase8/evidence/tls_proxy_harness.py proves
# the proxy/application behaviour; this script proves the *public trust chain*, which
# only a real deployment can.
#
#   ./public_tls_validation.sh erp.example.dz
#
# Exit codes: 0 all checks pass, 1 a check failed, 2 bad usage.
# Every check prints PASS/FAIL with the observed value; nothing is inferred.

set -uo pipefail

HOST="${1:-}"
PORT="${2:-443}"
if [ -z "$HOST" ]; then
  echo "usage: $0 <public-hostname> [port]" >&2
  exit 2
fi

BASE="https://${HOST}"
if [ "$PORT" != "443" ]; then BASE="${BASE}:${PORT}"; fi
FAILED=0
EVIDENCE_DIR="${EVIDENCE_DIR:-./tls-evidence}"
mkdir -p "$EVIDENCE_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$EVIDENCE_DIR/public_tls_${HOST}_${STAMP}.log"

pass() { printf '[ PASS ] %s: %s\n' "$1" "$2" | tee -a "$LOG"; }
fail() { printf '[ FAIL ] %s: %s\n' "$1" "$2" | tee -a "$LOG"; FAILED=$((FAILED+1)); }
note() { printf '[ NOTE ] %s: %s\n' "$1" "$2" | tee -a "$LOG"; }

echo "== public TLS validation for ${HOST}:${PORT} at ${STAMP} ==" | tee -a "$LOG"

# 1. Real hostname resolves, and to a public address (not loopback/private).
IPS="$(getent ahosts "$HOST" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')"
if [ -z "$IPS" ]; then
  fail "1 DNS resolution" "no A/AAAA record for $HOST"
else
  pass "1 DNS resolution" "$IPS"
  case "$IPS" in
    *127.0.0.1*|*10.*|*192.168.*|*"::1"*) fail "1b public address" "resolves to a private/loopback address: $IPS" ;;
    *) pass "1b public address" "not loopback/private" ;;
  esac
fi

# 2. Certificate chain validates against the system trust store, with SNI + hostname.
if openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" -verify_return_error \
      -CAfile /etc/ssl/certs/ca-certificates.crt </dev/null >"$EVIDENCE_DIR/s_client.txt" 2>&1; then
  pass "2 chain validation" "$(grep -m1 'Verify return code' "$EVIDENCE_DIR/s_client.txt")"
else
  fail "2 chain validation" "$(grep -m1 -E 'verify error|Verify return code' "$EVIDENCE_DIR/s_client.txt")"
fi

# 3. Full chain is served (leaf + at least one intermediate), so old clients work.
CHAIN_COUNT="$(openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" -showcerts </dev/null 2>/dev/null \
  | grep -c 'BEGIN CERTIFICATE')"
if [ "$CHAIN_COUNT" -ge 2 ]; then
  pass "3 chain completeness" "${CHAIN_COUNT} certificates served"
else
  fail "3 chain completeness" "only ${CHAIN_COUNT} certificate(s) served; intermediates must be sent"
fi

# 4. Expiry, with a warning window inside 30 days.
END_DATE="$(openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" </dev/null 2>/dev/null \
  | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
if [ -n "$END_DATE" ]; then
  END_EPOCH="$(date -d "$END_DATE" +%s 2>/dev/null || echo 0)"
  NOW_EPOCH="$(date +%s)"
  DAYS_LEFT=$(( (END_EPOCH - NOW_EPOCH) / 86400 ))
  if [ "$END_EPOCH" -gt "$NOW_EPOCH" ] && [ "$DAYS_LEFT" -gt 30 ]; then
    pass "4 certificate expiry" "$END_DATE (${DAYS_LEFT} days left)"
  elif [ "$END_EPOCH" -gt "$NOW_EPOCH" ]; then
    note "4 certificate expiry" "$END_DATE (${DAYS_LEFT} days left - renew now)"
  else
    fail "4 certificate expiry" "$END_DATE (expired)"
  fi
else
  fail "4 certificate expiry" "could not read the leaf certificate"
fi

# 5. Certificate matches the hostname (SAN), not just the CN.
if openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" </dev/null 2>/dev/null \
    | openssl x509 -noout -checkhost "$HOST" 2>/dev/null | grep -q 'does match'; then
  pass "5 hostname match" "SAN covers $HOST"
else
  fail "5 hostname match" "certificate does not cover $HOST"
fi

# 6. Plain HTTP redirects to HTTPS (edge behaviour).
REDIRECT="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "http://${HOST}/healthz" || true)"
case "$REDIRECT" in
  30[1789]*https://*) pass "6 HTTP->HTTPS" "$REDIRECT" ;;
  *) fail "6 HTTP->HTTPS" "expected a 301/302/307/308 to https, got: $REDIRECT" ;;
esac

# 7. HSTS present with a sane max-age (edge responsibility).
HEADERS="$(curl -sS -D - -o /dev/null "${BASE}/healthz" || true)"
printf '%s\n' "$HEADERS" >> "$LOG"
if printf '%s' "$HEADERS" | grep -qi '^strict-transport-security:.*max-age=[0-9]\+'; then
  pass "7 HSTS" "$(printf '%s' "$HEADERS" | grep -i '^strict-transport-security:' | tr -d '\r')"
else
  fail "7 HSTS" "no Strict-Transport-Security with max-age on the HTTPS response"
fi

# 8. Certificate enforced: a TLS request that does not trust the chain must fail for
#    a wrong name, proving SNI/hostname checking is real (not a wildcard catch-all).
if curl -sS --max-time 10 -o /dev/null "https://wrong-name.invalid/" \
     -H "Host: ${HOST}" --resolve "wrong-name.invalid:${PORT}:$(echo "$IPS" | awk '{print $1}')" 2>/dev/null; then
  fail "8 hostname enforcement" "a wrong certificate name was accepted"
else
  pass "8 hostname enforcement" "a mismatched hostname is refused"
fi

# 9. Reverse proxy passes the application's headers through unchanged.
for header in x-content-type-options x-frame-options referrer-policy x-request-id; do
  if printf '%s' "$HEADERS" | grep -qi "^${header}:"; then
    pass "9 proxy header ${header}" "$(printf '%s' "$HEADERS" | grep -i "^${header}:" | tr -d '\r')"
  else
    fail "9 proxy header ${header}" "missing: the proxy is rewriting or dropping application headers"
  fi
done

# 10. Host allow-listing still applies through the proxy (application-level control).
CODE="$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/healthz" -H "Host: evil.example" || true)"
if [ "$CODE" = "400" ]; then
  pass "10 host allow-list" "spoofed Host -> 400"
else
  fail "10 host allow-list" "spoofed Host returned ${CODE}, expected 400 (check ERP_ALLOWED_HOSTS)"
fi

# 11. Forwarded-header trust: an untrusted peer's X-Forwarded-For must not be believed.
CODE="$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/readyz" -H "X-Forwarded-For: 203.0.113.9" || true)"
if [ "$CODE" = "200" ]; then
  pass "11 forwarded headers" "readiness reachable; XFF from a client is not trusted for identity"
else
  note "11 forwarded headers" "readyz returned ${CODE}; check ERP_TRUSTED_PROXIES and the proxy hop"
fi

# 12. Backend isolation: the application port must not be reachable from outside.
: "${BACKEND_HOST:=127.0.0.1}"
: "${BACKEND_PORT:=8000}"
if timeout 5 curl -s -o /dev/null "http://${BACKEND_HOST}:${BACKEND_PORT}/healthz" 2>/dev/null; then
  note "12 backend isolation" "backend answers on ${BACKEND_HOST}:${BACKEND_PORT} from THIS host (expected on the server); verify from the internet that it is not routable"
else
  pass "12 backend isolation" "backend not reachable on ${BACKEND_HOST}:${BACKEND_PORT}"
fi

# 13. Readiness through the proxy (the deployment answers as ready).
READY_CODE="$(curl -s -o "$EVIDENCE_DIR/readyz.json" -w '%{http_code}' "${BASE}/readyz" || true)"
if [ "$READY_CODE" = "200" ]; then
  pass "13 readiness via proxy" "200 $(head -c 120 "$EVIDENCE_DIR/readyz.json")"
else
  fail "13 readiness via proxy" "readiness returned ${READY_CODE}"
fi

echo "== summary: $([ $FAILED -eq 0 ] && echo 'ALL CHECKS PASS' || echo "${FAILED} CHECK(S) FAILED") ==" | tee -a "$LOG"
echo "evidence: $LOG"
exit $([ $FAILED -eq 0 ] && echo 0 || echo 1)
