#!/usr/bin/env python3
"""listen.py - block announcements from the peer to peer network, input of PH-M-004 and PH-M-005.

One listener for two chain families, Zcash and Bitcoin Cash. It holds
connections to several nodes for a given number of minutes and writes every
block announcement with its time of arrival. The network profiles, the message
layer and the header layout come from headers/ph_headers.py.

What this is and what it is not: ONE listener at ONE place. It sees only what
the connected nodes announce to it, and the time of arrival includes the path
from the node to us. It is not a node and does not validate blocks.

Peers come from the command line only, as DNS seed names (--seed) or as a
file with one host:port per line (--peers-file). Addresses and host names are
never printed, logged or saved. A connection is named by a label per run
(zebra-1, zakura-2 for Zcash, peer_1, peer_2 for Bitcoin Cash) and by the user
agent of the peer, cut before the first "(" and with anything that looks like
an address or a host name replaced (agent_label()).

Zcash (zcash-mainnet, zcash-testnet)
  Up to 8 connections per node software (Zebra and Zakura), other software is
  not kept. For every new block hash the listener asks the first announcer for
  the block (getdata) and keeps only its header, so stale blocks get a parent,
  a time and nBits as well.

Bitcoin Cash (bch-tempnet, bch-chipnet)
  - Fork probe: after the handshake getheaders from the last block both chains
    share (327,988). On the tempnet the first header must be the tempnet block
    327,989, on the chipnet it must not be.
  - Every announcement is written with its message type, inv, headers or
    cmpctblock. Every peer is asked for compact blocks in high bandwidth mode
    (sendcmpct), every second peer also for header announcements (sendheaders).
  - Compact block path: for every new block the listener asks the first
    announcer for a cmpctblock (getdata type 4), unless one came on its own
    within 3 s. It keeps the transaction ids peers announce (relay=1 in the
    handshake) and counts the short ids of each cmpctblock (BIP152, SipHash-2-4
    with the key from SHA256(header || nonce), 6 bytes) it does not know. That
    estimates what a node with exactly these transactions would have had to
    ask for, not the number of a real node.

Output in --out: ereignisse.jsonl (announcements), koepfe.jsonl (headers),
compact.jsonl (Bitcoin Cash only) and meta.json. The field names are those of
our recorded series, see FORMATS.md.

  python3 listener/listen.py --network bch-chipnet --seed SEED --minutes 330 --out RUN_DIR
  python3 listener/listen.py --network zcash-mainnet --seed SEED --minutes 330 --out RUN_DIR
  python3 listener/listen.py --selftest
"""
import argparse
import hashlib
import json
import os
import random
import re
import socket
import struct
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "headers"))
import ph_headers as H  # noqa: E402

PROFILES = ("zcash-mainnet", "zcash-testnet", "bch-tempnet", "bch-chipnet")
MSG_TX, MSG_BLOCK, MSG_CMPCT_BLOCK = 1, 2, 4
PER_SOFTWARE = 8          # Zcash: connections kept per node software
RETRY_S = 60              # how often missing connections are filled up
SOURCES_S = 1800          # how often seeds and the peers file are read again
TX_KEEP_S = 3 * 3600      # Bitcoin Cash: how long announced transaction ids are kept
CMPCT_WAIT_S = 3          # Bitcoin Cash: wait this long for an unasked cmpctblock
LAG = 100                 # a peer more than this many blocks behind is not kept
LAST_SHARED = 327988      # Bitcoin Cash: last block chipnet and tempnet share
FIRST_TEMPNET = 327989

_IP4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IP6 = re.compile(r"\[?\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b\]?")
_HOST = re.compile(r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z][A-Za-z0-9-]{1,62}\b")


def utc_now(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def agent_label(raw):
    """The user agent of a peer as we keep it: the part before the first "(",
    with anything like an IPv4 or IPv6 address or a host name replaced by
    "[removed]", only printable characters, at most 80. A peer may write
    anything into its user agent, so it is only ever kept in this form."""
    s = str(raw).split("(")[0]
    for r in (_IP4, _IP6, _HOST):
        s = r.sub("[removed]", s)
    s = "".join(c if c.isprintable() else "?" for c in s)
    return s[:80] or "?"


def zcash_software(agent):
    a = agent.lower()
    if "zakura" in a:
        return "zakura"
    if "zebra" in a:
        return "zebra"
    return "other"


# ------------------------------------------------------------- BIP152 short ids
def _rotl(x, b):
    return ((x << b) | (x >> (64 - b))) & 0xFFFFFFFFFFFFFFFF


def siphash24(k0, k1, data):
    """SipHash-2-4 over bytes, key as two 64 bit numbers (BIP152)."""
    m64 = 0xFFFFFFFFFFFFFFFF
    v0, v1 = k0 ^ 0x736F6D6570736575, k1 ^ 0x646F72616E646F6D
    v2, v3 = k0 ^ 0x6C7967656E657261, k1 ^ 0x7465646279746573

    def rnd():
        nonlocal v0, v1, v2, v3
        v0 = (v0 + v1) & m64; v1 = _rotl(v1, 13) ^ v0; v0 = _rotl(v0, 32)  # noqa: E702
        v2 = (v2 + v3) & m64; v3 = _rotl(v3, 16) ^ v2  # noqa: E702
        v0 = (v0 + v3) & m64; v3 = _rotl(v3, 21) ^ v0  # noqa: E702
        v2 = (v2 + v1) & m64; v1 = _rotl(v1, 17) ^ v2; v2 = _rotl(v2, 32)  # noqa: E702
    n = len(data)
    end = n - n % 8
    for i in range(0, end, 8):
        m = int.from_bytes(data[i:i + 8], "little")
        v3 ^= m; rnd(); rnd(); v0 ^= m  # noqa: E702
    last = (n & 0xFF) << 56 | int.from_bytes(data[end:].ljust(8, b"\0")[:7], "little")
    v3 ^= last; rnd(); rnd(); v0 ^= last  # noqa: E702
    v2 ^= 0xFF
    for _ in range(4):
        rnd()
    return (v0 ^ v1 ^ v2 ^ v3) & m64


def short_id(k0, k1, txid_internal):
    return siphash24(k0, k1, txid_internal) & 0xFFFFFFFFFFFF


def read_cmpct(p):
    """cmpctblock: header (80), nonce (8), short ids (6 bytes each), prefilled
    transactions. Returns the header, the key, the short ids and the number of
    prefilled transactions."""
    header, nonce = p[:80], p[80:88]
    n, i = H.read_varint(p, 88)
    ids = [int.from_bytes(p[i + 6 * j:i + 6 * j + 6], "little") for j in range(n)]
    i += 6 * n
    pre, _ = H.read_varint(p, i)
    h = hashlib.sha256(header + nonce).digest()
    return header, int.from_bytes(h[:8], "little"), int.from_bytes(h[8:16], "little"), ids, pre


def zcash_header_of_block(payload):
    """The header of a Zcash block message: 140 fixed bytes, then the Equihash solution."""
    sol, i = H.read_varint(payload, 140)
    return payload[:i + sol]


# ------------------------------------------------------------- connection
class ListenerPeer(H.Peer):
    """A peer of ph_headers with the relay flag set as asked (Bitcoin Cash
    announces transactions to us only with relay=1)."""

    def __init__(self, net, addr, timeout=12, relay=False):
        super().__init__(net, addr, timeout)
        self.relay = relay

    def handshake(self):
        empty = struct.pack("<Q", 0) + b"\0" * 10 + b"\xff\xff" + b"\0" * 4 + struct.pack(">H", 0)
        payload = (struct.pack("<iQq", self.profile["protocol"], 0, int(time.time())) + empty + empty
                   + struct.pack("<Q", random.getrandbits(64)) + H.varint(len(H.USER_AGENT)) + H.USER_AGENT
                   + struct.pack("<i", 0) + (b"\x01" if self.relay else b"\0"))
        self.send("version", payload)
        got_version = got_verack = False
        until = time.time() + 30
        while not (got_version and got_verack):
            if time.time() > until:
                raise TimeoutError("handshake takes too long")
            cmd, p = self.receive()
            if cmd == "version":
                self.version = struct.unpack_from("<i", p, 0)[0]
                n, i = H.read_varint(p, 80)
                self.agent = agent_label(p[i:i + n].decode(errors="replace"))
                self.height = struct.unpack_from("<i", p, i + n)[0]
                self.send("verack")
                got_version = True
            elif cmd == "verack":
                got_verack = True
            elif cmd == "reject":
                raise ConnectionError("version rejected")


# ------------------------------------------------------------- listener
class Listener:
    def __init__(self, network, end, out_dir, clock=time.time, schedule=None):
        self.network = network
        self.net = H.NETWORKS[network]
        self.family = self.net["family"]
        self.end = end
        self.clock = clock
        self.schedule = schedule or (lambda delay, fn, args: threading.Timer(delay, fn, args=args).start())
        self.lock = threading.Lock()
        names = ("ereignisse", "koepfe", "compact") if self.family == "bch" else ("ereignisse", "koepfe")
        self.files = {n: open(os.path.join(out_dir, f"{n}.jsonl"), "w") for n in names}
        self.blocks = {}          # bch: hash -> {"erster": label, "cmpct": bool, "geholt": bool}
        self.asked = set()        # zcash: hashes asked for with getdata
        self.heads_seen = set()
        self.tx = {}              # bch: internal txid -> time of the first announcement
        self.active = {}          # label -> software (zcash) or peer (bch)
        self.counter = {"zebra": 0, "zakura": 0, "bch": 0}
        self.peers = []           # meta per connection, without address
        self.refused_probe = 0
        self.highest = 0
        if self.family == "bch":
            fixed = H.NETWORKS["bch-tempnet"]["fixed"]
            self.probe_start = bytes.fromhex(fixed[LAST_SHARED])[::-1]
            self.tempnet_first = bytes.fromhex(fixed[FIRST_TEMPNET])[::-1]

    def close(self):
        for f in self.files.values():
            f.close()

    def write(self, name, obj):
        with self.lock:
            f = self.files[name]
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")
            f.flush()

    def store_header(self, raw, label, source):
        k = H.parse_header(self.family, raw)
        h = k["hash"][::-1].hex()
        with self.lock:
            if h in self.heads_seen:
                return h
            self.heads_seen.add(h)
        self.write("koepfe", {"hash": h, "prev": k["prev"][::-1].hex(), "zeit": k["time"],
                              "nbits": f"{k['bits']:08x}", "von": label, "quelle": source})
        return h

    # --- Bitcoin Cash
    def announce(self, t_ms, label, kind, h, peer, internal=None):
        self.write("ereignisse", {"t_ms": t_ms, "peer": label, "art": kind, "hash": h})
        with self.lock:
            new = h not in self.blocks
            if new:
                self.blocks[h] = {"erster": label, "cmpct": kind == "cmpctblock", "geholt": False}
        if new and internal is not None and kind != "cmpctblock":
            self.schedule(CMPCT_WAIT_S, self.fetch_cmpct, (h, internal, peer))

    def fetch_cmpct(self, h, internal, peer):
        with self.lock:
            b = self.blocks.get(h)
            if not b or b["cmpct"] or b["geholt"]:
                return
            b["geholt"] = True
        try:
            peer.send("getdata", H.varint(1) + struct.pack("<I", MSG_CMPCT_BLOCK) + internal)
        except OSError:
            pass

    def read_compact(self, t_ms, label, payload, unsolicited):
        header, k0, k1, ids, pre = read_cmpct(payload)
        h = self.store_header(header, label, "cmpctblock")
        with self.lock:
            limit = self.clock() - TX_KEEP_S
            for t in [t for t, z in self.tx.items() if z < limit]:
                del self.tx[t]
            known = {short_id(k0, k1, t) for t in self.tx}
            if h in self.blocks:
                self.blocks[h]["cmpct"] = True
        missing = sum(1 for x in ids if x not in known)
        self.write("compact", {"t_ms": t_ms, "peer": label, "hash": h, "von_selbst": unsolicited,
                               "kurz_ids": len(ids), "vorbefuellt": pre, "fehlend_geschaetzt": missing,
                               "tx_ids_bekannt": len(known)})
        return h

    def handle_bch(self, peer, label, info, cmd, payload, t_ms):
        if cmd == "inv":
            n, i = H.read_varint(payload, 0)
            for _ in range(n):
                kind = struct.unpack_from("<I", payload, i)[0]
                internal = payload[i + 4:i + 36]
                i += 36
                if kind == MSG_TX:
                    with self.lock:
                        self.tx.setdefault(internal, self.clock())
                elif kind == MSG_BLOCK:
                    self.announce(t_ms, label, "inv", internal[::-1].hex(), peer, internal)
                    info["ankuendigungen"] += 1
        elif cmd == "headers":
            for raw in H.split_headers("bch", payload):
                h = self.store_header(raw, label, "headers")
                self.announce(t_ms, label, "headers", h, peer, H.sha256d(raw))
                info["ankuendigungen"] += 1
        elif cmd == "cmpctblock":
            with self.lock:
                h_before = H.sha256d(payload[:80])[::-1].hex()
                fetched = self.blocks.get(h_before, {}).get("geholt", False) \
                    and self.blocks[h_before]["erster"] == label
            h = self.read_compact(t_ms, label, payload, unsolicited=not fetched)
            if not fetched:
                self.announce(t_ms, label, "cmpctblock", h, peer)
                info["ankuendigungen"] += 1
        elif cmd == "block":
            # the whole block came instead of a cmpctblock: keep the header only
            self.store_header(payload[:80], label, "block")
            info["ganze_bloecke"] = info.get("ganze_bloecke", 0) + 1

    # --- Zcash
    def handle_zcash(self, peer, label, info, cmd, payload, t_ms):
        if cmd == "inv":
            n, i = H.read_varint(payload, 0)
            new = []
            for _ in range(n):
                kind = struct.unpack_from("<I", payload, i)[0]
                internal = payload[i + 4:i + 36]
                i += 36
                if kind != MSG_BLOCK:
                    continue
                h = internal[::-1].hex()
                self.write("ereignisse", {"t_ms": t_ms, "peer": label, "art": "inv", "hash": h})
                info["ankuendigungen"] += 1
                with self.lock:
                    if h not in self.asked:
                        self.asked.add(h)
                        new.append(internal)
            if new:
                peer.send("getdata", H.varint(len(new)) + b"".join(struct.pack("<I", MSG_BLOCK) + x for x in new))
        elif cmd == "headers":
            for raw in H.split_headers("zcash", payload):
                h = self.store_header(raw, label, "headers")
                self.write("ereignisse", {"t_ms": t_ms, "peer": label, "art": "headers", "hash": h})
                info["ankuendigungen"] += 1
        elif cmd == "block":
            self.store_header(zcash_header_of_block(payload), label, "block")

    def handle(self, peer, label, info, cmd, payload, t_ms):
        if self.family == "bch":
            self.handle_bch(peer, label, info, cmd, payload, t_ms)
        else:
            self.handle_zcash(peer, label, info, cmd, payload, t_ms)

    def run_peer(self, peer, label, info):
        peer.s.settimeout(90)
        try:
            while self.clock() < self.end:
                try:
                    cmd, payload = peer.receive()
                except socket.timeout:
                    continue
                self.handle(peer, label, info, cmd, payload, int(self.clock() * 1000))
        except (OSError, ConnectionError, TimeoutError, struct.error, IndexError, ValueError, H.CheckError) as e:
            info["ende_grund"] = type(e).__name__
        finally:
            info["getrennt_utc"] = utc_now(self.clock())
            peer.close()
            with self.lock:
                self.active.pop(label, None)

    def keep_bch(self, peer, addr, tried):
        """Fork probe and lag. Returns a label, or None if the peer is not kept."""
        probe = peer.headers([self.probe_start], wait=20)
        on_tempnet = bool(probe) and H.sha256d(probe[0]) == self.tempnet_first
        if not probe or on_tempnet != (self.network == "bch-tempnet"):
            self.refused_probe += 1
            return None
        if peer.height < self.highest - LAG:
            tried.discard(addr)  # still syncing, ask again later
            return None
        with self.lock:
            self.counter["bch"] += 1
            label = f"peer_{self.counter['bch']}"
            self.active[label] = peer
        return label

    def keep_zcash(self, peer, addr, tried, state):
        sw = zcash_software(peer.agent)
        if sw == "other" or peer.height < self.highest - LAG:
            return None
        if state.get(sw, 0) >= PER_SOFTWARE:
            tried.discard(addr)  # only full at the moment, ask again later
            return None
        with self.lock:
            self.counter[sw] += 1
            label = f"{sw}-{self.counter[sw]}"
            self.active[label] = sw
        return label

    def zcash_state(self):
        with self.lock:
            return {s: sum(1 for x in self.active.values() if x == s) for s in ("zebra", "zakura")}

    def connect(self, addrs, tried):
        for addr in addrs:
            if self.clock() >= self.end:
                return
            state = None
            if self.family == "zcash":
                state = self.zcash_state()
                if all(v >= PER_SOFTWARE for v in state.values()):
                    return
            if addr in tried:
                continue
            tried.add(addr)
            try:
                peer = ListenerPeer(self.net, addr, timeout=12, relay=self.family == "bch")
                peer.handshake()
                label = self.keep_bch(peer, addr, tried) if self.family == "bch" else \
                    self.keep_zcash(peer, addr, tried, state)
            except (OSError, ConnectionError, TimeoutError, struct.error, IndexError, H.CheckError):
                continue
            if label is None:
                peer.close()
                continue
            self.highest = max(self.highest, peer.height)
            info = {"peer": label, "software": peer.agent, "version": peer.version, "gemeldete_hoehe": peer.height,
                    "verbunden_utc": utc_now(self.clock())}
            if self.family == "bch":
                way = "headers" if self.counter["bch"] % 2 == 0 else "inv"
                try:
                    peer.send("sendcmpct", b"\x01" + struct.pack("<Q", 1))
                    if way == "headers":
                        peer.send("sendheaders")
                except OSError:
                    pass
                info["erbeten"] = ["cmpctblock", way]
            info["ankuendigungen"] = 0
            self.peers.append(info)
            H.log(f"connected {label} {peer.agent} height {peer.height}")
            threading.Thread(target=self.run_peer, args=(peer, label, info), daemon=True).start()

    def meta(self, start, actual_end, minutes):
        m = {
            "art": f"one listener (not a node), connections to {self.network} nodes from the given seeds",
            "netz": self.network,
            "protokoll": self.net["protocol"],
            "start_utc": utc_now(start),
            "ende_utc": utc_now(actual_end),
            "geplantes_ende_utc": utc_now(self.end),
            "dauer_min": minutes,
            "peers": self.peers,
        }
        if self.family == "zcash":
            m["ziel_verbindungen_je_software"] = PER_SOFTWARE
            m["block_hashes_gesehen"] = len(self.asked | self.heads_seen)
        else:
            m["peers_ohne_abzweig_abgelehnt"] = self.refused_probe
            m["block_hashes_gesehen"] = len(self.blocks)
            m["labels"] = "peer_N per run, not across runs"
        m["koepfe_geholt"] = len(self.heads_seen)
        m["zeitquelle"] = "clock of the host (NTP), milliseconds"
        return m


# ------------------------------------------------------------- self test
class _FakePeer:
    def __init__(self):
        self.sent = []

    def send(self, cmd, payload=b""):
        self.sent.append((cmd, payload))


def _replay(network, messages, out_dir):
    """Feed messages to a listener with a fixed clock, as one connection."""
    tick = [1795000000.0]

    def clock():
        tick[0] += 0.25
        return tick[0]
    later = []
    ls = Listener(network, end=2e9, out_dir=out_dir, clock=clock, schedule=lambda d, fn, a: later.append((fn, a)))
    peer = _FakePeer()
    label = "peer_1" if ls.family == "bch" else "zebra-1"
    info = {"peer": label, "software": "/Test:1.0/", "ankuendigungen": 0}
    for cmd, payload in messages:
        ls.handle(peer, label, info, cmd, payload, int(clock() * 1000))
        while later:
            fn, a = later.pop(0)
            fn(*a)
    ls.close()
    return ls, peer, info


def recorded_messages(family, raws):
    """Messages built from real headers of our fixtures: inv, headers, and for
    Bitcoin Cash a cmpctblock and transaction ids, for Zcash a block."""
    msgs = []
    tx = [hashlib.sha256(bytes([i])).digest() for i in range(6)]
    if family == "bch":
        inv_tx = H.varint(4) + b"".join(struct.pack("<I", MSG_TX) + t for t in tx[:4])
        msgs.append(("inv", inv_tx))
    inv = H.varint(2) + b"".join(struct.pack("<I", MSG_BLOCK) + H.sha256d(r) for r in raws[:2])
    msgs.append(("inv", inv))
    hdr = H.varint(3) + b"".join(r + b"\0" for r in raws[2:5])
    msgs.append(("headers", hdr))
    if family == "bch":
        nonce = struct.pack("<Q", 7)
        k = hashlib.sha256(raws[0] + nonce).digest()
        k0, k1 = int.from_bytes(k[:8], "little"), int.from_bytes(k[8:16], "little")
        ids = [short_id(k0, k1, t) for t in tx]  # 4 known, 2 unknown
        msgs.append(("cmpctblock", raws[0] + nonce + H.varint(len(ids))
                     + b"".join(x.to_bytes(6, "little") for x in ids) + H.varint(1) + b"\0\x01\x00"))
        nonce = struct.pack("<Q", 9)
        msgs.append(("cmpctblock", raws[5] + nonce + H.varint(0) + H.varint(1) + b"\0\x01\x00"))
        msgs.append(("block", raws[6] + b"\x01" + b"\0" * 10))
    else:
        msgs.append(("block", raws[0] + b"\x01" + b"\0" * 10))
        msgs.append(("inv", H.varint(1) + struct.pack("<I", MSG_TX) + tx[0]))
    return msgs


def selftest():
    import tempfile
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    # SipHash-2-4 reference values (key 00..0f): empty message and 00..0e
    key = bytes(range(16))
    k0, k1 = int.from_bytes(key[:8], "little"), int.from_bytes(key[8:], "little")
    ok(siphash24(k0, k1, b"") == 0x726FDB47DD0E0E31, "siphash of the empty message")
    ok(siphash24(k0, k1, bytes(range(15))) == 0xA129CA6149BE45E5, "siphash of 15 bytes")
    ok(short_id(k0, k1, b"") == 0x726FDB47DD0E0E31 & 0xFFFFFFFFFFFF, "short id is the low 6 bytes")
    # test names and addresses from the documentation ranges, put together at
    # run time, so that no host name or address stands in this file
    dom = lambda *p: ".".join(p)  # noqa: E731
    host, ip4, ip6 = dom("node7", "example", "net"), dom("192", "0", "2", "17"), ":".join(["2001", "db8", "", "1"])
    for raw, want in ((f"/Bitcoin Cash Node:29.2.1({dom('chipnet', 'example', 'org')})/", "/Bitcoin Cash Node:29.2.1"),
                      ("/Zebra:7.1.0/", "/Zebra:7.1.0/"),
                      (f"/x:1 {host}:8333/", "/x:1 [removed]:8333/"),
                      (f"/x:1 {ip4}/", "/x:1 [removed]/"),
                      (f"/x:1 {ip6}/", "/x:1 [removed]/"),
                      (f"({dom('only', 'comment', 'example', 'com')})", "?")):
        ok(agent_label(raw) == want, f"agent label of a test agent gives {want}")
    ok(zcash_software("/Zakura:1.8.9/") == "zakura" and zcash_software("/Zebra:7.1.0/") == "zebra"
       and zcash_software("/MagicBean:6/") == "other", "zcash software classes")

    here = os.path.join(os.path.dirname(HERE), "headers", "fixtures")
    sums = {}
    with open(os.path.join(HERE, "examples", "SHA256SUMS")) as f:
        for line in f:
            s, name = line.split()
            sums[name] = s
    for network, fixture in (("bch-tempnet", "bch-tempnet-327989.json"), ("zcash-mainnet", "zcash-mainnet-3543405.json")):
        with open(os.path.join(here, fixture)) as f:
            raws = [bytes.fromhex(x) for x in json.load(f)["headers"]]
        family = H.NETWORKS[network]["family"]
        with tempfile.TemporaryDirectory() as tmp:
            ls, peer, info = _replay(network, recorded_messages(family, raws), tmp)
            files = sorted(x for x in os.listdir(tmp) if x.endswith(".jsonl"))
            rows = {x: [json.loads(z) for z in open(os.path.join(tmp, x))] for x in files}
            for x in files:
                with open(os.path.join(tmp, x), "rb") as f:
                    ok(hashlib.sha256(f.read()).hexdigest() == sums.get(f"output/replay-{network}-{x}"),
                       f"sha256 of the replay output {network} {x}")
        ev, heads = rows["ereignisse.jsonl"], rows["koepfe.jsonl"]
        want_hash = [H.sha256d(r)[::-1].hex() for r in raws]
        if family == "bch":
            ok([e["art"] for e in ev] == ["inv", "inv", "headers", "headers", "headers", "cmpctblock"],
               "bch announcements in order, the asked cmpctblock is no announcement")
            ok(info["ankuendigungen"] == 6 and info["ganze_bloecke"] == 1, "bch counts per connection")
            got = [p for c, p in peer.sent if c == "getdata"]
            ok(len(got) == 5 and all(struct.unpack_from("<I", p, 1)[0] == MSG_CMPCT_BLOCK for p in got),
               "bch asks once per new block for a cmpctblock")
            c = rows["compact.jsonl"]
            ok(c[0]["kurz_ids"] == 6 and c[0]["fehlend_geschaetzt"] == 2 and c[0]["tx_ids_bekannt"] == 4
               and c[0]["vorbefuellt"] == 1, "bch short ids, 2 of 6 unknown")
            ok(c[0]["von_selbst"] is False and c[1]["von_selbst"] is True, "asked and unasked cmpctblock")
            ok([h["quelle"] for h in heads] == ["headers"] * 3 + ["cmpctblock"] * 2 + ["block"], "bch header sources")
        else:
            ok([e["art"] for e in ev] == ["inv", "inv", "headers", "headers", "headers"], "zcash announcements")
            got = [p for c, p in peer.sent if c == "getdata"]
            ok(len(got) == 1 and got[0][0] == 2, "zcash asks for both new blocks in one getdata")
            ok([h["quelle"] for h in heads] == ["headers"] * 3 + ["block"], "zcash header sources")
        ok([e["hash"] for e in ev[:5]] == want_hash[:5], f"{network} block hashes from the raw headers")
        ok(heads[0]["prev"] == H.hexhash(raws[2][4:36]), f"{network} parent hash of a header")
    print(f"{n} checks, all tests passed")


def main():
    ap = argparse.ArgumentParser(description="Block announcements over the peer to peer protocol, PH-M-004.")
    ap.add_argument("--network", choices=PROFILES)
    ap.add_argument("--seed", action="append", default=[], help="DNS seed name of the network, repeatable")
    ap.add_argument("--peers-file", help="file with one host:port per line")
    ap.add_argument("--minutes", type=float, help="how long to listen")
    ap.add_argument("--out", help="directory for ereignisse.jsonl, koepfe.jsonl, compact.jsonl and meta.json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.network and a.minutes and a.out):
        ap.error("--network, --minutes and --out are needed")
    if not a.seed and not a.peers_file:
        ap.error("give peers with --seed or --peers-file")
    os.makedirs(a.out, exist_ok=True)
    net = H.NETWORKS[a.network]
    start = time.time()
    ls = Listener(a.network, start + a.minutes * 60, a.out)
    addrs, read_at = H.read_peers(a.seed, a.peers_file, net["port"]), time.time()
    tried, last_note = set(), 0
    while time.time() < ls.end:
        if time.time() - read_at > SOURCES_S:
            addrs, read_at = H.read_peers(a.seed, a.peers_file, net["port"]), time.time()
            tried = set()
        ls.connect(addrs, tried)
        if time.time() - last_note > 600:
            with ls.lock:
                n_active, n_hash = len(ls.active), len(ls.blocks) + len(ls.asked)
            H.log(f"{utc_now()[11:16]} UTC connected {n_active}, block hashes {n_hash}")
            last_note = time.time()
        time.sleep(min(RETRY_S, max(0, ls.end - time.time())))
    actual_end = time.time()
    time.sleep(2)  # running threads write their end
    meta = ls.meta(start, actual_end, a.minutes)
    ls.close()
    with open(os.path.join(a.out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(json.dumps({"peers": len(ls.peers), "hashes": meta["block_hashes_gesehen"]}))


if __name__ == "__main__":
    main()
