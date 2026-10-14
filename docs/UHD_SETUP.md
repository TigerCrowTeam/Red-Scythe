# Direct UHD setup and bench checks

The hardware backend uses Ettus `uhd.usrp.MultiUSRP` directly. The target is one
B205mini per workstation. B205 devices use UHD's `type=b200` selector. The initial
verified host is Ubuntu 26.04.1, UHD 4.9.0.1-1ubuntu1, system Python 3.14.4 and USB 3.
Other operating systems/UHD builds have not been qualified for this project.

## Python environment

UHD's compiled Python bindings must match the Python interpreter. This host's
Conda Python 3.13 cannot import its system UHD bindings; `/usr/bin/python3` can.
Do not add the system extension directory to an incompatible Python's path.

On Ubuntu, install the distribution's matching packages if absent:

```bash
sudo apt install uhd-host python3-uhd python3-venv
/usr/bin/uhd_config_info --version
/usr/bin/python3 -c 'import uhd; print(uhd.__file__)'
/usr/bin/python3 -m venv --system-site-packages .venv-uhd
source .venv-uhd/bin/activate
python -m pip install /path/to/packet_radio-1.0.0-py3-none-any.whl
packet-radio-conformance
mkdir -p ~/packet-radio-work
cp -r "$VIRTUAL_ENV/share/packet-radio/configs" ~/packet-radio-work/
cd ~/packet-radio-work
packet-radio-validate configs/examples/peer-a-bpsk.yaml configs/examples/peer-b-bpsk.yaml
```

Install matching FPGA/firmware images with the UHD image downloader if the driver
reports missing images. Ensure your user has USB access through the distribution's
UHD udev rules. Avoid running the application as root. Start the application with this environment's
Python/entry point, not an unrelated Conda installation.

See the [Ettus Python API guide](https://files.ettus.com/manual/page_python.html)
and [B2x0 hardware manual](https://files.ettus.com/manual/page_usrp_b200.html).

## Select the device and antenna ports

```bash
uhd_find_devices --args type=b200
python -m packet_radio.cli.validate configs/examples/peer-a-bpsk.yaml --hardware
```

`--hardware` opens the device, validates supported ranges, applies the profile,
reads actual settings, briefly starts RX, then releases the streamers/device.
It **does not send samples**. It changes radio settings, so stop other radio
processes first. The report includes device identity, RX/TX sample rate, bandwidth,
gain, antenna, and frequency. Rates differing by more than 1 ppm or frequency
readback errors over 1 Hz are rejected. Gain/bandwidth adjustments are logged and
reported. These tolerances compare digital settings, not oscillator accuracy.

For unambiguous selection, add the discovered serial to the local profile:

```yaml
radio:
  device: {type: b200, serial: "3457464"}
  channel: 0
  rx_antenna: RX2
  tx_antenna: TX/RX
```

Use the other SDR's serial on its host. The example profiles leave serial selection
to the user and explicitly select `RX2` and `TX/RX`. The old `driver` selector is
rejected. Supply UHD device arguments as string values.

The confirmed conducted wiring is peer A `TX/RX` → 30 dB attenuator → peer B `RX2`,
and peer B `TX/RX` → 30 dB attenuator → peer A `RX2`. Keep initial TX gain at 0 dB.
Ettus specifies at least 30 dB attenuation for loopback and a maximum RF input of
−15 dBm; attenuation alone does not justify increasing TX power arbitrarily.

## Streaming and diagnostics

Host samples are owned NumPy `complex64` arrays (`fc32`); USB samples use `sc16`.
The NumPy bridge is initialized before background streaming starts to avoid a
first-use import race seen with the installed bindings. Each direction has one
streamer owner. RX continues while TX completes; application
packet scheduling remains half duplex. The bounded RX queue owns copied blocks,
and overflow drops its oldest block with a counter. Tuning/clock resets stop and
drain old RX data before restarting. Clean shutdown stops the RX worker before
releasing UHD references. UHD has no required `closeStream` compatibility layer.

TX handles partial transfers, uses one start-of-burst marker, sends a separate
zero-length end-of-burst marker, and waits for UHD's asynchronous burst completion.
A device burst acknowledgement is distinct from the remote protocol ACK. Send
and completion waits have bounded deadlines. No fixed post-transmit sleep is used.

`rx_overflows`, `rx_timeouts`, `rx_stream_errors`, `rx_buffer_drops`,
`tx_underflows`, `tx_stream_errors`, `tx_timeouts`, and `timed_transmit_late` are
available through existing status/metrics output. RX timeout/overflow recover on
subsequent reads; malformed/late/broken RX streams surface an error. TX failures
surface to the application's existing retry logic. Device disconnects require
process restart after reconnecting; automatic hotplug recovery is not implemented.

Timestamp conversion preserves integer nanoseconds without converting an entire
Unix epoch to floating point. Timed TX and RX use UHD metadata/stream commands.
The local hardware test recorded a roughly 2.1 µs timed RX offset; do not assume
nanosecond-accurate RF timing from the timestamp representation.

Timed RF tuning is not implemented on this backend. Timed FPGA sampling support
is not a qualification of LO hop timing. Schema 1 profiles expose only a fixed
center frequency. Ordinary frequency changes work, but measured host call duration
is not RF settling time.

## Local bench profiles and tools

Tool commands below assume a source checkout. With a wheel installation, replace
`tools/<script>.py` with `"$VIRTUAL_ENV/share/packet-radio/tools/<script>.py"`;
`PYTHONPATH=.` is unnecessary. Run from the directory containing your copied configs.

The `configs/bench/peer-*-bpsk.yaml`, `configs/bench/peer-*-2fsk.yaml`,
`configs/bench/peer-*-msk.yaml`, `configs/bench/peer-*-gmsk.yaml` and
`configs/bench/peer-*-qpsk.yaml` pairs select
serials `3457464`/`3457480`, TX 20 dB/RX 40 dB gains, background decoders, timed
TX, hardware clock synchronization and matching transmit windows. These gains
are specific to the confirmed attenuated setup. Generic profiles retain lower
startup gains. QPSK uses the documented 20 ms DATA/30 ms SYNC lead, 600 ms ACK
timeout and 16-byte Reed–Solomon setting; see `CONFIGURATION.md`. Provision keys using `docs/PROGRAM_GUIDE.md` for normal application use.

| Peer | UDP input | UDP delivery | HTTP status |
|---|---:|---:|---:|
| A / 3457464 | 50100 | 50101 | 8765 |
| B / 3457480 | 50200 | 50201 | 8766 |

With no other radio processes running, the local workload runner creates temporary
signed identities and uses real UDP endpoints. It releases devices, sockets and
keys on completion. Select `bpsk`, `2fsk`, `msk`, `gmsk` or `qpsk`:

```bash
PYTHONPATH=. python tools/uhd_link_check.py --modulation 2fsk \
  --duration 60 --restart-follower --output /tmp/udp-check.json
PYTHONPATH=. python tools/uhd_link_check.py --modulation 2fsk \
  --echo --duration 60 --rate 1 --output /tmp/echo.json
```

Explicit profiles replace fixed bench filenames without editing the runner:

```bash
PYTHONPATH=. python tools/uhd_link_check.py \
  --profiles configs/bench/peer-a-qpsk.yaml configs/bench/peer-b-qpsk.yaml \
  --duration 30 --rate 5 --interrupt-seconds 17 --restart-follower \
  --output /tmp/radio-check.json
```

Pass the leader first and follower second. The runner preserves their node IDs and
configuration overrides, but uses fresh temporary signing identities and disables
HTTP/log status for the run. Profiles must use distinct local UDP delivery ports.
`--profiles` and `--modulation` are mutually exclusive. `--output` is optional;
when requested, its temporary JSON contains report version 1, profile paths/hashes,
effective settings, hardware readbacks and UHD version, before/after counters,
workload conditions and outcomes. Temporary key paths are redacted. Successful
shutdown is acknowledged only after resources close and processes exit; cleanup
errors fail the run. Retain final summaries in `MODULATION_RESULTS.md`, then discard
the temporary report. No results directory is required.

A failed delivery, stream, latency or recovery check returns a nonzero exit code.
Echo and restart checks must run separately. `--interrupt-seconds 17` in a non-echo
run temporarily sends zero-valued IQ on both paths, leaving RX/USB active, then
checks recovery within five seconds. This is distinct from physical disconnection.

The runner preserves stored timing settings. Test-only `--ack-timeout`,
`--tdd-period`, `--tdd-guard` and `--tx-gain` overrides do not modify profiles.
`--scheduled-tdd` is an experimental fixed hardware-slot scheduler that has not
passed qualification; it is disabled in all supplied bench profiles.

For isolated hardware diagnosis, the direct probe actively sends a 0.3-second tone
per direction at amplitude 0.25 and 1 Msps, then releases both devices:

```bash
PYTHONPATH=. python tools/uhd_direct_probe.py --serials 3457464 3457480 \
  --tx-gain 20 --rx-gain 40 --output direct-probe.json
```

It bypasses application framing and buffering. Its FFT peak-to-median-bin ratio is
a diagnostic spectral measurement, not calibrated SNR or power in dBm.

All final measured outcomes, qualification conditions and full reproduction
commands are maintained in [MODULATION_RESULTS.md](../MODULATION_RESULTS.md).
The local runner uses two processes on one computer; independent-host acceptance
remains deferred. Do not infer performance at untested rates, gains or frequencies.

## Coordinated two-computer UDP checks

This workflow is software-tested with real loopback UDP. Physical two-host
qualification remains deferred; it is not implied by local two-SDR results.

Run `python -m packet_radio --config <local-profile>` on each computer with its
own SDR and provisioned, mutually trusted keys. Use the same RF/modem/protection
settings on both radios. Keep local HTTP status enabled. Start each probe from the
same working directory as its radio, using the exact same profile. The probe
checks the running radio's effective configuration before opening UDP.

Choose a fresh shared run ID and a future Unix UTC start time. For example, obtain
one time value with `python -c 'import time; print(int(time.time()) + 120)'`, then
set `radio_test_start` to that **same printed value on both hosts**. Both host
clocks must be close enough for their test intervals to overlap; startup more
than one second late is rejected. The probes never derive one-way latency by
subtracting timestamps from different computers.

Host A, in another terminal alongside its radio:

```bash
PYTHONPATH=. python tools/udp_peer_check.py \
  --profile configs/bench/peer-a-qpsk.yaml --run-id radio-check-01 \
  --start-at "$radio_test_start" --duration 300 --rate 5 --payload 256 \
  --output /tmp/peer-a-check.json
```

Host B, alongside its radio:

```bash
PYTHONPATH=. python tools/udp_peer_check.py \
  --profile configs/bench/peer-b-qpsk.yaml --run-id radio-check-01 \
  --start-at "$radio_test_start" --duration 300 --rate 5 --payload 256 \
  --output /tmp/peer-b-check.json
```

Use matching run ID, time, duration, rate and payload size. Both reports must pass;
a single successful report cannot qualify both directions. For RTT, choose a new
start/run ID and use `--mode echo` on A and `--mode reflector` on B, with
`--duration 60 --rate 1`. RTT is measured only on A's monotonic clock. Defaults
provide five seconds of delivery grace. Optional `--status-url` overrides the
local HTTP base URL; it does not provide remote process control.

Each datagram contains a 24-byte run/identity/sequence header plus deterministic
payload bytes. This detects duplicates, corruption and mismatched runs. Supported
payload sizes are 24 through the profile's limit (up to 1024 bytes), so 64/256/1024
boundary tests work here. Tiny-payload identity audits, automated remote restart
and coordinated signal muting remain local-runner capabilities; the two-host
probe does not claim those checks. It leaves the radio processes running and
releases only its own UDP sockets. Stream counter deltas span the initial status
snapshot through the end of delivery grace, including the wait for scheduled start.

Both modes require at least 99% delivery, no unexpected/duplicate payloads, no
stream or endpoint errors and no retry exhaustion; echo additionally requires
p95 RTT at most one second. The reports include missing sequences, profile hashes,
configuration/hardware snapshots and stream deltas. Summarize completed hardware
results in `MODULATION_RESULTS.md` and discard temporary JSON afterward.
