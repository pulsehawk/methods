#!/usr/bin/env python3
"""ph_headers.py - block headers straight from the peer to peer network (PH-M-001).

One header client for two networks, Zcash and Bitcoin Cash. The networks differ
only in their parameters (message start, port, protocol version), in the
layout of the header (Zcash carries an Equihash solution after the nonce) and
in their fixed points. Everything else is shared.

No explorer, no account, no API. We ask peers with the same messages nodes use
to sync each other (version, getheaders, headers), and we check every header
before we keep it.

Procedure
  1. Peers come from the command line only, as DNS seed names (--seed) or as
     a file with one host:port per line (--peers-file). This repository holds
     no address. Addresses and host names are never printed, logged or saved.
     We name peers by their software (user agent) only.
  2. Start point is a known (height, hash): an anchor (--anchor), the highest
     published checkpoint below --from (--checkpoints), or the fixed point of
     the chain profile.
  3. Target height: several peers are asked for their height. Only peers that
     pass the fork probe of the profile count. The second highest height wins,
     so a single peer cannot set it alone.
  4. getheaders with a locator like real nodes use, until no new header comes.
  5. Every header is checked. Its parent hash must be the hash of the header
     before. Its hash must not exceed the target from its own nBits (for Zcash
     we do not verify the Equihash solution). Every fixed point and checkpoint
     in range must match. A peer on another branch is dropped and its headers
     are fetched again from the next peer.
  6. Cross check. Further peers deliver the last 500 headers again. The hashes
     must be equal.

Output
  heads CSV (gzip, no timestamp in the gzip header, so equal headers give
  equal bytes), columns hoehe,hash,zeit,nbits,version, see FORMATS.md, and a
  small JSON file with the fetch time, the tip, the checks and the cross check.
  With --raw-out the raw headers are written as well (fixture format, see
  FORMATS.md), so a third party can check them offline with --check-raw.

  python3 headers/ph_headers.py --network zcash-mainnet --seed SEED --checkpoints main-checkpoints.txt \\
      --from 3543000 --out heads.csv.gz --meta heads-meta.json
  python3 headers/ph_headers.py --check-raw headers/fixtures/zcash-mainnet-3543405.json --out heads.csv.gz
  python3 headers/ph_headers.py --selftest
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import random
import socket
import struct
import sys
import time
from datetime import datetime, timezone

USER_AGENT = b"/pulsehawk-methods:1.0/"
HEADER_COLUMNS = ["hoehe", "hash", "zeit", "nbits", "version"]

# Chain profiles. Public chain parameters only, no peer, seed or host.
# fixed: height -> hash that must match. not_at: height -> hash that must NOT
# appear there (to tell two chains with the same message start apart).
NETWORKS = {
    "zcash-mainnet": {
        "family": "zcash", "magic": "24e92764", "port": 8233, "protocol": 170190,
        "fixed": {}, "not_at": {},
        "note": "protocol as reported by Zebra 7.0.0 on mainnet, 23 Oct 2026",
    },
    "zcash-testnet": {
        "family": "zcash", "magic": "fa1af9bf", "port": 18233, "protocol": 170180,
        "fixed": {}, "not_at": {},
        "note": "protocol as reported by Zebra 7.0.0-rc.0 on testnet, 15 Oct 2026",
    },
    "bch-tempnet": {
        "family": "bch", "magic": "e2b7daaf", "port": 48333, "protocol": 70016,
        "fixed": {327988: "00000000000002bcc82b979f71d827d7788c84b981975101cb9e415ace23c46c",
                  327989: "00000000e18af2c66d44a1bbfe165acc4b87a625d211a09e33d5ee61d35dcb66"},
        "not_at": {},
        "start": 327988,
        "note": "tempnet 2027 (one minute blocks), branched from chipnet after 327,988, ended 15 Nov 2026 12:00 utc",
    },
    "bch-chipnet": {
        "family": "bch", "magic": "e2b7daaf", "port": 48333, "protocol": 70016,
        "fixed": {327988: "00000000000002bcc82b979f71d827d7788c84b981975101cb9e415ace23c46c"},
        "not_at": {327989: "00000000e18af2c66d44a1bbfe165acc4b87a625d211a09e33d5ee61d35dcb66"},
        "note": "same message start and port as the tempnet, told apart at height 327,989",
    },
}


class CheckError(Exception):
    """A header failed a check. The run stops instead of guessing."""


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def sha256d(b):
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def varint(n):
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def read_varint(b, i):
    v = b[i]
    if v < 0xFD:
        return v, i + 1
    if v == 0xFD:
        return struct.unpack_from("<H", b, i + 1)[0], i + 3
    if v == 0xFE:
        return struct.unpack_from("<I", b, i + 1)[0], i + 5
    return struct.unpack_from("<Q", b, i + 1)[0], i + 9


def target_from_nbits(bits):
    exp = bits >> 24
    mant = bits & 0x007FFFFF
    return mant >> (8 * (3 - exp)) if exp <= 3 else mant << (8 * (exp - 3))


# Header layout. Both start with version (4) and previous hash (32).
# bch:   merkle root 32, time 4, bits 4, nonce 4                      = 80 bytes
# zcash: merkle 32, block commitments 32, time 4, bits 4, nonce 32,
#        then a varint and the Equihash solution (1,344 bytes on mainnet)
TIME_OFFSET = {"bch": 68, "zcash": 100}


def split_headers(family, payload):
    """Raw headers of a headers message, without the transaction count."""
    n, i = read_varint(payload, 0)
    out = []
    for _ in range(n):
        start = i
        if family == "bch":
            i += 80
        else:
            i += 4 + 32 + 32 + 32 + 4 + 4 + 32
            sol, i = read_varint(payload, i)
            i += sol
        if i > len(payload):
            raise CheckError("headers message is cut off")
        out.append(payload[start:i])
        _, i = read_varint(payload, i)  # transaction count, always 0
    return out


def parse_header(family, raw):
    version, = struct.unpack_from("<i", raw, 0)
    t, bits = struct.unpack_from("<II", raw, TIME_OFFSET[family])
    return {"version": version, "prev": raw[4:36], "time": t, "bits": bits, "hash": sha256d(raw), "raw": raw}


def hexhash(h):
    return h[::-1].hex()


def check_chain(family, start, raws, fixed=None, not_at=None, checkpoints=None):
    """Check raw headers that follow start = (height, hash bytes).

    Returns the list of (height, header). Raises CheckError on the first
    header that breaks linkage, proof of work, a fixed point or a checkpoint.
    """
    fixed, not_at, checkpoints = fixed or {}, not_at or {}, checkpoints or {}
    height, last = start
    chain = []
    for raw in raws:
        h = parse_header(family, raw)
        height += 1
        if h["prev"] != last:
            raise CheckError(f"linkage breaks at height {height}")
        if int.from_bytes(h["hash"], "little") > target_from_nbits(h["bits"]):
            raise CheckError(f"proof of work missing at height {height}")
        want = fixed.get(height) or checkpoints.get(height)
        if want and want != hexhash(h["hash"]):
            raise CheckError(f"fixed point or checkpoint {height} does not match")
        if not_at.get(height) == hexhash(h["hash"]):
            raise CheckError(f"height {height} belongs to the other chain")
        chain.append((height, h))
        last = h["hash"]
    return chain


def heads_csv_bytes(chain):
    """Heads CSV, gzip with mtime 0, so equal headers give equal bytes."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER_COLUMNS)
    for height, h in chain:
        w.writerow([height, hexhash(h["hash"]), h["time"], f"{h['bits']:08x}", h["version"]])
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as g:
        g.write(buf.getvalue().encode())
    return out.getvalue()


def read_checkpoints(path):
    """Checkpoint file with one "height hash" per line, as published by Zebra."""
    cps = {}
    with open(path) as f:
        for line in f:
            p = line.split()
            if len(p) == 2 and p[0].isdigit() and len(p[1]) == 64:
                cps[int(p[0])] = p[1].lower()
    if not cps:
        raise CheckError("no checkpoint in the file")
    return cps


def read_peers(seeds, peers_file, port):
    """Resolve the given sources. Only the count is ever printed."""
    found = set()
    if peers_file:
        with open(peers_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                host, _, p = line.rpartition(":")
                found.add((host.strip("[]"), int(p)))
    for seed in seeds:
        try:
            for _, _, _, _, sa in socket.getaddrinfo(seed, port, proto=socket.IPPROTO_TCP):
                found.add((sa[0], sa[1]))
        except OSError as e:
            log(f"a seed did not resolve ({type(e).__name__})")
    out = list(found)
    random.shuffle(out)
    log(f"{len(out)} peer addresses from {len(seeds)} seeds and {'a' if peers_file else 'no'} peers file")
    return out


class Peer:
    def __init__(self, net, addr, timeout=12):
        self.net = net
        self.magic = bytes.fromhex(net["magic"])
        fam = socket.AF_INET6 if ":" in addr[0] else socket.AF_INET
        self.s = socket.socket(fam, socket.SOCK_STREAM)
        self.s.settimeout(timeout)
        self.s.connect(addr)
        self.buf = b""
        self.agent, self.version, self.height = "?", 0, 0

    def send(self, cmd, payload=b""):
        head = self.magic + cmd.encode().ljust(12, b"\0") + struct.pack("<I", len(payload)) + sha256d(payload)[:4]
        self.s.sendall(head + payload)

    def _read(self, n):
        while len(self.buf) < n:
            d = self.s.recv(1 << 16)
            if not d:
                raise ConnectionError("peer closed the connection")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def receive(self):
        head = self._read(24)
        if head[:4] != self.magic:
            raise ConnectionError("wrong message start")
        cmd = head[4:16].rstrip(b"\0").decode(errors="replace")
        size = struct.unpack("<I", head[16:20])[0]
        if size > 8 << 20:
            raise ConnectionError("message too large")
        payload = self._read(size)
        if sha256d(payload)[:4] != head[20:24]:
            raise ConnectionError("wrong checksum")
        if cmd == "ping":
            self.send("pong", payload)
        return cmd, payload

    def handshake(self):
        empty = struct.pack("<Q", 0) + b"\0" * 10 + b"\xff\xff" + b"\0" * 4 + struct.pack(">H", 0)
        payload = (struct.pack("<iQq", self.net["protocol"], 0, int(time.time())) + empty + empty
                   + struct.pack("<Q", random.getrandbits(64)) + varint(len(USER_AGENT)) + USER_AGENT
                   + struct.pack("<i", 0) + b"\0")
        self.send("version", payload)
        got_version = got_verack = False
        until = time.time() + 30
        while not (got_version and got_verack):
            if time.time() > until:
                raise TimeoutError("handshake takes too long")
            cmd, p = self.receive()
            if cmd == "version":
                self.version = struct.unpack_from("<i", p, 0)[0]
                n, i = read_varint(p, 80)
                self.agent = p[i:i + n].decode(errors="replace")
                self.height = struct.unpack_from("<i", p, i + n)[0]
                self.send("verack")
                got_version = True
            elif cmd == "verack":
                got_verack = True
            elif cmd == "reject":
                raise ConnectionError("version rejected")

    def headers(self, locator, wait=60):
        """getheaders with a locator (hashes, newest first). An unasked headers
        message (a new block announcement) does not hang on the locator and is
        skipped."""
        known = set(locator)
        self.send("getheaders", struct.pack("<I", self.net["protocol"]) + varint(len(locator))
                  + b"".join(locator) + b"\0" * 32)
        until = time.time() + wait
        while time.time() < until:
            cmd, p = self.receive()
            if cmd != "headers":
                continue
            out = split_headers(self.net["family"], p)
            if out and out[0][4:36] not in known:
                continue
            return out
        raise TimeoutError("no headers answer")

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def software(agent):
    """The program name of a user agent, without version or comment. A peer may
    put anything into its comment, so only this part is printed."""
    return agent.strip("/").split(":")[0].split("(")[0] or "?"


def connect(net, addrs, skip, min_height=0):
    """First reachable peer that reports at least min_height."""
    for a in addrs:
        if a in skip:
            continue
        skip.add(a)
        try:
            p = Peer(net, a)
            p.handshake()
        except (OSError, ConnectionError, TimeoutError, struct.error, IndexError) as e:
            log(f"peer refused ({type(e).__name__})")
            continue
        log(f"peer connected, software {software(p.agent)}, protocol {p.version}, reports {p.height}")
        if p.height < min_height:
            log(f"peer is behind ({min_height}), next")
            p.close()
            continue
        return a, p
    return None, None


def locator(chain, start_hash):
    """Newest first, ten single hashes, then in doubling steps, the start last."""
    out, i, step = [], len(chain) - 1, 1
    while i >= 0:
        out.append(chain[i][1]["hash"])
        if len(out) >= 10:
            step *= 2
        i -= step
    return out + [start_hash]


def probe_ok(net, p, start):
    """Fork probe: a peer counts only if the header after start matches the
    fixed point (or is not the excluded one) of the profile."""
    h_next = start[0] + 1
    if h_next not in net["fixed"] and h_next not in net["not_at"]:
        return True
    got = p.headers([start[1]], wait=20)
    if not got:
        return False
    hx = hexhash(sha256d(got[0]))
    if h_next in net["fixed"]:
        return hx == net["fixed"][h_next]
    return hx != net["not_at"][h_next]


def fetch(net, addrs, start, stop_height, checkpoints, cross_checks, max_errors=12):
    skip, reported, refused = set(), [], 0
    probe_start = start
    for fh in sorted(set(net["fixed"]) | set(net["not_at"])):
        if fh - 1 in net["fixed"]:
            probe_start = (fh - 1, bytes.fromhex(net["fixed"][fh - 1])[::-1])
            break
    tries = 0
    while len(reported) < 4 and tries < 20:
        tries += 1
        a, p = connect(net, addrs, skip, min_height=start[0])
        if p is None:
            break
        try:
            if probe_ok(net, p, probe_start):
                reported.append(p.height)
            else:
                refused += 1
                log(f"probe {software(p.agent)}: not on this chain, does not count")
        except (OSError, ConnectionError, TimeoutError, CheckError) as e:
            log(f"probe {software(p.agent)} without answer ({type(e).__name__})")
        p.close()
    if not reported:
        raise SystemExit("no peer passed the probe of this chain")
    target = sorted(reported)[-2] if len(reported) >= 2 else reported[0]
    if stop_height:
        target = min(target, stop_height)
    floor = target - 20
    log(f"target height {target} from {len(reported)} peers ({refused} refused), main peer from {floor}")

    skip = set()
    chain, agents, branches, errors = [], [], [], 0
    addr, peer = connect(net, addrs, skip, min_height=floor)

    def tip():
        return (chain[-1][0], chain[-1][1]["hash"]) if chain else start

    while True:
        if stop_height and tip()[0] >= stop_height:
            del chain[stop_height - start[0]:]
            break
        if peer is None:
            if tip()[0] >= floor:
                log(f"no peers left, ending at {tip()[0]} (target {target})")
                break
            raise SystemExit("no peer left or too many peer errors")

        def switch(reason):
            nonlocal addr, peer, errors
            log(f"peer {reason}, switching")
            peer.close()
            errors += 1
            addr, peer = (None, None) if errors > max_errors else connect(net, addrs, skip, min_height=floor)

        try:
            raws = peer.headers(locator(chain, start[1]))
        except (OSError, ConnectionError, TimeoutError, CheckError) as e:
            switch(f"failed ({type(e).__name__})")
            continue
        if peer.agent not in agents:
            agents.append(peer.agent)
        if not raws:
            if tip()[0] < target - 3:
                switch(f"sends nothing more at {tip()[0]}")
                continue
            break
        prev = raws[0][4:36]
        if prev != tip()[1]:
            if prev == start[1]:
                pos = 0
            else:
                pos = next((i + 1 for i in range(len(chain) - 1, -1, -1) if chain[i][1]["hash"] == prev), None)
                if pos is None:
                    switch("answers without a link to our chain")
                    continue
            dropped = chain[pos:]
            branches.append({"dropped": [(h, x["hash"]) for h, x in dropped], "then_from": software(peer.agent)})
            log(f"other branch, fetching {len(dropped)} headers again")
            del chain[pos:]
        try:
            chain += check_chain(net["family"], tip(), raws, net["fixed"], net["not_at"], checkpoints)
        except CheckError as e:
            switch(f"failed a check ({e})")
            continue
        if len(chain) % 8000 < len(raws):
            log(f"... height {tip()[0]}")
    if peer is not None:
        peer.close()
    final = {h: x["hash"] for h, x in chain}
    forks = []
    for b in branches:
        other = [h for h, x in b["dropped"] if final.get(h) != x]
        forks.append({"fork_height": other[0] if other else None, "blocks_on_other_branch": len(other),
                      "then_from": b["then_from"]})

    # Cross check: further peers deliver the last 500 headers again.
    probes, no_answer = [], []
    if cross_checks > 0 and chain:
        by_h = final
        cc_h = max(start[0], tip()[0] - 500)
        cc_hash = by_h.get(cc_h, start[1])
        skip.add(addr)
        deadline, tries = time.time() + 360, 0
        while len(probes) < cross_checks and tries < 25 and time.time() < deadline:
            tries += 1
            a2, p2 = connect(net, addrs, skip, min_height=tip()[0] - 50)
            if p2 is None:
                break
            try:
                if not probe_ok(net, p2, probe_start):
                    p2.close()
                    continue
                loc = [by_h[cc_h - i] for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 20, 50, 100, 200)
                       if cc_h - i in by_h] or [cc_hash]
                raws = []
                for _ in range(5):
                    part = p2.headers(loc, wait=45)
                    if not part:
                        break
                    raws += part
                    loc = [sha256d(part[-1])]
                height_of = {x: h for h, x in by_h.items()}
                from_h = height_of.get(raws[0][4:36], cc_h) + 1 if raws else cc_h + 1
                equal = sum(1 for i, r in enumerate(raws) if by_h.get(from_h + i) == sha256d(r))
                overlap = sum(1 for i in range(len(raws)) if from_h + i in by_h)
                probes.append({"software": p2.agent, "compared": overlap, "equal": equal,
                               "peer_tip": from_h + len(raws) - 1})
                log(f"cross check {software(p2.agent)}: {equal} of {overlap} equal")
            except (OSError, ConnectionError, TimeoutError, CheckError) as e:
                no_answer.append({"software": p2.agent, "error": type(e).__name__})
            p2.close()
            if p2.agent not in agents:
                agents.append(p2.agent)
    meta = {"target_height": target, "target_from_peers": len(reported), "probes_refused": refused,
            "peer_errors": errors, "forks_dropped": forks, "cross_checks": probes,
            "cross_checks_without_answer": no_answer, "peer_software": agents}
    return chain, meta


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def fixture_check(path):
    """Check a fixture file offline. Returns (chain, fixture dict)."""
    with open(path) as f:
        fx = json.load(f)
    net = NETWORKS[fx["network"]]
    start = (fx["anchor_height"], bytes.fromhex(fx["anchor_hash"])[::-1])
    raws = [bytes.fromhex(x) for x in fx["headers"]]
    chain = check_chain(net["family"], start, raws, net["fixed"], net["not_at"])
    return chain, fx


def selftest():
    here = os.path.dirname(os.path.abspath(__file__))
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    # nBits: difficulty 1 of Bitcoin and a known compact value.
    ok(target_from_nbits(0x1d00ffff) == 0xffff << (8 * 26), "target of 1d00ffff")
    ok(target_from_nbits(0x03123456) == 0x123456, "target with exponent 3")
    ok(target_from_nbits(0x01120000) == 0x12, "target with exponent 1")
    for v in (0, 252, 253, 65535, 65536, 2 ** 32):
        ok(read_varint(varint(v), 0)[0] == v, f"varint {v}")
    ok(software("/Bitcoin Cash Node:29.2.1(some.comment)/") == "Bitcoin Cash Node", "software drops the comment")
    ok(software("/Zebra:7.0.0/") == "Zebra", "software of Zebra")
    # Locator: ten single hashes, then doubling steps, start last.
    fake = [(i, {"hash": bytes([i % 256]) * 32}) for i in range(100)]
    loc = locator(fake, b"\0" * 32)
    ok(loc[0] == fake[-1][1]["hash"] and loc[-1] == b"\0" * 32 and len(loc) == 17, "locator shape")

    sums = {}
    with open(os.path.join(here, "fixtures", "SHA256SUMS")) as f:
        for line in f:
            s, name = line.split()
            sums[name] = s
    fixtures = sorted(x for x in os.listdir(os.path.join(here, "fixtures")) if x.endswith(".json"))
    ok(len(fixtures) >= 2, "one fixture per network")
    for name in fixtures:
        path = os.path.join(here, "fixtures", name)
        with open(path, "rb") as f:
            ok(hashlib.sha256(f.read()).hexdigest() == sums.get(name), f"sha256 of {name}")
        chain, fx = fixture_check(path)
        net = NETWORKS[fx["network"]]
        ok(len(chain) == 20, f"{name} has 20 headers")
        ok(chain[0][0] == fx["anchor_height"] + 1, f"{name} first height")
        ok(hexhash(chain[-1][1]["hash"]) == fx["last_hash"], f"{name} last hash")
        csv_name = name[:-5] + ".csv.gz"
        ok(hashlib.sha256(heads_csv_bytes(chain)).hexdigest() == sums.get(csv_name), f"sha256 of the CSV of {name}")
        family = net["family"]
        start = (fx["anchor_height"], bytes.fromhex(fx["anchor_hash"])[::-1])
        raws = [bytes.fromhex(x) for x in fx["headers"]]
        # Each check must catch its own fault.
        broken = bytearray(raws[5])
        broken[4] ^= 1
        try:
            check_chain(family, start, raws[:5] + [bytes(broken)] + raws[6:])
            ok(False, f"{name} linkage fault caught")
        except CheckError as e:
            ok("linkage" in str(e), f"{name} linkage fault caught")
        hard = bytearray(raws[0])
        struct.pack_into("<I", hard, TIME_OFFSET[family] + 4, 0x03000001)
        try:
            check_chain(family, start, [bytes(hard)])
            ok(False, f"{name} work fault caught")
        except CheckError as e:
            ok("proof of work" in str(e), f"{name} work fault caught")
        try:
            check_chain(family, start, raws, checkpoints={chain[3][0]: "00" * 32})
            ok(False, f"{name} checkpoint fault caught")
        except CheckError as e:
            ok("checkpoint" in str(e), f"{name} checkpoint fault caught")
        msg = varint(len(raws)) + b"".join(r + b"\0" for r in raws)
        ok(split_headers(family, msg) == raws, f"{name} headers message split")
        if fx["network"] == "bch-tempnet":
            try:
                check_chain(family, start, raws, NETWORKS["bch-chipnet"]["fixed"], NETWORKS["bch-chipnet"]["not_at"])
                ok(False, "tempnet headers refused as chipnet")
            except CheckError as e:
                ok("other chain" in str(e), "tempnet headers refused as chipnet")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Block headers over the peer to peer protocol, PH-M-001.")
    ap.add_argument("--network", choices=sorted(NETWORKS))
    ap.add_argument("--seed", action="append", default=[], help="DNS seed name of the network, repeatable")
    ap.add_argument("--peers-file", help="file with one host:port per line")
    ap.add_argument("--checkpoints", help="checkpoint file, one 'height hash' per line")
    ap.add_argument("--anchor", help="known start point height:hash")
    ap.add_argument("--from", dest="start", type=int, help="start below this height at the highest checkpoint")
    ap.add_argument("--to", type=int, help="stop at this height")
    ap.add_argument("--cross-checks", type=int, default=2)
    ap.add_argument("--out", help="heads CSV (gzip)")
    ap.add_argument("--meta", help="JSON with fetch time, tip and checks")
    ap.add_argument("--raw-out", help="also write the raw headers as a fixture file (JSON)")
    ap.add_argument("--check-raw", help="check a fixture file offline and write its heads CSV")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.check_raw:
        try:
            chain, fx = fixture_check(a.check_raw)
        except CheckError as e:
            raise SystemExit(f"check failed: {e}")
        data = heads_csv_bytes(chain)
        if a.out:
            write_bytes(a.out, data)
        print(json.dumps({"network": fx["network"], "headers": len(chain), "first": chain[0][0],
                          "last": chain[-1][0], "csv_sha256": hashlib.sha256(data).hexdigest()}))
        return
    if not a.network or not a.out or not a.meta:
        ap.error("--network, --out and --meta are needed to fetch")
    if not a.seed and not a.peers_file:
        ap.error("give peers with --seed or --peers-file")
    net = NETWORKS[a.network]
    cps = read_checkpoints(a.checkpoints) if a.checkpoints else {}
    if a.anchor:
        h, x = a.anchor.split(":")
        start = (int(h), bytes.fromhex(x)[::-1])
    elif cps and a.start is not None:
        h = max(k for k in cps if k <= a.start)
        start = (h, bytes.fromhex(cps[h])[::-1])
    elif "start" in net:
        h = net["start"]
        start = (h, bytes.fromhex(net["fixed"][h])[::-1])
    else:
        ap.error("give --anchor, or --checkpoints with --from")
    log(f"start at height {start[0]}")
    addrs = read_peers(a.seed, a.peers_file, net["port"])
    if not addrs:
        raise SystemExit("no peers")
    chain, info = fetch(net, addrs, start, a.to, cps, a.cross_checks)
    if not chain:
        raise SystemExit("no header after the start")
    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = heads_csv_bytes(chain)
    write_bytes(a.out, data)
    tip_h, tip_x = chain[-1][0], hexhash(chain[-1][1]["hash"])
    meta = {"method": "PH-M-001", "network": a.network, "source": "peer to peer protocol (getheaders)",
            "fetched_utc": fetched, "start_height": start[0], "first_height": chain[0][0],
            "tip_height": tip_h, "tip_hash": tip_x,
            "fixed_points": sorted(set(net["fixed"]) | set(net["not_at"])),
            "checkpoints_checked": sum(1 for h, _ in chain if h in cps),
            "checks": ["parent hash linkage", "hash <= target from nBits", "fixed points and checkpoints"],
            "not_checked": ["equihash solution" if net["family"] == "zcash" else "difficulty adjustment rule",
                            "block contents", "consensus rules beyond the header"],
            "csv_sha256": hashlib.sha256(data).hexdigest()}
    meta.update(info)
    meta["peer_software"] = [software(x) for x in info["peer_software"]]
    for c in meta["cross_checks"] + meta["cross_checks_without_answer"]:
        c["software"] = software(c["software"])
    write_bytes(a.meta, (json.dumps(meta, indent=2) + "\n").encode())
    if a.raw_out:
        raws = [h["raw"].hex() for _, h in chain]
        fx = {"network": a.network, "fetched_utc": fetched, "anchor_height": start[0],
              "anchor_hash": hexhash(start[1]), "last_hash": tip_x, "headers": raws}
        write_bytes(a.raw_out, (json.dumps(fx, indent=1) + "\n").encode())
    print(json.dumps({"network": a.network, "tip": tip_h, "headers": len(chain), "fetched": fetched}))


if __name__ == "__main__":
    main()
