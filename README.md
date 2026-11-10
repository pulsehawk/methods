# methods

Methods and scripts behind PulseHawk reports. Reproduce our numbers in three commands.

## Reproduce the Zcash NU7 upgrade report in three commands

You need Python 3.9 or later and nothing else. The scripts use the standard library only.

```
git clone https://github.com/pulsehawk/methods && cd methods
python3 upgrade_report/report_v1.py --selftest
python3 upgrade_report/report_v1.py --config examples/zcash-testnet-2026-10-17/config.json --heads examples/zcash-testnet-2026-10-17/heads.csv.gz --raw examples/zcash-testnet-2026-10-17/raw --out out
```

The self test checks the rules of every metric and recomputes the example. It must end with `all tests passed`. The third command writes the report CSV, the uncertainty CSV, a `.sha256` file and the sha256 of every input to `out/`. Check the result with

```
cd out && sha256sum -c report-v1-zcash-testnet-example-nu7-example.sha256
```

The example is a short excerpt of the Zcash testnet, from our own header chain and our own listener on 17 Oct 2026. It places a pretend activation at height 4,512,127 inside that recording, so the example is not a statement about the real testnet upgrade. See `examples/zcash-testnet-2026-10-17/README.md`.

The block intervals around the Zcash NU7 activation are in `data/zcash-nu7/`, the testnet from 4 to 18 Oct 2026 and the first 250 mainnet intervals after activation. The inputs of the full mainnet report follow there once the measurement window has closed. The report names the commit of this repository and the sha256 of every input it was computed with.

## What is here

| Path | Content |
|---|---|
| `upgrade_report/report_v1.py` | raw values M1 to M7 of the upgrade report, specification v1.0 |
| `upgrade_report/configs/` | one config per chain and upgrade |
| `METHODS.md` | the named methods PH-M-001 to PH-M-006, uncertainty and traceability |
| `FORMATS.md` | the input and output formats |
| `ROUND_ROBIN.md` | how a third party counts one metric with their own data |
| `tools/heads_from_rpc.py` | heads CSV from your own node, for the round robin |
| `examples/` | small example inputs with the expected sha256 |
| `data/zcash-nu7/` | block intervals around the Zcash NU7 activation, testnet and mainnet, with sha256 |

## Principles

We do not claim any accreditation. We work along the same lines as a test laboratory. Every number comes from a named method, every metric states its uncertainty, every input can be traced by its sha256, and we invite others to count the same metric with their own data.

Every number in a report carries its kind. counted is counted by us from our own data, source is taken from a named outside source, computed comes from a model, a forecast or a difference to a forecast.

## History

The scripts were written in our working repository and published here between 4 and 9 Nov 2026. The first commit in this repository is from 4 Nov 2026.

Results we published with a date, all times in utc.

| Date | Result | Link |
|---|---|---|
| 15 Oct 2026, 15:52 | forum post, plan and method for an independent count of the NU7 testnet | [NU7 timeline #24](https://forum.zcashcommunity.com/t/nu7-timeline/57655/24) |
| 18 Oct 2026, 07:36 | forum post, final counts on the NU7 testnet, blocks 4,465,026 to 4,514,228 | [NU7 timeline #26](https://forum.zcashcommunity.com/t/nu7-timeline/57655/26) |
| 22 Oct 2026, 11:02 | forum post, CSV of every testnet block interval | [NU7 timeline #29](https://forum.zcashcommunity.com/t/nu7-timeline/57655/29) |
| 29 Oct 2026, 23:18 | forum post, forecast of the mainnet activation time | [NU7 timeline #33](https://forum.zcashcommunity.com/t/nu7-timeline/57655/33) |
| 6 Nov 2026, 09:04 | forum post, activation at height 3,543,405 counted | [NU7 timeline #35](https://forum.zcashcommunity.com/t/nu7-timeline/57655/35) |
| from 22 Oct 2026 | track record, every published value with its date and result | [pulsehawk.io/track-record](https://pulsehawk.io/track-record) |
| window to 9 Nov 2026 | upgrade report PH-UR-2026-001, Zcash NU7, method v1.0 | [pulsehawk.io/reports/zcash-nu7](https://pulsehawk.io/reports/zcash-nu7) |

## License

MIT, see `LICENSE`.
