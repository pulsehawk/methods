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

## Heads meta (`heads-meta.json`)

Written by `headers/ph_headers.py` next to the heads CSV.

| Key | Meaning |
|---|---|
| `method` | `PH-M-001` |
| `network` | chain profile, for example `zcash-mainnet` or `bch-tempnet` |
| `fetched_utc` | time of the fetch |
| `start_height` | the known start point, the first row is the height after it |
| `first_height`, `tip_height`, `tip_hash` | range of the CSV |
| `fixed_points` | heights the profile fixes or excludes |
| `checkpoints_checked` | number of published checkpoints inside the range that matched |
| `target_height`, `target_from_peers`, `probes_refused` | the target height, from how many peers on this chain, and how many probes were on another chain |
| `forks_dropped` | branches we dropped, with fork height and length |
| `cross_checks` | per further peer `software`, `compared`, `equal`, `peer_height_checked` (the highest height in the headers this peer sent for the cross check, not the tip of its chain) |
| `cross_checks_without_answer` | further peers that did not answer |
| `peer_software` | software of every peer used, without version comment |
| `csv_sha256` | sha256 of the heads CSV |

Our older heads meta files carry German keys with the same content (`abruf_utc`, `spitze_hoehe`, `gegenproben` with `verglichen` and `gleich`).

## Raw header fixture (`headers/fixtures/*.json`)

Raw headers as they came from a peer, so anyone can check them offline with `python3 headers/ph_headers.py --check-raw FILE`.

| Key | Meaning |
|---|---|
| `network` | chain profile |
| `fetched_utc` | time of the fetch |
| `anchor_height`, `anchor_hash` | the known header before the first one |
| `last_hash` | hash of the last header, in display order |
| `headers` | the raw headers in hex, in height order (80 bytes for Bitcoin Cash, with the Equihash solution for Zcash) |

`headers/fixtures/SHA256SUMS` lists the sha256 of each fixture and of the heads CSV made from it.

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

## Listener records read by `bch_tempnet/`

A listener folder holds one folder per run (`YYYYMMDDTHHMM`) and two files of the last evaluation over all runs. The full description of the listener files is in the next section; here are the fields the tempnet tools read.

| File | Field | Meaning |
|---|---|---|
| `<run>/meta.json` | `start_utc`, `ende_utc` | start and end (`ende`) of the run, used for the coverage |
| | `peers` | per connection `peer` (`peer_1`, `peer_2` and so on, numbered again per run), `software` (user agent), `version` (protocol), `verbunden_utc` (connected), `getrennt_utc` (disconnected, missing if the connection lasted to the end); `build_switch.py` only |
| `<run>/koepfe.jsonl.gz` | `hash`, `prev`, `nbits` | one line per header the run fetched, also for stale blocks; `chunks.py` only |
| `<run>/ereignisse.jsonl.gz` | `hash`, `peer`, `t_ms` | one line per announcement (`Ereignis`, event), arrival in unix milliseconds; `build_switch.py` only |
| `bloecke.csv` | `height`, `hash`, `first_seen_utc`, `interval_s`, `timestamp_deviation_s`, `propagation_spread_ms`, `peers_announced` | one row per chain block (`Bloecke`, blocks); `first_seen_utc` is empty when no run saw the block |
| `zusammenfassung.json` | `stale.bloecke` | the summary (`Zusammenfassung`); per stale block `height`, `hash`, `first_seen_utc`, `peers_announced` |

## Listener output (`listener/listen.py`)

One folder per run. The field names are those of our recorded series since October 2026, partly German, so that old and new recordings read the same; the German word is given in brackets. Times are UTC, `t_ms` is unix milliseconds from the clock of the host.

| File | Field | Meaning |
|---|---|---|
| `ereignisse.jsonl` (events) | `t_ms`, `peer`, `art`, `hash` | one line per block announcement: time of arrival, connection label, message type (`art`, kind: `inv`, `headers`, or for Bitcoin Cash `cmpctblock`), block hash |
| `koepfe.jsonl` (headers) | `hash`, `prev`, `zeit`, `nbits`, `von`, `quelle` | one line per block header the run got, once per hash, also for blocks that end up stale: hash, parent hash, header time (`zeit`), nBits, the connection it came from (`von`) and how (`quelle`, source: `headers`, `block`, `cmpctblock`) |
| `compact.jsonl` (Bitcoin Cash only) | `t_ms`, `peer`, `hash`, `von_selbst`, `kurz_ids`, `vorbefuellt`, `fehlend_geschaetzt`, `tx_ids_bekannt` | one line per cmpctblock: whether it came unasked (`von_selbst`), the number of short ids (`kurz_ids`), prefilled transactions (`vorbefuellt`), short ids that match no transaction announced to us (`fehlend_geschaetzt`, estimated missing) and how many transaction ids we held (`tx_ids_bekannt`) |
| `meta.json` | `netz`, `protokoll`, `start_utc`, `ende_utc`, `geplantes_ende_utc`, `dauer_min` | network profile, protocol version we announced, actual start and end, planned end, planned minutes |
| | `peers` | per connection `peer` label (`zebra-1`, `zakura-1` for Zcash, `peer_1` for Bitcoin Cash, numbered again per run), `software` (user agent cut before the first "(", anything like an address or a host name replaced by `[removed]`), `version`, `gemeldete_hoehe` (reported height), `verbunden_utc`, `getrennt_utc`, `ende_grund` (why the connection ended), `ankuendigungen` (announcements), for Bitcoin Cash `erbeten` (what we asked for) and `ganze_bloecke` (whole blocks instead of a cmpctblock) |
| | `block_hashes_gesehen`, `koepfe_geholt` | block hashes seen, headers got |
| | `ziel_verbindungen_je_software` | Zcash: connections kept per node software |
| | `peers_ohne_abzweig_abgelehnt` | Bitcoin Cash: peers refused by the fork probe |

Our recorded Zcash series name the files of a run `sNN-ereignisse.jsonl.gz`, `sNN-koepfe.jsonl.gz` and `sNN-meta.json` in one folder; `evaluate_zcash.py` reads them with `--events`, `--heads-seen` and `--meta`.

## Listener evaluations (`listener/evaluate_bch.py`, `listener/evaluate_zcash.py`, `listener/propagation.py`)

`evaluate_bch.py --csv` writes one row per chain block whose header time lies in a run or that a run saw: `height`, `hash`, `header_time_utc`, `interval_s`, `first_seen_utc` and `first_seen_type` (first announcement over all connections), `first_inv_utc`, `first_headers_utc`, `first_cmpctblock_utc`, `timestamp_deviation_s` (first seen minus header time), `propagation_spread_ms` (last minus first announcement over the connections, from two connections), `peers_announced`, `cmpctblock` (`yes` or `no`), `cmpctblock_unsolicited_peers`, `short_ids`, `prefilled`, `missing_tx_estimate` (of the first cmpctblock). Empty means not seen or not given.

`evaluate_bch.py --out` writes the summary: the runs, the hours covered, the user agents, the chain tip, `chain_blocks`, `announced_seen`, `first_seen_type` per message type, `timestamp_deviation_s`, `propagation_spread_ms` and `peers_per_block` as `n`, `mean`, `median`, `p90`, `p99`, `min`, `max` (nearest rank), the compact block path and `stale` with `count`, `confirmations`, `share_pct`, `open_below_confirmations`, `without_header` and one entry per stale block (`height`, `hash`, `header_time_utc`, `first_seen_utc`, `first_seen_type`, `peers_announced`, `canonical_hash_at_height`).

`evaluate_zcash.py --out` writes the window, first and last announcement and the minutes covered, connections and announcements per software, `chain_blocks_in_window`, `announced_seen`, `stale_count`, `stale_without_header`, with `--activation` the `phases` before and from the activation and from it per 6 h (`chain_blocks_in_window`, `announced_seen`, `stale`, `stale_share_pct`), the branches with `max_depth` and `depths`, up to 20 `stale_examples`, `zakura_minus_zebra_ms`, `spread_all_connections_ms` and `first_arrival_minus_header_time_s` (interpolated quantiles), and the meta of the runs.

`propagation.py` writes `n` (blocks both listeners saw), `a_blocks`, `median_s`, `p10_s`, `p90_s`, `min_s`, `max_s` of first seen at A minus first seen at B, and `a_first`, `b_first`.

## Listener example (`listener/examples/`)

Two chipnet runs (`bch-chipnet/`), two Zcash mainnet runs (`zcash-mainnet/`) and the tempnet runs of two places for one evening (`bch-tempnet/a`, `bch-tempnet/b`), each with our heads for the span, cut to the fields the tools read. `SHA256SUMS` lists every input and every output of `listener/example.py`, including the replay of `listen.py`'s self test.

## Tempnet chunk CSV (`chunks.csv`, `chunks-no-diff1.csv`)

Written by `bch_tempnet/chunks.py`, one row per chunk of 120 heights.

| Column | Meaning |
|---|---|
| `chunk_n` | number of the chunk, from 1 at the first row of the heads CSV |
| `first_height`, `last_height`, `start_utc` | heights of the chunk and header time of its first block |
| `mean_difficulty` | mean difficulty of the counted blocks |
| `mean_block_time_s`, `median_block_time_s` | header time minus header time of the parent, per counted block |
| `stale_blocks`, `stale_events` | stale blocks by height, and heights with one or more stale blocks; empty without full coverage |
| `listener_coverage_pct`, `full_coverage` | share of the time of the chunk a listener run covered, and `yes` from 99.9 % |
| `block_count` | `chunks-no-diff1.csv` only: blocks not at difficulty 1 |

The slot CSVs (`--slot-csv`, `--slot-nomin-csv`) have the columns `chunk_n`, `first_height`, `last_height`, `start_utc`, `mean_difficulty`, `mean_block_time_s`, `median_block_time_s` (here the gap between consecutive counted blocks inside the chunk), `block_count`, `stale_events` (slots with a fork), `stale_forks`, `stale_blocks` (deduplicated by hash), `stale_max_fork_depth`, `listener_coverage_pct` and `full_coverage`.

## Tempnet example (`bch_tempnet/examples/`)

`SHA256SUMS` lists the sha256 of every input (`input/<path>`) and of every output that `bch_tempnet/example.py` writes (`output/<name>`).

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
