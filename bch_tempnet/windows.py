#!/usr/bin/env python3
"""windows.py - windows of hash entering and leaving the Bitcoin Cash tempnet, PH-M-008.

From our own headers (checked for linkage and work when fetched) and,
optionally, the records of our listener.

Difficulty per header from nBits: difficulty = target at 0x1d00ffff / target
from nBits.

Window: work rate over 10 blocks = sum of the difficulty of the last 10
blocks / time between the header before them and the last one (header times).
A block belongs to a hash window when this rate reaches THRESHOLD (5
difficulty per second), ten times the highest hourly rate outside such windows
(about 0.5), or when the 10 blocks came within 60 s, ten times the target
rate. The second condition catches the ramp at the start of an entry, when the
blocks already come fast but the difficulty is still low. Runs less than 30 min
apart are merged. Entry is the header time of the first block, exit the header
time of the last block of the run. The edges are the first and last block whose
own interval alone reaches the threshold.

Comparison (--compare): a CSV height,difficulty from someone else, read as
data only. We count for how many common heights the difficulty differs
(relatively more than 1e-9).

Stale blocks (with --blocks, --summary and --runs): from the listener summary
(stale after 6 confirmations). A stale block belongs to a hash window by its
height (between the first and last block of the window), to a list of hours
(--hours) by its first announcement at our listener. Share = stale / (announced
chain blocks + stale) in the same range, chain blocks from the blocks CSV. Where
the listener did not run, nothing is counted; the coverage per window is given.

  python3 bch_tempnet/windows.py --heads heads.csv.gz [--blocks bloecke.csv --summary zusammenfassung.json \\
      --runs RUN_DIR...] [--hours hours.csv] [--compare difficulty.csv] --out windows.json
  python3 bch_tempnet/windows.py --selftest
"""
import argparse
import csv
import gzip
import json
import os
from datetime import datetime, timezone

THRESHOLD = 5.0     # difficulty per second
N_RATE = 10         # blocks for the work rate
GAP_S = 1800        # merge runs closer than this
RAMP_S = 60         # 10 blocks within 60 s, ten times the target rate (60 s per block)
TARGET_1 = 0xffff * (1 << (8 * (0x1d - 3)))


def difficulty(nbits):
    n = int(nbits, 16)
    e, m = n >> 24, n & 0xffffff
    return TARGET_1 / (m * (1 << (8 * (e - 3))) if e >= 3 else m >> (8 * (3 - e)))


def ts(text):
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_windows(heads):
    """heads: list (height, time, difficulty) by height. Returns runs [(i_from, i_to)]."""
    inside = []
    for i in range(N_RATE, len(heads)):
        dt = heads[i][1] - heads[i - N_RATE][1]
        work = sum(k[2] for k in heads[i - N_RATE + 1:i + 1])
        if work / max(dt, 1) >= THRESHOLD or dt <= RAMP_S:
            inside.append(i)
    runs = []
    for i in inside:
        if runs and heads[i][1] - heads[runs[-1][1]][1] <= GAP_S:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    # The rate over 10 blocks reaches past the edges. So the edge moves to the
    # first and last block whose own interval alone reaches the threshold
    # (difficulty / interval to the parent, at least 1 s).
    def fast(j):
        if j == 0:
            return False
        dt = max(heads[j][1] - heads[j - 1][1], 1)
        return heads[j][2] / dt >= THRESHOLD or dt <= RAMP_S / N_RATE
    edges = []
    for a, b in runs:
        cand = [j for j in range(max(a - N_RATE + 1, 1), b + 1) if fast(j)]
        if cand:
            edges.append((cand[0], cand[-1]))
    return edges


def coverage(runs, start, end):
    """Seconds in [start, end) that a listener run covers."""
    pieces = sorted((max(a, start), min(b, end)) for a, b in runs if b > start and a < end)
    total, last = 0, start
    for a, b in pieces:
        a = max(a, last)
        if b > a:
            total += b - a
            last = b
    return total


def share(chain, stale):
    return {"chain_blocks": chain, "stale": stale,
            "share_pct": round(100 * stale / (chain + stale), 3) if chain + stale else None}


def read_heads(path):
    heads = []
    with gzip.open(path, "rt") as f:
        for z in csv.DictReader(f):
            heads.append((int(z.get("hoehe") or z["height"]), int(z.get("zeit") or z["time"]), difficulty(z["nbits"])))
    return sorted(heads)


def run_times(run_dirs):
    """(start, end) of each listener run from its meta.json (keys start_utc, ende_utc)."""
    out = []
    for d in run_dirs:
        p = os.path.join(d, "meta.json")
        if os.path.exists(p):
            with open(p) as f:
                m = json.load(f)
            if m.get("start_utc") and m.get("ende_utc"):
                out.append((ts(m["start_utc"]), ts(m["ende_utc"])))
    return out


def analyse(heads, blocks=None, stale=None, runs=(), hours=(), compare=None):
    out = {"heads": {"from": heads[0][0], "to": heads[-1][0], "count": len(heads)},
           "rule": f"work rate over {N_RATE} blocks at least {THRESHOLD} difficulty per second or {N_RATE} blocks within "
                   f"{RAMP_S} s, runs less than {GAP_S // 60} min apart merged"}
    if compare is not None:
        common = [(h, d) for h, _, d in heads if h in compare]
        diff = [h for h, d in common if abs(d - compare[h]) > 1e-9 * max(1.0, d)]
        out["comparison"] = {
            "heights_there": [min(compare), max(compare)], "common_heights": len(common),
            "differences": len(diff), "first_differences": diff[:10],
            "only_there": len([h for h in compare if h < heads[0][0] or h > heads[-1][0]])}
    with_listener = blocks is not None and stale is not None
    seen_h = [int(b["height"]) for b in blocks if b.get("first_seen_utc")] if with_listener else []
    stale_h = [int(s["height"]) for s in stale if s.get("height") not in (None, "")] if with_listener else []
    win = []
    for a, b in find_windows(heads):
        h1, h2 = heads[a][0], heads[b][0]
        t1, t2 = heads[a][1], heads[b][1]
        dmax = max(k[2] for k in heads[a:b + 1])
        w = {"entry_utc": iso(t1), "exit_utc": iso(t2), "heights": [h1, h2], "blocks": h2 - h1 + 1,
             "difficulty_max": round(dmax, 1)}
        if with_listener:
            k = sum(1 for h in seen_h if h1 <= h <= h2)
            s = sum(1 for h in stale_h if h1 <= h <= h2)
            cov = coverage(runs, t1, t2 + 1)
            w["listener_coverage_pct"] = round(100 * cov / max(t2 + 1 - t1, 1), 1)
            w.update(share(k, s))
        win.append(w)
    out["hash_windows"] = win
    if not with_listener:
        return out
    in_k = sum(w["chain_blocks"] for w in win)
    in_s = sum(w["stale"] for w in win)
    all_k, all_s = len(seen_h), len(stale_h)
    out["inside_and_outside"] = {"in_hash_windows": share(in_k, in_s),
                                 "outside": share(all_k - in_k, all_s - in_s),
                                 "stale_without_height": len(stale) - len(stale_h)}
    if hours:
        hr = []
        seen_t = [ts(b["first_seen_utc"]) for b in blocks if b.get("first_seen_utc")]
        stale_t = [ts(s["first_seen_utc"]) for s in stale if s.get("first_seen_utc")]
        for v, b in hours:
            a, e = ts(v), ts(b)
            hr.append({"from_utc": v, "to_utc": b, "listener_coverage_pct": round(100 * coverage(runs, a, e) / (e - a), 1),
                       **share(sum(1 for t in seen_t if a <= t < e), sum(1 for t in stale_t if a <= t < e))})
        rk, rs = sum(r["chain_blocks"] for r in hr), sum(r["stale"] for r in hr)
        out["hours"] = {"windows": hr, "together": share(rk, rs),
                        "rest": share(len(seen_t) - rk, len(stale_t) - rs)}
    return out


def selftest():
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    ok(abs(difficulty("1d00ffff") - 1.0) < 1e-12, "difficulty of 1d00ffff is 1")
    ok(abs(difficulty("1d009691") - 0xffff / 0x9691) < 1e-12, "difficulty of 1d009691")
    ok(abs(difficulty("1b0404cb") - 16307.420938523983) < 1e-6, "difficulty of 1b0404cb")
    # 200 quiet blocks every 60 s at difficulty 2, then 100 blocks every 6 s at 1000, then quiet
    k, t, h = [], 0, 1000
    for _ in range(200):
        t += 60; k.append((h, t, 2.0)); h += 1
    for _ in range(100):
        t += 6; k.append((h, t, 1000.0)); h += 1
    for _ in range(200):
        t += 600; k.append((h, t, 1.0)); h += 1
    f = find_windows(k)
    ok(len(f) == 1, "one window")
    ok(f and k[f[0][0]][0] == 1200, "entry at the first fast block")
    ok(f and 1299 <= k[f[0][1]][0] <= 1301, "exit at the end of the fast blocks or just after")
    k2, t, h = [], 0, 1000
    for _ in range(200):
        t += 60; k2.append((h, t, 2.0)); h += 1
    for _ in range(50):
        t += 3; k2.append((h, t, 1.0)); h += 1
    for _ in range(200):
        t += 60; k2.append((h, t, 2.0)); h += 1
    f2 = find_windows(k2)
    ok(len(f2) == 1 and k2[f2[0][0]][0] == 1200, "ramp at low difficulty is found")
    ok(coverage([(0, 50)], 0, 100) == 50, "coverage half")
    ok(coverage([(0, 60), (40, 100)], 0, 100) == 100, "coverage overlapping")
    blocks = [{"height": str(x), "first_seen_utc": iso(k[x - 1000][1])} for x in range(1000, 1500)]
    stale = [{"height": 1250, "first_seen_utc": iso(k[250][1])}, {"height": 1100, "first_seen_utc": iso(k[100][1])}]
    r = analyse(k, blocks, stale, [(0, 10 ** 7)], hours=[(iso(k[0][1]), iso(k[150][1]))], compare={1000: 2.0, 1001: 3.0})
    ok(r["hash_windows"][0]["stale"] == 1 and r["inside_and_outside"]["outside"]["stale"] == 1, "stale inside and outside")
    ok(r["hours"]["windows"][0]["stale"] == 1 and r["hours"]["windows"][0]["chain_blocks"] == 150, "stale by hours")
    ok(r["comparison"]["common_heights"] == 2 and r["comparison"]["differences"] == 1, "comparison counts a difference")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Hash windows of the Bitcoin Cash tempnet, PH-M-008.")
    ap.add_argument("--heads")
    ap.add_argument("--blocks", help="blocks CSV of the listener")
    ap.add_argument("--summary", help="summary JSON of the listener (stale blocks)")
    ap.add_argument("--runs", nargs="*", default=[], help="listener run folders with meta.json")
    ap.add_argument("--hours", help="CSV from_utc,to_utc of hours to compare, [from, to)")
    ap.add_argument("--compare", help="CSV height,difficulty from someone else")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.heads or not a.out:
        ap.error("--heads and --out are needed")
    compare = None
    if a.compare:
        with open(a.compare, newline="") as f:
            compare = {int(z["height"]): float(z["difficulty"]) for z in csv.DictReader(f)}
    hours = []
    if a.hours:
        with open(a.hours, newline="") as f:
            hours = [(z["from_utc"], z["to_utc"]) for z in csv.DictReader(f)]
    blocks = stale = None
    if a.blocks and a.summary:
        with open(a.blocks, newline="") as f:
            blocks = list(csv.DictReader(f))
        with open(a.summary) as f:
            stale = json.load(f)["stale"]["bloecke"]
    out = analyse(read_heads(a.heads), blocks, stale, run_times(a.runs), hours, compare)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(json.dumps({"hash_windows": len(out["hash_windows"]), "heads": out["heads"]}))


if __name__ == "__main__":
    main()
