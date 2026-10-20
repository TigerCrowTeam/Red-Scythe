# General-Purpose SDR Packet Radio

Packet Radio 1.1.0 carries arbitrary binary packets between two Ettus UHD SDRs.
Applications submit and receive packets through local UDP sockets. BPSK, 2FSK,
MSK and GMSK are the release-focus modulations; QPSK is retained and tested.

The supported measured setup is two B205mini radios on one Ubuntu workstation,
connected through attenuated cables. Independent-computer qualification remains
deferred. GMSK and QPSK use the shorter five-minute qualification requested for
this version. Configurable settings are not performance guarantees: see
[final measurements](MODULATION_RESULTS.md) and [supported settings](docs/CONFIGURATION.md).

**Version 1.1.0** adds configurable radio emulation. The
[version 1.1 roadmap](docs/RADIO_V1_1_ROADMAP.md) records the milestones
and acceptance gates for the documented reference scope: the [initial 2FSK emulation contract](docs/EMULATION_PROFILE.md)
is defined, processing layers are separated, and the
[configurable framing API](docs/CONFIGURATION.md#version-11-configurable-framing-api)
is implemented. The [live Python API](docs/CONFIGURATION.md#version-11-live-simple-link-modes)
adds transmit-only, receive-only and bidirectional simple-frame operation without
ACKs or beacons. [Schema-2 YAML profiles](docs/CONFIGURATION.md#version-11-reusable-profiles-schema-2)
now select either format without source changes and retain resolved settings in
capture/replay. Start from the paired [node examples](configs/v1_1/peer-a-simple.yaml)
and [reusable definition](configs/profiles/simple-2fsk-v1.yaml). Schema 1 and legacy
captures remain supported. The [hopping profile](configs/profiles/simple-2fsk-hopping-v1.yaml)
adds a deterministic UTC schedule to simple 2FSK. The separate
[X310 hopping profile](configs/profiles/simple-2fsk-x310-hopping-v1.yaml) selects
2 hops/s on the qualified shared X310/UBX setup; the original remains at 1 hop/s. See the
[configuration and timing limits](docs/CONFIGURATION.md#version-11-frequency-hopping).
[Step 7 reference rates](docs/CONFIGURATION.md#step-7-reference-rates) add tested
100/500 kbit/s simple 2FSK profiles alongside 250 kbit/s. Non-integral resampling,
wider clock tracking and target-specific shaping remain explicitly deferred.
[Step 8 fidelity checks](docs/CONFIGURATION.md#step-8-fidelity-acceptance) verify
independent packet/IQ references, digital spectrum and receiver recovery. Calibrated
RF measurements and actual-target interoperability remain unqualified.

The distribution and commands are named **`packet-radio`**; Python imports use
**`packet_radio`**. See [migration](docs/CONFIGURATION.md#upgrading-to-packet-radio-100)
when updating an older `telemetry-radio` installation.

- [Usage and architecture guide](docs/PROGRAM_GUIDE.md)
- [Current packet protocol](docs/PROTOCOL.md)

## Repository layout

```text
README.md                 Getting started
MODULATION_RESULTS.md     Final modulation summaries
packet_radio/          Application, transmit/receive processing, modems, protocol and hardware
    cli/                  Optional command-line tools
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
docs/                     Configuration, protocol, operating guides and 1.1 roadmap
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

## Develop and verify without hardware


```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m packet_radio.cli.conformance
python -m packet_radio.cli.validate configs/examples/peer-a-bpsk.yaml configs/examples/peer-b-bpsk.yaml
```

Validation does not open sockets, load private keys, or access an SDR unless
`--hardware` is explicitly selected. Paired validation checks identities, timing
roles, waveform, frequency schedule, payload limit, and authentication enablement.
Unknown configuration keys fail rather than being silently ignored.

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

Planned performance work is tracked in the [X310 improvement plan](docs/X310_IMPROVEMENT_PLAN.md).
