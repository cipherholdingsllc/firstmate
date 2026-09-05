#!/usr/bin/env bash
# Regression tests for the fixed-timer wedge settlement shadow score.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null 2>&1 || { echo "skip: python3 not found"; exit 0; }

SCORE="$ROOT/bin/fm-wedge-score.py"
TMP_ROOT=$(fm_test_tmproot fm-wedge-score)
LOG=.wedge-settlements.jsonl

test_settle_records_project_lane() {
  local state out
  state="$TMP_ROOT/project/state"
  mkdir -p "$state"
  printf 'project=demo\n' > "$state/task.meta"
  "$SCORE" settle --state "$state" --window sess:win --task task \
    --idle-secs 12 --outcome resumed
  out=$(python3 - "$state/$LOG" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    row = json.loads(handle.readline())
assert row["lane"] == "demo", row
assert row["idle_secs"] == 12, row
assert row["outcome"] == "resumed", row
assert row["window"] == "sess:win", row
print("ok")
PY
)
  [ "$out" = ok ] || fail "settle row validation failed"
  pass "settle appends a row with the project lane"
}

test_score_requires_minimum_observations() {
  local state out check
  state="$TMP_ROOT/insufficient/state"
  mkdir -p "$state"
  printf 'project=demo\n' > "$state/task.meta"
  python3 - "$state/$LOG" <<'PY'
import json
import sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for idle in (40, 50, 60):
        handle.write(json.dumps({"lane": "demo", "window": "s:w",
                                 "idle_secs": idle,
                                 "outcome": "resumed"}) + "\n")
PY
  out=$("$SCORE" score --state "$state" --task task --idle-secs 100 \
    --fixed-threshold 240)
  check=$(python3 - "$out" <<'PY'
import json
import sys
row = json.loads(sys.argv[1])
assert row["score"] is None, row
assert row["graded_flag"] is None, row
assert row["reason"] == "insufficient data: 3<8", row
print("ok")
PY
) || fail "insufficient-data assertions failed for $out"
  [ "$check" = ok ] || fail "insufficient-data validation failed for $out"
  pass "score reports insufficient data below the minimum observations"
}

test_score_flags_graded_and_fixed_thresholds() {
  local state high low check
  state="$TMP_ROOT/scored/state"
  mkdir -p "$state"
  printf 'project=demo\n' > "$state/task.meta"
  python3 - "$state/$LOG" <<'PY'
import json
import sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for idle in (40, 45, 50, 55, 60, 65, 70, 80, 90, 100):
        handle.write(json.dumps({"lane": "demo", "window": "s:w",
                                 "idle_secs": idle,
                                 "outcome": "resumed"}) + "\n")
    handle.write(json.dumps({"lane": "demo", "window": "s:w",
                             "idle_secs": 240,
                             "outcome": "escalated"}) + "\n")
PY
  high=$("$SCORE" score --state "$state" --task task --idle-secs 600 \
    --fixed-threshold 240)
  low=$("$SCORE" score --state "$state" --task task --idle-secs 50 \
    --fixed-threshold 240)
  check=$(python3 - "$high" "$low" <<'PY'
import json
import sys
high, low = (json.loads(value) for value in sys.argv[1:])
assert high["graded_flag"] is True, high
assert high["fixed_flag"] is True, high
assert high["score"] > 0, high
assert low["graded_flag"] is False, low
assert low["fixed_flag"] is False, low
print("ok")
PY
) || fail "graded/fixed assertions failed for high=$high low=$low"
  [ "$check" = ok ] || fail "graded/fixed validation failed for high=$high low=$low"
  pass "score separates graded and fixed threshold flags"
}

# The escalation bound is FM_STALE_ESCALATE_SECS, not a constant, so the caller
# supplies it and fixed_flag reports what the fixed timer actually did.
test_fixed_flag_follows_the_supplied_threshold() {
  local state out check
  state="$TMP_ROOT/threshold/state"
  mkdir -p "$state"
  out=$("$SCORE" score --state "$state" --task missing --idle-secs 120 \
    --fixed-threshold 120)
  check=$(python3 - "$out" <<'PY'
import json
import sys
row = json.loads(sys.argv[1])
assert row["fixed_flag"] is True, row
print("ok")
PY
) || fail "fixed_flag did not follow the supplied threshold: $out"
  [ "$check" = ok ] || fail "fixed_flag validation failed for $out"
  pass "fixed_flag follows the caller's escalation threshold"
}

# A pane that stays wedged re-escalates every threshold; counting those repeats
# as separate incidents would inflate the base rate and lower the graded bar.
test_repeated_escalations_count_as_one_incident() {
  local state repeated distinct check
  state="$TMP_ROOT/incidents/state"
  mkdir -p "$state"
  printf 'project=demo\n' > "$state/task.meta"
  python3 - "$state/$LOG" <<'PY'
import json
import sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for idle in (40, 45, 50, 55, 60, 65, 70, 80):
        handle.write(json.dumps({"lane": "demo", "window": "s:healthy",
                                 "idle_secs": idle,
                                 "outcome": "resumed"}) + "\n")
    for _ in range(6):
        handle.write(json.dumps({"lane": "demo", "window": "s:wedged",
                                 "idle_secs": 240,
                                 "outcome": "escalated"}) + "\n")
PY
  repeated=$("$SCORE" score --state "$state" --task task --idle-secs 300 \
    --fixed-threshold 240)
  python3 - "$state/$LOG" <<'PY'
import json
import sys
with open(sys.argv[1], "a", encoding="utf-8") as handle:
    for window in ("s:a", "s:b", "s:c", "s:d", "s:e"):
        handle.write(json.dumps({"lane": "demo", "window": window,
                                 "idle_secs": 240,
                                 "outcome": "escalated"}) + "\n")
PY
  distinct=$("$SCORE" score --state "$state" --task task --idle-secs 300 \
    --fixed-threshold 240)
  check=$(python3 - "$repeated" "$distinct" <<'PY'
import json
import sys
repeated, distinct = (json.loads(value) for value in sys.argv[1:])
assert repeated["n_total"] == 9, repeated
assert distinct["n_total"] == 14, distinct
assert distinct["base_rate"] > repeated["base_rate"], (repeated, distinct)
print("ok")
PY
) || fail "incident counting failed for repeated=$repeated distinct=$distinct"
  [ "$check" = ok ] || fail "incident validation failed for repeated=$repeated distinct=$distinct"
  pass "repeated escalations of one window count as a single incident"
}

test_settle_missing_meta_uses_unknown_lane() {
  local state out
  state="$TMP_ROOT/missing-meta/state"
  mkdir -p "$(dirname "$state")"
  "$SCORE" settle --state "$state" --window sess:win --task missing \
    --idle-secs 4 --outcome escalated
  out=$(python3 - "$state/$LOG" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.loads(handle.readline())["lane"])
PY
)
  [ "$out" = unknown ] || fail "missing meta lane was '$out'"
  pass "settle uses unknown lane when task metadata is absent"
}

test_settlement_log_stays_bounded() {
  local state before after check
  state="$TMP_ROOT/bounded/state"
  mkdir -p "$state"
  python3 - "$state/$LOG" <<'PY'
import json
import sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for i in range(4000):
        handle.write(json.dumps({"ts": "2026-01-01T00:00:00Z", "window": "s:w",
                                 "task": "old", "lane": "demo",
                                 "idle_secs": i, "outcome": "resumed"}) + "\n")
PY
  before=$(wc -c < "$state/$LOG" | tr -d '[:space:]')
  [ "$before" -gt 262144 ] || fail "fixture log was only $before bytes"
  "$SCORE" settle --state "$state" --window sess:win --task newest \
    --idle-secs 7 --outcome escalated
  after=$(wc -c < "$state/$LOG" | tr -d '[:space:]')
  [ "$after" -lt "$before" ] || fail "settlement log was not trimmed: $after bytes"
  check=$(python3 - "$state/$LOG" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    rows = [json.loads(line) for line in handle if line.strip()]
assert rows, "trimming emptied the log"
assert rows[-1]["task"] == "newest", rows[-1]
assert rows[-1]["idle_secs"] == 7, rows[-1]
print("ok")
PY
) || fail "the newest settlement did not survive trimming"
  [ "$check" = ok ] || fail "trimmed log validation failed"
  pass "the settlement log is trimmed once it passes its size cap"
}

test_settle_records_project_lane
test_score_requires_minimum_observations
test_score_flags_graded_and_fixed_thresholds
test_fixed_flag_follows_the_supplied_threshold
test_repeated_escalations_count_as_one_incident
test_settle_missing_meta_uses_unknown_lane
test_settlement_log_stays_bounded
