#!/usr/bin/env bash
# Regression tests for the fixed-timer wedge settlement shadow score.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null 2>&1 || { echo "skip: python3 not found"; exit 0; }

SCORE="$ROOT/bin/fm-wedge-score.py"
TMP_ROOT=$(fm_test_tmproot fm-wedge-score)

test_settle_records_project_lane() {
  local home out
  home="$TMP_ROOT/project"
  mkdir -p "$home/state"
  printf 'project=demo\n' > "$home/state/task.meta"
  "$SCORE" settle --home "$home" --key key --window sess:win --task task \
    --idle-secs 12 --outcome resumed
  out=$(python3 - "$home/data/wedge-settlements.jsonl" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    row = json.loads(handle.readline())
assert row["lane"] == "demo"
assert row["idle_secs"] == 12
assert row["outcome"] == "resumed"
print("ok")
PY
)
  [ "$out" = ok ] || fail "settle row validation failed"
  pass "settle appends a row with the project lane"
}

test_score_requires_minimum_observations() {
  local home out
  home="$TMP_ROOT/insufficient"
  mkdir -p "$home/data"
  python3 - "$home/data/wedge-settlements.jsonl" <<'PY'
import json
import sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for idle in (40, 50, 60):
        handle.write(json.dumps({"lane": "demo", "idle_secs": idle,
                                 "outcome": "resumed"}) + "\n")
PY
  out=$("$SCORE" score --home "$home" --lane demo --idle-secs 100)
  python3 - "$out" <<'PY'
import json
import sys
row = json.loads(sys.argv[1])
assert row["score"] is None
assert row["reason"] == "insufficient data: 3<8"
PY
  pass "score reports insufficient data below the minimum observations"
}

test_score_flags_graded_and_fixed_thresholds() {
  local home high low
  home="$TMP_ROOT/scored"
  mkdir -p "$home/data"
  python3 - "$home/data/wedge-settlements.jsonl" <<'PY'
import json
import sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for idle in (40, 45, 50, 55, 60, 65, 70, 80, 90, 100):
        handle.write(json.dumps({"lane": "demo", "idle_secs": idle,
                                 "outcome": "resumed"}) + "\n")
    handle.write(json.dumps({"lane": "demo", "idle_secs": 240,
                             "outcome": "escalated"}) + "\n")
PY
  high=$("$SCORE" score --home "$home" --lane demo --idle-secs 600)
  low=$("$SCORE" score --home "$home" --lane demo --idle-secs 50)
  python3 - "$high" "$low" <<'PY'
import json
import sys
high, low = (json.loads(value) for value in sys.argv[1:])
assert high["graded_flag"] is True
assert high["fixed_flag"] is True
assert high["score"] > 0
assert low["graded_flag"] is False
PY
  pass "score separates graded and fixed threshold flags"
}

test_settle_missing_meta_uses_unknown_lane() {
  local home out
  home="$TMP_ROOT/missing-meta"
  mkdir -p "$home"
  "$SCORE" settle --home "$home" --key key --window sess:win --task missing \
    --idle-secs 4 --outcome escalated
  out=$(python3 - "$home/data/wedge-settlements.jsonl" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.loads(handle.readline())["lane"])
PY
)
  [ "$out" = unknown ] || fail "missing meta lane was '$out'"
  pass "settle uses unknown lane when task metadata is absent"
}

test_settle_records_project_lane
test_score_requires_minimum_observations
test_score_flags_graded_and_fixed_thresholds
test_settle_missing_meta_uses_unknown_lane
