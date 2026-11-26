#!/usr/bin/env python3
"""evaluate_zcash.py - stale blocks, their branches and arrival times from the Zcash listener, PH-M-004 and PH-M-005.

Input: the announcements (ereignisse), collected headers (koepfe) and meta of
one or more runs of listener/listen.py, and our header chain (heads CSV of
headers/ph_headers.py), fetched AFTER the runs. Runs come either as run
directories (--runs) or as files in the same order (--events, --heads-seen,
--meta), as in our recorded series. With more than one run the connections are
named s01/zebra-1 and so on. With --activation the count is split before and
from the activation height.

Definitions:
- Stale: an announced block hash that is not in the chain fetched later. Its
  height comes from its parent: a parent in the chain gives that height plus
  1, a parent among the stale blocks one more than that one.
- Depth of a stale branch: the number of blocks in the branch (1 = a single
  block at the tip replaced).
- Arrival: the first announcement of a block per connection. Zakura minus
  Zebra = the first announcement by any Zakura connection minus the first by
  any Zebra connection, only for blocks both announced.
- Stale share: stale blocks over (announced chain blocks + stale blocks), per
  phase (before and from the activation) and from the activation per 6 h.
- All from the view of ONE listener. The time of arrival includes the path
  from the node to us.

  python3 listener/evaluate_zcash.py --runs RUN_DIR... --heads heads.csv.gz --activation 3543405 --out summary.json
  python3 listener/evaluate_zcash.py --events s01-ereignisse.jsonl.gz ... --heads-seen s01-koepfe.jsonl.gz ... \\
      --meta s01-meta.json ... --heads heads.csv.gz --out summary.json
  python3 listener/evaluate_zcash.py --selftest
"""
import argparse
import csv
import gzip
import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone


def quantile(s, q):
    if not s:
        return None
    s = sorted(s)
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 1)


def jsonl(path):
    with (gzip.open(path, "rt") if path.endswith(".gz") else open(path)) as f:
        return [json.loads(z) for z in f if z.strip()]


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def run_files(run_dirs):
    """(events, heads, meta) per run directory, .jsonl or .jsonl.gz."""
    out = []
    for d in run_dirs:
        f = lambda n: next((os.path.join(d, n + x) for x in (".jsonl.gz", ".jsonl")  # noqa: E731
                            if os.path.exists(os.path.join(d, n + x))), os.path.join(d, n + ".jsonl"))
        out.append((f("ereignisse"), f("koepfe"), os.path.join(d, "meta.json")))
    return out


def evaluate(triples, heads_csv, activation=None):
    several = len(triples) > 1
    metas, ev, heads = [], [], {}
    for i, (pe, pk, pm) in enumerate(triples, 1):
        pre = f"s{i:02d}/" if several else ""
        with open(pm) as f:
            m = json.load(f)
        for p in m["peers"]:
            p["peer"] = pre + p["peer"]
        metas.append(m)
        ev += [dict(e, peer=pre + e["peer"]) for e in jsonl(pe)]
        for k in jsonl(pk):
            heads.setdefault(k["hash"], k)
    meta = dict(metas[0])
    meta["peers"] = [p for m in metas for p in m["peers"]]
    meta["start_utc"] = min(m["start_utc"] for m in metas)
    meta["ende_utc"] = max(m["ende_utc"] for m in metas)
    sw_of = {p["peer"]: p["peer"].split("/")[-1].split("-")[0] for p in meta["peers"]}

    chain_h, chain_t = {}, {}
    with gzip.open(heads_csv, "rt") as f:
        for z in csv.DictReader(f):
            chain_h[z["hash"]] = int(z["hoehe"])
            chain_t[z["hash"]] = int(z["zeit"])

    windows = [(ts(m["start_utc"]), ts(m["ende_utc"])) for m in metas]

    first = defaultdict(dict)
    for e in ev:
        d = first[e["hash"]]
        if e["peer"] not in d or e["t_ms"] < d[e["peer"]]:
            d[e["peer"]] = e["t_ms"]

    in_window = [h for h, t in chain_t.items() if any(a <= t <= b for a, b in windows)]
    seen_in_chain = [h for h in in_window if h in first]

    stale = []
    for h in [h for h in first if h not in chain_h]:
        k = heads.get(h)
        entry = {"hash": h, "first_announcement_utc": utc(min(first[h].values()) / 1000),
                 "announced_by": sorted({sw_of.get(p, "?") for p in first[h]}),
                 "connections": len(first[h])}
        if k:
            entry.update({"parent": k["prev"], "header_time_utc": utc(k["zeit"]), "nbits": k["nbits"]})
        stale.append(entry)
    by_hash = {a["hash"]: a for a in stale}

    def height(a, depth=0):
        if "height" in a or depth > 50:
            return a.get("height")
        v = a.get("parent")
        if v in chain_h:
            a["height"] = chain_h[v] + 1
        elif v in by_hash:
            hv = height(by_hash[v], depth + 1)
            a["height"] = hv + 1 if hv is not None else None
        else:
            a["height"] = None
        return a["height"]

    for a in stale:
        height(a)
    children = defaultdict(list)
    for a in stale:
        children[a.get("parent")].append(a["hash"])

    depth_of = {}

    def length(root):
        """Blocks in the longest branch from root, without recursion: a chain
        fetched before the end of the runs can leave thousands of later blocks
        as one long stale branch."""
        stack = [(root, False)]
        while stack:
            h, ready = stack.pop()
            if h in depth_of:
                continue
            kids = children.get(h, [])
            if ready or not kids:
                depth_of[h] = 1 + max((depth_of[c] for c in kids), default=0)
            else:
                stack.append((h, True))
                stack.extend((c, False) for c in kids if c not in depth_of)
        return depth_of[root]

    branches = [{"fork_height": chain_h[a["parent"]], "depth": length(a["hash"])}
                for a in stale if a.get("parent") in chain_h]
    no_header = sum(1 for a in stale if "parent" not in a)

    diff_ms, spread_ms, after_time_s = [], [], []
    zakura_first = 0
    for h in seen_in_chain:
        d = first[h]
        zb = [t for p, t in d.items() if sw_of.get(p) == "zebra"]
        zk = [t for p, t in d.items() if sw_of.get(p) == "zakura"]
        if zb and zk:
            diff_ms.append(min(zk) - min(zb))
            zakura_first += min(zk) < min(zb)
        if len(d) >= 2:
            spread_ms.append(max(d.values()) - min(d.values()))
        after_time_s.append(min(d.values()) / 1000 - chain_t[h])

    def share(chain_hashes, st):
        n_k = sum(1 for h in chain_hashes if h in first)
        return {"chain_blocks_in_window": len(chain_hashes), "announced_seen": n_k, "stale": len(st),
                "stale_share_pct": round(100 * len(st) / (n_k + len(st)), 3) if n_k + len(st) else None}

    phases = None
    if activation:
        act_hash = next((h for h, x in chain_h.items() if x == activation), None)
        before_k = [h for h in in_window if chain_h[h] < activation]
        from_k = [h for h in in_window if chain_h[h] >= activation]
        t_act = chain_t.get(act_hash) if act_hash else None

        def from_act(a):
            if a.get("height") is not None:
                return a["height"] >= activation
            return t_act is not None and min(first[a["hash"]].values()) / 1000 >= t_act
        phases = {"activation_height": activation, "activation_block_utc": utc(t_act) if t_act else None,
                  "before": share(before_k, [a for a in stale if not from_act(a)]),
                  "from": share(from_k, [a for a in stale if from_act(a)])}
        if t_act:
            per = []
            for i in range(0, 48, 6):
                a, b = t_act + i * 3600, t_act + (i + 6) * 3600
                kk = [h for h in from_k if a <= chain_t[h] < b]
                aa = [x for x in stale if from_act(x) and a <= min(first[x["hash"]].values()) / 1000 < b]
                if kk or aa:
                    per.append(dict(share(kk, aa), hours=f"{i} to {i + 6}"))
            phases["from_per_6h"] = per

    peers_sw, ann_sw = defaultdict(int), defaultdict(int)
    for p in meta["peers"]:
        peers_sw[sw_of[p["peer"]]] += 1
        ann_sw[sw_of[p["peer"]]] += p["ankuendigungen"]

    return {
        "kind": "one listener, not a node",
        "window_utc": [meta["start_utc"], meta["ende_utc"]],
        # covered: first to last announcement; shorter than the window if the listener fell silent
        "first_announcement_utc": utc(min(e["t_ms"] for e in ev) / 1000) if ev else None,
        "last_announcement_utc": utc(max(e["t_ms"] for e in ev) / 1000) if ev else None,
        "covered_min": round((max(e["t_ms"] for e in ev) - min(e["t_ms"] for e in ev)) / 60000, 1) if ev else 0,
        "connections_per_software": dict(peers_sw),
        "announcements_per_software": dict(ann_sw),
        "peer_software": sorted({p["software"] for p in meta["peers"]}),
        "chain_blocks_in_window": len(in_window),
        "announced_seen": len(seen_in_chain),
        "stale_count": len(stale),
        "stale_without_header": no_header,
        "phases": phases,
        "runs": [{"start_utc": m.get("start_utc"), "end_utc": m.get("ende_utc"),
                  "planned_end_utc": m.get("geplantes_ende_utc"), "protocol": m.get("protokoll")} for m in metas],
        "branches": len(branches),
        "max_depth": max((z["depth"] for z in branches), default=0),
        "depths": sorted((z["depth"] for z in branches), reverse=True)[:20],
        "stale_examples": sorted(stale, key=lambda a: a["first_announcement_utc"])[:20],
        "zakura_minus_zebra_ms": {"blocks": len(diff_ms), "median": quantile(diff_ms, 0.5),
                                  "p10": quantile(diff_ms, 0.1), "p90": quantile(diff_ms, 0.9),
                                  "zakura_first": zakura_first},
        "spread_all_connections_ms": {"blocks": len(spread_ms), "median": quantile(spread_ms, 0.5),
                                      "p90": quantile(spread_ms, 0.9), "max": max(spread_ms) if spread_ms else None},
        "first_arrival_minus_header_time_s": {"median": quantile(after_time_s, 0.5),
                                              "p90": quantile(after_time_s, 0.9)},
        "listener_meta": {k: v for k, v in meta.items() if k != "peers"},
        "connections": [{k: p[k] for k in ("peer", "software", "verbunden_utc", "getrennt_utc", "ankuendigungen") if k in p}
                        for p in meta["peers"]],
    }


def selftest():
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    ok(quantile([1, 2, 3, 4], 0.5) == 2.5 and quantile([], 0.5) is None, "interpolated quantile")
    with tempfile.TemporaryDirectory() as tmp:
        t0 = 1795000000
        chain = [(200 + i, f"{200 + i:064x}", t0 + 25 * i) for i in range(12)]
        with gzip.open(os.path.join(tmp, "heads.csv.gz"), "wt") as f:
            f.write("hoehe,hash,zeit,nbits,version\n")
            for h, x, t in chain:
                f.write(f"{h},{x},{t},1d00ffff,5\n")
        start = datetime.fromtimestamp(t0 - 10, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = datetime.fromtimestamp(t0 + 25 * 11 + 10, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        run = os.path.join(tmp, "run")
        os.makedirs(run)
        with open(os.path.join(run, "meta.json"), "w") as f:
            json.dump({"start_utc": start, "ende_utc": end, "peers": [
                {"peer": "zebra-1", "software": "/Zebra:7.1.0/", "ankuendigungen": 13},
                {"peer": "zakura-1", "software": "/Zakura:1.8.9/", "ankuendigungen": 11}]}, f)
        ev, hd = [], []
        for h, x, t in chain[1:]:
            ev.append({"t_ms": (t + 1) * 1000, "peer": "zebra-1", "art": "inv", "hash": x})
            ev.append({"t_ms": (t + 1) * 1000 + 300, "peer": "zakura-1", "art": "inv", "hash": x})
        # a branch of two stale blocks from height 205, and one without header
        ev.append({"t_ms": (chain[5][2] + 2) * 1000, "peer": "zebra-1", "art": "inv", "hash": "aa" * 32})
        ev.append({"t_ms": (chain[6][2] + 2) * 1000, "peer": "zebra-1", "art": "inv", "hash": "ab" * 32})
        ev.append({"t_ms": (chain[7][2] + 2) * 1000, "peer": "zakura-1", "art": "inv", "hash": "ff" * 32})
        hd.append({"hash": "aa" * 32, "prev": chain[4][1], "zeit": chain[5][2], "nbits": "1d00ffff"})
        hd.append({"hash": "ab" * 32, "prev": "aa" * 32, "zeit": chain[6][2], "nbits": "1d00ffff"})
        for name, rows in (("ereignisse", ev), ("koepfe", hd)):
            with open(os.path.join(run, name + ".jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
        s = evaluate(run_files([run]), os.path.join(tmp, "heads.csv.gz"), activation=206)
    ok(s["chain_blocks_in_window"] == 12 and s["announced_seen"] == 11, "chain blocks in the window")
    ok(s["stale_count"] == 3 and s["stale_without_header"] == 1, "stale blocks, one without header")
    ok(s["branches"] == 1 and s["max_depth"] == 2, "one branch of depth 2")
    heights = {a["hash"]: a["height"] for a in s["stale_examples"]}
    ok(heights["aa" * 32] == 205 and heights["ab" * 32] == 206 and heights["ff" * 32] is None, "heights from parents")
    ok(s["zakura_minus_zebra_ms"]["median"] == 300 and s["zakura_minus_zebra_ms"]["zakura_first"] == 0,
       "zakura minus zebra")
    ok(s["phases"]["before"]["stale"] == 1 and s["phases"]["from"]["stale"] == 2,
       "phases split at the activation, the block without header by time")
    ok(s["first_arrival_minus_header_time_s"]["median"] == 1.0, "first arrival minus header time")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Evaluate the Zcash listener, PH-M-004 and PH-M-005.")
    ap.add_argument("--runs", nargs="+", help="run directories of listener/listen.py")
    ap.add_argument("--events", nargs="+", help="announcement files, one per run")
    ap.add_argument("--heads-seen", nargs="+", help="header files of the listener, one per run")
    ap.add_argument("--meta", nargs="+", help="meta files, one per run")
    ap.add_argument("--heads", help="heads CSV (gzip) fetched after the runs")
    ap.add_argument("--activation", type=int, help="activation height; splits the count before and from it")
    ap.add_argument("--out", help="summary JSON")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.heads and a.out and (a.runs or a.events)):
        ap.error("--heads, --out and --runs or --events/--heads-seen/--meta are needed")
    if a.runs:
        triples = run_files(a.runs)
    else:
        if not (a.heads_seen and a.meta and len(a.events) == len(a.heads_seen) == len(a.meta)):
            ap.error("--events, --heads-seen and --meta need the same number of files")
        triples = list(zip(a.events, a.heads_seen, a.meta))
    out = evaluate(triples, a.heads, a.activation)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(json.dumps({k: out[k] for k in ("chain_blocks_in_window", "announced_seen", "stale_count", "max_depth")}))


if __name__ == "__main__":
    main()
