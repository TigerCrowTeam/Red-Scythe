# Radio Emulator 1.3

Radio Emulator 1.3 carries arbitrary binary packets between configurable SDR
endpoints using Ettus UHD. Applications submit and receive packets through local
UDP sockets. BPSK, 2FSK, MSK, GMSK and QPSK support configurable waveforms and
packet framing. The Python package release is **1.3.0**.

The same program supports two hardware arrangements:

- **X310:** one computer owns both UBX-160 channels in a shared process.
- **B205:** each of two computers owns one SDR; application data travels over RF.
  Ethernet and SSH are optional for administration and benchmark control.

Both arrangements have conducted packet-delivery checks. The latest X310 smoke
test verified discovery, GUI start/stop, both directions and simultaneous traffic.
B205 checks cover fixed-frequency operation and authenticated generic-v5 RF
hopping with independent computer clocks. See [measured results and calculated
payload tables](MODULATION_RESULTS.md) and [hardware setup and qualification
scope](docs/UHD_SETUP.md) for the exact settings and limitations.

The terminal GUI provides modulation-specific fields, input limits, packet
creation/editing, saved projects, hardware checks and owned radio start/stop.
[Schema-2 profiles](docs/CONFIGURATION.md#version-11-reusable-profiles-schema-2)
select reliable, signed `generic-v5` packets or configurable ACK-free
`simple-frame-v1` packets. All five modulations support both formats. Schema-1
configurations and legacy captures remain supported.

For X310 hopping, the program holds the analog LO at the hop-table midpoint and
changes digital frequency offsets. With a 1 MHz configured signal, the allowed
hop-center span is at most 159 MHz. Automatic payload sizing trades packet
capacity for dwell time, with a calculated software ceiling of 29 hops/s;
actual qualification depends on modulation and settings. B205 uses ordinary
retuning and RF leader synchronization. Its normal GUI limit remains **1 hop/s**;
higher measured rates use explicit benchmark overrides, not supported GUI defaults.

The B205 results include different bit rates and payload sizes. QPSK's newer
hopping checks use 500 kbit/s, while the other modulations use 250 kbit/s.
Larger QPSK packets reported receive overflows; 2FSK and GMSK also had
fixed-frequency simultaneous-traffic failures at a tested upper endpoint.
Passing one hopping workload does not qualify every frequency or traffic pattern.
[Independent fidelity checks](docs/CONFIGURATION.md#fidelity-acceptance) and the
[reference wire-format contract](docs/EMULATION_PROFILE.md) document the emulated
behavior. Interoperability with a particular commercial radio remains unverified.

The distribution and commands are named **`packet-radio`**; Python imports use
**`packet_radio`**. See [migration](docs/CONFIGURATION.md#upgrading-to-packet-radio-100)
when updating an older `telemetry-radio` installation.

- [Current packet protocol](docs/PROTOCOL.md)
- [Program guide](docs/PROGRAM_GUIDE.md) — what the radio does, its architecture, data flow and runtime behavior.
- [Terminal GUI guide](docs/TERMINAL_GUI_GUIDE.md) — editable fields, choices, units and limits for radio setup and packet building.

## Automatic SDR setup

Run `packet-radio-ui` for a new project. It discovers local UHD devices without
starting streams. One B205 selects a single node on this computer; one X310
selects the shared two-channel simple-frame setup. Multiple supported SDRs require
an explicit selection. No SDR found leaves the offline editor available.

For two computers, install the same program and matching UHD bindings on each,
attach one B205 to each computer, and choose different roles:

```bash
# Computer A
packet-radio-ui --node-role a
# Computer B
packet-radio-ui --node-role b
```

New drafts start at TX/RX gain 0 dB. The shipped fixed-frequency B205 examples
and bench preset use TX 20 dB / RX 40 dB from the earlier conducted tests. The
later RF-hopping measurements used TX 30 dB on dev5, TX 60 dB on dev10, and
RX 40 dB on both, with 30 dB attenuation in each crossed path. These are distinct
tested setups; use the matching [setup details](docs/UHD_SETUP.md#b205-timing-experiments).
Save each local project and use Start. The initial setup is bidirectional 2FSK,
915 MHz, 250 kbit/s, with simple frames and a fixed frequency. Keep the waveform
and packet settings matched on both computers. UDP endpoints stay on localhost;
the computers exchange packet data through their SDRs, not a LAN connection.
The automatic simple-frame setup stays fixed-frequency. For RF-synchronized
hopping, use the separate authenticated generic-v5 B205 profiles described in
[UHD setup](docs/UHD_SETUP.md#rf-synchronized-b205-hopping).

Use `--offline` to skip startup discovery, or Ctrl+D to detect a new setup from
the editor. Opening a saved project preserves its hardware and radio settings.
For headless operation, `packet-radio --config NODE.yaml --auto-device` selects
the local SDR; `--sdr-serial SERIAL` resolves multiple devices. Explicit
`--config NODE_A --peer-config NODE_B` X310 operation remains supported.
See [two-computer setup and the first packet check](docs/UHD_SETUP.md#two-computers-with-one-b205-each).

## Save a two-computer project

In the GUI, choose **Setup** (Ctrl+G). Select **B205 — remote radio peer**, enter
this SDR's serial, choose Computer A or B, and select a gain preset. The B205
conducted bench preset applies TX 20 dB / RX 40 dB for the crossed 30 dB attenuated
setup. **Keep current gains** preserves custom values. Apply edits the draft;
Save and Start are separate actions. The X310 mode retains its local channel pair.

For a fixed-frequency simple-frame B205 pair, choose **Export project** (Ctrl+E),
enter a new folder and the other B205's serial. Authenticated RF-hopping projects
use explicit node files and locally provisioned private keys instead.
The folder contains `computer-a.yaml`, `computer-b.yaml` and one shared
`profiles/` file. Copy the whole folder to the other computer. Open the matching
node on each host, then Start. Hardware settings can be edited separately; after
changing shared waveform/framing settings, export and distribute a new pair.
Local process readiness and recent RF peer activity are shown separately. SSH is
optional for administration; neither Ethernet nor SSH is required for radio data.

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
    profiles/             Reusable waveform and framing definitions
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
virtual environment described in [UHD setup](docs/UHD_SETUP.md). Install from
outside the checkout so local build metadata cannot mask the installed version.

```bash
cd /tmp
python -m pip install /path/to/packet_radio-1.3.0-py3-none-any.whl
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
keep replay state across restarts. See [upgrading to 1.3](docs/CONFIGURATION.md#upgrading-to-radio-emulator-13).
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
For variable-length simple-frame X310 hopping on any supported modulation,
**Payload sizing → automatic** calculates the
accepted byte limit from hop timing, bit rate and framing. **Max payload bytes**
becomes an upper ceiling; the effective limit appears in the form and summary.
The `configs/v1_1/peer-a-x310-auto-hopping.yaml` example starts at 8 hops/s with
1 ms guards, 8 ms timed TX lead and 16-packet TX batches. Higher rates trade payload capacity for speed; the displayed upper
bound is capped at 29 hops/s and may be lower when timing requires it. Oversized input is rejected.
Use Ctrl+S to save, F4 for Save As, F6 to export a complementary peer and F7 to
import a legacy schema-1 configuration into a new schema-2 draft. Editing and
saving work offline. New sessions discover devices automatically unless
`--offline` is selected. Additional hardware checks are explicit:

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

- **Ctrl+R / Start:** start the saved local node; simple-frame X310 projects
  automatically include the other local channel.
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
and hopping on the shared X310 or authenticated RF-synchronized B205 pair.
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
have no ACKs, retries, duplicate suppression or timing beacons. The basic simple-frame pair
is transmit-only/receive-only; the two-computer B205 and shared X310 examples
are bidirectional.

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
