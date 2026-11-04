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

For the Zcash mainnet NU7 report, the inputs are in `data/zcash-mainnet-nu7/` once the measurement window has closed. The report names the commit of this repository and the sha256 of every input it was computed with.

## What is here

| Path | Content |
|---|---|
| `upgrade_report/report_v1.py` | raw values M1 to M7 of the upgrade report, specification v1.0 |
| `upgrade_report/configs/` | one config per chain and upgrade |
| `METHODS.md` | the named methods PH-M-001 to PH-M-004, uncertainty and traceability |
| `FORMATS.md` | the input and output formats |
| `ROUND_ROBIN.md` | how a third party counts one metric with their own data |
| `tools/heads_from_rpc.py` | heads CSV from your own node, for the round robin |
| `examples/` | small example inputs with the expected sha256 |

## Principles

We do not claim any accreditation. We work along the same lines as a test laboratory: every number comes from a named method, every metric states its uncertainty, every input can be traced by its sha256, and we invite others to count the same metric with their own data.

Every number in a report carries its kind. counted is counted by us from our own data, source is taken from a named outside source, computed comes from a model, an estimate or a difference to an estimate.

## License

MIT, see `LICENSE`.
