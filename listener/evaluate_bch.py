#!/usr/bin/env python3
"""evaluate_bch.py - first seen, propagation spread and stale blocks from the Bitcoin Cash listener, PH-M-004 and PH-M-005.

Input: one or more run directories of listener/listen.py (ereignisse, koepfe
and compact as .jsonl or .jsonl.gz, and meta.json per run) and our header
chain (heads CSV of headers/ph_headers.py), fetched AFTER the runs. Output: one
row per chain block (CSV) and a summary (JSON).

Values per chain block:
- first_seen_utc: the first announcement of the block at our listener, over
  all connections and message types; per type (inv, headers, cmpctblock) the
  first one as well.
- timestamp_deviation_s: first_seen_utc minus the header time. Not a pure
  propagation value, because miners set the header time freely within the
  rules.
- propagation_spread_ms: last minus first announcement over the connections,
  per connection its first announcement, only blocks with at least two.
- Compact block path: whether a cmpctblock came (unasked in high bandwidth mode
  or on our request), short ids, prefilled transactions and the estimated
  missing transactions (short ids that match no transaction announced to us).
  Without our own mempool this is an estimate.
- Stale (PH-M-005): an announced block that is not in the chain although the
  chain reaches 6 blocks above its height.

Connections are labelled per run; across runs they are not joined (no
addresses, so no way to match them).

Percentiles are by the nearest rank, as in the comparison listener we checked
against (see METHODS.md).

  python3 listener/evaluate_bch.py --runs RUN_DIR... --heads heads.csv.gz --out summary.json --csv blocks.csv
  python3 listener/evaluate_bch.py --selftest
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

TYPES = ("inv", "headers", "cmpctblock")


def jsonl(path):
    if not os.path.exists(path):
        return []
    with (gzip.open(path, "rt") if path.endswith(".gz") else open(path)) as f:
        return [json.loads(z) for z in f if z.strip()]


def run_file(run_dir, name):
    gz = os.path.join(run_dir, name + ".jsonl.gz")
    return gz if os.path.exists(gz) else os.path.join(run_dir, name + ".jsonl")


def iso_ms(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t_ms % 1000:03d}Z"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def stats(values, digits=1):
    """Percentiles by the nearest rank."""
    if not values:
        return {"n": 0}
    s = sorted(values)
    rank = lambda q: s[max(0, math.ceil(q * len(s)) - 1)]  # noqa: E731
    return {"n": len(s), "mean": round(sum(s) / len(s), digits), "median": round(rank(0.5), digits),
            "p90": round(rank(0.9), digits), "p99": round(rank(0.99), digits), "min": round(s[0], digits),
            "max": round(s[-1], digits)}


def read_runs(run_dirs):
    """Join runs; connection labels get the run name in front."""
    ev, heads, cmpct, metas = [], {}, [], []
    for d in sorted(run_dirs):
        run = os.path.basename(os.path.normpath(d))
        with open(os.path.join(d, "meta.json")) as f:
            m = json.load(f)
        m["run"] = run
        metas.append(m)
        ev += [dict(e, peer=f"{run}/{e['peer']}") for e in jsonl(run_file(d, "ereignisse"))]
        cmpct += [dict(c, peer=f"{run}/{c['peer']}") for c in jsonl(run_file(d, "compact"))]
        for k in jsonl(run_file(d, "koepfe")):
            heads.setdefault(k["hash"], k)
    return ev, heads, cmpct, metas


def evaluate(run_dirs, heads_csv, confirmations=6):
    ev, heads, cmpct, metas = read_runs(run_dirs)
    windows = [(ts(m["start_utc"]), ts(m["ende_utc"])) for m in metas]

    chain_h, chain_t, by_h = {}, {}, {}
    with gzip.open(heads_csv, "rt") as f:
        for z in csv.DictReader(f):
            h = int(z["hoehe"])
            chain_h[z["hash"]], chain_t[z["hash"]], by_h[h] = h, int(z["zeit"]), z["hash"]
    tip = max(by_h)

    # first announcement per block: overall, per type, per connection
    first = defaultdict(lambda: {"all": None, "type": None, **{t: None for t in TYPES}, "peers": {}})
    for e in sorted(ev, key=lambda e: e["t_ms"]):
        d = first[e["hash"]]
        if d["all"] is None:
            d["all"], d["type"] = e["t_ms"], e["art"]
        if d[e["art"]] is None:
            d[e["art"]] = e["t_ms"]
        d["peers"].setdefault(e["peer"], e["t_ms"])
    cb = defaultdict(list)
    for c in cmpct:
        cb[c["hash"]].append(c)

    # one row per chain block whose header time lies in a run window
    rows = []
    for h in sorted(by_h):
        x = by_h[h]
        t = chain_t[x]
        if not any(a <= t <= b for a, b in windows) and x not in first:
            continue
        d = first.get(x)
        c = cb.get(x, [])
        c0 = min(c, key=lambda y: y["t_ms"]) if c else None
        peer_t = list(d["peers"].values()) if d else []
        before = by_h.get(h - 1)
        rows.append({
            "height": h, "hash": x, "header_time_utc": iso(t),
            "interval_s": t - chain_t[before] if before else "",
            "first_seen_utc": iso_ms(d["all"]) if d else "",
            "first_seen_type": d["type"] if d else "",
            **{f"first_{ty}_utc": (iso_ms(d[ty]) if d and d[ty] else "") for ty in TYPES},
            "timestamp_deviation_s": round(d["all"] / 1000 - t, 3) if d else "",
            "propagation_spread_ms": (max(peer_t) - min(peer_t)) if len(peer_t) >= 2 else "",
            "peers_announced": len(peer_t),
            "cmpctblock": "yes" if c else "no",
            "cmpctblock_unsolicited_peers": sum(1 for y in c if y["von_selbst"]),
            "short_ids": c0["kurz_ids"] if c0 else "",
            "prefilled": c0["vorbefuellt"] if c0 else "",
            "missing_tx_estimate": c0["fehlend_geschaetzt"] if c0 else "",
        })

    def height_of(x, depth=0):
        k = heads.get(x)
        if not k or depth > 50:
            return None
        if k["prev"] in chain_h:
            return chain_h[k["prev"]] + 1
        hv = height_of(k["prev"], depth + 1)
        return hv + 1 if hv is not None else None

    stale, still_open, no_header = [], 0, 0
    for x, d in first.items():
        if x in chain_h:
            continue
        hv = height_of(x)
        if hv is None:
            no_header += 1
            continue
        if hv > tip - confirmations:
            still_open += 1
            continue
        stale.append({"height": hv, "hash": x, "header_time_utc": iso(heads[x]["zeit"]),
                      "first_seen_utc": iso_ms(d["all"]), "first_seen_type": d["type"],
                      "peers_announced": len(d["peers"]), "canonical_hash_at_height": by_h.get(hv)})
    stale.sort(key=lambda s: s["height"])

    seen = [z for z in rows if z["first_seen_utc"]]
    with_cb = [z for z in seen if z["cmpctblock"] == "yes"]
    summary = {
        "kind": "one listener, not a node, one network path",
        "runs": [{"run": m["run"], "start_utc": m.get("start_utc"), "end_utc": m.get("ende_utc"),
                  "refused_by_probe": m.get("peers_ohne_abzweig_abgelehnt"), "peers": len(m["peers"])} for m in metas],
        "covered_h": round(sum(b - a for a, b in windows) / 3600, 1),
        "peer_software": sorted({p["software"] for m in metas for p in m["peers"]}),
        "chain_tip": tip,
        "chain_blocks": len(rows),
        "announced_seen": len(seen),
        "first_seen_type": {t: sum(1 for z in seen if z["first_seen_type"] == t) for t in TYPES},
        "timestamp_deviation_s": stats([z["timestamp_deviation_s"] for z in seen], 3),
        "propagation_spread_ms": stats([z["propagation_spread_ms"] for z in seen if z["propagation_spread_ms"] != ""], 0),
        "peers_per_block": stats([z["peers_announced"] for z in seen], 1),
        "compact": {
            "blocks_with_cmpctblock": len(with_cb),
            "unsolicited": sum(1 for z in with_cb if z["cmpctblock_unsolicited_peers"]),
            "short_ids": stats([z["short_ids"] for z in with_cb], 1),
            "missing_estimate": stats([z["missing_tx_estimate"] for z in with_cb], 1),
            "blocks_without_missing": sum(1 for z in with_cb if z["missing_tx_estimate"] == 0),
            "note": "estimated against the transactions announced to us, no mempool of our own",
        },
        "stale": {"count": len(stale), "confirmations": confirmations,
                  "share_pct": round(100 * len(stale) / (len(seen) + len(stale)), 3) if seen else None,
                  "open_below_confirmations": still_open, "without_header": no_header, "blocks": stale},
    }
    return rows, summary


def write_csv(path, rows):
    columns = list(rows[0].keys()) if rows else ["height"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def selftest():
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    ok(stats([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])["median"] == 5 and stats(list(range(1, 11)))["p90"] == 9,
       "nearest rank percentiles")
    with tempfile.TemporaryDirectory() as tmp:
        # chain 100..110, a stale block at 104, a stale one at 109 (still open), one without header
        chain = [(100 + i, f"{100 + i:064x}", 1795000000 + 60 * i) for i in range(11)]
        with gzip.open(os.path.join(tmp, "heads.csv.gz"), "wt") as f:
            f.write("hoehe,hash,zeit,nbits,version\n")
            for h, x, t in chain:
                f.write(f"{h},{x},{t},1d00ffff,1\n")
        run = os.path.join(tmp, "20261101T0000")
        os.makedirs(run)
        with open(os.path.join(run, "meta.json"), "w") as f:
            json.dump({"start_utc": "2026-11-17T08:26:00Z", "ende_utc": "2026-11-17T08:40:00Z",
                       "peers": [{"peer": "peer_1", "software": "/T:1/"}, {"peer": "peer_2", "software": "/T:1/"}]}, f)
        ev, hd, cp = [], [], []
        for h, x, t in chain[1:]:
            ev.append({"t_ms": (t + 2) * 1000, "peer": "peer_1", "art": "inv", "hash": x})
            ev.append({"t_ms": (t + 2) * 1000 + 150, "peer": "peer_2", "art": "headers", "hash": x})
        ev.append({"t_ms": (chain[4][2] + 5) * 1000, "peer": "peer_2", "art": "inv", "hash": "aa" * 32})
        hd.append({"hash": "aa" * 32, "prev": chain[3][1], "zeit": chain[4][2] + 1, "nbits": "1d00ffff"})
        ev.append({"t_ms": (chain[9][2] + 5) * 1000, "peer": "peer_1", "art": "inv", "hash": "bb" * 32})
        hd.append({"hash": "bb" * 32, "prev": chain[8][1], "zeit": chain[9][2] + 1, "nbits": "1d00ffff"})
        ev.append({"t_ms": (chain[5][2] + 5) * 1000, "peer": "peer_1", "art": "inv", "hash": "cc" * 32})
        cp.append({"t_ms": (chain[2][2] + 3) * 1000, "peer": "peer_1", "hash": chain[2][1], "von_selbst": False,
                   "kurz_ids": 10, "vorbefuellt": 1, "fehlend_geschaetzt": 0, "tx_ids_bekannt": 10})
        for name, rows in (("ereignisse", ev), ("koepfe", hd), ("compact", cp)):
            with open(os.path.join(run, name + ".jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
        rows, s = evaluate([run], os.path.join(tmp, "heads.csv.gz"))
    ok(s["chain_blocks"] == 10 and s["announced_seen"] == 10, "chain blocks and announced blocks")
    ok(rows[0]["first_seen_type"] == "inv" and rows[0]["propagation_spread_ms"] == 150, "first seen and spread")
    ok(rows[0]["timestamp_deviation_s"] == 2.0, "first seen minus header time")
    ok(s["stale"]["count"] == 1 and s["stale"]["blocks"][0]["height"] == 104, "stale block at its height")
    ok(s["stale"]["open_below_confirmations"] == 1, "a block within 6 of the tip stays open")
    ok(s["stale"]["without_header"] == 1, "a block without header has no height")
    ok(s["stale"]["share_pct"] == round(100 / 11, 3), "stale share over chain blocks plus stale")
    ok(s["compact"]["blocks_with_cmpctblock"] == 1 and s["compact"]["blocks_without_missing"] == 1, "compact path")
    ok(s["first_seen_type"] == {"inv": 10, "headers": 0, "cmpctblock": 0}, "first seen by type")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Evaluate the Bitcoin Cash listener, PH-M-004 and PH-M-005.")
    ap.add_argument("--runs", nargs="+")
    ap.add_argument("--heads", help="heads CSV (gzip) fetched after the runs")
    ap.add_argument("--out", help="summary JSON")
    ap.add_argument("--csv", help="one row per chain block")
    ap.add_argument("--confirmations", type=int, default=6)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.runs and a.heads and a.out and a.csv):
        ap.error("--runs, --heads, --out and --csv are needed")
    rows, summary = evaluate(a.runs, a.heads, a.confirmations)
    write_csv(a.csv, rows)
    with open(a.out, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(json.dumps({"chain_blocks": summary["chain_blocks"], "announced_seen": summary["announced_seen"],
                      "first_seen_type": summary["first_seen_type"], "stale": summary["stale"]["count"],
                      "cmpct": summary["compact"]["blocks_with_cmpctblock"]}))


if __name__ == "__main__":
    main()
