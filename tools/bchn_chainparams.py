#!/usr/bin/env python3
"""bchn_chainparams.py - seeds and checkpoints of one network from BCHN src/chainparams.cpp.

Reads a local copy of src/chainparams.cpp of Bitcoin Cash Node (public
source) and writes, for one parameter class, the checkpoints as a file with one
"height hash" per line, the format headers/ph_headers.py --checkpoints reads.
With --seeds it prints the DNS seed names of that class, one per line, so a
caller can pass them to ph_headers.py --seed without keeping them in a file of
this repository.

The file is read as text only, nothing in it is run.

  python3 tools/bchn_chainparams.py chainparams.cpp --class CMainParams --checkpoints bch-mainnet-checkpoints.txt
  python3 tools/bchn_chainparams.py chainparams.cpp --class CMainParams --seeds
  python3 tools/bchn_chainparams.py --selftest
"""
import argparse
import re
import sys


def class_part(text, name):
    """Text of one parameter class, from its declaration to the next class."""
    m = re.search(r"class\s+%s\b" % re.escape(name), text)
    if not m:
        raise SystemExit(f"class {name} not found")
    nxt = re.search(r"\nclass\s+\w+", text[m.end():])
    return text[m.start(): m.end() + nxt.start()] if nxt else text[m.start():]


def checkpoints(part):
    """(height, hash) pairs from checkpointData, hex strings may be split over lines."""
    block = part[part.index("checkpointData"):]
    block = block[: block.index("}};") + 3] if "}};" in block else block
    out = []
    for m in re.finditer(r'\{\s*(\d+)\s*,\s*BlockHash::fromHex\(((?:\s*"[0-9a-fA-F]+")+)\s*\)\s*\}', block):
        h = "".join(re.findall(r'"([0-9a-fA-F]+)"', m.group(2))).lower()
        if len(h) != 64:
            raise SystemExit(f"checkpoint {m.group(1)} has a hash of {len(h)} characters")
        out.append((int(m.group(1)), h))
    if not out:
        raise SystemExit("no checkpoint found")
    return out


def seeds(part):
    return re.findall(r'vSeeds\.emplace_back\("([^"]+)"\)', part)


def selftest():
    sample = '''
class CMainParams : public CChainParams {
    CMainParams() {
        vSeeds.emplace_back("seed.example.invalid");
        checkpointData = {
            /* .mapCheckpoints = */ {
                {11111, BlockHash::fromHex("0000000069e244f73d78e8fd29ba2fd2ed6"
                                           "18bd6fa2ee92559f542fdb26e7c1d")},
                {951145, BlockHash::fromHex("000000000000000000f64dbed68370486f945097ba90b17e30d7bf5c43df7a60")},
            }};
    }
};
class CTestNetParams : public CChainParams {
    CTestNetParams() {
        vSeeds.emplace_back("other.example.invalid");
        checkpointData = { {{1, BlockHash::fromHex("00")}} };
    }
};
'''
    part = class_part(sample, "CMainParams")
    cps = checkpoints(part)
    assert cps == [(11111, "0000000069e244f73d78e8fd29ba2fd2ed618bd6fa2ee92559f542fdb26e7c1d"),
                   (951145, "000000000000000000f64dbed68370486f945097ba90b17e30d7bf5c43df7a60")], cps
    assert seeds(part) == ["seed.example.invalid"]
    assert "other.example" not in part
    print("4 checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Seeds and checkpoints from BCHN chainparams.cpp.")
    ap.add_argument("path", nargs="?", help="local copy of src/chainparams.cpp")
    ap.add_argument("--class", dest="cls", default="CMainParams")
    ap.add_argument("--checkpoints", help="write the checkpoints to this file")
    ap.add_argument("--seeds", action="store_true", help="print the DNS seed names, one per line")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.path:
        ap.error("give the path of chainparams.cpp")
    with open(a.path, encoding="utf-8") as f:
        part = class_part(f.read(), a.cls)
    if a.checkpoints:
        cps = checkpoints(part)
        with open(a.checkpoints, "w") as f:
            f.writelines(f"{h} {x}\n" for h, x in cps)
        print(f"{len(cps)} checkpoints, highest {cps[-1][0]}", file=sys.stderr)
    if a.seeds:
        for s in seeds(part):
            print(s)


if __name__ == "__main__":
    main()
