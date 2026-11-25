#!/usr/bin/env python3
"""build_switch.py - the Bitcoin Cash tempnet before and after a node changed its build, PH-M-004 and PH-M-005.

A public tempnet node may change its build during the test. This script

1. lists per listener run the software of the peers (user agent and protocol
   version from the version handshake, as they stand in meta.json; no
   addresses),
2. looks for the time from which no peer reports the old build. Two ways:
   a) a new user agent replaces the old one (--old), then the last connection
      with the old one counts;
   b) the user agent stays the same: a new build means a restart of the node,
      so all our connections with this user agent drop within a few minutes.
      Every such common drop is reported.
   With --switch the time can be set by hand.
3. evaluates before and after that time separately: first announcement per
   block (timestamp_deviation, propagation_spread, peers per block, which
   software announced first), announced blocks that never entered the chain
   (stale after 6 confirmations), median and p90 of the block intervals.
4. timestamp_deviation (arrival minus header time) twice: all blocks, and
   without blocks whose header time is exactly 120 s after the parent (in our
   tempnet data these came almost all from one miner). Only the count is used.

Input: the listener folder (runs with meta.json and ereignisse.jsonl.gz, and
bloecke.csv and zusammenfassung.json of the last evaluation over all runs).
See FORMATS.md.

  python3 bch_tempnet/build_switch.py --listener LISTENER_DIR --old PART_OF_USER_AGENT [--from 2026-10-29T00:00:00Z] \\
      [--switch 2026-10-29T22:00:00Z] --json build-switch.json --md build-switch.md
  python3 bch_tempnet/build_switch.py --selftest
"""
import argparse
import csv
import glob
import gzip
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

DROP_WINDOW_S = 180
EXACT_S = 120


def ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def quantile(s, q):
    s = sorted(s)
    if not s:
        return None
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def read_runs(folder):
    out = []
    for d in sorted(glob.glob(os.path.join(folder, "2*"))):
        with open(os.path.join(d, "meta.json")) as f:
            m = json.load(f)
        out.append((os.path.basename(d), d, m))
    return out


def user_agents(runs, old):
    """Per run the user agents, the last time a connection with `old` was up,
    and every common drop of all connections of one user agent."""
    rows, old_last, drops = [], None, []
    for name, _, m in runs:
        end = ts(m["ende_utc"])
        c = Counter((p["software"], p["version"]) for p in m["peers"])
        rows.append({"run": name, "start_utc": m["start_utc"], "end_utc": m["ende_utc"],
                     "user_agents": [{"software": s, "protocol": v, "connections": n} for (s, v), n in sorted(c.items())]})
        for p in m["peers"]:
            if old in p["software"]:
                until = ts(p["getrennt_utc"]) if p.get("getrennt_utc") else end
                old_last = max(old_last or 0, until)
        # common drop: all connections with the user agent that were closed
        # before the end of the run lie within DROP_WINDOW_S
        per = defaultdict(list)
        for p in m["peers"]:
            if p.get("getrennt_utc") and ts(p["getrennt_utc"]) < end - 60:
                per[p["software"]].append(ts(p["getrennt_utc"]))
        for sw, times in per.items():
            every = [p for p in m["peers"] if p["software"] == sw
                     and ts(p["verbunden_utc"]) <= min(times)]
            if len(times) >= 2 and max(times) - min(times) <= DROP_WINDOW_S and len(times) >= len(every):
                drops.append({"run": name, "software": sw, "connections": len(times),
                              "from_utc": utc(min(times)), "to_utc": utc(max(times))})
    return rows, old_last, drops


def first_announcers(runs):
    """hash -> (t_ms, software of the first announcer)"""
    first = {}
    for _, d, m in runs:
        sw = {p["peer"]: p["software"] for p in m["peers"]}
        with gzip.open(os.path.join(d, "ereignisse.jsonl.gz"), "rt") as f:
            for z in f:
                e = json.loads(z)
                if e["hash"] not in first or e["t_ms"] < first[e["hash"]][0]:
                    first[e["hash"]] = (e["t_ms"], sw.get(e["peer"], "?"))
    return first


def side(blocks, stale, first, start, end, old):
    # blocks the listener never saw have no first_seen_utc and are left out
    b = [z for z in blocks if z.get("first_seen_utc") and start <= ts(z["first_seen_utc"]) < end]
    s = [z for z in stale if z.get("first_seen_utc") and start <= ts(z["first_seen_utc"]) < end]
    if not b:
        return None
    iv = [int(z["interval_s"]) for z in b if z["interval_s"] not in ("", None)]
    dev = [float(z["timestamp_deviation_s"]) for z in b if z["timestamp_deviation_s"]]
    dev_without = [float(z["timestamp_deviation_s"]) for z in b
                   if z["timestamp_deviation_s"] and z["interval_s"] != str(EXACT_S)]
    exact = sum(1 for z in b if z["interval_s"] == str(EXACT_S))
    spr = [int(z["propagation_spread_ms"]) for z in b if z["propagation_spread_ms"]]
    peers = [int(z["peers_announced"]) for z in b if z["peers_announced"]]
    by_first = Counter(first[z["hash"]][1] for z in b if z["hash"] in first)
    stale_first = Counter(first[z["hash"]][1] for z in s if z["hash"] in first)
    r = lambda x, n=1: None if x is None else round(x, n)  # noqa: E731
    return {
        "window_utc": [utc(start), utc(end)],
        "chain_blocks": len(b), "heights": [int(b[0]["height"]), int(b[-1]["height"])],
        "intervals": {"n": len(iv), "median_s": r(quantile(iv, 0.5)), "p90_s": r(quantile(iv, 0.9))},
        "timestamp_deviation_s": {"median": r(quantile(dev, 0.5), 2), "p90": r(quantile(dev, 0.9), 2),
                                  "p99": r(quantile(dev, 0.99), 2), "n": len(dev)},
        "timestamp_deviation_without_120s": {"median": r(quantile(dev_without, 0.5), 2),
                                             "p90": r(quantile(dev_without, 0.9), 2),
                                             "p99": r(quantile(dev_without, 0.99), 2), "n": len(dev_without),
                                             "left_out": exact},
        "propagation_spread_ms": {"median": r(quantile(spr, 0.5), 0), "p90": r(quantile(spr, 0.9), 0)},
        "peers_per_block_median": r(quantile(peers, 0.5), 0),
        "first_announced_by": dict(by_first.most_common()),
        "share_first_old_pct": r(100 * sum(n for k, n in by_first.items() if old in k) / max(1, sum(by_first.values()))),
        "stale": {"count": len(s), "share_pct": r(100 * len(s) / (len(b) + len(s)), 2),
                  "first_by": dict(stale_first.most_common()),
                  "blocks": [{"height": z["height"], "hash": z["hash"], "first_seen_utc": z["first_seen_utc"],
                              "peers": z["peers_announced"]} for z in s]},
    }


def analyse(folder, old, switch=None, search_from=None):
    runs = read_runs(folder)
    rows, old_last, drops = user_agents(runs, old)
    ab = ts(search_from) if search_from else 0
    old_drops = [a for a in drops if old in a["software"]
                 and ts(a["from_utc"].replace(" ", "T") + "Z") >= ab]
    agents = sorted({k["software"] for z in rows for k in z["user_agents"]})
    data_end = ts(runs[-1][2]["ende_utc"])
    if switch:
        sw, reason = ts(switch), "set by hand"
    elif old_last and ab <= old_last < data_end - 600:
        sw, reason = old_last, f"last connection with '{old}'"
    elif old_drops:
        sw = ts(old_drops[-1]["to_utc"].replace(" ", "T") + "Z")
        reason = f"common drop of all connections with '{old}' (restart)"
    else:
        sw, reason = None, f"no switch found: '{old}' connected to the end of the data, no common drop"
    with open(os.path.join(folder, "bloecke.csv")) as f:
        blocks = list(csv.DictReader(f))
    with open(os.path.join(folder, "zusammenfassung.json")) as f:
        stale = json.load(f)["stale"]["bloecke"]
    first = first_announcers(runs)
    start = ts(runs[0][2]["start_utc"])
    return {"old": old, "user_agents_all": agents, "runs": rows, "common_drops": drops,
            "old_last_connected_utc": utc(old_last) if old_last else None,
            "data_to_utc": utc(data_end), "switch_utc": utc(sw) if sw else None, "switch_reason": reason,
            "before": side(blocks, stale, first, start, sw or data_end, old),
            "after": side(blocks, stale, first, sw, data_end, old) if sw else None}


def report(r):
    md = [f"# Bitcoin Cash tempnet, build switch of '{r['old']}'", "",
          f"Data to {r['data_to_utc']} UTC. Switch: {r['switch_utc'] or 'not found'} ({r['switch_reason']}).", "",
          "| user agent | protocol | first run | last run |", "|---|---|---|---|"]
    first_r, last_r = {}, {}
    for z in r["runs"]:
        for k in z["user_agents"]:
            key = (k["software"], k["protocol"])
            first_r.setdefault(key, z["start_utc"])
            last_r[key] = z["end_utc"]
    for (sw, v) in sorted(first_r):
        md.append(f"| `{sw}` | {v} | {first_r[(sw, v)]} | {last_r[(sw, v)]} |")
    if r["common_drops"]:
        md += ["", "Common drops (all connections of one user agent within 3 min):"]
        md += [f"- {a['software']}: {a['connections']} connections, {a['from_utc']} to {a['to_utc']} UTC"
               for a in r["common_drops"]]
    for name in ("before", "after"):
        s = r[name]
        if not s:
            continue
        w = s["timestamp_deviation_without_120s"]
        md += ["", f"## {name}, {s['window_utc'][0]} to {s['window_utc'][1]} UTC", "",
               f"- chain blocks {s['chain_blocks']}, heights {s['heights'][0]} to {s['heights'][1]}",
               f"- intervals median {s['intervals']['median_s']} s, p90 {s['intervals']['p90_s']} s",
               f"- timestamp_deviation, all {s['timestamp_deviation_s']['n']} blocks: median "
               f"{s['timestamp_deviation_s']['median']} s, p90 {s['timestamp_deviation_s']['p90']} s, "
               f"p99 {s['timestamp_deviation_s']['p99']} s",
               f"- timestamp_deviation without {w['left_out']} blocks at exactly 120 s ({w['n']} blocks): "
               f"median {w['median']} s, p90 {w['p90']} s, p99 {w['p99']} s",
               f"- propagation_spread median {s['propagation_spread_ms']['median']} ms, "
               f"p90 {s['propagation_spread_ms']['p90']} ms",
               f"- first announced by '{r['old']}': {s['share_first_old_pct']} %",
               f"- stale {s['stale']['count']} ({s['stale']['share_pct']} %), first by {s['stale']['first_by']}"]
    return "\n".join(md) + "\n"


def selftest():
    import tempfile
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    ok(quantile([], 0.5) is None and quantile([1, 3], 0.5) == 2, "quantile")
    with tempfile.TemporaryDirectory() as tmp:
        # two runs; in the second all connections of build A drop within 2 min (a restart)
        runs = [("20261001T0000", "2026-10-01T00:00:00Z", "2026-10-01T06:00:00Z", []),
                ("20261001T0600", "2026-10-01T06:00:00Z", "2026-10-01T12:00:00Z",
                 ["2026-10-01T09:00:00Z", "2026-10-01T09:01:30Z"])]
        for name, start, end, gone in runs:
            d = os.path.join(tmp, name)
            os.makedirs(d)
            peers = []
            for i in range(2):
                p = {"peer": f"peer_{i + 1}", "software": "/Node:1.0(build-a)/", "version": 70016,
                     "verbunden_utc": start}
                if gone:
                    p["getrennt_utc"] = gone[i]
                peers.append(p)
            peers.append({"peer": "peer_3", "software": "/Node:1.0/", "version": 70016, "verbunden_utc": start})
            with open(os.path.join(d, "meta.json"), "w") as f:
                json.dump({"start_utc": start, "ende_utc": end, "peers": peers}, f)
            with gzip.open(os.path.join(d, "ereignisse.jsonl.gz"), "wt") as f:
                base = ts(start)
                for k in range(3):
                    h = f"{name}-{k}"
                    f.write(json.dumps({"hash": h, "peer": "peer_1", "t_ms": int((base + 600 * k) * 1000)}) + "\n")
                    f.write(json.dumps({"hash": h, "peer": "peer_3", "t_ms": int((base + 600 * k) * 1000) + 5}) + "\n")
        with open(os.path.join(tmp, "bloecke.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["height", "hash", "first_seen_utc", "interval_s", "timestamp_deviation_s",
                        "propagation_spread_ms", "peers_announced"])
            for i, (name, start, _, _) in enumerate(runs):
                for k in range(3):
                    w.writerow([100 + 3 * i + k, f"{name}-{k}", datetime.fromtimestamp(ts(start) + 600 * k, timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"), 120 if k == 0 else 60, 1.5 + k, 5, 2])
        with open(os.path.join(tmp, "zusammenfassung.json"), "w") as f:
            json.dump({"stale": {"bloecke": [{"height": 104, "hash": "x", "first_seen_utc": "2026-10-01T10:00:00Z",
                                              "peers_announced": 1}]}}, f)
        r = analyse(tmp, "build-a")
        ok(len(r["common_drops"]) == 1 and r["common_drops"][0]["connections"] == 2, "common drop of build A found")
        ok(r["switch_utc"] == "2026-10-01 09:01:30", "switch at the end of the common drop")
        ok(r["before"]["chain_blocks"] == 6 and r["after"] is None, "all blocks before the switch")
        ok(r["before"]["timestamp_deviation_without_120s"]["left_out"] == 2, "blocks at exactly 120 s left out")
        ok(r["before"]["share_first_old_pct"] == 100.0, "first announcer is build A")
        r2 = analyse(tmp, "build-a", switch="2026-10-01T06:00:00Z")
        ok(r2["before"]["chain_blocks"] == 3 and r2["after"]["chain_blocks"] == 3, "switch set by hand splits the data")
        ok(r2["after"]["stale"]["count"] == 1, "stale after the switch")
        ok(report(r2).startswith("# Bitcoin Cash tempnet, build switch"), "report")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Bitcoin Cash tempnet before and after a build switch.")
    ap.add_argument("--listener", help="listener folder with run folders, bloecke.csv and zusammenfassung.json")
    ap.add_argument("--old", help="part of the user agent of the old build")
    ap.add_argument("--switch", help="switch time UTC set by hand, otherwise from the data")
    ap.add_argument("--from", dest="search_from", help="look for the switch from this time UTC on")
    ap.add_argument("--json")
    ap.add_argument("--md")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.listener and a.old and a.json and a.md):
        ap.error("--listener, --old, --json and --md are needed")
    r = analyse(a.listener, a.old, a.switch, a.search_from)
    with open(a.json, "w") as f:
        json.dump(r, f, indent=1, ensure_ascii=False)
    with open(a.md, "w") as f:
        f.write(report(r))
    print(json.dumps({"switch_utc": r["switch_utc"], "reason": r["switch_reason"],
                      "before_blocks": r["before"]["chain_blocks"] if r["before"] else 0,
                      "after_blocks": r["after"]["chain_blocks"] if r["after"] else 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
