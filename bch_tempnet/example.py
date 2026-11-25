#!/usr/bin/env python3
"""example.py - runs weekly.py, windows.py and chunks.py on the example in bch_tempnet/examples/.

The example is a short excerpt of our own tempnet data:
- weekly-heads.csv.gz: the first 1,440 tempnet headers (327,989 to 329,428),
  with weekly-heads-meta.json, cutoff 2026-10-17T09:42:42Z;
- heads.csv.gz: the headers 354,400 to 355,599 (ten chunks, one hash window
  on 3 Nov 2026), and in listener/ the blocks, the stale blocks and three
  listener runs of our listener for these heights, cut to what the tools read.

The script writes all outputs to a temporary folder and compares the sha256
of every input and output with examples/SHA256SUMS. With --write it prints the
sha256 lines instead (for maintainers, after an intended change).

  python3 bch_tempnet/example.py
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "examples")
WEEKLY_CUTOFF = "2026-10-17T09:42:42Z"


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(args):
    subprocess.run([sys.executable, *args], check=True, stdout=subprocess.DEVNULL)


def outputs(tmp):
    runs = sorted(os.path.join(EX, "listener", d) for d in os.listdir(os.path.join(EX, "listener"))
                  if os.path.isdir(os.path.join(EX, "listener", d)))
    li = os.path.join(EX, "listener")
    run([os.path.join(HERE, "weekly.py"), "--heads", os.path.join(EX, "weekly-heads.csv.gz"),
         "--meta", os.path.join(EX, "weekly-heads-meta.json"), "--cutoff", WEEKLY_CUTOFF,
         "--json", os.path.join(tmp, "weekly.json"), "--md", os.path.join(tmp, "weekly.md")])
    run([os.path.join(HERE, "windows.py"), "--heads", os.path.join(EX, "heads.csv.gz"),
         "--blocks", os.path.join(li, "bloecke.csv"), "--summary", os.path.join(li, "zusammenfassung.json"),
         "--runs", *runs, "--out", os.path.join(tmp, "windows.json")])
    run([os.path.join(HERE, "chunks.py"), "--heads", os.path.join(EX, "heads.csv.gz"),
         "--summary", os.path.join(li, "zusammenfassung.json"), "--runs", *runs,
         "--csv", os.path.join(tmp, "chunks.csv"), "--no-diff1-csv", os.path.join(tmp, "chunks-no-diff1.csv"),
         "--slot-csv", os.path.join(tmp, "chunks-slot.csv"), "--slot-nomin-csv", os.path.join(tmp, "chunks-slot-nomin.csv"),
         "--out", os.path.join(tmp, "chunks.json")])
    return ["weekly.json", "weekly.md", "windows.json", "chunks.csv", "chunks-no-diff1.csv",
            "chunks-slot.csv", "chunks-slot-nomin.csv", "chunks.json"]


def inputs():
    out = []
    for root, _, files in os.walk(EX):
        for f in files:
            if f != "SHA256SUMS":
                out.append(os.path.relpath(os.path.join(root, f), EX))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description="Run the bch_tempnet tools on the example and check the sha256.")
    ap.add_argument("--write", action="store_true", help="print the sha256 lines instead of checking them")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        names = outputs(tmp)
        lines = [f"{sha(os.path.join(EX, p))}  input/{p}" for p in inputs()]
        lines += [f"{sha(os.path.join(tmp, n))}  output/{n}" for n in names]
    if a.write:
        print("\n".join(lines))
        return 0
    with open(os.path.join(EX, "SHA256SUMS")) as f:
        want = f.read().split("\n")
    want = [w for w in want if w]
    bad = sorted(set(want) ^ set(lines))
    for b in bad:
        print("MISMATCH", b)
    print(f"{len(lines)} files, " + ("all sha256 match" if not bad else f"{len(bad)} lines differ"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
