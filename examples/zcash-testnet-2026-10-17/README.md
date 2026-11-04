# Example: Zcash testnet, 17 Oct 2026

A short excerpt of our own data, so the report script can be checked end to end in a few seconds.

| File | Content |
|---|---|
| `heads.csv.gz` | heads 4,508,646 to 4,514,250 from our header client, checked for linkage and proof of work |
| `raw/ereignisse.jsonl.gz` | 5,684 block announcements our listener received from 14:51 to 20:21 utc |
| `raw/koepfe.jsonl.gz` | the 780 headers behind them |
| `raw/peers.json` | the 63 connections of that listener run, software and protocol only |
| `raw/wache-log.csv`, `raw/zustand.json` | log and state of our watch, replayed on that recording |
| `raw/SHA256SUMS` | sha256 of the raw files |
| `config.json` | the config of the example |
| `expected/*.sha256` | the sha256 the report must reproduce |

**What is real and what is not.**

- Real: the heads, the announcements, the headers and the connections are as recorded.
- Not real: the activation. Height 4,512,127 lies inside the recording and stands in for an upgrade. The real testnet NU7 activation was at height 4,465,026 on 4 Oct 2026.
- Example values: the estimate and its band in `config.json` only exercise the M7 rows. The probe times are 0 and 1 h, because the recording is only 5.5 h long.
- Replay: the log and the state come from replaying the recording through our watch. The replay ran 40 min past the end of the recording, and its last two log rows, both after 20:21:43 utc, are removed because they only reflect the end of the recording.

**What the example shows.** The listener covers only 5.5 h, so the uncertainty CSV reports most chain blocks of the before window as never announced to us. That is what it is for.
