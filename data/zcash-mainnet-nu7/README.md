# Zcash mainnet NU7, upgrade report v1

The first PulseHawk Upgrade Report on a real activation. NU7 activated at height 3,543,405. The header time of that block is 6 Nov 2026 02:34:02 utc.

| File | Content |
|---|---|
| `heads.csv.gz` | heads 3,542,245 to 3,553,625 from our header client, checked for linkage and proof of work. They cover 24 h before the activation to 72 h after it, with 10 min to spare on both sides. |
| `raw/` | the raw export of our watch after its window closed, as it came from the watch, with `raw/SHA256SUMS` |
| `expected/report-v1-zcash-mainnet-nu7.sha256` | the sha256 the report must reproduce |
| `output/` | the report CSV, the uncertainty CSV and the sha256 of all inputs, as computed by us |

The raw files hold no addresses. Peers are named by our own numbers, such as `zebra-3`.

## Reproduce

```
python3 upgrade_report/report_v1.py --selftest
python3 upgrade_report/report_v1.py --config upgrade_report/configs/zcash-mainnet-nu7.json \
    --heads data/zcash-mainnet-nu7/heads.csv.gz --raw data/zcash-mainnet-nu7/raw --out out
cd out && sha256sum -c ../data/zcash-mainnet-nu7/expected/report-v1-zcash-mainnet-nu7.sha256
```

## What to keep in mind

- **Forecast.** The M7 rows use our forecast of 4 Nov 2026 with its 90% band. The block came 6 min 56 s earlier than that forecast, inside the band. The field names `estimate` and `header_time_minus_estimate` belong to format v1 and stay.
- **Our watch hung.** From about 02:50 to 08:11 utc on 6 Nov the watch saw no new blocks, because the DNS seeds could not be resolved from its host. The block pause alarm in the log from 03:04 to 08:15 utc comes from that, not from the chain. M1, M2 and M6 come from the heads and are not affected. M3 and M4 come from the watch log and may miss tip switches in those hours.
- **Probes.** The peer probes reached 28, 12, 6 and 8 peers. The shares in M5 are shares of these few reachable peers, not of all nodes.
