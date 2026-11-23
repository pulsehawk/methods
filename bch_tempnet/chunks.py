#!/usr/bin/env python3
"""chunks.py - the Bitcoin Cash tempnet in chunks of 120 blocks, PH-M-008.

Per chunk, from our own headers (nBits, header times) and our listener:
  chunk_n, first and last height, start (header time of the first block, UTC),
  mean difficulty, mean and median block time (header time minus header time
  of the parent, per block of the chunk), stale blocks in the chunk (by
  height), stale events (a height with one or more candidates counts once),
  listener coverage in percent of the time from the start to the header time
  of the last block. Without full coverage (below 99.9 %) the chunk is marked
  and the stale values stay empty, not zero.

Variants:
- --no-diff1-csv: the same chunks without blocks at difficulty 1, columns as
  the first CSV plus block_count (see chunks()). Stale blocks follow the branch
  rule (branch_filter()); for it the script reads the headers the listener
  recorded per run.
- --slot-csv and --slot-nomin-csv: the slot count (slot_count() and
  slot_chunks()), all blocks and without difficulty 1.

  python3 bch_tempnet/chunks.py --heads heads.csv.gz --summary zusammenfassung.json --runs RUN_DIR... \\
      --csv chunks.csv [--no-diff1-csv chunks-no-diff1.csv] [--slot-csv slot.csv --slot-nomin-csv slot-nomin.csv] \\
      --out chunks.json
  python3 bch_tempnet/chunks.py --selftest
"""
import argparse
import csv
import gzip
import json
import os
import statistics
from collections import Counter
from datetime import datetime, timezone

CHUNK = 120
FULL = 99.9
TARGET_1 = 0xffff * (1 << (8 * (0x1d - 3)))
COLUMNS = ["chunk_n", "first_height", "last_height", "start_utc", "mean_difficulty", "mean_block_time_s",
           "median_block_time_s", "stale_blocks", "stale_events", "listener_coverage_pct", "full_coverage"]
SLOT_COLUMNS = ["chunk_n", "first_height", "last_height", "start_utc", "mean_difficulty", "mean_block_time_s",
                "median_block_time_s", "block_count", "stale_events", "stale_forks", "stale_blocks",
                "stale_max_fork_depth", "listener_coverage_pct", "full_coverage"]


def difficulty(nbits):
    n = int(nbits, 16)
    e, m = n >> 24, n & 0xffffff
    return TARGET_1 / (m * (1 << (8 * (e - 3))))


def ts(text):
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def coverage(runs, start, end):
    if end <= start:
        return 100.0 if any(a <= start <= b for a, b in runs) else 0.0
    pieces = sorted((max(a, start), min(b, end)) for a, b in runs if b > start and a < end)
    total, last = 0.0, start
    for a, b in pieces:
        a = max(a, last)
        if b > a:
            total += b - a
            last = b
    return 100.0 * total / (end - start)


def diff1(d):
    return abs(d - 1.0) < 1e-9


def chunks(heads, stale_heights, runs, no_diff1=False):
    """heads: list (height, time, difficulty) by height, without gaps.

    no_diff1: the chunks stay the same 120 heights with the same chunk_n, but
    blocks at difficulty 1 (nBits 1d00ffff) do not count. block_count is the
    number of the other blocks. Mean difficulty and block times hold for them
    only (block time is still header time minus header time of the parent,
    also when the parent has difficulty 1).

    Stale with no_diff1: the stale heights arrive already filtered by
    branch_filter() and count on all heights of the chunk, also where the chain
    block has difficulty 1, because the rule applies to the whole branch."""
    by_h = {h: (t, d) for h, t, d in heads}
    stale = Counter(stale_heights)
    rows = []
    for n, i in enumerate(range(0, len(heads), CHUNK), 1):
        part = heads[i:i + CHUNK]
        if len(part) < CHUNK:
            break  # the last, incomplete chunk is dropped
        h1, h2 = part[0][0], part[-1][0]
        kept = [k for k in part if not (no_diff1 and diff1(k[2]))]
        iv = [t - by_h[h - 1][0] for h, t, _ in kept if h - 1 in by_h]
        cov = coverage(runs, part[0][1], part[-1][1])
        full = cov >= FULL
        all_h = [h for h, _, _ in part]
        s_bl = sum(stale[h] for h in all_h)
        s_ev = sum(1 for h in all_h if stale[h])
        row = {"chunk_n": n, "first_height": h1, "last_height": h2, "start_utc": iso(part[0][1]),
               "mean_difficulty": round(sum(d for _, _, d in kept) / len(kept), 3) if kept else "",
               "mean_block_time_s": round(sum(iv) / len(iv), 2) if iv else "",
               "median_block_time_s": statistics.median(iv) if iv else "",
               "stale_blocks": s_bl if full else "", "stale_events": s_ev if full else "",
               "listener_coverage_pct": round(cov, 1), "full_coverage": "yes" if full else "no"}
        if no_diff1:
            row["block_count"] = len(kept)
        rows.append(row)
    return rows


def branch_filter(stale, header, chain):
    """Branch rule: leave out an entire forked branch if the main block or its
    sibling has difficulty 1.

    stale: list (height, hash) of the stale blocks. header: hash -> (prev,
    nbits) from the recorded headers. chain: height -> difficulty of the chain.
    Stale blocks linked by prev form a branch. The branch starts at its lowest
    height (fork point). It is left out entirely when the chain block there or
    a stale block of the branch at that height has difficulty 1. Without the
    header of a stale block at the fork point its difficulty is unknown; the
    branch stays and is counted as without_header.
    Returns (stale heights that stay, numbers)."""
    by_hash = {x: h for h, x in stale}
    parent = {}
    for h, x in stale:
        p = (header.get(x) or (None, None))[0]
        parent[x] = p if p in by_hash else None

    def root(x):
        while parent.get(x):
            x = parent[x]
        return x
    branches = {}
    for h, x in stale:
        branches.setdefault(root(x), []).append((h, x))
    kept, dropped, no_header, dropped_blocks, wider = [], 0, 0, 0, 0
    for r, members in branches.items():
        fork = min(h for h, _ in members)
        at_fork = [x for h, x in members if h == fork]
        unknown = any(x not in header for x in at_fork)
        d1 = diff1(chain.get(fork, 0)) or any(x in header and diff1(difficulty(header[x][1])) for x in at_fork)
        # as a check: the wider reading, difficulty 1 anywhere in the branch
        if not d1 and any(diff1(chain.get(h, 0)) or (x in header and diff1(difficulty(header[x][1]))) for h, x in members):
            wider += 1
        if d1:
            dropped += 1
            dropped_blocks += len(members)
            continue
        no_header += unknown
        kept += [h for h, _ in members]
    return kept, {"branches": len(branches), "branches_dropped": dropped, "stale_blocks_dropped": dropped_blocks,
                  "branches_without_header_at_fork": no_header, "stale_blocks_kept": len(kept),
                  "branches_different_in_wider_reading": wider}


def run_headers(run_dirs):
    """hash -> (prev, nbits) from the headers each listener run recorded (koepfe.jsonl.gz)."""
    header = {}
    for d in run_dirs:
        p = os.path.join(d, "koepfe.jsonl.gz")
        if not os.path.exists(p):
            continue
        with gzip.open(p, "rt") as f:
            for z in f:
                k = json.loads(z)
                if k.get("hash") and k.get("nbits"):
                    header[k["hash"]] = (k.get("prev"), k["nbits"])
    return header


def slot_count(stale, header, chain, nomin):
    """Slot count, rebuilt from our records.

    stale: list (height, hash). header: hash -> (prev, nbits). chain: height ->
    (hash, difficulty) of the chain. A fork is a tip: a stale block that is not
    the parent of another stale block. From the tip we go back over prev to the
    slot F+1, the first block after the chain. Every count belongs to the slot.
    With nomin a fork counts only when the chain block at the slot and its own
    slot block have difficulty above 1, and it ends before its first block at
    difficulty 1. Blocks are deduplicated by hash over everything. Without a
    header the fork stays uncut (kept as unknown).
    Returns (slot -> {forks, blocks, depths}, numbers)."""
    height = {x: h for h, x in stale}
    parent = {x: (header.get(x) or (None, None))[0] for _, x in stale}
    has_child = {p for p in parent.values() if p in height}
    seen, slots = set(), {}
    num = {"tips": 0, "main_min": 0, "sibling_min": 0, "cut": 0, "without_header": 0}
    for h, x in sorted(stale):
        if x in has_child:
            continue
        num["tips"] += 1
        path = [(h, x)]                    # from the tip back to the slot
        while parent.get(path[-1][1]) in height:
            p = parent[path[-1][1]]
            path.append((height[p], p))
        path.reverse()                     # slot first
        slot = path[0][0]
        if nomin:
            if diff1(chain.get(slot, (None, 0))[1]):
                num["main_min"] += 1
                continue
            cut = None
            for i, (hh, xx) in enumerate(path):
                if xx not in header:
                    num["without_header"] += 1
                    break
                if diff1(difficulty(header[xx][1])):
                    cut = i
                    break
            if cut == 0:
                num["sibling_min"] += 1
                continue
            if cut is not None:
                path = path[:cut]
                num["cut"] += 1
        s = slots.setdefault(slot, {"forks": 0, "blocks": 0, "depths": []})
        s["forks"] += 1
        s["depths"].append(len(path))
        for _, xx in path:
            if xx not in seen:
                seen.add(xx)
                s["blocks"] += 1
    return slots, num


def slot_chunks(heads, slots, runs, nomin):
    """Chunks for the slot count: block time as the gap between consecutive
    counted blocks in the chunk (the first block has no gap), with nomin
    without blocks at difficulty 1; stale per slot."""
    rows = []
    for n, i in enumerate(range(0, len(heads), CHUNK), 1):
        part = heads[i:i + CHUNK]
        if len(part) < CHUNK:
            break
        kept = [k for k in part if not (nomin and diff1(k[2]))]
        iv = [b[1] - a[1] for a, b in zip(kept, kept[1:])]
        cov = coverage(runs, part[0][1], part[-1][1])
        full = cov >= FULL
        sl = [slots[h] for h, _, _ in part if h in slots]
        rows.append({"chunk_n": n, "first_height": part[0][0], "last_height": part[-1][0], "start_utc": iso(part[0][1]),
                     "mean_difficulty": round(sum(d for _, _, d in kept) / len(kept), 3) if kept else "",
                     "mean_block_time_s": round(sum(iv) / len(iv), 2) if iv else "",
                     "median_block_time_s": statistics.median(iv) if iv else "",
                     "block_count": len(kept),
                     "stale_events": len(sl) if full else "", "stale_forks": sum(s["forks"] for s in sl) if full else "",
                     "stale_blocks": sum(s["blocks"] for s in sl) if full else "",
                     "stale_max_fork_depth": max([d for s in sl for d in s["depths"]], default=0) if full else "",
                     "listener_coverage_pct": round(cov, 1), "full_coverage": "yes" if full else "no"})
    return rows


def selftest():
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    k = [(1000 + i, 1000 * 60 + 60 * i, 1.0) for i in range(250)]
    z = chunks(k, [1005, 1005, 1007, 1200], [(0, 70000)])
    ok(len(z) == 2, "two full chunks, the rest is dropped")
    ok(z[0]["stale_blocks"] == 3, "3 stale blocks in the first")
    ok(z[0]["stale_events"] == 2, "2 stale events in the first")
    ok(z[0]["median_block_time_s"] == 60 and z[1]["mean_block_time_s"] == 60, "block time 60")
    z2 = chunks(k, [1005], [(0, 60000 + 60 * 50)])
    ok(z2[0]["full_coverage"] == "no" and z2[0]["stale_blocks"] == "", "partial coverage marked and empty")
    ok(z2[1]["listener_coverage_pct"] == 0.0, "no coverage is zero percent")
    # without difficulty 1: every third block of the first chunk at difficulty 1
    k3 = [(1000 + i, 60000 + 60 * i, 1.0 if i % 3 == 0 and i < 120 else 2.0) for i in range(250)]
    z3 = chunks(k3, [1005, 1006, 1006, 1007], [(0, 70000)], no_diff1=True)
    ok(z3[0]["block_count"] == 80 and z3[1]["block_count"] == 120, "block_count without difficulty 1")
    ok(z3[0]["mean_difficulty"] == 2.0, "difficulty of the other blocks only")
    ok(z3[0]["stale_blocks"] == 4 and z3[0]["stale_events"] == 3, "prefiltered stale count on all heights")
    ok(z3[0]["first_height"] == 1000 and z3[0]["last_height"] == 1119, "same chunk_n and same heights")
    z4 = chunks(k3, [1005, 1006, 1006, 1007], [(0, 70000)])
    ok("block_count" not in z4[0] and z4[0]["stale_blocks"] == 4, "without the switch unchanged")
    # branch rule: chain 1000 to 1009, difficulty 1 at 1003
    chain = {h: (1.0 if h == 1003 else 2.0) for h in range(1000, 1010)}
    n2, n1 = "1c00ffff", "1d00ffff"
    header = {"a5": ("k4", n2), "a6": ("a5", n2), "b3": ("k2", n2), "c7": ("k6", n1), "d8": ("k7", n2),
              "e5": ("k4", n2), "f6": ("a5", n1)}
    st = [(1005, "a5"), (1006, "a6"), (1003, "b3"), (1007, "c7"), (1008, "d8"), (1009, "x9"), (1005, "e5"), (1006, "f6")]
    bl, info = branch_filter(st, header, chain)
    ok(sorted(h for h in bl if h in (1005, 1006)) == [1005, 1005, 1006, 1006],
       "branch without difficulty 1 at the fork stays whole, also with difficulty 1 further up")
    ok(1003 not in bl, "chain block at the fork with difficulty 1, branch dropped")
    ok(1007 not in bl, "stale block at the fork with difficulty 1, branch dropped")
    ok(1009 in bl and info["branches_without_header_at_fork"] == 1, "branch without header stays and is counted")
    ok(info["branches_different_in_wider_reading"] == 1, "wider reading counts the branch with difficulty 1 above")
    ok(info["branches"] == 6 and info["branches_dropped"] == 2 and info["stale_blocks_dropped"] == 2, "branch numbers")
    # slot count: slot 1005 with three tips, s1 at difficulty 1 at the slot, two forks two blocks deep on one stem
    ch = {h: ("m%d" % h, 2.0) for h in range(1000, 1010)}
    hp = {"s1": ("m1004", "1d00ffff"), "a": ("m1004", "1c00ffff"), "b2": ("a", "1c00ffff"),
          "b3": ("a", "1c00ffff"), "c": ("m1006", "1c00ffff"), "cb": ("c", "1d00ffff"), "cc": ("cb", "1c00ffff")}
    st = [(1005, "s1"), (1005, "a"), (1006, "b2"), (1006, "b3"), (1007, "c"), (1008, "cb"), (1009, "cc")]
    sl, zl = slot_count(st, hp, ch, nomin=True)
    ok(sl[1005]["forks"] == 2 and sl[1005]["blocks"] == 3 and max(sl[1005]["depths"]) == 2 and zl["sibling_min"] == 1,
       "slot 1005: s1 dropped, two forks, three blocks, depth 2")
    ok(sl[1007]["blocks"] == 1 and zl["cut"] == 1, "fork cut before its first difficulty 1 block")
    sa, _ = slot_count(st, hp, ch, nomin=False)
    ok(sa[1005]["forks"] == 3 and sa[1005]["blocks"] == 4 and sa[1007]["blocks"] == 3, "without the rule all count")
    ch[1007] = ("m1007", 1.0)
    sm, zm = slot_count(st, hp, ch, nomin=True)
    ok(1007 not in sm and zm["main_min"] == 1, "chain block at the slot with difficulty 1, all forks there dropped")
    rows = slot_chunks(k3, sl, [(0, 70000)], nomin=True)
    ok(rows[0]["block_count"] == 80 and rows[0]["mean_block_time_s"] == round((40 * 60 + 39 * 120) / 79, 2),
       "slot chunks: gaps between counted blocks")
    print(f"{n} checks, all tests passed")


def write_csv(path, columns, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="The Bitcoin Cash tempnet in chunks of 120 blocks, PH-M-008.")
    ap.add_argument("--heads")
    ap.add_argument("--summary", help="summary JSON of the listener (stale blocks)")
    ap.add_argument("--runs", nargs="*", default=[], help="listener run folders with meta.json and koepfe.jsonl.gz")
    ap.add_argument("--csv")
    ap.add_argument("--no-diff1-csv", help="second CSV without blocks at difficulty 1, branch rule for stale")
    ap.add_argument("--slot-csv", help="slot count, all blocks")
    ap.add_argument("--slot-nomin-csv", help="slot count without difficulty 1")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.heads and a.summary and a.csv and a.out):
        ap.error("--heads, --summary, --csv and --out are needed")
    heads, chain2 = [], {}
    with gzip.open(a.heads, "rt") as f:
        for z in csv.DictReader(f):
            h, d = int(z.get("hoehe") or z["height"]), difficulty(z["nbits"])
            heads.append((h, int(z.get("zeit") or z["time"]), d))
            chain2[h] = (z["hash"], d)
    heads.sort()
    with open(a.summary) as f:
        stale_list = json.load(f)["stale"]["bloecke"]
    stale_h = [s["height"] for s in stale_list if s.get("height") not in (None, "")]
    stale_hx = [(s["height"], s["hash"]) for s in stale_list if s.get("height") not in (None, "")]
    runs = []
    for d in a.runs:
        p = os.path.join(d, "meta.json")
        if os.path.exists(p):
            with open(p) as f:
                m = json.load(f)
            if m.get("start_utc") and m.get("ende_utc"):
                runs.append((ts(m["start_utc"]), ts(m["ende_utc"])))
    rows = chunks(heads, stale_h, runs)
    write_csv(a.csv, COLUMNS, rows)
    full = [z for z in rows if z["full_coverage"] == "yes"]
    out = {"heads": [heads[0][0], heads[-1][0]], "chunks": len(rows), "fully_covered": len(full),
           "stale_in_full_chunks": sum(z["stale_blocks"] for z in full),
           "stale_events_in_full_chunks": sum(z["stale_events"] for z in full),
           "last_chunk_ends": rows[-1]["last_height"] if rows else None}
    header = run_headers(a.runs) if (a.no_diff1_csv or a.slot_csv or a.slot_nomin_csv) else {}
    if a.no_diff1_csv:
        chain = {h: d for h, _, d in heads}
        stale_z, info = branch_filter(stale_hx, header, chain)
        z1 = chunks(heads, stale_z, runs, no_diff1=True)
        write_csv(a.no_diff1_csv, COLUMNS + ["block_count"], z1)
        full1 = [z for z in z1 if z["full_coverage"] == "yes"]
        info.update({"stale_in_full_chunks": sum(z["stale_blocks"] for z in full1),
                     "stale_events_in_full_chunks": sum(z["stale_events"] for z in full1)})
        out["no_diff1_branch_rule"] = info
    for path, nomin, name in ((a.slot_csv, False, "slot_all"), (a.slot_nomin_csv, True, "slot_nomin")):
        if not path:
            continue
        slots, num = slot_count(stale_hx, header, chain2, nomin)
        zs = slot_chunks(heads, slots, runs, nomin)
        write_csv(path, SLOT_COLUMNS, zs)
        vz = [z for z in zs if z["full_coverage"] == "yes"]
        num.update({k: sum(z[k] for z in vz) for k in ("stale_events", "stale_forks", "stale_blocks")})
        out[name] = num
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
