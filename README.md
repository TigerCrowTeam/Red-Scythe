# General-Purpose SDR Packet Radio

Packet Radio 1.1.0 carries arbitrary binary packets between two radio endpoints using Ettus UHD.
Applications submit and receive packets through local UDP sockets. BPSK, 2FSK,
MSK and GMSK are the release-focus modulations; QPSK is retained and tested.

The reference setup is one X310 with two UBX-160 channels on one workstation,
connected through crossed, attenuated cables. B205mini radios remain supported,
but independent-computer qualification remains deferred. Configurable settings
are not performance guarantees: see the [calculated hop/payload tables](MODULATION_RESULTS.md)
and [supported settings](docs/CONFIGURATION.md).

**Version 1.1.0** supports configurable radio emulation. The
[2FSK reference contract](docs/EMULATION_PROFILE.md) defines the baseline wire format, and the
[configurable framing API](docs/CONFIGURATION.md#version-11-configurable-framing-api)
is implemented. The [live Python API](docs/CONFIGURATION.md#version-11-live-simple-link-modes)
adds transmit-only, receive-only and bidirectional simple-frame operation without
ACKs or beacons. [Schema-2 YAML profiles](docs/CONFIGURATION.md#version-11-reusable-profiles-schema-2)
now select either format without source changes and retain resolved settings in
capture/replay. Start from the paired [node examples](configs/v1_1/peer-a-simple.yaml)
and [reusable definition](configs/profiles/simple-2fsk-v1.yaml). Schema 1 and legacy
captures remain supported. The [hopping profile](configs/profiles/simple-2fsk-hopping-v1.yaml)
adds a deterministic UTC schedule to simple frames. All five current modulations
can use simple-frame hopping and automatic payload sizing. See
[payload tables](MODULATION_RESULTS.md) for calculated per-hop capacity, not proof of delivery. The separate
[X310 hopping profile](configs/profiles/simple-2fsk-x310-hopping-v1.yaml) selects
2 hops/s on the qualified shared X310/UBX setup; the original remains at 1 hop/s. See the
[configuration and timing limits](docs/CONFIGURATION.md#version-11-frequency-hopping).
[Reference rates](docs/CONFIGURATION.md#reference-rates) add tested
100/500 kbit/s simple 2FSK profiles alongside 250 kbit/s. Non-integral resampling,
wider clock tracking and target-specific shaping remain explicitly deferred.
[Fidelity checks](docs/CONFIGURATION.md#fidelity-acceptance) verify
independent packet/IQ references, digital spectrum and receiver recovery. Calibrated
RF measurements and actual-target interoperability remain unqualified.

The distribution and commands are named **`packet-radio`**; Python imports use
**`packet_radio`**. See [migration](docs/CONFIGURATION.md#upgrading-to-packet-radio-100)
when updating an older `telemetry-radio` installation.

- [Current packet protocol](docs/PROTOCOL.md)
- [Program guide](docs/PROGRAM_GUIDE.md) — what the radio does, its architecture, data flow and runtime behavior.
- [Terminal GUI guide](docs/TERMINAL_GUI_GUIDE.md) — editable fields, choices, units and limits for radio setup and packet building.

## Repository layout

```text
README.md                 Getting started
MODULATION_RESULTS.md     Per-modulation hop/payload/packet-size tables
packet_radio/          Application, transmit/receive processing, modems, protocol and hardware
    cli/                  Command-line entry points
    configuration/        Field catalog, validation, drafts and atomic project storage
    tui/                  Optional Textual editor, dialogs and live status display
    runtime/              Owned child lifecycle and read-only status monitoring
configs/
    examples/             Generic paired profiles to customize
    bench/                Profiles for the tested local SDR pair
    profiles/             Reusable version 1.1 radio definitions
    v1_1/                 Paired schema-2 node configurations
tests/
    modems/               Waveforms, FEC, burst framing and decoder tests
    protocol/             Packet codec, authentication and reliability tests
    hardware/             Mocked UHD, tuning and hardware-clock tests
    integration/          Application, configuration, endpoints and tooling tests
docs/                     Configuration, protocol and operating guides
tools/                    Hardware diagnostics and qualification runners
fixtures/                 Protocol-v5 reference packets
```

Run documented commands from the repository root. The hardware test runner uses
`configs/bench/`; manual startup examples use `configs/examples/`. The software
suite includes mocked hardware tests; running `pytest` does not transmit RF.
The UDP integration tests require permission to create local sockets.

## Install a release

Install the local wheel supplied with this release; no public package index
publication is implied. For hardware, first create the interpreter-matched UHD
virtual environment described in [UHD setup](docs/UHD_SETUP.md).

```bash
python -m pip install /path/to/packet_radio-1.1.0-py3-none-any.whl
packet-radio --help
packet-radio-conformance
```

The wheel includes guides, example and measured bench profiles, reference fixtures,
and diagnostic tools under `$VIRTUAL_ENV/share/packet-radio`. Create a writable
working directory and copy the profiles before editing them:

```bash
mkdir -p ~/packet-radio-work
cp -r "$VIRTUAL_ENV/share/packet-radio/configs" ~/packet-radio-work/
cd ~/packet-radio-work
packet-radio-validate configs/examples/peer-a-bpsk.yaml configs/examples/peer-b-bpsk.yaml
```

Choose each SDR's serial, provision keys and start the paired applications as
shown in the [program guide](docs/PROGRAM_GUIDE.md). Paths inside YAML resolve
relative to the working directory for schema 1, and to the node YAML for schema 2;
keep replay state across restarts. See [1.1 migration](docs/CONFIGURATION.md#upgrading-from-100-to-110).
Installed guides are in `$VIRTUAL_ENV/share/packet-radio/docs`.

To verify the installed wheel outside the checkout, without SDR access:

```bash
cd /tmp
python "$VIRTUAL_ENV/share/packet-radio/tools/check_install.py"
```

`check_install.py` checks the optional editor dependency as well as the headless
commands, bundled profiles, signed simulated delivery and capture replay. After
installing the same wheel with `[ui]`, run the shorter installed-editor workflow:

```bash
cd /tmp
python "$VIRTUAL_ENV/share/packet-radio/tools/check_ui_install.py"
```

It exercises editing, saving, reopening, peer export and owned start/stop with a
fake radio child. It opens no SDR and transmits no RF. Without the UI extra, the
same check verifies that the missing-dependency message explains how to install it.

## Develop and verify without hardware


```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,ui]'
python -m pytest -q
python -m packet_radio.cli.conformance
python -m packet_radio.cli.validate configs/examples/peer-a-bpsk.yaml configs/examples/peer-b-bpsk.yaml
```

Validation does not open sockets, load private keys, or access an SDR unless
`--hardware` is explicitly selected. Paired validation checks identities, timing
roles, waveform, frequency schedule, payload limit, and authentication enablement.
Unknown configuration keys fail rather than being silently ignored.

The terminal editor supports offline drafts, modulation-specific controls,
validation, saving, peer export and owned radio start/stop. See the
[terminal GUI guide](docs/TERMINAL_GUI_GUIDE.md). To launch it:

```bash
python -m pip install -e '.[ui,test]'
packet-radio-ui
# Or: packet-radio-ui configs/v1_1/peer-a-simple.yaml
python -m pytest -q tests/configuration tests/tui tests/runtime
```

Use Tab/Shift+Tab to navigate, Enter to apply text, F5 to validate, F2 for Advanced,
F8 to focus an error, Ctrl+O to open, Ctrl+N for a new draft and Ctrl+Q to quit. Selecting a modulation
applies its preset waveform settings and shows only applicable controls.
For variable-length 2FSK hopping, **Payload sizing → automatic** calculates the
accepted byte limit from hop timing, bit rate and framing. **Max payload bytes**
becomes an upper ceiling; the effective limit appears in the form and summary.
The `configs/v1_1/peer-a-x310-auto-hopping.yaml` example starts at 8 hops/s with
1 ms guards, 8 ms timed TX lead and 16-packet TX batches. Higher rates trade payload capacity for speed; the displayed upper
bound is capped at 29 hops/s and may be lower when timing requires it. Oversized input is rejected.
Use Ctrl+S to save, F4 for Save As, F6 to export a complementary peer and F7 to
import a legacy schema-1 configuration into a new schema-2 draft. Editing and
saving work offline. Hardware access happens only through explicit checks:

- **F9:** refresh devices; **F3:** view results and select a discovered device.
- **F10:** check the current draft against the selected SDR.
- **Ctrl+P:** select a second node YAML and check a shared X310 pair.
- **F11:** cancel an active check; **F3:** view capabilities and actual settings.

Checks run in an owned child process with a 30-second timeout, using the editor's
Python interpreter. That interpreter must have the UHD bindings installed (see
[UHD setup](docs/UHD_SETUP.md)). Checking applies tuning, gain, rate and antenna
settings; stop other software using that SDR first. It creates no streams and
sends no packets. Results distinguish software validity, device compatibility and
RF performance; edits invalidate the checked snapshot. The check reports device
adjustments, including the UBX-160's fixed 160 MHz analog bandwidth. Hardware
capability ranges do not guarantee every intermediate sample rate or measured
throughput. The editor can also run saved configurations:

- **Ctrl+R / Start:** start one saved node.
- **Ctrl+Shift+R / Start pair:** select a saved peer for a shared X310 launch.
- **Ctrl+B / Packets:** [build and edit packets](docs/TERMINAL_GUI_GUIDE.md#build-modify-and-inspect-packets), save templates, preview framing, send/repeat and inspect received data.
- **Ctrl+T or Ctrl+C:** stop the owned radio; **F12:** view live per-channel status, run settings and logs.

Start shows the operating mode and requires saving pending edits first. Generic-v5
without keys requires the same explicit isolated-bench unauthenticated opt-in as
the CLI. Starting transmit-capable modes may emit control traffic without a UDP
payload. The child uses a frozen temporary configuration; edits cannot change a
running radio. Stop and restart to apply new settings.

Running means hardware, payload endpoints and status services have initialized;
it does not mean RF acquisition or payload delivery. Startup has a 30-second
timeout. Stop allows five seconds for graceful cleanup, then kills only the owned
child if necessary. Normal quit stops that child; Linux parent-death protection
also kills it if the editor exits unexpectedly. This owned launch mode requires
Linux. The editor never adopts or stops another process based on a status port.
Hardware discovery/probing is disabled while its radio is active. Run logs stay
in memory; no additional result/status files are created.

F12 refreshes each channel's read-only status at up to 2 Hz. It shows RF acquisition,
peer activity, payload delivery and throughput separately from process readiness,
plus frequency readbacks, waveform/rate, queues, ACK backlog, decoding counters
and UHD errors. A run ID, process ID and effective-configuration fingerprint must
match before readings are accepted. Failed requests, repeated snapshots and old
readings are marked stale; stopped sessions show historical values only. HTTP
status must be enabled in the saved node settings for live readings. Missing or
inapplicable fields appear as N/A. Ctrl+T and Ctrl+C also stop from the details
dialog. No network control endpoint is added.

The terminal interface supports all five modulations, configurable simple frames
and phase-coordinated hopping on the shared X310.
Hopping retains its whole-burst boundary-drop policy; F12 shows admission drops
and hop timing diagnostics. Arbitrary packet-arrival phases can still lose bursts.

Choose a new node YAML path outside the shipped `configs/` templates. Saving
creates that node and an immutable `profiles/<fingerprint>.yaml` beside it.
Reopen the node with Ctrl+O or `packet-radio-ui /path/to/node.yaml`. YAML comments
and formatting are normalized; supported settings and the locations referred to
by key, recording and replay-state paths are preserved. External source edits
block saving until you reopen the project. Existing unrelated files are never
overwritten. Old profiles are retained so a failed save cannot break the old node.

Peer export asks for an explicit SDR identifier and separate ports. Authenticated
peers also require their own private-key and replay-state paths. Export does not
copy keys, replay state or recordings; verify the exported paths on the peer's
host before running. Legacy conversion supports validated fixed-frequency,
`generic-v5` schema-1 configurations and always requires a new destination:

```bash
packet-radio-ui --import-legacy /path/to/legacy.yaml
```

## Software-only packet exchange

```python
from packet_radio.config import FrequencyConfig, RadioConfig, SDRConfig
from packet_radio.simulation import SimulatedNetwork

configs = {
    name: RadioConfig(
        node_id=node,
        peer_id=peer,
        timing_role=role,
        sdr=SDRConfig(samples_per_symbol=4),
        frequency=FrequencyConfig((915_000_000,), epoch=0, guard_seconds=0),
    )
    for name, node, peer, role in (("a", 1, 2, "leader"), ("b", 2, 1, "follower"))
}
network = SimulatedNetwork(configs)
try:
    network.step(4)
    network.inject("a", b"hello\x00\xff")
    network.step(100)
    assert network.received("b") == [b"hello\x00\xff"]
finally:
    network.close()
```

Simulation uses the real application, frame codec, modem, error correction, and
reliability code with memory endpoints and an impaired sample channel. It does not
measure real SDR timing or RF performance. This example intentionally omits keys
inside an isolated software simulation; normal CLI startup requires authentication.

## Payload behavior

Each input UDP datagram becomes one packet for the configured peer. Defaults are
input `127.0.0.1:50000`, output `127.0.0.1:50001`, and 1–1024 payload bytes.
Packets are not interpreted, combined, coalesced, or assigned content-based priority.
Empty/oversized input is rejected explicitly, never truncated.

For generic-v5, the data FIFO holds at most 256 admitted packets, including those awaiting ACK.
When full it rejects the newest input and increments `queue_drops`. A separate
bounded control queue lets ACKs and timing beacons proceed without evicting data.
The link retries unacknowledged packets, suppresses duplicate delivery, and records
exhausted retries. An RF ACK confirms radio receipt, not that the local UDP consumer
processed the packet; local output errors are counted separately. Simple-frame modes
have no ACKs, retries, duplicate suppression or timing beacons. Their supplied pair
is transmit-only/receive-only; set both nodes to `bidirectional` for two-way traffic.

## Compatibility and development

Both endpoints must select matching packet formats. Generic-v5 uses protocol **5**. ACKs carry their own sender sequence plus
a reference to the acknowledged DATA session and sequence. Older protocol traffic
is rejected. Keep the active golden vectors in `fixtures/protocol_v5.json` unchanged
unless deliberately versioning the wire format.

Before committing changes, run the configured Ruff, Mypy, pytest coverage,
conformance, and package checks. Hardware work must use a suitable attenuated
conducted setup. Example frequency and gain values are placeholders for bench
configuration, not measured performance profiles or authorization to radiate.

## Build a release locally

From a development environment at the repository root:

```bash
python -m build
python -m twine check dist/*
```

This creates a wheel and source archive in `dist/`. The source archive includes
tests, tools, all profiles and guides; the wheel installs runtime code and the
shared resources described above. CI installs the wheel into a fresh environment
and runs `check_install.py` from outside the checkout. UHD remains a host-provided
dependency and is not bundled into the wheel. Python 3.10+ is declared; the CI
matrix targets 3.10–3.14, while the measured UHD host uses Python 3.14.4.

For two endpoints on one X310, use `--config` and `--peer-config` in a single
process; see [the shared-device setup](docs/UHD_SETUP.md#two-endpoints-on-one-x310).
