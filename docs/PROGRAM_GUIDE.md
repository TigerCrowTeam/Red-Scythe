# Packet Radio Program Guide

This guide explains what Packet Radio does, how data moves through the program,
and how its configuration, radio links, authentication and runtime components work.
For explanations of the editable GUI fields, use the separate
[Terminal GUI Guide](TERMINAL_GUI_GUIDE.md).

## Current scope

Release **1.1.0** supports both reliable generic-v5 and ACK-free simple frames on all five modulations.
Use the [1.1 migration and profile table](CONFIGURATION.md#upgrading-from-100-to-110)
for node examples, operating modes, rates and the qualified hopping schedule.
The generic link behavior below applies only when generic-v5 is selected.

Two peer applications carry arbitrary byte packets over the radio link. Either
peer can send. A timing leader/follower setting coordinates the radio schedule;
it does not change the meaning or priority of application data.

The generic core, direct UHD backend and shared configuration interfaces are
implemented. The shared-channel X310 is the reference for the documented conducted
setup. B205 operation needs workload-specific validation, and independent-host
testing remains deferred. See the calculated [hop/payload tables](../MODULATION_RESULTS.md)
and [UHD setup](UHD_SETUP.md).

## Configuration

Commands and inline file paths are relative to the repository root or a writable
working directory containing copied release profiles. See README.md for wheel installation.

Use matching `configs/examples/peer-a-bpsk.yaml` and `configs/examples/peer-b-bpsk.yaml` profiles.
Select `peer-*-2fsk.yaml` for 2FSK, including the explicit frequency deviation.
Select `peer-*-msk.yaml` for MSK; deviation is derived from the bit rate.
Select `peer-*-gmsk.yaml` for GMSK, with explicit Gaussian BT and span settings.
Separate `peer-*-qpsk.yaml` examples retain QPSK. Each pair is intended for separate
computers, so their loopback input/output/status ports can be identical. Multiple
instances on one computer require distinct bind and status ports.

Schema-1 profiles use `schema_version: 1`. Use `radio` for center frequency, sample
rate, bandwidth, UHD selection, gains and antennas; `modem` for modulation and
bit rate; `error_correction` for FEC; and `packet` for the built-in format and
payload limit. `endpoint`, `link`, `authentication`, `status` and `recording`
retain their separate responsibilities.

See [configuration schema and migration](CONFIGURATION.md) for exact names, rate
semantics, airtime admission and examples. Old unversioned YAML is rejected.
Profiles are applied at startup. Configured hopping is supported for simple frames on all five modulations;
changing a running configuration requires stopping and restarting both matching peers.

## Authentication setup

Generate separate endpoint identities using the existing key CLI:

```bash
python -m packet_radio.cli.key_cli generate 1 keys --label initial
python -m packet_radio.cli.key_cli generate 2 keys --label initial
```

Set each profile's `private_key_file` to its own generated private key, and both
`public_key_files` entries to the corresponding public keys. The generated files are `keys/node1-initial-private.pem`,
`keys/node1-initial-public.pem` and the corresponding `node2` files. Update the
example `keys/peer-a-private.pem` placeholders to those actual paths.
Copy only the required private identity to each endpoint and exchange public keys.
Preserve each endpoint's replay-state file across restarts.

Normal CLI startup requires a configured private key. Authentication verifies
identity and integrity; payloads are not encrypted. `--allow-unauthenticated` is
an explicit isolated-bench option, not the default profile.

## Start the paired radios

After provisioning keys and editing the device serials, validate the pair offline.
On host A, run `packet-radio --config configs/examples/peer-a-bpsk.yaml`; on host B,
run `packet-radio --config configs/examples/peer-b-bpsk.yaml`. Substitute the same
modulation suffix on both hosts for 2FSK, MSK, GMSK or retained QPSK. Use Ctrl+C
for clean shutdown. The supplied example pair uses identical local ports because
it targets separate hosts; use the measured `configs/bench/` pair for the current
two-radio workstation setup, with keys provisioned there too. The two-host workflow
is documented but still awaits physical qualification.

## Local packet interface

The radio reads input UDP packets on port 50000 and emits received packets to
port 50001 by default. These are local application ports; RF is the connection
between computers. No Internet service is involved.

The packet helper supports text, hexadecimal bytes, and binary files:

```bash
python -m packet_radio.cli.payload_cli receive --port 50001 --count 1
python -m packet_radio.cli.payload_cli send --port 50000 --hex 000102ff
```

Use `packet-radio-payload send --port 50000 --file payload.bin` to submit a
1–1024-byte binary file as one packet; larger files are not automatically split.
The receiver prints JSON containing the exact hexadecimal payload.

Run a receiver on the destination computer before sending. A successful local
send only means UDP submission succeeded. Use the destination output and status
counters to verify RF delivery. The helper works with running radio processes;
it does not itself access SDR hardware.

## Application structure

`RadioApp.step()` tunes as needed, processes received samples, polls a bounded
number of local packets, queues synchronization traffic, transmits eligible
packets, and checks recovery. Hardware and payload interfaces are injectable.

The package is grouped by responsibility:

```text
packet_radio/
    app.py                  Resource ownership, application loop and recovery
    transmission.py         Burst preparation, TDD admission and timed TX
    reception.py            Receive samples/diagnostics and dispatch decoded frames
    config.py               Startup schema and validation
    profiles.py             Schema-2 reusable definitions, composition and provenance
    endpoint.py             Local UDP and memory interfaces
    scheduler.py            Frequency/slot scheduling
    clock_sync.py           Hardware clock alignment
    modems/
        base.py             Modem contract, bounded streaming and timestamps
        bpsk.py             BPSK waveform and receiver
        qpsk.py              QPSK waveform and receiver
        fsk.py               2FSK waveform and shared continuous-phase processing
        msk.py               MSK constraints and derived frequency deviation
        gmsk.py              Gaussian shaping, receive template and delay correction
        registry.py          Waveform selection and rate derivation
        burst.py             Byte envelope and packet protection
        fec.py               Error-correction primitives
        decoder.py           Background receive decoder
    protocol/
        interfaces.py        Packet-codec and link-behavior contracts
        registry.py          Complete processing-profile selection
        codec.py             Generic-v5 codec adapter and validation
        generic.py           Generic-v5 ACK/replay/beacon and delivery behavior
        framing.py           Frozen DATA/ACK/SYNC wire codec
        simple.py            Configurable frame codec and reference 2FSK sample pipeline
        simple_link.py       ACK-free modes, FIFO, framed input and live SimpleProfile
        authentication.py    Key loading and fingerprints
        replay_protection.py Session/sequence replay checks
        reliability.py       Queues, ACK/retry and duplicate suppression
    hardware/
        uhd.py               UHD adapter and buffered sample transport
    cli/                     Payload, keys, status, validation, replay and conformance commands
    simulation.py            Paired software radio/channel model
    recording.py             IQ/frame capture and replay
    statistics.py            Link counters
    status.py                Status, health and metrics service
```

Command modules live under `cli/`; `__main__.py` remains the radio entry point.
Installed command names now use the `packet-radio` prefix. Module
invocations use `python -m packet_radio.cli.validate` (and the corresponding
CLI module for other tools). Reinstall the package after updating an existing
installation so its console entry points reference the new locations.

`app.py` owns resources and shared link state, and delegates burst scheduling to
`transmission.py` and incoming packet processing to `reception.py`. Waveform
algorithms belong in `modems/`, packet rules in `protocol/`, and device operations
in `hardware/`. The deployment preflight command and unsupported timed frequency
tuning have been removed. Immediate frequency tuning, timed transmission and
hardware clock synchronization remain available.

The processing profile in `protocol/registry.py` supplies a `PacketCodec`, a
`LinkBehavior` and a `BurstProcessing` composition. Live TX/RX, size validation and
IQ replay select the same profile. The packet codec owns encoding, decoding,
candidate validation and byte counts; the behavior owns identity allocation,
ACK/retry/replay decisions, beacon generation, recovery and delivery. Existing
protocol state remains on `RadioApp` for timing/diagnostic tooling, initialized by
the selected behavior.

`modems/burst.py` defines the byte-transform and envelope interfaces. A
`TransformChain` applies transforms in explicit transmit order and reverses them
on receive. The generic chain is Hamming/interleaving followed by optional RS.
The packet codec supplies CRC/signature handling before this chain. Burst framing
supplies training, sync, length-field interpretation and size; waveform constructors
receive those components before building acquisition tables. The current interfaces
operate on byte-aligned data. `protocol/simple.py` supplies configurable frame
components and the reference 2FSK sample pipeline. The live profile connects it to
`RadioApp(processing=SimpleProfile(...))` through `protocol/simple_link.py`; whitening, differential
encoding and non-byte fields remain unsupported until required by a target.

Schema 1 retains generic-v5 and its existing files unchanged. Schema 2 resolves a
reusable radio definition plus local host settings through `profiles.py`, then
`resolve_processing` selects generic-v5 or the simple format. The same resolved
configuration drives validation, live operation and capture-format-3 replay.
Schema-1 selection of `simple-frame-v1` still fails validation. See `CONFIGURATION.md`
for composition, overrides, migration and the retained Python API.
Software sample tests do not qualify a new RF operating profile.

For a future waveform, add a module under `modems/`, implement the `Modem`
contract (or reuse `StreamingModem` where appropriate), and register it in
`modems/registry.py`. BPSK, QPSK, 2FSK, MSK and GMSK share buffering through `StreamingModem`;
none inherits another waveform's detector. Keep waveform-specific acquisition in
the corresponding module. Tests are grouped under `tests/modems/`,
`tests/protocol/`, `tests/hardware/` and `tests/integration/`; hardware runners
remain in `tools/`. Run a group with `python -m pytest tests/modems/`, or run
`python -m pytest` for the full suite.

Python imports follow the grouped layout, for example:

```python
from packet_radio.modems import BPSKModem, FSK2Modem, QPSKModem
from packet_radio.modems.registry import make_modem
from packet_radio.protocol.framing import RadioPacket
from packet_radio.hardware.uhd import UHDRadio
```

No application payload parsing, content-based priority, or automatic stream
requests occur. One packet's contents never replace another queued packet.

## Status and recording

The status service exposes `/status`, `/metrics`, and `/healthz` on
`127.0.0.1:8765` by default. Text, JSON and metrics report the waveform, configured
bit rate, active frequency, rolling delivered throughput, queue drops and errors.
`/status` also includes effective configuration (authentication file paths redacted)
and startup hardware readbacks when provided by the backend.

Three independent activity flags expire after the profile's `sync_loss_seconds`:

- `receiver_acquired`: a frame passed protocol/CRC/authentication validation; it
  need not belong to the configured peer.
- `peer_active`: accepted traffic from the configured peer, including a matching
  ACK, fresh SYNC or valid DATA. This does not imply local application delivery.
- `application_delivering`: payload bytes reached the local endpoint successfully.

Their ages are `last_frame_age_seconds`, `last_peer_age_seconds` and
`last_delivery_age_seconds`; absent observations have age `null`.
`delivered_payload_bps` counts successful local payload bytes over the last five
seconds (or elapsed uptime during startup). Polling status does not reset it;
it falls to zero after idle time. `throughput_window_seconds` gives its denominator.
UDP delivery means the local socket accepted the datagram, not that a separate
application consumed it.

`/healthz` returns 200 only while the radio is ready, synchronized and recently
heard its peer; otherwise it returns 503. An idle leader can therefore be
unhealthy after its peer-activity timer expires, even though its own scheduler is
synchronized. No payload traffic is required for health. Use delivery counters,
activity flags, `retry_exhausted` and endpoint errors to assess application traffic.
`radio_ready` becomes false at shutdown; `shutdown_errors` records cleanup failures.

Recording and replay remain available through `recording` configuration and
`python -m packet_radio.cli.replay`. Captures declare the packet protocol and modem
settings. Old packet protocol versions are rejected during packet inspection.
For signed frames, supply the trusted public keys to replay.

## Running and stopping

After installing the current hardware adapter, provisioning keys, and preparing
an appropriate conducted setup, the application entry point remains:

```bash
python -m packet_radio --config configs/examples/peer-a-bpsk.yaml
```

Use the paired profile on the other computer. These profiles have not completed
hardware acceptance. Ctrl+C or SIGTERM closes endpoints and streams.

Run the application manually using the command above. Local hardware qualification
uses `tools/uhd_link_check.py`; `tools/udp_peer_check.py` probes separately running
radios on two computers. See `docs/UHD_SETUP.md` for instructions and current limits. Automatic
service installation and monitoring deployment templates are not included.


The live simple profile supplies transmit-only, receive-only and bidirectional
behavior without ACKs/retries/SYNC, and validates preframed input separately from
payload input. Its FIFO includes in-flight capacity and reports terminal TX drops.
Transmit-only mode drains receive samples without decoding; receive-only mode never
submits TX samples. Health requires local readiness, not a synchronized peer.
The UHD adapter flushes a small zero-valued idle interval for this profile so the
final CRC bits survive TX pipeline drain. The modem retains bounded idle history
around maximum-size bursts. See the [live API](CONFIGURATION.md#version-11-live-simple-link-modes)
for construction, limits and the repeatable `tools/simple_link_check.py` bench check.
Schema-2 YAML makes the same selection available and retains its
complete configuration in capture format 3. Existing capture formats 1/2 remain readable.


Schema-2 examples are in `configs/v1_1/`, referencing shared definitions in
`configs/profiles/`. The validator's `--resolved` option prints the effective
settings and definition fingerprint without accessing an SDR or exposing key paths.
Profile paths and local file paths are node-relative in schema 2; schema 1 retains
working-directory paths. See [reusable profiles](CONFIGURATION.md#version-11-reusable-profiles-schema-2)
for the supported fields and mode-aware pair checks.

### Hopping runtime

Schema-2 simple profiles can resolve a frequency table into `FrequencyScheduler`.
`RadioApp._retune` tracks absolute slots, counts skipped slots/clock reversals and
serializes tuning with `DecoderWorker.retune`. The worker owns the decode lock,
clears old output and partial frames, and discards samples during the post-tune
guard. Synchronous reception applies the same guard. `transmission.transmit_one`
rechecks complete-burst admission after modulation and drops crossing simple
frames without retry. Fixed-frequency scheduling and wire formats are unchanged.
Configuration limits and the external-UTC synchronization contract are documented
in `CONFIGURATION.md`; `tools/hopping_check.py` exercises the conducted paths.


Automatic payload sizing can prioritize hop speed over packet capacity in the
variable-length 2FSK hopping mode. The radio calculates how many payload bytes fit
within each dwell, up to the user's ceiling, and displays that effective limit.
It accounts for framing/protection and timing reserves; it does not truncate or
fragment messages. Both peers must use matching settings. See the terminal GUI
guide for the Payload sizing, Hop rate and Guard seconds controls, and the final
modulation results for measured hardware performance.


For X310/UBX-160 hopping, `hardware/tuning.py` sets each RX/TX analog LO once
per channel session at the hop table midpoint, then uses DSP-only requests.
Runtime and hardware preflight share this implementation. The backend verifies
center-frequency and LO readbacks. DSP hops retain continuous RX and trim old IQ
using a hardware-time fence, while the decoder resets at each hop. Input waits do
not hold the decoder lock; generation checks exclude blocks acquired across a
retune. Other hardware and fixed-frequency operation use ordinary automatic tuning.
The current supported cap is 29 hops/s. See MODULATION_RESULTS.md for the calculated
payload tradeoff; the cap is an application limit, not the X310's absolute
hardware tuning limit.
