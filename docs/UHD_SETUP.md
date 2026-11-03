# Direct UHD setup and bench checks

The hardware backend uses Ettus `uhd.usrp.MultiUSRP` directly. The current conducted
reference is one X310 with two shared UBX-160 channels on one workstation. B205mini
endpoints remain supported but need qualification for their specific workload;
B205 uses `type=b200`, and X310 uses `type=x300`. The verified host is Ubuntu 26.04.1,
UHD 4.9.0.1-1ubuntu1 and system Python 3.14.4.
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
python -m pip install /path/to/packet_radio-1.1.0-py3-none-any.whl
packet-radio-conformance
mkdir -p ~/packet-radio-work
cp -r "$VIRTUAL_ENV/share/packet-radio/configs" ~/packet-radio-work/
cd ~/packet-radio-work
packet-radio-validate configs/examples/peer-a-bpsk.yaml configs/examples/peer-b-bpsk.yaml
```

For the terminal editor, install the same wheel with its optional UI extra in
this interpreter-matched environment:

```bash
python -m pip install "/path/to/packet_radio-1.1.0-py3-none-any.whl[ui]"
packet-radio-ui
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

TX handles partial transfers, uses one start-of-burst marker, carries the
end-of-burst flag on the final IQ submission, and waits for UHD's asynchronous
burst completion. An empty end marker is reserved for error cleanup.
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
errors fail the run. Reports are temporary diagnostics; no results directory is required.

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

The [payload tables](../MODULATION_RESULTS.md) contain calculated hopping capacity,
not hardware qualification records.
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
configuration/hardware snapshots and stream deltas. These are diagnostic outputs;
`MODULATION_RESULTS.md` is limited to the hop/payload/packet-size tables.

## Two computers with one B205 each

Install the same wheel (including the UI extra when using the editor) and the
Python-matched UHD bindings on both computers. Connect one B205 to each host.
Use a new GUI session on each:

```bash
# Computer A
packet-radio-ui --node-role a
# Computer B
packet-radio-ui --node-role b
```

Discovery binds the local B205 serial and channel 0. Save a local node on each
computer, check its hardware, then Start on both. A missing or unsupported SDR
leaves the editor usable; Ctrl+D retries detection. If several radios are found,
select the intended device. Opening an existing node preserves its saved selector.

The shipped headless examples use bidirectional simple frames at fixed 915 MHz,
250 kbit/s, 2 Msps, 1 MHz bandwidth, conservative 0 dB gains, TX/RX transmit and
RX2 receive antennas. Adjust gains for the actual link and keep waveform/frame
settings identical at both ends. From a checkout or a copied installed `configs/`
directory:

```bash
# Computer A
packet-radio --config configs/v1_1/peer-a-b205-two-host.yaml --auto-device
# Computer B
packet-radio --config configs/v1_1/peer-b-b205-two-host.yaml --auto-device
```

Add `--sdr-serial SERIAL` if several supported devices are visible. Automatic
selection checks the discovered model, not just UHD's broad `b200`/`x300` family.
It does not rewrite the saved file. No remote IP address is required: both nodes
use local UDP input 50000, local UDP delivery 50001, and local HTTP status 8765.
Identical port numbers on different computers do not conflict. GUI-generated
projects likewise keep application UDP on 127.0.0.1.

To check the first packet, start a local receiver on computer B **before** sending
from A. Close any other application or Packet Builder listener on that same
UDP delivery port:

```bash
# Computer B, separate terminal while its radio runs
packet-radio-payload receive --count 1 --timeout 30
# Computer A, separate terminal while its radio runs
packet-radio-payload send --text 'hello from A'
```

Repeat with A receiving and B sending. The receiver prints the exact payload as
hexadecimal. Submission to UDP does not prove RF reception. Use the local GUI
status or `packet-radio-status` to inspect packet/stream counters. The data path
is local application → local UDP → local B205 → RF → remote B205 → remote UDP →
remote application. No LAN traffic carries the radio payload between computers.

This initial automatic setup is fixed-frequency and ACK-free; it provides no
remote negotiation or automatic retransmission. Separate computer clocks do not
need alignment for this mode. Automatic B205 binding rejects hopping profiles;
independent-host hopping needs explicit synchronization and qualification. This
is software-validated setup support, not a completed two-computer RF qualification.

For the X310, `packet-radio-ui` instead detects the shared board. With simple
frames, Start generates a complementary channel with separate local ports.
The headless `--auto-device` path does the same for a simple-frame source node.
Generic-v5 needs an explicit peer profile for its identities/keys. Existing
explicit X310 commands below remain supported, including hopping.

## Two endpoints on one X310

The X310/UBX shared-channel setup is the reference configuration. Hardware
acceptance applies to the exact waveform, traffic and timing settings tested;
B205 performance and independent-host operation need their own checks.

Use a single process to own the board, with channel 0 for daughterboard A and
channel 1 for B. Do not launch two independent processes against the same X310.
The paired launcher opens one `MultiUSRP`, initializes its UTC clock once before
streaming, and gives each endpoint its own RX/TX streamers. Channel controls are
serialized; closing one endpoint does not release the other's stream. A runtime
failure stops both endpoints.

For the conducted bench, cross A TX/RX through 30 dB attenuation to B RX2 and B
TX/RX through 30 dB attenuation to A RX2. The supplied node examples select
192.168.10.2 and use distinct UDP and status ports:

```bash
packet-radio --config configs/v1_1/peer-a-x310-simple.yaml \
  --peer-config configs/v1_1/peer-b-x310-simple.yaml
```

Both profiles must select the same UHD `type: x300` device and different channel
numbers, with compatible waveform/link settings. Generic packet formats still
require authentication keys or the explicit isolated-bench unauthenticated flag.
The paired launcher is currently limited to the X300 device family.

UBX-160 boards have a fixed analog filter. The requested profile bandwidth remains
the waveform's configured bandwidth; UHD's reported actual analog bandwidth is
160 MHz at 915 MHz. The adapter accepts a narrower request only when the hardware
reports a fixed filter, records both requested and actual values, and still
requires the exact configured digital sample rate. This does not provide a
narrow analog filter or establish spectral-mask compliance.

`tools/uhd_link_check.py` and `tools/simple_link_check.py` accept
`--shared-device --profiles NODE_A NODE_B` for the same paired setup.
`tools/hopping_check.py --shared-address 192.168.10.2 --seconds 60` runs the short
conducted 1 hop/s workload using channels 0 and 1. Add `--hop-rate 2` to select the
separate 2 hop/s X310 profile. Each 60-second run adds six seconds of 1/64/1024-byte
payload boundaries and deliberately rejected late packets on both channels, then
six dwells for off-channel interruption and recovery. It checks exact ordered
payloads, zero stream errors, zero missed steady hops/deadlines, TX target/sample
bounds against both guards and clean shutdown. `--first-phase` changes channel 0's
injection phase in seconds; the default is 0.2. These commands transmit RF.

The hopping tool separately reports host stop/drain duration, RX/TX tuning and
readback call duration, restart-command duration, first buffered RX arrival,
first samples passed to the decoder after settling, and successful packet delivery.
Phase values are relative to the dwell boundary, not packet latency. The RF-control
call duration is not a PLL lock measurement. TX target checks use a device-time read
bracketed by host timestamps and include the bracket uncertainty at the trailing
guard. This is software/timestamp evidence, not an independent RF edge measurement.
Instrumentation is local to this benchmark and does not add clocks or wrappers to
normal application operation.
Generic shared-device CPU results describe the whole process, including both
endpoints and the UDP driver; do not add them as independent per-node values.
The old B205 baseline used two processes and independent device clocks, so the
comparison includes those architectural differences as well as the RF hardware.

### X310 host transport assessment

For the two-channel 2 Msps reference workload, retain the existing 1 Gb Ethernet
transport unless measurements show a constraint. Compare application latency,
CPU and stream errors before changing socket or UHD buffers; removing a startup
warning alone does not establish a performance improvement. A smaller transport
buffer was not adopted. Independent-host interoperability remains unqualified.

The measured host uses `enp132s0`, MTU 1500 and a 1000 Mb/s full-duplex link.
`net.core.rmem_max` is 50,000,000 bytes and `net.core.wmem_max` is 1,048,576 bytes;
both default socket sizes are 212,992 bytes. UHD requests 2,453,333 bytes for its
stream transport buffers. RX receives that allocation; TX is capped at 1,048,576
bytes and UHD emits its startup warning. These observations describe this host,
not required installation-wide defaults.

Inspect the path and counters before and after a workload:

```bash
ip route get 192.168.10.2
ip -s link show dev enp132s0
ethtool enp132s0
ethtool -S enp132s0
sysctl net.core.rmem_max net.core.wmem_max net.core.rmem_default net.core.wmem_default
ss -unmp dst 192.168.10.2
```

While UHD is open, `ss` shows connected stream sockets on remote UDP port 49153.
Its `skmem` `rb`/`tb` fields use Linux's doubled socket-buffer accounting: the
observed default stream values are `rb4906666,tb2097152`. A 524,288-byte UHD TX
request produces `tb1048576`. These are capacity limits, not queue occupancy or
latency; inspect the queue and error counters as well. Control sockets can have
different sizes. Linux documents this accounting in [socket(7)](https://man7.org/linux/man-pages/man7/socket.7.html).

For a reversible, per-process comparison, use **test copies** of both paired node
files and add a string-valued transport argument under each `hardware.device`:

```yaml
hardware:
  device:
    type: x300
    addr: 192.168.10.2
    send_buff_size: '524288'
```

Keep every other field unchanged, including TX batch size. The current generic
X310 QPSK examples use 256 transport packets per call. If test copies are outside
the configuration directory, resolve their `profile` path to an absolute path. Both nodes must have identical device arguments. Close the
previous process before reopening with the new setting; delete the test override
to restore the original behavior. `recv_buff_size` is also a UHD device argument,
but RX buffer sizing was not changed in this assessment. UHD's
[transport notes](https://files.ettus.com/manual/page_transport.html) describe these
parameters and their kernel caps.

Use the existing workload for each pair of test nodes:

```bash
PYTHONPATH=. python tools/uhd_link_check.py --shared-device \
  --profiles NODE_A NODE_B --duration 30 --rate 5 --payload 256 \
  --timing-diagnostics
```

Require exact delivery, no stream/timing errors, no expired or queued payloads,
and clean shutdown. Compare packet p95 and whole shared-process CPU over the
same workload and drain period. Repeat an apparent improvement with alternating
settings before adopting it. Do not run software tests or other RF benchmarks
concurrently. The 256 KiB pilot is below UHD's reported 307,200-byte minimum
recommendation and is not an adopted setting.

A larger-than-cap test requires administrator authentication, which was unavailable
in this session. It was not necessary to change the cap to assess the current
workload. If a later measured bottleneck warrants that test, save the original
`net.core.wmem_max`, temporarily set it to the intended request with
`sudo sysctl -w net.core.wmem_max=2453333`, reopen UHD and repeat the same workload,
then restore the saved value with `sudo sysctl -w net.core.wmem_max=OLD_VALUE`.
Verify the restored value and close/reopen UHD again. Do not write a persistent
sysctl configuration until a reproducible improvement has been established.
No receive-limit, NIC offload/coalescing, scheduling-priority or CPU-affinity
change is prescribed by these measurements.

The NIC's cumulative error count needs startup context. In this assessment,
`rx_length_errors` and `rx_errors` each increased by six during UHD initialization
and remained stable during application traffic. UHD 4.9's
[X300 Ethernet source](https://github.com/EttusResearch/uhd/blob/v4.9.0.1/host/lib/usrp/x300/x300_eth_mgr.cpp)
probes frame sizes up to its discovery ceiling. A separate transport check with
`recv_frame_size=1472,send_frame_size=1472` constrained that ceiling and eliminated
the startup error-counter increment, while the default and capped runs both
streamed cleanly at the same actual 1472-byte frame size. This supports the
probe explanation, not a claim that every NIC error is harmless. No frame-size
override was adopted; compare deltas during the workload before changing MTU.

At sc16, two 2 Msps channels carry 128 Mbit/s of sample payload per direction,
before transport overhead. This arithmetic is not a maximum-capacity guarantee;
the simultaneous RX/TX transport test supplies the workload evidence. Consider
10 Gb Ethernet only for a separately specified higher-rate/channel workload that
exceeds the measured path, then qualify its NIC, FPGA image, link and host settings.

### Optional packet timing diagnostics

Add `--timing-diagnostics` to `python -m packet_radio` (including paired
`--peer-config` launches), `tools/uhd_link_check.py`, or
`tools/simple_link_check.py`. This is a runtime
diagnostic option, disabled by default; it does not change the profile schema.
Use JSON status or the local status HTTP endpoint to inspect the
`timing_diagnostics` object. The benchmark includes it in peer snapshots.
No capture or output file is required.

Each metric reports lifetime count, mean, minimum and maximum, plus
`recent_count` and `recent_p95` over at most the last 256 observations.
Snapshots include startup activity; compare before/after counts and weighted
means for workload-only analysis. Rolling p95 values cannot be subtracted.
Observations are aggregated separately for each radio application.

- Host monotonic durations have an `_ms` suffix: payload enqueue, packet
  selection, fresh encoding/modulation, radio transmit, RX decode, frame
  handling through successful endpoint delivery, and ACK handling.
- `selected_packet_age_ms` includes queue residence and repeated selections;
  `enqueue_to_ack_ms` includes retries. `tx_completion_to_ack_ms` starts at
  the latest completed transmission, not its first RF sample. Queue depth
  remains available; `oldest_pending_packet_age_ms` is refreshed on link
  updates and is available even when timing instrumentation is disabled.
- `recognized_batch_queue_ms` measures decoder-output residence before the
  main loop handles a batch. It is a host duration, not RF propagation time.
- `rx_buffer_queue_ms` measures host residence from enqueue in `BufferedRadio`
  to dequeue by the receiver. Unknown enqueue times are omitted. It excludes
  device/NIC buffering and the UHD read itself; it never subtracts an RF
  timestamp from a host timestamp. `rx_buffer_queue_blocks` samples the number
  of blocks remaining at dequeue, not a continuous high-water mark.
- `rx_frame_buffer_tail_ms` converts samples already buffered beyond a decoded
  frame's consumed end to milliseconds at the sample rate. It helps identify
  burst-end waiting, but is not a host duration: it can include shaping tails,
  read-block rounding, or following packets already in the same buffer.
- `app_step`, `decoder_feed`, `decoder_status` and `rx_worker` each expose
  `_wall_ms` and `_cpu_ms` summaries. CPU uses the current thread's CPU clock;
  wall time includes blocking and scheduling. Their difference is **not a
  measurement of GIL contention**. Application steps include receive/TX waits;
  decoder feed covers the modem call; decoder status covers the application
  reading worker diagnostics; RX worker covers read/copy/enqueue after acquiring
  its I/O lock. These are scoped costs, not complete lifetime thread utilization.
  Application-step time includes decoder-status time; do not sum nested scopes.
- `uhd_control`, `decoder_state` and `rx_io` expose `_wait_ms` and `_hold_ms`.
  They measure explicit lock acquisition and ownership separately, then record
  observations after release. Acquisition duration can include host scheduling
  delay; it is not proof of mutex blocking alone. UHD control includes device-time reads and tuning,
  not independent stream sends/receives. RX I/O measurements cover the drain
  loop; decoder state measurements cover its statistics readers/writers.
  Disabled instrumentation reads neither elapsed nor thread-CPU clocks for
  these scopes. Rolling p95 and lifetime extrema have the definitions above;
  they are not hard scheduling bounds.
- UHD metrics include requested/accepted samples and data send calls per
  attempt, individual send duration, submission duration (including EOB),
  completion wait, and total burst duration. Sample counts exclude
  empty error-cleanup calls and include configured transport idle. Successful
  submissions carry EOB on the final data call. Completed send calls are
  counted; a call that throws before returning is not counted. Submission
  duration requires successful EOB; completion and total burst duration require
  a burst ACK. Error counters must therefore be reviewed alongside timings.
- `first_send_hardware_lead_ms` subtracts current **device time** from the
  intended target immediately before first submission. Negative values mean
  the target has already passed. `scheduled_tx_target_ns` is a hardware
  timestamp, never a host monotonic timestamp; for ordinary timed data it is
  updated after successful transmission.
- `host_tx_late_rejections` counts already-expired targets rejected by the UHD
  adapter before submission. `uhd_tx_late_events` counts asynchronous UHD
  time-error events. Their sum retains the existing `timed_transmit_late`
  meaning. Scheduling deferrals remain `tdd_opportunities_missed`.

Instrumentation performs additional clock reads and bounded locked updates.
Compare identical short workloads with diagnostics off and on before using
these measurements to justify optimizations. Temporary benchmark reports are disposable.

### Shared-channel execution and decoder statistics

The paired launcher retains one device owner and independent application, RX
and decoder workers. Decoder processing p95/max remain exact statistics of the
last 2,048 observations. The worker maintains a bounded sorted view alongside
the arrival-order window, removing one expired value and inserting the new
value under its existing state lock. Status reads no longer sort that history.
This preserves duplicate values, rolling-window eviction and current counters;
there is no cached/stale status interval or additional process/IPC path.

### Receive search scheduling

All five receivers can search a live burst before its energy returns
to idle. A plausible preamble/length schedules the next search when enough
samples for that candidate, including the known receive shaping delay, exist.
Payload validation is unchanged; a length field alone never delivers a packet.
Without a usable length, live searches are spaced by at least 4 ms of new IQ
or one larger read block, except that idle/full-buffer checks take priority.
Timestamp discontinuities and reset clear pending search hints. The maximum
buffer bound and search-before-trim protection for long tails remain in place.

For BPSK, the required samples include the receive RRC filter tail when shaping
is enabled, plus two symbols of lookahead during live reception. This lets
neighboring timing candidates finish before an early alignment is declared
corrupt. A continuous RX stream supplies that lookahead after the burst.
Both PSK receivers consider distinct frames in arrival order and rank alignment
quality within each frame, so a stronger later DATA frame cannot discard an
earlier decodable ACK. QPSK retains its existing live-burst search scheduling.
These receive-only changes do not alter transmitted samples, framing, profile identity or TDD
policy. The 4,096-sample RX blocks and 256-block queue are unchanged: measured
host queue residence did not justify a buffering-size change. Extra correlation
work is a tradeoff, so assess end-to-end delivery latency and stream errors,
not decoder-call duration alone.

### UHD transmit batching

The default remains one transport packet per Python send call. Set
`runtime.hardware_tx_batch_packets` in schema 2 (or
`link.hardware_tx_batch_packets` in schema 1) to select bounded submissions at
application startup; integer values 1..256 are accepted. The shared-device
benchmark's `--tx-batch-packets` flag overrides this same host setting on both
nodes. `actual_settings.tx_batch_packets` and `tx_batch_samples` report the
configured count and sample limit. On the tested 1 Gb X310, 256 packets is
93,184 samples instead of 364. UHD fragments each submission;
the maximum transport packet size is not a maximum host-buffer size. This follows
the installed UHD 4.9 header and the
[UHD TX streamer contract](https://files.ettus.com/manual_archive/v4.9.0.0/html/classuhd_1_1tx__streamer.html).

Partial acceptance advances by the returned sample count. The adapter keeps its
owned IQ buffer alive, applies the timestamp/start flag only to the first accepted
transfer, and attaches EOB to the final submission of data and existing idle
samples. UHD applies EOB only to the final transport fragment; a partial return
leaves that fragment unsent, so the remaining samples retain EOB on retry.
This removes the Python scheduling gap between the last IQ and a separate end
marker. Empty EOB is used only to clean up an interrupted/failed submission.
Each batch rechecks the burst deadline. Because UHD's send timeout is per
transport packet, the remaining timeout budget is divided by the number of
fragments in that batch, capped at the existing 100 ms per-packet allowance.
This limits blocking waits; it is not a hard real-time guarantee against host
scheduling delays. Asynchronous completion and stream errors are still checked.

Batching changes transport submission granularity, not waveform samples, bit
rate, packet framing, RF gain or scheduled target time. It reduces Python call
overhead; it does not eliminate time spent waiting for a timed burst or an ACK.
The setting belongs to the host, not the portable emulated-radio definition;
changing it does not change the profile identity. The supplied
`configs/v1_1/peer-{a,b}-x310-qpsk.yaml` pair selects 256 packets with the existing
unslotted QPSK profile. This cap fits the qualified ACK (7,936 samples), SYNC
(8,448) and signed 1,024-byte DATA (72,960) in one Python-to-UHD call,
avoiding Python scheduling gaps between their transport fragments. UHD still
fragments internally. Other node examples and the B205 default stay at one.
Use this pair for the conducted X310 QPSK setup; selecting QPSK in an older node
file alone does not enable batching. This is a startup setting, not a live
stream reconfiguration API. Keep it matched when comparing runs; the conducted
QPSK setup does not qualify every device, rate or profile for batching.

```sh
python tools/uhd_link_check.py --shared-device \
  --profiles configs/v1_1/peer-a-x310-qpsk.yaml configs/v1_1/peer-b-x310-qpsk.yaml \
  --duration 30 --timing-diagnostics
```

The paired application launcher accepts the same files and requires the usual
authentication keys. The benchmark supplies temporary keys itself.

### Timed-transmission preparation and recovery

Relative targets are selected after the adapter copies/validates IQ and drains
old asynchronous events. A single serialized device-time read then selects or
checks the target. Explicit targets, including timestamps carried in sync
packets, are preserved; an expired target is rejected before any sample is sent.

The optional `link.hardware_min_tx_lead_seconds` also rejects a still-future
target with insufficient remaining lead. The application requeues this unsent
packet without counting a transmission failure or dropping simple-radio data.
`tx_lead_rejections` counts adapter margin rejections, and `tx_lead_deferrals`
counts application deferrals, including preflight checks. Actual expired-target
rejections retain the existing host-late counter. A zero minimum preserves prior
configuration behavior.

Stale asynchronous reports are drained together, with a 256-event safety bound,
before one aggregate failure is raised. Each UHD event is still counted. A
fragmented failed burst therefore does not require one new application attempt
per queued error. This does not identify every event as a separate failed burst.

When diagnostics are enabled, `uhd_late_attempt_ms`,
`uhd_underflow_attempt_ms` and `uhd_error_attempt_ms` summarize attempts that
observe those failures; the corresponding `*_attempt_first_send_lead_ms`
summarizes their first-submission lead. Delayed device reports can refer to an
earlier physical transmission, so these metrics identify the observing attempt,
not guaranteed one-to-one RF causality.

The paired X310 BPSK diagnostic profiles use 10 ms data lead, 20 ms sync lead and
2 ms minimum remaining lead. Run the default submission size with:

```sh
python tools/uhd_link_check.py --shared-device \
  --profiles configs/bench/peer-a-x310-bpsk.yaml configs/bench/peer-b-x310-bpsk.yaml \
  --duration 15 --timing-diagnostics
```

Add `--tx-batch-packets 16` only to repeat the historical batching comparison;
the current qualified QPSK node examples select 256.
Use the Python environment with the installed UHD bindings. These commands
transmit RF and assume the documented crossed 30 dB attenuated bench connections.
The profiles preserve the legacy reliable-link schedule, which still backlogs
under the normal BPSK workload.

### Unslotted reliable X310 pair

`configs/v1_1/peer-a-x310-generic.yaml` and
`configs/v1_1/peer-b-x310-generic.yaml` use explicit unslotted generic-v5 profiles.
They keep authentication, replay/duplicate protection, one in-flight data packet
per peer and bounded control traffic. They allow return ACKs as soon as decoding
and channel checks permit them, without waiting for an alternating TDD window.
Queue size remains 256; pending data expires after five seconds in these examples.

This is a different scheduling policy, **not a compatible replacement for a
half-duplex or TDD radio being emulated**. Measurements are limited to the crossed,
attenuated TX/RX-to-other-RX2 setup, where both channels can transmit and receive.
It does not establish single-antenna or over-the-air self-interference performance.

With the X310 connected as documented, a short matched BPSK workload is:

```sh
python tools/uhd_link_check.py --shared-device \
  --profiles configs/v1_1/peer-a-x310-generic.yaml configs/v1_1/peer-b-x310-generic.yaml \
  --duration 30 --timing-diagnostics
```

The paired application launcher also accepts these profiles. It requires the
configured authentication keys or the existing explicit isolated-bench opt-in;
the benchmark generates temporary keys itself. The runtime submission default
remains one transport packet per send. The portable profiles retain their
`unqualified` labels: conducted acceptance applies to the exact X310 host
configuration and workload. It does not qualify those profiles on every device or
establish target-radio interoperability. Complete delivery alone does not clear
an observed UHD underflow.
