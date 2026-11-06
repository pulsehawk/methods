# Zcash NU7, block intervals

Block intervals around the Zcash NU7 activation, counted from our own header chain. Every header is checked for linkage and proof of work. Licensed under MIT like the rest of this repository, see `LICENSE`.

| File | Content |
|---|---|
| `zcash-nu7-testnet-intervals-2026-10-18.csv` | testnet, heights 4,465,026 to 4,514,228, header times 4 Oct 2026 18:21:45 utc to 18 Oct 2026 05:59:48 utc, 49,202 intervals. The same file is published on pulsehawk.io. |
| `zcash-mainnet-nu7-intervals-2026-11-06.csv` | mainnet, heights 3,543,405 to 3,543,655, header times 6 Nov 2026 02:34:02 utc to 05:13:14 utc, the first 250 intervals after activation. Our heads were cross checked against two Zebra 7.0.0 peers, 500 of 500 hashes equal. |
| `SHA256SUMS` | sha256 of both CSV files |

**Format.** Both files share it. The columns are `height,timestamp,interval_s`, one row per block, heights without gaps, comma separated, no quoting, LF line ends. `timestamp` is the header time in unix seconds, utc. `interval_s` is the header time of the block minus the header time of the block before it, in seconds. It is empty in the first row, which is the activation block, because the block before it is not in the file. Header times are set by miners, so an interval can be zero or negative. Neither file contains a negative interval.

Check the files with

```
cd data/zcash-nu7 && sha256sum -c SHA256SUMS
```

The files contain heights, header times and intervals only. They contain no addresses, no IP addresses, no host names and no peer data.
