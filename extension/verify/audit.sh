#!/usr/bin/env bash
# Convenience wrapper: syntax + build freshness + a fast static security sweep.
# The authoritative checks live in tests/static.test.js (run `npm test`).
set -u
cd "$(dirname "$0")/.."
fail=0
note() { printf '  %s\n' "$1"; }
bad() { printf 'FAIL: %s\n' "$1"; fail=1; }

echo "== syntax =="
for f in sw.js content.js bookmarklet.js popup.js build.js lib/*.js src/*.js testbed/selftest.js; do
  node --check "$f" 2>/dev/null && note "ok  $f" || bad "syntax $f"
done

echo "== json =="
for f in manifest.json testbed/autofill.mock.json package.json; do
  node -e "JSON.parse(require('fs').readFileSync('$f','utf8'))" && note "ok  $f" || bad "json $f"
done

echo "== build freshness =="
node build.js --check && note "bundles up to date" || bad "content.js / bookmarklet.js are stale (run: node build.js)"

echo "== manifest constraints =="
grep -q '"content_security_policy"' manifest.json && \
  node -e "const m=require('./manifest.json');process.exit(m.content_security_policy.extension_pages===\"script-src 'self'; object-src 'none'\"?0:1)" \
  && note "CSP locked" || bad "CSP not exactly script-src 'self'; object-src 'none'"
grep -Eq '<all_urls>|\*://\*|"tabs"|"webRequest"|"cookies"' manifest.json && bad "broad permission in manifest" || note "no broad permissions"
grep -q 'content_scripts' manifest.json && bad "static content_scripts present" || note "no static content_scripts"

echo "== injected-bundle safety (comments stripped) =="
strip() { sed -E 's://.*$::' "$1" | tr -d '\n'; }
for b in content.js bookmarklet.js; do
  s="$(strip "$b")"
  case "$s" in
    *".submit("*|*"requestSubmit"*|*".click("*|*"MutationObserver"*|*"eval("*|*"allFrames"*|*".focus("*|*".blur("*)
      bad "$b contains a forbidden call" ;;
    *) note "ok  $b" ;;
  esac
done

echo "== storage.session must never appear in shipped code =="
if grep -RIl 'storage\.session' sw.js popup.js content.js bookmarklet.js lib src 2>/dev/null | grep -q .; then
  bad "storage.session referenced in shipped code"
else
  note "no storage.session"
fi

echo
[ "$fail" -eq 0 ] && echo "AUDIT PASS" || echo "AUDIT FAIL"
exit "$fail"
