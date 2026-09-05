#!/usr/bin/env python3
# Usage: fm-wedge-score.py settle|score [options].
# JSONL schema: {"ts":"<iso8601 utc>","key":"<key>","window":"<window>","task":"<task>","lane":"<lane>","idle_secs":<int>,"outcome":"resumed|escalated"}.
# This script is the single owner of the wedge-settlements.jsonl schema.

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from argyle_gates import norm_cdf, norm_ppf


def _home(value):
    return value or os.environ.get("FM_HOME", ".")


def _lane_for_task(home, task):
    if not task:
        return "unknown"
    meta = os.path.join(home, "state", f"{task}.meta")
    try:
        with open(meta, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("project="):
                    return line.rstrip("\n").split("=", 1)[1] or "unknown"
    except OSError:
        pass
    return "unknown"


def _settle(args):
    try:
        home = _home(args.home)
        data_dir = os.path.join(home, "data")
        os.makedirs(data_dir, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "key": args.key,
            "window": args.window,
            "task": args.task,
            "lane": _lane_for_task(home, args.task),
            "idle_secs": args.idle_secs,
            "outcome": args.outcome,
        }
        with open(
            os.path.join(data_dir, "wedge-settlements.jsonl"),
            "a",
            encoding="utf-8",
        ) as handle:
            json.dump(row, handle, separators=(",", ":"))
            handle.write("\n")
    except Exception as exc:
        print(f"fm-wedge-score settle: {exc}", file=sys.stderr)
    return 0


def _read_rows(home, lane):
    path = os.path.join(home, "data", "wedge-settlements.jsonl")
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


def _score(args):
    rows = _read_rows(_home(args.home), args.lane)
    healthy = [
        float(row["idle_secs"])
        for row in rows
        if row.get("outcome") == "resumed"
    ]
    n = len(healthy)
    n_total = len(rows)
    n_esc = sum(row.get("outcome") == "escalated" for row in rows)
    fixed_flag = args.idle_secs >= args.fixed_threshold
    if n < args.min_obs:
        result = {
            "lane": args.lane,
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
            "lane": args.lane,
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
    parser.add_argument("--home", default=os.environ.get("FM_HOME", "."))
    subparsers = parser.add_subparsers(dest="command", required=True)

    settle = subparsers.add_parser("settle")
    settle.add_argument("--home", default=None)
    settle.add_argument("--key", required=True)
    settle.add_argument("--window", required=True)
    settle.add_argument("--task", default="")
    settle.add_argument("--idle-secs", required=True, type=int)
    settle.add_argument("--outcome", choices=("resumed", "escalated"), required=True)
    settle.set_defaults(handler=_settle)

    score = subparsers.add_parser("score")
    score.add_argument("--home", default=None)
    score.add_argument("--lane", required=True)
    score.add_argument("--idle-secs", required=True, type=int)
    score.add_argument("--fixed-threshold", default=240, type=int)
    score.add_argument("--min-obs", default=8, type=int)
    score.set_defaults(handler=_score)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
