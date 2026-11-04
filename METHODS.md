# Methods

Version 1.0 of the PulseHawk upgrade report. Each metric of the report names the method it was counted or computed with. The method ids are stable: a change to a method gets a new version in this file and in the script (`VERSION`), never a silent edit.

## PH-M-001 Block intervals from headers

**Input.** Block headers of the chain, fetched from peers over the peer to peer protocol and checked before use for linkage (each header names its parent), proof of work against its own nBits, and the published checkpoints of a node implementation. One row per height, see `FORMATS.md`.

**Procedure.** The interval of block h is the header time of h minus the header time of h minus 1, in whole seconds. Windows are cut by height, see the docstring of `report_v1.py`. Quantiles use linear interpolation between the closest ranks. The model values assume exponential intervals with a mean equal to the target, so p50 is ln 2, p90 is ln 10 and p99 is ln 100 times the target, and the share above 5 times the target is e to the power of minus 5.

**Used for.** M1, M2, M6, and the header time in M7.

**Time base.** Header times. Miners set them within the consensus rules, so they are not a clock and need not increase. The report counts negative intervals.

## PH-M-002 Tip switches and chain split

**Input.** The log of our watch. The watch keeps a tree of all headers announced to it and follows the branch with the most work.

**Procedure.** A tip switch is logged whenever our tip moves to a branch that does not contain the old tip. Its depth is the number of blocks of the old tip that drop out of our chain. M3 reports all switches as a distribution by depth (1, 2, 3 or more) and a headline from depth 2. A chain split (M4) is open while two branches from the activation height are each announced by at least the configured number of peers and both are deeper than the configured depth. Its duration runs from the alarm to the all clear.

**Used for.** M3, M4.

**Time base.** Arrival at our watch, from the host clock.

## PH-M-003 Peer protocol version at handshake

**Input.** Peer addresses from the public address sources of the chain (DNS seeds). Addresses are used for the connection only and are never stored.

**Procedure.** At each probe time the watch opens a connection to each address, completes the version handshake, records the protocol version the peer reports and disconnects. The share is the number of peers below the minimum protocol of the upgrade over all peers that answered.

**Used for.** M5.

## PH-M-004 First seen and propagation

**Input.** Every block announcement our listener receives (inv, headers), with the time of arrival in milliseconds and a label per connection.

**Procedure.** First seen of a block is the earliest arrival over all connections. The spread of a block is the last minus the first arrival over the connections that announced it. Both describe one listener with a few dozen connections, not the propagation across the whole network.

**Used for.** first seen in M7.

**Time base.** Arrival at our listener, from the host clock.

## PH-M-005 Stale blocks after 6 confirmations

**Input.** As PH-M-004, plus our header chain.

**Procedure.** A block counts as stale when it was announced to us and is not in our chain 6 blocks later. The share is stale blocks over chain blocks plus stale blocks.

**Used for.** not in the upgrade report v1. The implementation follows in this repository.

## PH-M-006 Forecast of the activation time

**Input.** Our header chain up to the tip at the time of the forecast.

**Procedure.** The remaining blocks up to the activation height are drawn many times with replacement from the counted intervals of a recent window, and the drawn intervals are added up. The median of the sums is the estimate, the 5 % and 95 % points form the 90 % band. The window and the number of draws are stated with each forecast.

**Used for.** the estimate rows of M7, which are computed, not counted. The implementation follows in this repository.

## M6 in specification v1.0

Time to stable is the first time, at the earliest 60 min after the activation block, from which the rolling mean of the intervals of the last 60 min stays within plus or minus 20 % of the target for 60 min without a break, where those 60 min must lie fully inside the data. The value is in seconds after the header time of the activation block. A rolling median within 10 % of the target was tested first and rejected: with exponential intervals the median lies at about 69 % of the target, and a simulated ideal 25 s chain was inside that band only 1.7 % of the time.

## Validation so far

| Method | Validation |
|---|---|
| PH-M-001 | every header fetch is cross checked with two further peers, 500 of 500 hashes equal (Zcash mainnet 2 Nov 2026, BCH tempnet 28 Oct 2026) |
| PH-M-002 | replay of the watch against our Zcash testnet recording of 17 Oct 2026, with built in switches and splits |
| PH-M-003 | the same replay; no independent comparison yet |
| PH-M-004 | comparison with the public data of another tempnet listener on 28 Oct 2026, 4,207 blocks matched by hash |
| PH-M-005 | the same comparison; the two counts measure different things, see the method |
| PH-M-006 | none yet; the NU7 activation on mainnet is the first check against the forecast |

## Measurement uncertainty

For every metric the script writes one row to the uncertainty CSV with five fields.

| Field | What it states |
|---|---|
| time_base | header times or arrival at our listener |
| clock | how good that time is; for arrival, the host clock is synced by NTP and its offset is not measured |
| missing_data | heights missing from the heads, chain blocks never announced to us, peers that did not answer |
| gaps | pauses without any announcement longer than 600 s in the listener data |
| peers | the number of distinct peers behind the value |

The quantiles of M1 are exact for the chain in the window. Their uncertainty is in the window, not in the counting: the window edges follow header times.

## Traceability

Each report names

- the commit of this repository it was computed with,
- the sha256 of every input file (`*-inputs.json`),
- the sha256 of every output file (`*.sha256`),
- the method id of every row.

The raw export of our watch keeps the byte layout of the files on our data branch, so the sha256 in its `SHA256SUMS` stay valid.

## Round robin

See `ROUND_ROBIN.md`.
