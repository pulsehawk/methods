#!/usr/bin/env python3
"""conf_wait.py - counted waiting time for k confirmations, beside the computed one.

Reads a heads CSV (columns hoehe,hash,zeit,nbits,version, gzip, as written by
headers/ph_headers.py) and counts, for every block h in the window, the time
from block h-1 until k more blocks have followed (block h+k-1), by header
time. "Usual" is the median, "9 of 10" the 90th percentile, "99 of 100" the
99th. Beside it stands the computed value for an ideal chain with exponential
intervals at the target spacing (Erlang distribution with k stages).

Header times are set by miners and are not strictly increasing, so a single
interval can be negative. We keep those intervals as they are and report how
many there are. The window is closed: a wait counts only if its last block
also lies in the CSV.

  python3 tools/conf_wait.py --heads heads.csv.gz --target 600 --k 1 3 6 --from-height 951146 --json wait.json
  python3 tools/conf_wait.py --selftest
"""
import argparse
import csv
import gzip
import json
import math


def quantile(values, p):
    """Linear interpolation between order statistics (numpy default)."""
    s = sorted(values)
    pos = (len(s) - 1) * p
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def erlang_cdf(x, k, mean):
    lam = x / mean
    return 1 - math.exp(-lam) * sum(lam ** i / math.factorial(i) for i in range(k))


def erlang_quantile(p, k, mean):
    lo, hi = 0.0, mean * (k + 20) * 4
    for _ in range(200):
        mid = (lo + hi) / 2
        if erlang_cdf(mid, k, mean) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def read_heads(path):
    with gzip.open(path, "rt") as f:
        return {int(r["hoehe"]): int(r["zeit"]) for r in csv.DictReader(f)}


def waits(times, k, first=None, last=None):
    """Waits for k confirmations, for every h with h-1 and h+k-1 present."""
    hs = sorted(times)
    first = hs[0] + 1 if first is None else first
    last = hs[-1] if last is None else last
    out = []
    for h in range(first, last - k + 2):
        if h - 1 in times and h + k - 1 in times:
            out.append(times[h + k - 1] - times[h - 1])
    return out


def summary(times, ks, target, first=None, last=None, ps=(0.5, 0.9, 0.99)):
    res = {"target_s": target, "k": {}}
    one = waits(times, 1, first, last)
    res["intervals"] = len(one)
    res["negative_intervals"] = sum(1 for v in one if v < 0)
    res["mean_interval_s"] = round(sum(one) / len(one), 2) if one else None
    for k in ks:
        w = waits(times, k, first, last)
        res["k"][str(k)] = {
            "n": len(w),
            "counted_s": {str(p): round(quantile(w, p), 1) for p in ps} if w else {},
            "computed_s": {str(p): round(erlang_quantile(p, k, target), 1) for p in ps},
        }
    return res


def selftest():
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    ok(abs(erlang_quantile(0.5, 1, 600) - 600 * math.log(2)) < 0.01, "median of one interval is 600 ln 2")
    ok(round(erlang_quantile(0.5, 6, 600)) == 3402, "median of six intervals at 600 s")
    ok(round(erlang_quantile(0.9, 1, 600)) == 1382, "p90 of one interval is 600 ln 10")
    t = {h: h * 600 for h in range(100, 121)}
    ok(waits(t, 1) == [600] * 20, "even chain, one confirmation")
    ok(waits(t, 6) == [3600] * 15, "even chain, six confirmations, closed window")
    t2 = dict(t)
    t2[110] = t[109] - 30
    s = summary(t2, [1], 600)
    ok(s["negative_intervals"] == 1, "negative interval is counted")
    ok(quantile([1, 2, 3, 4], 0.5) == 2.5, "quantile interpolates")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Counted and computed waiting time for k confirmations.")
    ap.add_argument("--heads", help="heads CSV (gzip)")
    ap.add_argument("--target", type=float, help="target block spacing in seconds")
    ap.add_argument("--k", type=int, nargs="+", default=[1, 3, 6])
    ap.add_argument("--from-height", type=int, help="first block h of the window (default: second row)")
    ap.add_argument("--to-height", type=int, help="last block that may end a wait (default: last row)")
    ap.add_argument("--json", help="write the result here")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.heads or not a.target:
        ap.error("--heads and --target are needed")
    times = read_heads(a.heads)
    res = summary(times, a.k, a.target, a.from_height, a.to_height)
    hs = sorted(times)
    res["heads_first"], res["heads_last"] = hs[0], hs[-1]
    res["window"] = {"from_height": a.from_height or hs[0] + 1, "to_height": a.to_height or hs[-1]}
    text = json.dumps(res, indent=2) + "\n"
    if a.json:
        with open(a.json, "w") as f:
            f.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
