#!/usr/bin/env python3
"""example.py - runs the listener tools on the example in listener/examples/.

The example is a short excerpt of our own recordings, cut to the fields the
tools read (the meta files keep start, end, planned end, minutes, protocol,
the count of peers refused by the fork probe, and per connection its label,
its user agent cut by agent_label(), version, connect and disconnect time and
the number of announcements):
- bch-chipnet: two runs of our chipnet listener on 24 Nov 2026 and our
  chipnet headers from one hour before to one hour after them;
- zcash-mainnet: two runs of our Zcash mainnet listener on 25 Nov 2026 and
  our mainnet headers for the same span;
- bch-tempnet: one run of a listener at a second place (a) and the two runs of
  our first listener (b) that overlap it, on 9 Nov 2026, with our tempnet
  headers for the span.

The script runs evaluate_bch.py, evaluate_zcash.py and propagation.py, replays
the recorded messages of listen.py's self test, writes all outputs to a
temporary folder and compares the sha256 of every input and output with
examples/SHA256SUMS. With --write it prints the sha256 lines instead (for
maintainers, after an intended change).

  python3 listener/example.py
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "examples")
ACTIVATION = 3543405
sys.path.insert(0, HERE)
import listen  # noqa: E402


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(args):
    subprocess.run([sys.executable, *args], check=True, stdout=subprocess.DEVNULL)


def runs(*parts):
    d = os.path.join(EX, *parts)
    return sorted(os.path.join(d, x) for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))


def outputs(tmp):
    names = []
    for name, rdirs, heads in (("chipnet", runs("bch-chipnet", "runs"), os.path.join(EX, "bch-chipnet", "heads.csv.gz")),
                               ("tempnet-a", runs("bch-tempnet", "a"), os.path.join(EX, "bch-tempnet", "heads.csv.gz")),
                               ("tempnet-b", runs("bch-tempnet", "b"), os.path.join(EX, "bch-tempnet", "heads.csv.gz"))):
        run([os.path.join(HERE, "evaluate_bch.py"), "--runs", *rdirs, "--heads", heads,
             "--out", os.path.join(tmp, f"bch-{name}.json"), "--csv", os.path.join(tmp, f"bch-{name}.csv")])
        names += [f"bch-{name}.json", f"bch-{name}.csv"]
    run([os.path.join(HERE, "evaluate_zcash.py"), "--runs", *runs("zcash-mainnet", "runs"),
         "--heads", os.path.join(EX, "zcash-mainnet", "heads.csv.gz"), "--activation", str(ACTIVATION),
         "--out", os.path.join(tmp, "zcash.json")])
    run([os.path.join(HERE, "propagation.py"), "--a", os.path.join(tmp, "bch-tempnet-a.csv"),
         "--b", os.path.join(tmp, "bch-tempnet-b.csv"), "--out", os.path.join(tmp, "propagation.json")])
    names += ["zcash.json", "propagation.json"]
    fixtures = os.path.join(os.path.dirname(HERE), "headers", "fixtures")
    for network, fixture in (("bch-tempnet", "bch-tempnet-327989.json"), ("zcash-mainnet", "zcash-mainnet-3543405.json")):
        with open(os.path.join(fixtures, fixture)) as f:
            raws = [bytes.fromhex(x) for x in json.load(f)["headers"]]
        out = os.path.join(tmp, "replay-" + network)
        os.makedirs(out)
        listen._replay(network, listen.recorded_messages(listen.H.NETWORKS[network]["family"], raws), out)
        for x in sorted(os.listdir(out)):
            os.rename(os.path.join(out, x), os.path.join(tmp, f"replay-{network}-{x}"))
            names.append(f"replay-{network}-{x}")
    return names


def inputs():
    out = []
    for root, _, files in os.walk(EX):
        for f in files:
            if f != "SHA256SUMS":
                out.append(os.path.relpath(os.path.join(root, f), EX))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description="Run the listener tools on the example and check the sha256.")
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
        want = [w for w in f.read().split("\n") if w]
    bad = sorted(set(want) ^ set(lines))
    for b in bad:
        print("MISMATCH", b)
    print(f"{len(lines)} files, " + ("all sha256 match" if not bad else f"{len(bad)} lines differ"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
