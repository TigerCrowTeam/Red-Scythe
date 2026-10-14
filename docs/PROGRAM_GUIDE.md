# Packet Radio Program Guide

## Current scope

Two peer applications carry arbitrary byte packets over the radio link. Either
peer can send. A timing leader/follower setting coordinates the radio schedule;
it does not change the meaning or priority of application data.

The generic core, direct UHD backend and shared configuration interfaces are
implemented. All five modulations passed local sustained, boundary-payload, echo and
signal-recovery checks, with shorter GMSK/QPSK qualification; independent-host testing
is deferred. See
[modulation results](../MODULATION_RESULTS.md) and [UHD setup](UHD_SETUP.md).

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

Profiles require `schema_version: 1`. Use `radio` for center frequency, sample
rate, bandwidth, UHD selection, gains and antennas; `modem` for modulation and
bit rate; `error_correction` for FEC; and `packet` for the built-in format and
payload limit. `endpoint`, `link`, `authentication`, `status` and `recording`
retain their separate responsibilities.

See [configuration schema and migration](CONFIGURATION.md) for exact names, rate
semantics, airtime admission and examples. Old unversioned YAML is rejected.
Profiles are applied at startup; hopping and live changes remain future work.

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
    reception.py            Receive diagnostics, packet validation and delivery
    config.py               Startup schema and validation
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
        framing.py           DATA/ACK/SYNC packet codec
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
