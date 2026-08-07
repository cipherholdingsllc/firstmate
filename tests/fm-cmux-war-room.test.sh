#!/usr/bin/env bash
# Regression tests for the manual cmux war-room helper.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/fm-cmux-war-room-test.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

fail() { printf 'not ok - %s\n' "$1" >&2; exit 1; }
pass() { printf 'ok - %s\n' "$1"; }

cat >"$TMP/cmux" <<'EOF'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >>"$CMUX_LOG"
case "$1" in
  list-panes)
    printf '{"panes":[{"surface_ids":["surface-target-id"],"surface_refs":["surface:7"]}]}\n'
    ;;
  close-surface|close-workspace) : ;;
  *) : ;;
esac
EOF
chmod +x "$TMP/cmux"
CMUX_LOG="$TMP/calls" PATH="$TMP:$PATH" CMUX_WORKSPACE_ID="workspace:caller" \
  "$ROOT/bin/fm-cmux-war-room.sh" teardown-surfaces --workspace "workspace:target" \
  || fail "teardown-surfaces failed from a different caller workspace"

grep -F -- 'close-surface --workspace workspace:target --surface surface-target-id' "$TMP/calls" \
  >/dev/null || fail "teardown did not scope close-surface to target workspace"
if [ "$(grep -c -F -- 'close-surface --workspace workspace:target' "$TMP/calls")" -ne 1 ]; then
  fail "teardown did not close exactly one target surface"
fi
if grep -F -- 'close-surface --workspace workspace:caller' "$TMP/calls" >/dev/null; then
  fail "teardown used caller workspace"
fi
pass "teardown-surfaces scopes closes when caller workspace differs"

if PATH="$TMP:$PATH" "$ROOT/bin/fm-cmux-war-room.sh" teardown-surfaces --workspace "" \
  >/dev/null 2>"$TMP/error"; then
  fail "empty workspace ref was accepted"
fi
grep -F -- 'workspace is empty' "$TMP/error" >/dev/null || fail "empty workspace error was not fail-loud"
pass "empty workspace ref fails closed"
