# Round robin: count M1 after activation with your own data

We ask a third party to count one metric of our report with their own node, independent of our data, and publish both numbers side by side.

## The metric

M1, the median block interval after activation (`p50_after` in our report CSV), method PH-M-001.

- Window: every block from the activation height up to, but not including, the first block whose header time is 72 h or more after the header time of the activation block.
- Interval of block h: header time of h minus header time of h minus 1, in seconds. The interval of the activation block itself counts.
- Median: linear interpolation between the two middle values, so an even count gives the mean of the two middle values.

For Zcash NU7 the activation height is 3,543,405 and the target after activation is 25 s.

## How to count it

With your own Zcash node (zcashd or Zebra) and its JSON-RPC port:

```
export RPC_URL=http://127.0.0.1:8232
python3 tools/heads_from_rpc.py --from 3543404 --to <tip> --out my-heads.csv.gz
python3 upgrade_report/report_v1.py --config upgrade_report/configs/zcash-mainnet-nu7.json --heads my-heads.csv.gz --out my-report
grep '^M1,p50_after,' my-report/report-v1-zcash-mainnet-nu7.csv
```

The tool reads the RPC address and any credentials from `RPC_URL` only. Do not put credentials in a file you share. Or count it in your own way and send us the number, the window (first and last height) and the number of intervals.

## What we publish

Your number, our number, the first and last height of the window and the number of intervals, with your name only if you agree. If the numbers differ we look for the cause together and publish it.
