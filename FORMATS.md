# Formats

All times are UTC. Our watch and our header client write some field names in German; this file gives each name and its meaning. The names are kept as they are, so the sha256 of our published raw data stay valid.

## Heads CSV (`heads.csv.gz`)

One row per height, written by our header client, gzip compressed. The report also accepts the English column names in brackets.

| Column | Meaning |
|---|---|
| `hoehe` (`height`) | block height |
| `hash` | block hash, hex, in the usual display order |
| `zeit` (`time`) | header time, unix seconds |
| `nbits` | compact target from the header, hex |
| `version` | block version (not used by the report) |

The chain must be continuous over the windows of the report. The uncertainty CSV counts missing heights.

## Watch log (`wache-log.csv`)

One row per change of state of the watch. Lines end with `\r\n`, because the file is written with Python's csv module.

| Column | Meaning |
|---|---|
| `utc` | time of the row, `YYYY-MM-DD HH:MM:SS` |
| `hoehe` | height of our tip at that time, may be empty |
| `signal` | which signal (see below) |
| `wert` | value of the signal |
| `schwelle` | threshold of the signal |
| `stufe` | level: `info`, `hinweis` (notice), `alarm`, `entwarnung` (all clear), or a status word for `signal` = `wache` |

| Signal | Meaning |
|---|---|
| `reorg_gesehen` | a tip switch; `wert` is its depth in blocks (used for M3) |
| `kettenspaltung` | chain split; from `alarm` to `entwarnung` is one episode (used for M4) |
| `reorg` | alarm for tip switches from the configured depth |
| `blockpause` | no new block for `wert` seconds |
| `median_abstand` | median of the last intervals above the threshold |
| `peers_vor_upgrade` | share of peers below the minimum protocol, in percent |
| `eigene_sicht` | the watch has fewer connections than the threshold |
| `wache` | status of the watch: `start`, `ruht` (waiting for the window), `lauf` (hourly), `aktivierung_gesehen`, `fenster_zu` (window closed), `ende` |

## Watch state (`zustand.json`)

| Key | Meaning |
|---|---|
| `aktiv_gesehen_utc` | time our listener first saw the block at the activation height |
| `proben` | probes of PH-M-003, keyed by hours after activation (`"0"`, `"24"`, ...) |
| `proben.<h>.utc` | time of the probe |
| `proben.<h>.peers` | peers that answered the handshake, the n of M5 |
| `proben.<h>.vor_upgrade` | peers below the minimum protocol |
| `proben.<h>.anteil_prozent` | their share in percent |
| `proben.<h>.hoehe` | height of our tip at the probe |
| `proben.<h>.versionen` | number of peers per reported protocol version |
| `signale` | internal state of each signal (not used by the report) |

## Raw export of the watch (`raw/`)

Only data from the measurement window. The gzip files carry no timestamp in their header, so equal data gives equal bytes.

| File | Rows |
|---|---|
| `ereignisse.jsonl.gz` | one announcement per line: `t_ms` arrival in milliseconds, `peer` our label of the connection (for example `zebra-3`), `art` message type (`inv` or `headers`), `hash` |
| `koepfe.jsonl.gz` | one header per line: `hash`, `prev`, `zeit` header time, `nbits`, `von` peer label, `quelle` how we got it |
| `peers.json` | per connection: `lauf` listener run, `peer` label, `software` user agent, `version` protocol, `gemeldete_hoehe` reported height, `verbunden_utc`, `getrennt_utc`, `ankuendigungen` number of announcements |
| `wache-log.csv`, `zustand.json` | as above |
| `SHA256SUMS` | sha256 of each file, in sha256sum format |

No file contains an address, a host name or anything that identifies a peer beyond its software.

## Report CSV (`report-v1-<chain>-<upgrade>.csv`)

| Column | Meaning |
|---|---|
| `metric` | M1 to M7 |
| `key` | the value within the metric, with `_before` or `_after` for the window |
| `value` | the number, or `missing` / `never` |
| `unit` | `s`, `%`, `UTC`, `blocks`, ... |
| `kind` | `counted`, `source` or `computed` |
| `method` | PH-M-001 to PH-M-004 |
| `window_from_utc`, `window_to_utc` | the window of the value |
| `n` | the number of values behind it |
| `source` | the input file or outside source |
| `note` | definition and time base |

## Uncertainty CSV (`report-v1-<chain>-<upgrade>-uncertainty.csv`)

One row per metric and window with `metric`, `method`, `time_base`, `clock`, `missing_data`, `gaps`, `peers`, `note`. See `METHODS.md`.

## Inputs and checksums

`report-v1-<chain>-<upgrade>.sha256` lists the sha256 of both CSV files in sha256sum format. `report-v1-<chain>-<upgrade>-inputs.json` lists the sha256 of the config, the heads and every raw file, and the script version.

## Config (`config.json`)

| Key | Meaning |
|---|---|
| `chain` | name used in the file names |
| `upgrade.name`, `upgrade.height`, `upgrade.height_source` | the upgrade and where its height comes from |
| `upgrade.estimate_utc`, `upgrade.band_utc` | our forecast and its 90 % band (optional) |
| `target_before_s`, `target_after_s`, `target_source` | target block interval before and after the upgrade |
| `min_protocol` | minimum peer protocol version after the upgrade |
| `fork.min_peers`, `fork.min_depth` | definition of a chain split |
| `probes_h` | probe times in hours after activation |
