#!/usr/bin/env python3
"""report_v1.py - raw values M1 to M7 of the PulseHawk Upgrade Report, specification v1.0.

Per network upgrade:
  M1  block intervals p50, p90, p99 against the target          (PH-M-001)
  M2  longest gap, number of gaps above 5 x target                (PH-M-001)
  M3  tip switches by depth (1, 2, 3+), headline from depth 2     (PH-M-002)
  M4  chain split, duration                                       (PH-M-002)
  M5  share of peers below the minimum protocol at activation,
      +24 h and +72 h (and further probe times), each with n      (PH-M-003)
  M6  time to stable: rolling 60 min mean of the intervals within
      +-20 % of the target, held for 60 min that lie fully inside
      the data; seconds after the activation block's header time  (PH-M-001)
  M7  first block at the activation height: header time, first
      seen by our listener, offset to our estimate                (PH-M-001, PH-M-004)

Every row carries its kind:
  counted   counted by us from our own data
  source    taken from a named outside source
  computed  computed from a model, an estimate, or a difference to an estimate
plus the method id, the window in UTC and n. The CSV gets a .sha256 file in
sha256sum format, and a second CSV lists the measurement uncertainty per
metric. See METHODS.md and FORMATS.md.

Windows. t_act is the header time of the block at the activation height.
  before  blocks below the activation height from t_act - 24 h
  after   blocks from the activation height up to t_act + 72 h
The windows are cut by height: the first block whose header time reaches the
limit, and every block after it (header times need not increase).

  python3 upgrade_report/report_v1.py --config CONFIG --heads HEADS.csv.gz [--raw RAW_DIR] --out OUT_DIR
  python3 upgrade_report/report_v1.py --selftest

Standard library only, Python 3.9 or later.
"""
import argparse
import bisect
import csv
import gzip
import hashlib
import io
import json
import math
import os
import sys
from datetime import datetime, timezone

VERSION = "1.0.0"
COLUMNS = ["metric", "key", "value", "unit", "kind", "method", "window_from_utc", "window_to_utc", "n", "source",
           "note"]
U_COLUMNS = ["metric", "method", "time_base", "clock", "missing_data", "gaps", "peers", "note"]
BEFORE_H, AFTER_H = 24, 72
STABLE_WINDOW_S, STABLE_HOLD_S, STABLE_TOLERANCE = 3600, 3600, 0.20
HEADLINE_DEPTH = 2          # M3 headline (and the watch alarm) from this depth
LISTENER_GAP_S = 600        # a pause without any announcement longer than this counts as a gap
HERE = os.path.dirname(os.path.abspath(__file__))


def ts(text):
    """'2026-10-17 15:14:57', '2026-10-17T15:14:57Z' or an ISO time with offset -> unix seconds (UTC)."""
    text = text.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    d = datetime.fromisoformat(text)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def quantile(s, q):
    """Linear interpolation between the closest ranks (numpy's default method)."""
    s = sorted(s)
    if not s:
        return None
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def rnd(x, n=1):
    return "" if x is None else round(x, n)


def read_heads(path):
    """height -> (hash, header time). Columns hoehe,hash,zeit (as written by our header client) or height,hash,time."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        rows = list(csv.DictReader(f))
    h, t = ("hoehe", "zeit") if rows and "hoehe" in rows[0] else ("height", "time")
    return {int(x[h]): (x["hash"], int(x[t])) for x in rows}


def windows(chain, act, t_act):
    heights = sorted(chain)
    before = [h for h in heights if h < act]
    a = next((i for i, h in enumerate(before) if chain[h][1] >= t_act - BEFORE_H * 3600), None)
    before = before[a:] if a is not None else []
    after = [h for h in heights if h >= act]
    e = next((i for i, h in enumerate(after) if chain[h][1] >= t_act + AFTER_H * 3600), None)
    after = after[:e] if e is not None else after
    return before, after


def intervals(chain, heights):
    return [(h, chain[h][1] - chain[h - 1][1]) for h in heights if h - 1 in chain]


def stable_from(series, lo, hi, hold_s=STABLE_HOLD_S):
    """First time t from which every value in [t, t + hold_s] lies in [lo, hi].

    series: [(time, value)] ascending. t only counts if the whole hold_s after it
    lies inside the data (t + hold_s <= last time); a band that only holds until
    the end of the data does not count (specification v1.0).
    """
    if not series:
        return None
    end = series[-1][0]
    inside = [lo <= v <= hi for _, v in series]
    for i, (t, _) in enumerate(series):
        if not inside[i] or t + hold_s > end:
            continue
        j = i
        while j < len(series) and series[j][0] <= t + hold_s and inside[j]:
            j += 1
        if j == len(series) or series[j][0] > t + hold_s:
            return t
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def build(cfg, heads_path, raw_dir):
    up = cfg["upgrade"]
    act, name = up["height"], up["name"]
    t_before, t_after = cfg["target_before_s"], cfg["target_after_s"]
    estimate = ts(up["estimate_utc"]) if up.get("estimate_utc") else None
    band = up.get("band_utc")
    chain = read_heads(heads_path)
    if act not in chain:
        raise SystemExit(f"height {act} not in the heads (range {min(chain)} to {max(chain)})")
    t_act = chain[act][1]
    before, after = windows(chain, act, t_act)
    rows, unc = [], []
    src_heads = os.path.basename(heads_path)

    def row(m, k, value, unit, kind, method, frm, to, n, source="", note=""):
        rows.append({"metric": m, "key": k, "value": value, "unit": unit, "kind": kind, "method": method,
                     "window_from_utc": utc(frm) if isinstance(frm, (int, float)) else frm,
                     "window_to_utc": utc(to) if isinstance(to, (int, float)) else to,
                     "n": n, "source": source, "note": note})

    win = {}
    for part, heights, target in (("before", before, t_before), ("after", after, t_after)):
        if not heights:
            continue
        iv = intervals(chain, heights)
        w = [x for _, x in iv]
        frm, to = chain[heights[0]][1], chain[heights[-1]][1]
        missing = (heights[-1] - heights[0] + 1) - len(heights)
        win[part] = (frm, to, iv, target, heights, missing)
        n = len(w)
        hb = f"heights {heights[0]} to {heights[-1]}; time base header times"
        row("M1", f"target_{part}", target, "s", "source", "PH-M-001", frm, to, "", cfg.get("target_source", ""),
            "target block interval")
        for q, nm, factor in ((0.5, "p50", math.log(2)), (0.9, "p90", math.log(10)), (0.99, "p99", math.log(100))):
            row("M1", f"{nm}_{part}", rnd(quantile(w, q)), "s", "counted", "PH-M-001", frm, to, n, src_heads, hb)
            row("M1", f"{nm}_{part}_model", rnd(factor * target), "s", "computed", "PH-M-001", frm, to, "", "",
                "exponential intervals with mean = target")
        row("M1", f"mean_{part}", rnd(sum(w) / n, 2), "s", "counted", "PH-M-001", frm, to, n, src_heads, hb)
        row("M1", f"negative_{part}", sum(1 for x in w if x < 0), "intervals", "counted", "PH-M-001", frm, to, n,
            src_heads, "header time earlier than the parent's")
        h_max, l_max = max(iv, key=lambda z: z[1])
        row("M2", f"longest_gap_{part}", l_max, "s", "counted", "PH-M-001", frm, to, n, src_heads,
            f"before block {h_max}, header time {utc(chain[h_max][1])} UTC; time base header times")
        row("M2", f"gaps_above_5x_target_{part}", sum(1 for x in w if x > 5 * target), "intervals", "counted",
            "PH-M-001", frm, to, n, src_heads, f"interval above {5 * target} s; time base header times")
        row("M2", f"gaps_above_5x_target_{part}_model", rnd(n * math.exp(-5), 1), "intervals", "computed",
            "PH-M-001", frm, to, n, "", "n x e^-5 for exponential intervals")

    # ------------------------------------------------ raw data of the watch
    log, state, events = [], {}, []
    peers_meta = []
    if raw_dir:
        p = os.path.join(raw_dir, "wache-log.csv")
        if os.path.exists(p):
            with open(p, newline="") as f:
                log = list(csv.DictReader(f))
        p = os.path.join(raw_dir, "zustand.json")
        if os.path.exists(p):
            state = json.load(open(p))
        p = os.path.join(raw_dir, "ereignisse.jsonl.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                events = [json.loads(z) for z in f if z.strip()]
        p = os.path.join(raw_dir, "peers.json")
        if os.path.exists(p):
            peers_meta = json.load(open(p)).get("peers", [])
    first_ms = {}
    for e in events:
        if e["hash"] not in first_ms or e["t_ms"] < first_ms[e["hash"]]:
            first_ms[e["hash"]] = e["t_ms"]
    src_log = "wache-log.csv (our watch)"

    def inside(text, frm, to):
        t = ts(text)
        return frm <= t < to

    def listener_quality(frm, to, heights):
        ev = sorted(e["t_ms"] / 1000 for e in events if frm <= e["t_ms"] / 1000 < to + 1)
        peers = sorted({e["peer"] for e in events if frm <= e["t_ms"] / 1000 < to + 1})
        if not ev:
            return {"peers": 0, "gaps": "no listener data in the window", "unseen": len(heights)}
        stops = [frm] + ev + [to]
        gaps = [(a, b) for a, b in zip(stops, stops[1:]) if b - a > LISTENER_GAP_S]
        unseen = sum(1 for h in heights if chain[h][0] not in first_ms)
        g = (f"{len(gaps)} pause(s) over {LISTENER_GAP_S} s without any announcement, longest "
             f"{round(max(b - a for a, b in gaps))} s" if gaps else f"no pause over {LISTENER_GAP_S} s")
        return {"peers": len(peers), "gaps": g, "unseen": unseen}

    for part, (frm, to, iv, _, heights, _) in win.items():
        to_log = to + 1   # include the last second of the window
        if not log:
            row("M3", f"tip_switches_{part}", "missing", "", "counted", "PH-M-002", frm, to, "", "", "no watch log")
            row("M4", f"chain_split_duration_{part}", "missing", "", "counted", "PH-M-002", frm, to, "", "",
                "no watch log")
            continue
        depths = [int(float(z["wert"])) for z in log if z["signal"] == "reorg_gesehen" and inside(z["utc"], frm, to_log)]
        tb = "time base arrival at our watch"
        row("M3", f"tip_switches_depth_{HEADLINE_DEPTH}_plus_{part}", sum(1 for x in depths if x >= HEADLINE_DEPTH),
            "switches", "counted", "PH-M-002", frm, to, len(iv), src_log,
            f"headline: switches of our tip with depth {HEADLINE_DEPTH} or more (as the alarm); n = blocks in the window; {tb}")
        for nm, test in (("1", lambda x: x == 1), ("2", lambda x: x == 2), ("3_plus", lambda x: x >= 3)):
            row("M3", f"tip_switches_depth_{nm}_{part}", sum(1 for x in depths if test(x)), "switches", "counted",
                "PH-M-002", frm, to, len(depths), src_log, f"distribution of all switches; n = all switches; {tb}")
        row("M3", f"tip_switch_max_depth_{part}", max(depths) if depths else 0, "blocks", "counted", "PH-M-002",
            frm, to, len(depths), src_log, tb)
        eps, start = [], None
        for z in log:
            if z["signal"] != "kettenspaltung":
                continue
            t = ts(z["utc"])
            if z["stufe"] == "alarm" and start is None:
                start = t
            elif z["stufe"] == "entwarnung" and start is not None:
                eps.append((start, t))
                start = None
        if start is not None:
            eps.append((start, to))
        eps = [(max(a, frm), min(b, to)) for a, b in eps if b > frm and a < to]
        k = cfg["fork"]
        definition = (f"two branches from the activation height, each announced by at least {k['min_peers']} peers, "
                      f"both deeper than {k['min_depth']} blocks")
        row("M4", f"chain_split_duration_{part}", round(sum(b - a for a, b in eps)), "s", "counted", "PH-M-002",
            frm, to, len(eps), src_log, definition + "; n = episodes; " + tb)
        row("M4", f"chain_split_longest_{part}", round(max((b - a for a, b in eps), default=0)), "s", "counted",
            "PH-M-002", frm, to, len(eps), src_log, tb)

    probes = state.get("proben", {})
    for h in [str(x) for x in cfg["probes_h"]]:
        title = "activation" if h == "0" else f"activation_plus_{h}h"
        p = probes.get(h)
        if not p:
            row("M5", f"peers_below_min_protocol_{title}", "missing", "%", "counted", "PH-M-003", "", "", "", "",
                "no probe in the watch state")
            continue
        row("M5", f"peers_below_min_protocol_{title}", p["anteil_prozent"], "%", "counted", "PH-M-003", p["utc"],
            p["utc"], p["peers"], "zustand.json (our probe, version handshake)",
            f"{p['vor_upgrade']} of {p['peers']} below {cfg['min_protocol']}; versions {p.get('versionen', {})}")

    if "after" in win:
        frm, to, iv, target, _, _ = win["after"]
        pts = sorted((chain[h][1], x) for h, x in iv)
        times = [t for t, _ in pts]
        series = []
        for i, (t, _) in enumerate(pts):
            if t < frm + STABLE_WINDOW_S:
                continue
            a = bisect.bisect_right(times, t - STABLE_WINDOW_S)
            w60 = [x for _, x in pts[a:i + 1]]
            series.append((t, sum(w60) / len(w60)))
        lo, hi = target * (1 - STABLE_TOLERANCE), target * (1 + STABLE_TOLERANCE)
        inside_band = [lo <= m <= hi for _, m in series]
        stable = stable_from(series, lo, hi)
        note = (f"rolling mean of the intervals of the last 60 min within {round(lo, 1)} to {round(hi, 1)} s "
                f"(target {target} s +-20 %); stable = first time from which it stays in the band for 60 min without "
                f"a break, at the earliest 60 min after activation; those 60 min must lie fully inside the data; "
                f"seconds after the activation block's header time; n = time points from then on; time base header times")
        row("M6", "time_to_stable", int(round(stable - frm)) if stable is not None else "never", "s", "counted",
            "PH-M-001", frm, to, len(series), src_heads,
            note + (f"; stable from {utc(stable)} UTC" if stable is not None else ""))
        row("M6", "share_in_band", rnd(100 * sum(inside_band) / len(inside_band) if inside_band else None), "%",
            "counted", "PH-M-001", frm, to, len(series), src_heads,
            "share of time points at which the rolling mean lies in the band")

    h_act, t_head = chain[act]
    row("M7", "activation_height", act, "height", "source", "PH-M-001", "", "", "", up.get("height_source", ""), name)
    row("M7", "first_block_header_time", utc(t_head), "UTC", "counted", "PH-M-001", t_head, t_head, 1, src_heads,
        f"hash {h_act}")
    first = first_ms.get(h_act)
    if first is None and state.get("aktiv_gesehen_utc"):
        first = ts(state["aktiv_gesehen_utc"]) * 1000
    if first is not None:
        row("M7", "first_block_first_seen", utc(first / 1000), "UTC", "counted", "PH-M-004", first / 1000,
            first / 1000, 1, "our listener", "first announcement to us")
        row("M7", "first_seen_minus_header_time", round(first / 1000 - t_head, 1), "s", "counted", "PH-M-004", "",
            "", 1, "", "")
    else:
        row("M7", "first_block_first_seen", "missing", "UTC", "counted", "PH-M-004", "", "", "", "",
            "no listener data")
    if estimate is not None:
        row("M7", "estimate", utc(estimate), "UTC", "computed", "PH-M-001", "", "", "", "our forecast",
            f"90 % band {band[0]} to {band[1]}" if band else "")
        row("M7", "header_time_minus_estimate", round(t_head - estimate), "s", "computed", "PH-M-001", "", "", 1, "",
            "positive means later than estimated")
        if band:
            row("M7", "in_band", "yes" if ts(band[0]) <= t_head <= ts(band[1]) else "no", "", "computed",
                "PH-M-001", band[0], band[1], 1, "", "")

    # ------------------------------------------------ uncertainty, one row per metric
    clock = ("header times are set by miners within the consensus rules and are not a clock; "
             "not monotonic, see the negative intervals in M1")
    lclock = "listener host clock synced by NTP, offset not measured; arrival times in milliseconds"
    for part, (frm, to, iv, _, heights, missing) in win.items():
        q = listener_quality(frm, to, heights) if events else None
        miss_h = f"{missing} heights missing in the heads ({part})"
        unc.append({"metric": f"M1 {part}", "method": "PH-M-001", "time_base": "header times", "clock": clock,
                    "missing_data": miss_h, "gaps": "none, the heads are a continuous chain" if not missing else miss_h,
                    "peers": "heads fetched from peers and checked for linkage and proof of work",
                    "note": "the quantiles are exact for the window; the window edges follow header times"})
        unc.append({"metric": f"M2 {part}", "method": "PH-M-001", "time_base": "header times", "clock": clock,
                    "missing_data": miss_h, "gaps": "a gap is a property of the chain, not of our coverage",
                    "peers": "as M1", "note": ""})
        unc.append({"metric": f"M3 {part}", "method": "PH-M-002", "time_base": "arrival at our watch",
                    "clock": lclock,
                    "missing_data": (f"{q['unseen']} of {len(heights)} chain blocks never announced to us"
                                     if q else "no listener data"),
                    "gaps": q["gaps"] if q else "no listener data",
                    "peers": f"{q['peers']} distinct peers announced blocks" if q else "",
                    "note": "a switch we did not see is not counted; a node that sees the winning block first never switches"})
        unc.append({"metric": f"M4 {part}", "method": "PH-M-002", "time_base": "arrival at our watch",
                    "clock": lclock, "missing_data": "as M3", "gaps": q["gaps"] if q else "no listener data",
                    "peers": f"{q['peers']} distinct peers; a split needs at least {cfg['fork']['min_peers']} peers per side" if q else "",
                    "note": "the duration has the resolution of the watch cycle"})
    unc.append({"metric": "M5", "method": "PH-M-003", "time_base": "probe time of the watch", "clock": lclock,
                "missing_data": "peers that do not answer the handshake are not in n",
                "gaps": "one probe per time point", "peers": "n per probe in the report row",
                "note": "only peers reachable from public address sources; the share is of reachable peers, not of all nodes"})
    if "after" in win:
        unc.append({"metric": "M6", "method": "PH-M-001", "time_base": "header times", "clock": clock,
                    "missing_data": win["after"][5] and f"{win['after'][5]} heights missing" or "none",
                    "gaps": "the 60 min hold must lie fully inside the data",
                    "peers": "as M1", "note": "the rolling mean of a 60 min window is noisy; see METHODS.md"})
    unc.append({"metric": "M7", "method": "PH-M-001 PH-M-004", "time_base": "header time and arrival at our listener",
                "clock": clock + "; " + lclock,
                "missing_data": "first seen is missing if the listener was not connected at activation",
                "gaps": "", "peers": f"{len(peers_meta)} peer connections in the raw data" if peers_meta else "",
                "note": "the estimate is a forecast, not a measurement"})
    return rows, unc, t_act


def write(out_dir, stem, rows, unc, cfg, inputs):
    os.makedirs(out_dir, exist_ok=True)
    outputs = {}
    for suffix, cols, data in (("", COLUMNS, rows), ("-uncertainty", U_COLUMNS, unc)):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(data)
        content = buf.getvalue().encode()
        name = f"{stem}{suffix}.csv"
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(content)
        outputs[name] = hashlib.sha256(content).hexdigest()
    with open(os.path.join(out_dir, stem + ".sha256"), "w") as f:
        f.write("".join(f"{h}  {n}\n" for n, h in outputs.items()))
    with open(os.path.join(out_dir, stem + "-inputs.json"), "w") as f:
        json.dump({"script": "upgrade_report/report_v1.py", "version": VERSION, "outputs_sha256": outputs,
                   "inputs_sha256": inputs, "upgrade": cfg["upgrade"]["name"], "chain": cfg["chain"]}, f, indent=1)
        f.write("\n")
    return outputs


def run(config, heads, raw, out):
    cfg = json.load(open(config))
    rows, unc, _ = build(cfg, heads, raw)
    inputs = {os.path.basename(config): sha256_file(config), os.path.basename(heads): sha256_file(heads)}
    if raw and os.path.isdir(raw):
        for n in sorted(os.listdir(raw)):
            p = os.path.join(raw, n)
            if os.path.isfile(p):
                inputs["raw/" + n] = sha256_file(p)
    stem = f"report-v1-{cfg['chain']}-{cfg['upgrade']['name']}"
    return stem, write(out, stem, rows, unc, cfg, inputs)


# ---------------------------------------------------------------- self test
def selftest():
    fails = []

    def ok(name, cond, info=""):
        print("%-4s %s%s" % ("ok" if cond else "FAIL", name, ("  " + str(info)) if not cond and info else ""))
        if not cond:
            fails.append(name)

    v = [(t, 25.0) for t in range(0, 7201, 60)]
    ok("in the band throughout: stable from the first point", stable_from(v, 20, 30) == 0)
    v = [(t, 40.0 if t < 600 else 25.0) for t in range(0, 7201, 60)]
    ok("in the band from 600 s: stable from 600", stable_from(v, 20, 30) == 600)
    v = [(t, 40.0 if t < 3000 else 25.0) for t in range(0, 6001, 60)]
    ok("band holds 50 min until the end of the data: not stable", stable_from(v, 20, 30) is None)
    v = [(t, 40.0 if t < 2400 else 25.0) for t in range(0, 6001, 60)]
    ok("band holds exactly 60 min until the end of the data: stable", stable_from(v, 20, 30) == 2400)
    v = [(t, 40.0 if t in (1200, 4800) else 25.0) for t in range(0, 9001, 60)]
    ok("breaks at 1200 and 4800: stable from 4860", stable_from(v, 20, 30) == 4860, stable_from(v, 20, 30))
    ok("edge values 20 and 30 count as in the band", stable_from([(0, 20.0), (1800, 30.0), (3600, 25.0)], 20, 30) == 0)
    ok("empty series", stable_from([], 20, 30) is None)
    ok("never in the band", stable_from([(t, 50.0) for t in range(0, 7201, 60)], 20, 30) is None)
    ok("median of 1..4 is 2.5", quantile([4, 1, 3, 2], 0.5) == 2.5)
    ok("p90 of 0..10 is 9", quantile(list(range(11)), 0.9) == 9)

    ex = os.path.join(HERE, "..", "examples", "zcash-testnet-2026-10-17")
    if os.path.isdir(ex):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            stem, outs = run(os.path.join(ex, "config.json"), os.path.join(ex, "heads.csv.gz"),
                             os.path.join(ex, "raw"), d)
            expected = {}
            with open(os.path.join(ex, "expected", stem + ".sha256")) as f:
                for line in f:
                    h, n = line.split()
                    expected[n] = h
            ok("example reproduces the expected sha256", outs == expected, outs)
    else:
        print("skip example (examples/ not found)")
    print("")
    print(f"{len(fails)} failed" if fails else "all tests passed")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("--heads")
    ap.add_argument("--raw", help="directory with the raw export of the watch (optional)")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.config and a.heads and a.out):
        ap.error("--config, --heads and --out are required")
    stem, outs = run(a.config, a.heads, a.raw, a.out)
    print(json.dumps({"out": a.out, "files": outs}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
