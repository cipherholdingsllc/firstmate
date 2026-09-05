#!/usr/bin/env python3
# Usage: fm-wedge-score.py settle|score --state <dir> [options].
# JSONL schema: {"ts":"<iso8601 utc>","window":"<window>","task":"<task>","lane":"<lane>","idle_secs":<int>,"outcome":"resumed|escalated"}.
# This script is the single owner of the .wedge-settlements.jsonl schema, of the
# task-to-lane rule, and of the log's size bound. The log is watcher-owned
# bookkeeping and lives beside .watch-triage.log in the state directory the
# caller resolves.
#
# The graded score is a shadow comparator, not a calibrated one: its healthy
# sample is right-censored by the fixed timer it shadows (a pane idle past the
# threshold escalates instead of settling as resumed), so it never observes a
# legitimately long healthy tail.

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from argyle_gates import norm_cdf, norm_ppf

SETTLEMENTS = ".wedge-settlements.jsonl"
MAX_BYTES = 262144


def _lane_for_task(state, task):
    if not task:
        return "unknown"
    meta = os.path.join(state, f"{task}.meta")
    try:
        with open(meta, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("project="):
                    return line.rstrip("\n").split("=", 1)[1] or "unknown"
    except OSError:
        pass
    return "unknown"


def _trim(path):
    if os.path.getsize(path) < MAX_BYTES:
        return
    kept, size = [], 0
    with open(path, encoding="utf-8") as handle:
        for line in reversed(handle.readlines()):
            size += len(line.encode("utf-8"))
            if size > MAX_BYTES // 2:
                break
            kept.append(line)
    kept.reverse()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.writelines(kept)
    os.replace(tmp, path)


def _settle(args):
    try:
        os.makedirs(args.state, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "window": args.window,
            "task": args.task,
            "lane": _lane_for_task(args.state, args.task),
            "idle_secs": args.idle_secs,
            "outcome": args.outcome,
        }
        path = os.path.join(args.state, SETTLEMENTS)
        with open(path, "a", encoding="utf-8") as handle:
            json.dump(row, handle, separators=(",", ":"))
            handle.write("\n")
        _trim(path)
    except Exception as exc:
        print(f"fm-wedge-score settle: {exc}", file=sys.stderr)
    return 0


def _read_rows(state, lane):
    path = os.path.join(state, SETTLEMENTS)
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("lane") == lane:
                    rows.append(row)
    except OSError:
        pass
    return rows


def _escalation_incidents(rows):
    # A pane that stays wedged re-escalates every threshold, so only the first
    # escalation of a window since its last resume counts as an incident.
    unresolved = set()
    count = 0
    for row in rows:
        window = row.get("window", "")
        if row.get("outcome") == "escalated":
            if window not in unresolved:
                unresolved.add(window)
                count += 1
        elif row.get("outcome") == "resumed":
            unresolved.discard(window)
    return count


def _score(args):
    lane = _lane_for_task(args.state, args.task)
    rows = _read_rows(args.state, lane)
    healthy = [
        float(row["idle_secs"])
        for row in rows
        if row.get("outcome") == "resumed"
    ]
    n = len(healthy)
    n_esc = _escalation_incidents(rows)
    n_total = n + n_esc
    fixed_flag = args.idle_secs >= args.fixed_threshold
    if n < args.min_obs:
        result = {
            "lane": lane,
            "n": n,
            "score": None,
            "graded_flag": None,
            "fixed_flag": fixed_flag,
            "reason": f"insufficient data: {n}<{args.min_obs}",
        }
    else:
        logs = [math.log(max(value, 1.0)) for value in healthy]
        mu = sum(logs) / n
        variance = sum((value - mu) ** 2 for value in logs) / (n - 1)
        sigma = max(math.sqrt(variance), 0.25)
        z = (math.log(max(args.idle_secs, 1)) - mu) / sigma
        base_rate = (n_esc + 1) / (n_total + 2)
        score = z - norm_ppf(1 - base_rate)
        p_tail = 1 - norm_cdf(z)
        result = {
            "lane": lane,
            "n": n,
            "n_total": n_total,
            "base_rate": round(base_rate, 4),
            "z": round(z, 4),
            "p_tail": round(p_tail, 4),
            "score": round(score, 4),
            "graded_flag": score > 0,
            "fixed_flag": fixed_flag,
            "reason": "ok",
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    settle = subparsers.add_parser("settle")
    settle.add_argument("--state", required=True)
    settle.add_argument("--window", required=True)
    settle.add_argument("--task", default="")
    settle.add_argument("--idle-secs", required=True, type=int)
    settle.add_argument("--outcome", choices=("resumed", "escalated"), required=True)
    settle.set_defaults(handler=_settle)

    score = subparsers.add_parser("score")
    score.add_argument("--state", required=True)
    score.add_argument("--task", default="")
    score.add_argument("--idle-secs", required=True, type=int)
    score.add_argument("--fixed-threshold", required=True, type=int)
    score.add_argument("--min-obs", default=8, type=int)
    score.set_defaults(handler=_score)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
