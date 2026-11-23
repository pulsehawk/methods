#!/usr/bin/env python3
"""weekly.py - weekly count of the Bitcoin Cash tempnet 2027 (one minute blocks), PH-M-007.

Input: a heads CSV (see FORMATS.md) that covers the tempnet from its first
block 327,989, the heads meta of the fetch, and optionally the result of an
outside cross check (JSON with `verglichen`, `gleich` or `fehler`).
Output: one JSON file with all numbers and a short Markdown report, both with
their window (from and to, UTC, block heights).

Fixed choices, on purpose in the code:
- Range: from 327,989 (first tempnet block) to the highest block whose header
  time is at or before the cutoff. The first counted interval is the one of
  327,990, because 327,988 is still a chipnet block.
- Interval: header time minus header time of the parent, as it is (zero and
  negative intervals stay in).
- MTP is the median of the header times of the last 11 blocks. The MTP lag of
  a block is its header time minus the MTP of the 11 blocks before it. Against
  the fetch time: fetch time minus MTP of the tip, and fetch time minus header
  time of the tip.
- Difficulty = target(powLimit 0x1d00ffff) / target(nBits), as getdifficulty.
- Difficulty tracking as in our Zcash NU7 report: windows of 100 blocks, a jump
  from factor 2 (confirmed by the next window), caught up when the difficulty
  is within 15 % of the hash rate of the next 100 blocks times 60 s. Blocks and
  minutes are measured over the same jumps.
- Expectation: exponential intervals with a mean of 60 s, their sums by the
  Erlang distribution, MTP values by simulation (fixed seed, 200,000 draws).

No price, no rating. Numbers only.

  python3 bch_tempnet/weekly.py --heads heads.csv.gz --meta heads-meta.json --cutoff 2026-11-15T12:00:00Z \\
      --json weekly.json --md weekly.md
  python3 bch_tempnet/weekly.py --selftest
"""
import argparse
import csv
import gzip
import json
import math
import random
import statistics
from collections import OrderedDict
from datetime import datetime, timezone

FIRST = 327_989
TARGET_S = 60.0
POW_LIMIT_BITS = 0x1D00FFFF
WINDOW = 100
JUMP = 2.0
NEAR = 0.15
LONG_S = 300          # "above 5 min"
N_WINDOW = 10         # 10 blocks against 1 block of 600 s
OLD_S = 600.0


def target(bits):
    exp, mant = bits >> 24, bits & 0x007FFFFF
    return mant >> (8 * (3 - exp)) if exp <= 3 else mant << (8 * (exp - 3))


def difficulty(bits):
    return target(POW_LIMIT_BITS) / target(bits)


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def quantile(s, q):
    s = sorted(s)
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def erlang_cdf(x, n, m):
    lam, term, total = x / m, math.exp(-x / m), 0.0
    for k in range(n):
        total += term
        term *= lam / (k + 1)
    return 1 - total


def erlang_q(q, n, m):
    lo, hi = 0.0, m * n * 20
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if erlang_cdf(mid, n, m) < q else (lo, mid)
    return (lo + hi) / 2


def stats(values):
    s = sorted(values)
    return {"count": len(s), "mean": round(sum(s) / len(s), 2), "median": quantile(s, 0.5),
            "p90": round(quantile(s, 0.9), 1), "p99": round(quantile(s, 0.99), 1), "max": s[-1]}


def mtp_simulation(n=200_000, seed=2027):
    """Expected MTP lag for ideal 60 s blocks (exponential intervals)."""
    rnd = random.Random(seed)
    lag, age_mtp, age_tip = [], [], []
    for _ in range(n):
        # 11 intervals before the block, header times relative to the block (0)
        t, times = 0.0, []
        for _ in range(11):
            t -= rnd.expovariate(1 / TARGET_S)
            times.append(t)
        lag.append(-statistics.median(times))
        # fetch at a random time: the age of the tip is exponential (memoryless)
        a = rnd.expovariate(1 / TARGET_S)
        age_tip.append(a)
        # MTP of the tip = median of the last 11 blocks including the tip
        last = [0.0] + times[:10]
        age_mtp.append(a - statistics.median(last))
    return {"lag_block": stats(lag), "fetch_minus_mtp": stats(age_mtp),
            "fetch_minus_tip": stats(age_tip), "draws": n, "seed": seed}


def tracking(heights, times, diffs, bits=None):
    """As in our Zcash NU7 report. The tempnet mixes blocks at minimum difficulty
    (nBits = powLimit) into the ASERT blocks. The hash rate comes from the work
    of all blocks; it is compared with the last valid ASERT difficulty (last
    block with nBits != powLimit), not with the single block."""
    W, n = WINDOW, len(heights)
    asert, last = [], None
    for i in range(n):
        if bits is None or bits[i] != POW_LIMIT_BITS:
            last = diffs[i]
        asert.append(last if last is not None else diffs[i])

    def rate(a, b):
        dt = times[b] - times[a]
        return sum(diffs[a + 1:b + 1]) / dt if dt > 0 else None

    def fits(j):
        if j + W >= n:
            return False
        h = rate(j, j + W)
        return bool(h) and abs(asert[j] / (h * TARGET_S) - 1) <= NEAR

    edges, b = [], W
    while b + 2 * W < n:
        before, after, later = rate(b - W, b), rate(b, b + W), rate(b + W, b + 2 * W)
        ok = (before and after and later and after != before
              and math.log(later / before) / math.log(after / before) >= 0.5)
        if ok and (after / before >= JUMP or before / after >= JUMP):
            edges.append((b, after / before))
            b += 2 * W
        else:
            b += W // 2
    ev = []
    for i, (b, f) in enumerate(edges):
        end = edges[i + 1][0] if i + 1 < len(edges) else n
        k = next((j for j in range(b, min(b + 2000, end)) if fits(j) and fits(j + W // 4)), None)
        ev.append({"edge_height": heights[b], "time_utc": utc(times[b]), "hash_rate_factor": round(f, 2),
                   "caught_up_after_blocks": (k - b) if k is not None else None,
                   "caught_up_after_min": round((times[k] - times[b]) / 60, 1) if k is not None else None,
                   "replaced": k is None and end < n and end < b + 2000,
                   "open_at_cutoff": k is None and end >= n and b + 2000 > n,
                   "not_within_2000": k is None and not (end < n and end < b + 2000) and b + 2000 <= n})
    up = [e for e in ev if e["caught_up_after_blocks"] is not None]
    return {
        "method": (f"windows of {W} blocks, jump from factor {JUMP} (confirmed), caught up when the ASERT "
                   f"difficulty is within {int(NEAR * 100)} % of the hash rate of the next {W} blocks times 60 s"),
        "jumps": len(ev), "caught_up": len(up),
        "replaced": sum(1 for e in ev if e["replaced"]),
        "open_at_cutoff": sum(1 for e in ev if e["open_at_cutoff"]),
        "not_within_2000_blocks": sum(1 for e in ev if e["not_within_2000"]),
        # blocks and minutes over the same caught up jumps
        "median_blocks": statistics.median([e["caught_up_after_blocks"] for e in up]) if up else None,
        "median_min": statistics.median([e["caught_up_after_min"] for e in up]) if up else None,
        "max_blocks": max((e["caught_up_after_blocks"] for e in up), default=None),
        "events": ev,
    }


def minimum_difficulty(heights, times, bits):
    """Blocks at minimum difficulty, per day and by the gap before them.
    An observation, not a rule check: we read headers only."""
    md = [i for i in range(1, len(bits)) if bits[i] == POW_LIMIT_BITS]
    norm = [i for i in range(1, len(bits)) if bits[i] != POW_LIMIT_BITS]
    days = OrderedDict()
    for i in range(len(bits)):
        day = datetime.fromtimestamp(times[i], timezone.utc).strftime("%Y-%m-%d")
        a = days.setdefault(day, [0, 0])
        a[0] += 1
        a[1] += bits[i] == POW_LIMIT_BITS
    first_asert = next((heights[i] for i in range(len(bits)) if bits[i] != POW_LIMIT_BITS), None)
    return {
        "count": sum(1 for b in bits if b == POW_LIMIT_BITS), "of": len(bits),
        "per_day": [{"day_utc": k, "blocks": v[0], "minimum": v[1]} for k, v in days.items()],
        "gap_before_above_120s": sum(1 for i in md if times[i] - times[i - 1] > 2 * TARGET_S),
        "gap_before_up_to_120s": sum(1 for i in md if times[i] - times[i - 1] <= 2 * TARGET_S),
        "max_gap_before_asert_block_s": max((times[i] - times[i - 1] for i in norm), default=None),
        "first_asert_block": first_asert,
    }


def per_day(heights, times, diffs, bits=None):
    days = OrderedDict()
    for i in range(len(heights)):
        days.setdefault(datetime.fromtimestamp(times[i], timezone.utc).strftime("%Y-%m-%d"), []).append(i)
    out = []
    for day, idx in sorted(days.items()):
        iv = [times[i] - times[i - 1] for i in idx if i > 0]
        out.append({"day_utc": day, "blocks": len(idx), "from": heights[min(idx)], "to": heights[max(idx)],
                    "mean_interval_s": round(sum(iv) / len(iv), 1) if iv else None,
                    # only blocks with ASERT difficulty (nBits != powLimit)
                    "difficulty_median": round(statistics.median(
                        [diffs[i] for i in idx if bits is None or bits[i] != POW_LIMIT_BITS] or [1.0]), 2)})
    return out


def software(agent):
    """Software name and version without the comment in brackets."""
    return agent.strip("/").split("(")[0].strip()


def table(head, rows):
    t = ["| " + " | ".join(head) + " |", "|" + "|".join(" --- " for _ in head) + "|"]
    return "\n".join(t + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows])


def num(x, nd=1):
    if x is None:
        return "-"
    if isinstance(x, int):
        return f"{x:,}"
    return f"{x:,.{nd}f}"


def report(r):
    B, A, E, M, F, N, G = (r["range"], r["intervals"], r["expected_intervals"], r["mtp"],
                          r["window_10"], r["tracking"], r.get("outside_cross_check"))
    src = r["source"]
    checks = src.get("cross_checks") or [{"software": g.get("agent", ""), "equal": g.get("gleich"),
                                          "compared": g.get("verglichen")} for g in src.get("gegenproben", [])]
    z = [f"# Bitcoin Cash tempnet 2027 (one minute blocks), count at {r['cutoff_utc']}", "",
         f"**Window:** {B['from_time_utc']} to {B['to_time_utc']} UTC, blocks {num(B['from_height'])} to "
         f"{num(B['to_height'])} ({num(B['blocks'])} blocks). **Fetch:** {r['fetched_utc']}.", "",
         "Source: our own headers over the peer to peer protocol, checked for linkage and work against nBits, "
         "first tempnet block 327,989 as fixed point, cross check with further peers: "
         + (", ".join(f"{software(c['software'])} {c['equal']}/{c['compared']}" for c in checks) or "none")
         + ". Peer software: " + ", ".join(sorted({software(s) for s in src.get("peer_software", [])})) + ".", "",
         "Outside cross check: " + (
             f"{G['gleich']} of {G['verglichen']} sampled hashes equal" if G and G.get("verglichen")
             else f"not available ({(G or {}).get('fehler', 'not run')})") + ".", "",
         "## Block intervals against 60 s", "",
         table(["", "counted", "expected (exponential, 60 s)"], [
             ["mean", f"{num(A['mean'], 2)} s", "60 s"],
             ["median", f"{num(float(A['median']))} s", f"{num(E['median'])} s"],
             ["p90", f"{num(A['p90'])} s", f"{num(E['p90'])} s"],
             ["p99", f"{num(A['p99'])} s", f"{num(E['p99'])} s"],
             ["above 5 min", f"{num(r['share_above_5min'] * 100, 2)} % ({num(r['count_above_5min'])})",
              f"{num(E['share_above_5min'] * 100, 2)} %"],
             ["longest gap", f"{num(A['max'])} s (height {num(r['longest_gap_height'])})",
              f"about {num(E['longest_gap'], 0)} s"],
             ["interval 0 / negative", f"{num(r['zero'])} / {num(r['negative'])}", ""]]), "",
         "## MTP lag (median of the last 11 blocks)", "",
         table(["", "counted", "expected (simulation, 60 s)"], [
             ["header time minus MTP before, median", f"{num(float(M['lag_block']['median']))} s",
              f"{num(float(M['expected']['lag_block']['median']))} s"],
             ["same, p90 / p99", f"{num(M['lag_block']['p90'])} / {num(M['lag_block']['p99'])} s",
              f"{num(M['expected']['lag_block']['p90'])} / {num(M['expected']['lag_block']['p99'])} s"],
             ["same, smallest value (consensus needs > 0)", f"{num(M['lag_block_min'])} s", ""],
             ["fetch time minus MTP of the tip", f"{num(M['fetch_minus_mtp'])} s",
              f"median {num(float(M['expected']['fetch_minus_mtp']['median']))} s, p90 {num(M['expected']['fetch_minus_mtp']['p90'])} s"],
             ["fetch time minus header time of the tip", f"{num(M['fetch_minus_tip'])} s",
              f"median {num(float(M['expected']['fetch_minus_tip']['median']))} s, p90 {num(M['expected']['fetch_minus_tip']['p90'])} s"]]),
         "", "The fetch time is one measurement per run; weekly runs collect more.", "",
         "## 10 blocks against 1 block of 600 s", "",
         table(["", "10 blocks counted", "10 x 60 s expected", "1 x 600 s expected"], [
             [q, f"{num(float(F['counted'][k]))} s", f"{num(F['expected_10x60'][k])} s", f"{num(F['expected_1x600'][k])} s"]
             for q, k in (("median", "median"), ("p90", "p90"), ("p99", "p99"))]),
         "", f"{num(F['counted']['count'])} overlapping windows.", "",
         "## Difficulty tracking", "",
         f"Method: {N['method']}. Jumps {N['jumps']}, caught up {N['caught_up']}, replaced by the next jump "
         f"{N['replaced']}, open at the cutoff {N['open_at_cutoff']}, not caught up within 2,000 blocks "
         f"{N['not_within_2000_blocks']}. Caught up after a median of "
         f"{num(N['median_blocks'])} blocks or {num(N['median_min'])} min (the same jumps), at most "
         f"{num(N['max_blocks'])} blocks.", ""]
    if N["events"]:
        z += [table(["height", "time UTC", "factor", "caught up after blocks", "min"], [
            [num(e["edge_height"]), e["time_utc"], num(e["hash_rate_factor"], 2),
             "replaced" if e["replaced"] else ("open" if e["open_at_cutoff"] else (
                 "not within 2,000" if e["not_within_2000"] else num(e["caught_up_after_blocks"]))),
             num(e["caught_up_after_min"])] for e in N["events"][:20]]), ""]
    mi = r["minimum"]
    z += ["## Per day (UTC)", "", table(["day", "blocks", "heights", "mean interval", "ASERT difficulty median"], [
        [t["day_utc"], num(t["blocks"]), f"{num(t['from'])} to {num(t['to'])}", f"{num(t['mean_interval_s'])} s",
         num(t["difficulty_median"], 2)] for t in r["per_day"]]), "",
          "## Blocks at minimum difficulty", "",
          f"{num(mi['count'])} of {num(mi['of'])} blocks carry nBits = powLimit (difficulty 1). "
          f"The first block with another difficulty is {num(mi['first_asert_block'])}. "
          f"Before {num(mi['gap_before_above_120s'])} of these blocks the gap was above 120 s, before "
          f"{num(mi['gap_before_up_to_120s'])} it was up to 120 s. The longest gap before a block with another "
          f"difficulty was {num(mi['max_gap_before_asert_block_s'])} s. An observation from the headers; we do not "
          "check the rule itself.", "",
          table(["day", "blocks", "at minimum difficulty"], [
              [x["day_utc"], num(x["blocks"]), num(x["minimum"])] for x in mi["per_day"]]), "",
          "## Not measured", "",
          "- Block contents and the ASERT rule itself (headers only, linkage, work against nBits).",
          "- The tracking method of the Zcash report assumes that every block carries the running difficulty. "
          "With blocks at minimum difficulty in between, the balance ASERT difficulty = hash rate x 60 s does not "
          "hold, so the numbers there have limited meaning.",
          "- Stale blocks and arrival times (no listener, no own node in this count).", ""]
    return "\n".join(z)


def read_heads(path):
    with gzip.open(path, "rt") as f:
        rows = []
        for z in csv.DictReader(f):
            rows.append((int(z.get("hoehe") or z["height"]), int(z.get("zeit") or z["time"]), int(z["nbits"], 16)))
    return sorted(rows)


def analyse(rows, meta, cutoff, cross=None):
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    st = int(datetime.strptime(cutoff, fmt).replace(tzinfo=timezone.utc).timestamp())
    fetched = meta.get("fetched_utc") or meta["abruf_utc"]
    fetch_t = int(datetime.strptime(fetched, fmt).replace(tzinfo=timezone.utc).timestamp())
    for i in range(1, len(rows)):
        assert rows[i][0] == rows[i - 1][0] + 1, "gap in the heights"
    ab = next(i for i, r in enumerate(rows) if r[0] == FIRST)
    to = max(i for i, r in enumerate(rows) if r[1] <= st)
    sel = rows[ab:to + 1]
    h = [r[0] for r in sel]
    t = [r[1] for r in sel]
    d = [difficulty(r[2]) for r in sel]
    iv = [t[i] - t[i - 1] for i in range(1, len(t))]
    A = stats(iv)
    n = len(iv)
    E = {"mean": TARGET_S, "median": round(TARGET_S * math.log(2), 1), "p90": round(TARGET_S * math.log(10), 1),
         "p99": round(TARGET_S * math.log(100), 1), "share_above_5min": round(math.exp(-LONG_S / TARGET_S), 5),
         "longest_gap": round(TARGET_S * (math.log(n) + 0.5772), 1)}

    # MTP lag: block i against the median of the 11 blocks before it (chipnet blocks before the branch count too)
    all_t = [r[1] for r in rows[:to + 1]]
    off = ab
    lag = [all_t[off + i] - statistics.median(all_t[off + i - 11:off + i]) for i in range(len(t)) if off + i >= 11]
    mtp_tip = statistics.median([r[1] for r in rows[-11:]])
    M = {"lag_block": stats(lag), "lag_block_min": min(lag),
         "fetch_minus_mtp": fetch_t - mtp_tip, "fetch_minus_tip": fetch_t - rows[-1][1],
         "tip_at_fetch": rows[-1][0], "expected": mtp_simulation()}

    sp = sorted(t[i + N_WINDOW] - t[i] for i in range(len(t) - N_WINDOW))
    F = {"counted": stats(sp),
         "expected_10x60": {k: round(erlang_q(q, N_WINDOW, TARGET_S), 1) for k, q in (("median", .5), ("p90", .9), ("p99", .99))},
         "expected_1x600": {k: round(-OLD_S * math.log(1 - q), 1) for k, q in (("median", .5), ("p90", .9), ("p99", .99))}}

    i_max = max(range(n), key=lambda i: iv[i])
    bits = [r_[2] for r_ in sel]
    return {
        "title": "Bitcoin Cash tempnet 2027, independent count",
        "cutoff_utc": datetime.fromtimestamp(st, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "range": {"from_height": h[0], "to_height": h[-1], "blocks": len(h),
                  "from_time_utc": utc(t[0]), "to_time_utc": utc(t[-1])},
        "fetched_utc": fetched,
        "source": meta,
        "intervals": A, "expected_intervals": E,
        "share_above_5min": round(sum(1 for x in iv if x > LONG_S) / n, 5),
        "count_above_5min": sum(1 for x in iv if x > LONG_S),
        "longest_gap_height": h[i_max + 1],
        "zero": sum(1 for x in iv if x == 0), "negative": sum(1 for x in iv if x < 0),
        "mtp": M, "window_10": F,
        "tracking": tracking(h, t, d, bits),
        "minimum": minimum_difficulty(h, t, bits),
        "per_day": per_day(h, t, d, bits),
        "minimum_difficulty_blocks": sum(1 for b in bits if b == POW_LIMIT_BITS),
        "outside_cross_check": cross,
    }


def selftest():
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    ok(difficulty(0x1D00FFFF) == 1.0, "difficulty of powLimit is 1")
    ok(abs(difficulty(0x1D009691) - 0xFFFF / 0x9691) < 1e-12, "difficulty of 1d009691")
    ok(abs(erlang_q(0.5, 1, 60) - 60 * math.log(2)) < 1e-6, "Erlang with one stage is exponential")
    ok(abs(erlang_cdf(erlang_q(0.9, 10, 60), 10, 60) - 0.9) < 1e-9, "Erlang quantile inverts the cdf")
    ok(quantile([1, 2, 3, 4], 0.5) == 2.5, "quantile interpolates")
    sim = mtp_simulation(n=2000)
    ok(sim == mtp_simulation(n=2000), "simulation is repeatable with its seed")
    # a chain with an even 60 s and one jump of the hash rate by 4 that the difficulty follows after 300 blocks
    hs, ts, ds = [], [], []
    t = 0
    for i in range(1500):
        rate_ = 1.0 if i < 600 else 4.0
        diff = 1.0 if i < 900 else 4.0
        t += round(60 * diff / rate_)
        hs.append(1000 + i)
        ts.append(t)
        ds.append(diff)
    tr = tracking(hs, ts, ds)
    ok(tr["jumps"] == 1 and tr["events"][0]["edge_height"] == 1600, "one jump found at its height")
    ok(tr["caught_up"] == 1 and 250 <= tr["median_blocks"] <= 320, "caught up near the difficulty change")
    md = minimum_difficulty([1, 2, 3, 4], [0, 200, 260, 300], [0x1C00FFFF, POW_LIMIT_BITS, POW_LIMIT_BITS, 0x1C00FFFF])
    ok(md["count"] == 2 and md["gap_before_above_120s"] == 1 and md["gap_before_up_to_120s"] == 1,
       "minimum difficulty blocks by gap")
    ok(md["first_asert_block"] == 1 and md["max_gap_before_asert_block_s"] == 40, "first ASERT block and gap")
    ok(software("/Bitcoin Cash Node:29.2.1(some comment)/") == "Bitcoin Cash Node:29.2.1", "software drops the comment")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Weekly count of the Bitcoin Cash tempnet 2027, PH-M-007.")
    ap.add_argument("--heads")
    ap.add_argument("--meta")
    ap.add_argument("--cutoff", help="YYYY-MM-DDTHH:MM:SSZ")
    ap.add_argument("--cross-check", help="optional JSON of an outside cross check")
    ap.add_argument("--json")
    ap.add_argument("--md")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.heads and a.meta and a.cutoff and a.json and a.md):
        ap.error("--heads, --meta, --cutoff, --json and --md are needed")
    with open(a.meta) as f:
        meta = json.load(f)
    cross = None
    if a.cross_check:
        with open(a.cross_check) as f:
            cross = json.load(f)
    r = analyse(read_heads(a.heads), meta, a.cutoff, cross)
    with open(a.json, "w") as f:
        json.dump(r, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(a.md, "w") as f:
        f.write(report(r))
    print(json.dumps({"blocks": r["range"]["blocks"], "from": r["range"]["from_height"], "to": r["range"]["to_height"],
                      "mean": r["intervals"]["mean"], "median": r["intervals"]["median"]}))


if __name__ == "__main__":
    main()
