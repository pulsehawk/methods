#!/usr/bin/env python3
"""propagation.py - first seen at two listeners compared, PH-M-004.

Input: two block tables of listener/evaluate_bch.py (--csv), one per listener
at a different place (vantage point A and B). For every block both listeners
saw, the difference is first_seen_utc at A minus first_seen_utc at B, in
seconds with milliseconds. A negative value means A saw the block first.

The difference contains the path from the nodes to each listener and the
clocks of both hosts. It describes two places, not the propagation across the
network. Percentiles take the value at the index (n - 1) * q, rounded down.

  python3 listener/propagation.py --a blocks-a.csv --b blocks-b.csv --out propagation.json
  python3 listener/propagation.py --selftest
"""
import argparse
import csv
import json
import os
import statistics
import tempfile
from datetime import datetime, timezone


def ts(text):
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def first_seen(path):
    with open(path) as f:
        return {r["hash"]: ts(r["first_seen_utc"]) + (float("0." + r["first_seen_utc"][20:23])
                                                     if "." in r["first_seen_utc"] else 0)
                for r in csv.DictReader(f) if r.get("first_seen_utc")}


def compare(a_path, b_path):
    a, b = first_seen(a_path), first_seen(b_path)
    d = sorted(a[h] - b[h] for h in a if h in b)
    if not d:
        return {"n": 0}
    q = lambda p: d[int((len(d) - 1) * p)]  # noqa: E731
    return {"n": len(d), "a_blocks": len(a), "median_s": round(statistics.median(d), 3), "p10_s": round(q(0.1), 3),
            "p90_s": round(q(0.9), 3), "min_s": round(d[0], 3), "max_s": round(d[-1], 3),
            "a_first": sum(1 for x in d if x < 0), "b_first": sum(1 for x in d if x > 0)}


def selftest():
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    with tempfile.TemporaryDirectory() as tmp:
        rows_a = [("x1", "2026-11-17T10:00:00.100Z"), ("x2", "2026-11-17T10:01:00.500Z"), ("x3", ""),
                  ("x4", "2026-11-17T10:03:00Z")]
        rows_b = [("x1", "2026-11-17T10:00:00.300Z"), ("x2", "2026-11-17T10:01:00.250Z"), ("x3", "2026-11-17T10:02:00Z"),
                  ("x4", "2026-11-17T10:03:00Z")]
        for name, rows in (("a.csv", rows_a), ("b.csv", rows_b)):
            with open(os.path.join(tmp, name), "w") as f:
                f.write("height,hash,first_seen_utc\n")
                f.writelines(f"{i},{h},{t}\n" for i, (h, t) in enumerate(rows))
        r = compare(os.path.join(tmp, "a.csv"), os.path.join(tmp, "b.csv"))
    ok(r["n"] == 3 and r["a_blocks"] == 3, "only blocks both saw, a block without first seen is left out")
    ok(r["min_s"] == -0.2 and r["max_s"] == 0.25 and r["median_s"] == 0, "differences with milliseconds")
    ok(r["a_first"] == 1 and r["b_first"] == 1, "who saw it first, equal times count for neither")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="First seen at two listeners compared, PH-M-004.")
    ap.add_argument("--a", help="block table of listener A")
    ap.add_argument("--b", help="block table of listener B")
    ap.add_argument("--out", help="result JSON")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.a and a.b and a.out):
        ap.error("--a, --b and --out are needed")
    r = compare(a.a, a.b)
    with open(a.out, "w") as f:
        json.dump(r, f, indent=2)
        f.write("\n")
    print(json.dumps(r))


if __name__ == "__main__":
    main()
