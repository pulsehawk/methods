# Methods

Version 1.0 of the PulseHawk upgrade report. Each metric of the report names the method it was counted or computed with. The method ids are stable. A change to a method gets a new version in this file and in the script (`VERSION`), never a silent edit.

## PH-M-001 Block intervals from headers

**Input.** Block headers of the chain, fetched from peers over the peer to peer protocol and checked before use for linkage (each header names its parent), proof of work against its own nBits, and the published checkpoints of a node implementation. One row per height, see `FORMATS.md`.

**Procedure.** The interval of block h is the header time of h minus the header time of h minus 1, in whole seconds. Windows are cut by height, see the docstring of `report_v1.py`. Quantiles use linear interpolation between the closest ranks. The model values assume exponential intervals with a mean equal to the target, so p50 is ln 2, p90 is ln 10 and p99 is ln 100 times the target, and the share above 5 times the target is e to the power of minus 5.

**Used for.** M1, M2, M6, and the header time in M7.

**Time base.** Header times. Miners set them within the consensus rules, so they are not a clock and need not increase. The report counts negative intervals.

**Implementation.** `headers/ph_headers.py`, one header client for Zcash and Bitcoin Cash. The two networks differ in their parameters, in the header layout and in their fixed points, the procedure is the same.

- Peers are given on the command line as DNS seed names or as a file. The repository holds no address, and the client prints no address or host name. A peer is named by its software only, without the comment part of its user agent.
- The start is a known height and hash, given as an anchor, taken as the highest published checkpoint below the first height, or the fixed point of the chain profile.
- Only peers that pass the fork probe of the profile count towards the target height. For Bitcoin Cash mainnet, whose message start and port BSV shares, a peer passes only if the first header it sends follows our start block. The second highest reported height is the target, so one peer cannot set it alone.
- Each header must name the hash of the header before it, its hash must not exceed the target from its own nBits, and every fixed point and checkpoint in range must match. The Equihash solution of a Zcash header is not verified.
- A peer on another branch is dropped, and its headers are fetched again from the next peer.
- After the fetch, two further peers deliver the last 500 headers again, and the hashes are compared.
- For Bitcoin Cash mainnet the checkpoints come from BCHN `src/chainparams.cpp` (class CMainParams), read with `tools/bchn_chainparams.py`.

The self test runs offline. It checks 20 real headers per network from `headers/fixtures/` for linkage and work, compares the sha256 of the fixture and of the heads CSV made from it with `headers/fixtures/SHA256SUMS`, and shows that each check catches its own fault.

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

**Implementation.** `listener/listen.py`, one listener for Zcash and Bitcoin Cash with the network profiles, the message layer and the header layout of `headers/ph_headers.py`.

- Peers are given on the command line as DNS seed names or as a file, as for the header client. The listener prints and saves no address and no host name. A connection is named by a label per run and by its user agent, cut before the first "(" and with anything like an address or a host name replaced.
- Zcash: up to 8 connections per node software (Zebra, Zakura), other software is not kept. For every new block hash the first announcer is asked for the block, and only its header is kept, so stale blocks get a parent, a time and nBits.
- Bitcoin Cash: a connection counts only after a fork probe at height 327,988 (the tempnet block must follow on the tempnet, it must not on the chipnet). Every connection is asked for compact blocks in high bandwidth mode, every second one also for header announcements, so all three message types occur. For every new block the first announcer is asked for a cmpctblock unless one came on its own within 3 s, and its short ids are counted against the transaction ids announced to us. That is an estimate, the listener has no mempool.
- `listener/evaluate_bch.py` and `listener/evaluate_zcash.py` give first seen, the spread over the connections, the arrival minus the header time, the compact block path (Bitcoin Cash), the arrival of Zakura minus Zebra (Zcash) and the stale blocks of PH-M-005. `listener/propagation.py` compares first seen at two listeners at different places, per block both saw; the difference contains the path to each listener and both host clocks.

The self tests run offline. `listen.py` checks SipHash-2-4 against its reference values, the cutting of user agents, and replays messages built from the real headers in `headers/fixtures/` through the message handling of both families; the sha256 of the replay output is in `listener/examples/SHA256SUMS`. `listener/example.py` runs the evaluations on a short excerpt of our own recordings in `listener/examples/` and compares the sha256 of all inputs and outputs.

## PH-M-005 Stale blocks after 6 confirmations

**Input.** As PH-M-004, plus our header chain.

**Procedure.** A block counts as stale when it was announced to us and is not in our chain 6 blocks later. The share is stale blocks over chain blocks plus stale blocks.

**Used for.** not in the upgrade report v1. The stale counts per window and chunk in PH-M-008 use it.

**Implementation.** `listener/evaluate_bch.py` as defined above, with `--confirmations` (default 6). A block within that many blocks of the tip stays open, a block whose header the listener never got has no height and is counted apart. `listener/evaluate_zcash.py` counts every announced block that is not in the chain fetched after the runs, gives it a height from its parent and groups the stale blocks into branches with their depth; the chain must therefore be fetched after the end of the runs, otherwise later chain blocks count as one long stale branch.

## PH-M-006 Forecast of the activation time

**Input.** Our header chain up to the tip at the time of the forecast.

**Procedure.** The remaining blocks up to the activation height are drawn many times with replacement from the counted intervals of a recent window, and the drawn intervals are added up. The median of the sums is the forecast, the 5 % and 95 % points form the 90 % band. The window and the number of draws are stated with each forecast.

**Used for.** the forecast rows of M7 (keys `estimate` and `header_time_minus_estimate` in format v1), which are computed, not counted. The implementation follows in this repository.

## PH-M-007 Weekly count of the Bitcoin Cash tempnet

**Input.** Our heads CSV of the tempnet 2027 (one minute blocks) from its first block 327,989, fetched with PH-M-001, and the heads meta of the fetch.

**Procedure.** The range runs from 327,989 to the highest block whose header time is at or before the cutoff. Intervals follow PH-M-001 and are compared with exponential intervals at 60 s. The MTP lag of a block is its header time minus the median of the header times of the 11 blocks before it, and the expectation comes from a simulation with a fixed seed and 200,000 draws. Ten consecutive intervals are compared with ten times 60 s (Erlang) and with one block at 600 s. Difficulty is target(0x1d00ffff) over target(nBits). The tracking of the difficulty uses windows of 100 blocks: a jump of the hash rate from factor 2, confirmed by the next window, is caught up when the last difficulty of a block not at powLimit lies within 15 % of the hash rate of the next 100 blocks times 60 s. Blocks at minimum difficulty (nBits = powLimit) are counted per day and by the gap before them. This is an observation from the headers, the rule itself is not checked.

**Used for.** our weekly tempnet reports, 21 Oct to 15 Nov 2026.

**Time base.** Header times, and the fetch time from the heads meta for the age of the tip.

**Implementation.** `bch_tempnet/weekly.py`.

## PH-M-008 Hash windows and chunks of 120 blocks

**Input.** Our heads CSV of the tempnet and, for stale blocks, the records of our listener (stale after 6 confirmations, PH-M-005), see `FORMATS.md`.

**Procedure.** A block belongs to a hash window when the work over the last 10 blocks (sum of their difficulty over the time since the header before them) reaches 5 difficulty per second, or when the 10 blocks came within 60 s. Runs less than 30 min apart are merged, and the edges move to the first and last block whose own interval alone reaches the threshold. Stale blocks belong to a window by height, the share is stale over announced chain blocks plus stale.

Chunks are 120 consecutive heights from the first row of the heads CSV, the last incomplete chunk is dropped. Per chunk we give the mean difficulty, the mean and median block time (header time minus header time of the parent), stale blocks and stale events (a height with one or more stale blocks counts once), and the listener coverage. Below 99.9 % coverage the stale values stay empty. Two further counts follow rules proposed by a reader of our forum posts. Without difficulty 1, a whole stale branch is left out when the chain block or a stale block at its fork point has difficulty 1. The slot count assigns every stale fork to its slot (the first block after the chain), deduplicates blocks by hash, and without difficulty 1 drops a fork whose chain block or own block at the slot has difficulty 1 and cuts a fork before its first block at difficulty 1. In the slot count the block time is the gap between consecutive counted blocks inside the chunk.

**Used for.** the hash windows and the chunk tables in our tempnet posts, 10 to 18 Nov 2026.

**Time base.** Header times for windows and chunks, arrival at our listener for the coverage and for lists of hours.

**Implementation.** `bch_tempnet/windows.py` and `bch_tempnet/chunks.py`. `bch_tempnet/build_switch.py` splits the listener data at the time a public node changed its build and evaluates PH-M-004 and PH-M-005 before and after.

The self tests run offline. `bch_tempnet/example.py` runs `weekly.py`, `windows.py` and `chunks.py` on a short excerpt of our own tempnet data in `bch_tempnet/examples/` and compares the sha256 of all inputs and outputs with `bch_tempnet/examples/SHA256SUMS`.

## M6 in specification v1.0

Time to stable is the first time, at the earliest 60 min after the activation block, from which the rolling mean of the intervals of the last 60 min stays within plus or minus 20 % of the target for 60 min without a break, where those 60 min must lie fully inside the data. The value is in seconds after the header time of the activation block. A rolling median within 10 % of the target was tested first and rejected. With exponential intervals the median lies at about 69 % of the target, and a simulated ideal 25 s chain was inside that band only 1.7 % of the time.

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

The quantiles of M1 are exact for the chain in the window. Their uncertainty is in the window, not in the counting. The window edges follow header times.

## Traceability

Each report names

- the commit of this repository it was computed with,
- the sha256 of every input file (`*-inputs.json`),
- the sha256 of every output file (`*.sha256`),
- the method id of every row.

The raw export of our watch keeps the byte layout of the files on our data branch, so the sha256 in its `SHA256SUMS` stay valid.

## Round robin

See `ROUND_ROBIN.md`.
