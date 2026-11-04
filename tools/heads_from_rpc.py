#!/usr/bin/env python3
"""heads_from_rpc.py - heads CSV from your own node over JSON-RPC, for the round robin.

Asks a zcashd or Zebra node for getblockhash and getblockheader (verbose) per
height and writes the heads CSV that upgrade_report/report_v1.py reads
(columns hoehe,hash,zeit,nbits,version, gzip without timestamp).

The node address and any credentials come from the environment variable
RPC_URL only, for example http://user:password@127.0.0.1:8232. Nothing is
written to disk except the CSV.

  RPC_URL=http://127.0.0.1:8232 python3 tools/heads_from_rpc.py --from 3543404 --to 3555000 --out my-heads.csv.gz
  python3 tools/heads_from_rpc.py --selftest
"""
import argparse
import base64
import csv
import gzip
import io
import json
import os
import sys
import urllib.parse
import urllib.request


def rpc(url, method, params):
    u = urllib.parse.urlsplit(url)
    plain = urllib.parse.urlunsplit((u.scheme, u.hostname + (f":{u.port}" if u.port else ""), u.path or "/", "", ""))
    req = urllib.request.Request(plain, data=json.dumps({"jsonrpc": "1.0", "id": 1, "method": method,
                                                         "params": params}).encode(),
                                 headers={"Content-Type": "application/json"})
    if u.username:
        token = base64.b64encode(f"{urllib.parse.unquote(u.username)}:{urllib.parse.unquote(u.password or '')}"
                                 .encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if out.get("error"):
        raise SystemExit(f"rpc {method}: {out['error']}")
    return out["result"]


def heads(url, start, end):
    rows = []
    for h in range(start, end + 1):
        hsh = rpc(url, "getblockhash", [h])
        hd = rpc(url, "getblockheader", [hsh, True])
        rows.append({"hoehe": h, "hash": hsh, "zeit": int(hd["time"]), "nbits": hd["bits"],
                     "version": hd.get("version", "")})
        if h % 1000 == 0:
            print(f"height {h}", file=sys.stderr, flush=True)
    return rows


def write(rows, path):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["hoehe", "hash", "zeit", "nbits", "version"], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    with open(path, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as g:
        g.write(buf.getvalue().encode())


def selftest():
    """Against a small fake node on localhost; checks the request and the CSV, not a real node."""
    import http.server
    import tempfile
    import threading

    class Fake(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            q = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(self.headers.get("Authorization"))
            if q["method"] == "getblockhash":
                res = f"{q['params'][0]:064x}"
            else:
                h = int(q["params"][0], 16)
                res = {"time": 1000 + 25 * h, "bits": "1d00ffff", "version": 5}
            body = json.dumps({"result": res, "error": None, "id": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    seen = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://u:p@127.0.0.1:{srv.server_port}/"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "h.csv.gz")
        write(heads(url, 10, 12), p)
        rows = list(csv.DictReader(gzip.open(p, "rt")))
    srv.shutdown()
    ok = [r["hoehe"] for r in rows] == ["10", "11", "12"] and rows[1]["zeit"] == "1275" and \
        seen[0] == "Basic " + base64.b64encode(b"u:p").decode()
    print("ok   fake node, 3 heights, basic auth" if ok else "FAIL fake node")
    print("all tests passed" if ok else "1 failed")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--from", dest="start", type=int)
    ap.add_argument("--to", dest="end", type=int)
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    url = os.environ.get("RPC_URL")
    if not (url and a.start is not None and a.end is not None and a.out):
        ap.error("RPC_URL, --from, --to and --out are required")
    write(heads(url, a.start, a.end), a.out)
    print(f"wrote {a.out}, heights {a.start} to {a.end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
