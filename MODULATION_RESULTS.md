# Modulation results

This is the single maintained results record. Update the relevant modulation here
when qualification changes; do not create per-step status or completion documents.
Only final summaries are retained in the repository. Implementation details belong in
`docs/PROTOCOL.md` and `docs/CONFIGURATION.md`. Planned work belongs in the
[version 1.1 roadmap](docs/RADIO_V1_1_ROADMAP.md).

## Qualification summary

BPSK/2FSK measured 2026-09-28; MSK/GMSK/QPSK measured 2026-09-29. Two-computer testing is deferred at the user's request.

| Modulation | Status | Sustained delivery, each direction | Echo p95 | Signal recovery |
|---|---|---|---|---|
| BPSK | Locally qualified | 9,000/9,000 over 30 minutes | 0.288 s | 1.105 s |
| 2FSK | Locally qualified | 9,000/9,000 over 30 minutes | 0.279 s | 2.159 s |
| MSK | Locally qualified | 9,000/9,000 over 30 minutes | 0.295 s | 1.188 s |
| GMSK | Locally short-run validated | 1,500/1,500 over **5 minutes**; 30 minutes not run | 0.280 s | 1.188 s |
| QPSK | Locally short-run validated | 1,500/1,500 over **5 minutes**; 30 minutes not run | 0.314 s | 1.112 s |

These qualify the specified conducted profile, not over-air range, all configurable
rates/deviations, or independent-host operation. QPSK remains supported but is not
a release-focus modulation.

## Shared hardware and measurement method

- Two B205mini devices, serials `3457464` and `3457480`, in separate processes on
  one workstation. Each TX/RX connects to the other device's RX2 through a
  user-confirmed 30 dB attenuator.
- Python 3.14.4, UHD 4.9.0.1, NumPy 2.3.5; reedsolo in the hardware environment.
- 915 MHz center, 2 Msps, 1 MHz bandwidth, 250 kbit/s nominal channel rate,
  TX gain 20 dB, RX gain 40 dB, background decoding. QPSK uses 500 kbit/s
  at the same sample rate and bandwidth.
- Alternating 180 ms TDD period, 2 ms guards and inter-burst spacing, eight bursts
  maximum per window, 250 ms ACK timeout, timed DATA lead 10 ms and hardware clock
  synchronization. QPSK uses a 600 ms ACK timeout, 20 ms DATA lead and
  30 ms SYNC lead. Experimental fixed hardware-slot scheduling is disabled.
- Authenticated protocol-v5 packets with Hamming/interleaving. 2FSK uses continuous
  phase, rectangular frequency pulses and ±125 kHz deviation (250 kHz tone spacing).
  MSK uses the same pulse shape with derived ±62.5 kHz deviation (h = 0.5).
  QPSK uses rectangular symbols and 16 outer Reed–Solomon parity bytes per block.
- Sustained workload: 256-byte packets, five packets/second/direction for 30 minutes.
  Boundary workloads: five minutes each at one packet/second/direction. GMSK/QPSK use
  the user-requested five-minute sustained/one-minute boundary scope below.
- Initial acquisition is measured after both devices report ready. Measurement
  follows warmup; five seconds of final delivery grace are allowed. Goodput divides
  delivered payload bits by injection duration, including packets arriving in grace.
- Echo uses a separate 60-probe run and one monotonic clock, including the remote
  UDP echo service. Tiny payloads use an out-of-band protocol identity audit to
  distinguish repeated byte values without enlarging application packets.
- Signal interruption sends zero-valued TX IQ on both paths for 17 seconds while
  RX/USB remain active. The follower entered recovery before restoration. This
  tests loss of decodable signals, not cable/USB disconnection. Restart is separate.

The maintained `configs/bench/peer-*-bpsk.yaml`, `configs/bench/peer-*-2fsk.yaml`,
`configs/bench/peer-*-msk.yaml`, `configs/bench/peer-*-gmsk.yaml` and
`configs/bench/peer-*-qpsk.yaml` profiles match the recorded settings. Generic profiles retain conservative gains.
All final workloads had zero changed payloads, duplicate deliveries or stream errors.

## BPSK

| Measurement | Final result |
|---|---|
| Sustained workload | 9,000/9,000 each way; 10,240 payload bit/s each way; 1/2 retries |
| 1-byte boundary | 300/300 each way; no retries |
| 64-byte boundary | 300/300 each way; no retries |
| 1024-byte boundary | 300/300 each way; 7/4 retries |
| Echo | 60/60; p95 0.288 s; no retries |
| Acquisition / signal recovery / follower restart | Within 0.052 / 1.105 / 0.151 s |

No retry limits were exhausted. One-way same-host p95 latency was 0.202/0.193 s;
this is distinct from echo RTT. Qualification included CRC-guided receiver candidate
selection and reuse of prepared DATA/ACK bursts across missed transmit windows.
Packet and waveform bytes stayed unchanged.

Software at qualification: 370 tests passed, 79.69% coverage; lint, formatting,
types, conformance and packaging passed.

## 2FSK

| Measurement | Final result |
|---|---|
| Sustained workload | 9,000/9,000 each way; 10,240 payload bit/s each way; no retries |
| 1-byte boundary | 300/300 each way; no retries |
| 64-byte boundary | 300/300 each way; no retries |
| 1024-byte boundary | 300/300 each way; no retries |
| Echo | 60/60; p95 0.279 s; no retries |
| Acquisition / signal recovery / follower restart | Within 0.181 / 2.159 / 0.132 s |

One-way same-host p95 latency was 0.194/0.205 s. Independent analytical references
verified tone polarity, spacing, continuous phase and exact sample counts. Replay
and timed split-stream reception passed. Software at qualification: 438 tests,
79.96% coverage; lint, formatting, types, conformance, builds and installed-wheel
checks passed.

## MSK

Locally qualified 2026-09-29 at the shared bench settings above. Frequency deviation
is derived as one quarter of the bit rate; contradictory overrides are rejected.

| Measurement | Final result |
|---|---|
| Sustained workload | 9,000/9,000 each way; 10,240 payload bit/s each way; no retries |
| 1-byte boundary | 300/300 each way; identity audit passed; no retries |
| 64-byte boundary | 300/300 each way; no retries |
| 1024-byte boundary | 300/300 each way; no retries |
| Echo | 60/60; p95 0.295251 s; no retries |
| Acquisition across all six runs | Within 0.198 s after both devices ready |
| Signal interruption | 17 seconds of zero TX IQ; follower entered recovery; delivery resumed within 1.188 s |
| Follower restart | Both directions delivered within 0.240 s after readiness |

All measured traffic workloads had zero changed payloads, duplicate deliveries,
retry exhaustion, buffer drops, overflows, underflows, timeouts, late transmissions
or RX/TX stream errors. Sustained one-way same-host p95 latency was 0.195/0.206 s;
this is separate from echo RTT. Interruption and restart used a separate 30-second
traffic run (150/150 each way), leaving the 30-minute run uninterrupted.

Independent analytic phase calculations verified h = 0.5, per-bit phase increments,
constant envelope, phase continuity, exact sample counts and repeatable burst reset.
The noncoherent streaming receiver passed acquisition/timing tests, noise/offset
characterization and capture replay. No optimal-coherent-receiver, continuous clock
tracking, independent-host or over-air range performance is claimed.

## GMSK — short-run validation

Validated with the shortened Step 8 workload requested by the user, on the shared
conducted setup above. Gaussian BT=0.3, span=4 bit intervals, h=0.5 and derived
frequency scale ±62.5 kHz. This is **not a 30-minute endurance qualification**.

| Measurement | Final result |
|---|---|
| Five-minute sustained workload | 1,500/1,500 each way; 10,240 payload bit/s each way; no retries |
| 1-byte boundary, one minute | 60/60 each way; no retries |
| 64-byte boundary, one minute | 60/60 each way; no retries |
| 1024-byte boundary, one minute | 60/60 each way; no retries |
| Echo | 60/60; p95 0.280485 s; no retries |
| Acquisition across all six runs | Within 0.330 s after both devices ready |
| Signal interruption | 17 seconds of zero TX IQ; follower entered recovery; delivery resumed within 1.188 s |
| Follower restart | Both directions delivered within 0.230 s after readiness |

All measured traffic workloads had zero changed payloads, duplicate deliveries,
retry exhaustion, buffer drops, overflows, underflows, timeouts, late transmissions
or RX/TX stream errors. Sustained one-way same-host p95 latency was 0.197/0.227 s.
Interruption/restart used a separate 30-second traffic run (150/150 each way).

The waveform uses pseudorandom training to avoid concentrating its Gaussian-shaped
preamble at carrier DC. Independent scalar Gaussian convolution/phase references
verify the FIR, waveform samples, constant envelope, burst reset, full tails and
corrected receive timestamps. Software tests cover BT=0.3/0.5 and spans=4/6;
only BT=0.3/span=4 has the conducted measurements above. No optimal coherent
receiver, continuous clock tracking, independent-host or over-air range claim is made.

## QPSK — short-run validation

Validated on the shared conducted setup at **500 kbit/s, 2 Msps, eight samples per
symbol**, with rectangular pulses and `rs_parity_bytes: 16`. QPSK bench timing is
180 ms TDD, 20 ms DATA lead, 30 ms SYNC lead and a 600 ms ACK timeout. The signed
maximum-size burst occupies 39.040 ms. Example profiles also enable the same
Reed–Solomon protection; both peers must use matching settings.

| Measurement | Final result |
|---|---|
| Five-minute sustained workload | 1,500/1,500 each way; 10,240 payload bit/s each way; 0/11 retries |
| 1-byte boundary, one minute | 60/60 each way; no retries |
| 64-byte boundary, one minute | 60/60 each way; 1/0 retries |
| 1024-byte boundary, one minute | 60/60 each way; 2/12 retries |
| Echo | 60/60; p95 0.314466 s; no retries |
| Acquisition across all six runs | Within 0.344 s after both devices ready |
| Signal interruption | 17 seconds of zero TX IQ; follower entered recovery; delivery resumed within 1.112 s |
| Follower restart | Both directions delivered within 0.220 s after readiness |

All final measured traffic workloads had zero changed payloads, duplicate
deliveries, retry exhaustion, buffer drops, overflows, underflows, timeouts, late
transmissions or RX/TX stream errors. Interruption/restart used a separate
30-second traffic run (150/150 each way; 0/5 retries).

Retries and queueing increased latency in one direction: sustained one-way
same-host p95 was 1.496/0.354 s; maximum-payload boundary p95 was 4.426/0.458 s.
These are distinct from the separate one-packet-per-second echo measurement.
This profile meets the shortened delivery, echo and recovery gates; it does not
establish uniformly low latency at the sustained load. No 30-minute endurance,
independent-host, over-air range, RRC-shaped hardware or no-RS hardware qualification
is claimed. QPSK remains outside the original release-focus milestone order.

The receiver runs decision-directed carrier tracking only as a fallback and only
tracks the length field while a packet is incomplete. This avoids repeated full
payload carrier loops during streaming while preserving carrier-drift recovery.
The packet format and constellation mapping remain unchanged.

## Software characterization conditions

Frozen before tuning: 125/250/500 kbit/s at 2 Msps; payloads 1/64/256/1024 bytes;
carrier offsets −750/0/+750 Hz; independent Gaussian noise with standard deviation
0.08 in each I/Q component per sample on unit-amplitude IQ. Seeds: BPSK 5005,
2FSK 6006, MSK 7007, GMSK 8008, QPSK 9009. Separate TX/RX instances, random receive block boundaries and initial
sample phase were used. 2FSK deviation was half the bit rate; additional independent
reference tests covered offsets of ±one quarter of deviation. MSK deviation was
one quarter of bit rate; independent-reference tests covered all eight sample
phases and offsets ±15,625 Hz at 250 kbit/s. This is a synthetic
noise definition, not a calibrated RF SNR. QPSK covers both zero and 16-byte
Reed–Solomon parity profiles at every matrix point; signed QPSK application gates
use the maintained 16-byte protection profile.

BPSK, 2FSK, MSK, GMSK and QPSK passed signed application tests for 1,000 packets each way,
100 deliberately lost first ACKs with exactly-once delivery, bounded reject-newest
overload and recovery within five simulated seconds. A 30-second follower host-clock
offset was included. Simulation timings are not hardware throughput evidence.

## Reproduce or extend results

Follow `docs/UHD_SETUP.md` for the Python environment and attenuated setup. With no
other radio process running, substitute `bpsk`, `2fsk` or `msk` below:

```bash
PYTHONPATH=. python tools/uhd_link_check.py --modulation 2fsk \
  --duration 1800 --rate 5 --interrupt-seconds 17 --restart-follower --output /tmp/radio-sustained.json
PYTHONPATH=. python tools/uhd_link_check.py --modulation 2fsk \
  --echo --duration 60 --rate 1 --output /tmp/radio-echo.json
for size in 1 64 1024; do
  PYTHONPATH=. python tools/uhd_link_check.py --modulation 2fsk \
    --duration 300 --rate 1 --payload "$size" --output "/tmp/radio-boundary-$size.json"
done
```

For the shorter GMSK/QPSK checks, use `--modulation gmsk` or `--modulation qpsk`,
sustained `--duration 300` and boundary `--duration 60`. Run interruption/restart separately with
`--duration 30 --rate 5 --interrupt-seconds 17 --restart-follower`. The runner
produces temporary JSON for checking a run; discard it after recording the final
summary. Two-host qualification remains deferred.

## Current repository verification

Verified 2026-10-14 for Step 9 diagnostics/tooling: **924 passed in 419.51 seconds**.
Combined line-and-branch coverage is **88.51%** (minimum 75%).
Line coverage is **91.38%** (3,415/3,737);
branch coverage is **80.02%** (1,013/1,266).
Coverage measures `packet_radio`, excluding bench tools and real hardware runs.

Step 9 adds 26 cases covering activity/rate semantics, health reporting, profile
and report handling, shutdown failures, coordinated bidirectional/echo UDP probes,
invalid/mismatched traffic and capture/replay across all five maintained waveforms.
The probe tests use actual loopback sockets with synthetic status snapshots, not
radios. Existing modulation, authentication, lifecycle and hardware-mock regressions
all pass. Coverage measures the runtime package; bench/probe tools are tested but
excluded from the coverage percentage above.

Ruff lint, formatting, Mypy, all ten paired profiles, protocol conformance,
wheel/source builds and Twine checks pass. An installed wheel outside the checkout
passed simulated delivery, new diagnostics, JSON status, shutdown and fixture
conformance checks. The dependency audit found no known vulnerabilities.

Step 9 local hardware lifecycle checks passed on 2026-10-14 after both devices were
reconnected, as summarized below. The earlier sustained modulation qualifications
remain unchanged. The two-host probe implements coordinated traffic/echo for payloads 24..1024 bytes; tiny-payload
audits and automatic muting/restart remain local-runner capabilities. Physical
two-host qualification remains deferred. Commands and activity/health semantics
are documented in `docs/UHD_SETUP.md` and `docs/PROGRAM_GUIDE.md`.

### Step 9 hardware lifecycle verification — 2026-10-14

All five explicit `configs/bench/peer-{a,b}-<modulation>.yaml` pairs passed on
B205mini serials **3457464** and **3457480**, using UHD **4.9.0.1-1ubuntu1** and
NumPy **2.3.5**. Both radios ran on this workstation with the established crossed
TX/RX-to-RX2 connections and 30 dB attenuation per path. Hardware readbacks confirmed
915 MHz, 2 Msps, 1 MHz bandwidth, TX gain 20 dB and RX gain 40 dB.

Each check used 30 seconds of bidirectional traffic, 256-byte packets and five
packets/second/direction, followed by 17 seconds of zero-valued TX IQ and a separate
follower-process restart. Hardware clock synchronization and timed transmission
were enabled; fixed hardware-slot scheduling was off. BPSK/2FSK/MSK/GMSK used
250 kbps; QPSK retained its 500 kbps, RS16, 20 ms DATA lead, 30 ms SYNC lead and
600 ms ACK-timeout profile. The runner generated temporary authentication keys.

| Modulation | Delivery each way | Workload retries A/B | Signal recovery | Delivery after follower readiness |
|---|---|---|---|---|
| BPSK | 150/150 | 0/0 | 1.051 s | 0.172 s |
| 2FSK | 150/150 | 0/0 | 1.173 s | 0.176 s |
| MSK | 150/150 | 0/0 | 1.121 s | 0.172 s |
| GMSK | 150/150 | 0/0 | 1.133 s | 0.194 s |
| QPSK | 150/150 | 0/2 | 1.186 s | 0.189 s |

All runs passed initial acquisition and delivered both ways after interruption and
restart. Each follower entered recovery during the interruption. Workloads had
zero missing, changed or duplicate payloads, retry exhaustion, buffer/decoder drops,
RX/TX stream errors, overflows, underflows, timeouts or late transmissions.
Both final worker snapshots reported `radio_ready=false` and `shutdown_errors=0`,
and every worker exited cleanly. Successive runs reopened both devices; after QPSK,
independent `cli.validate --hardware` runs also opened and closed each SDR successfully.
Reports contained actual hardware settings, profile hashes, redacted effective
configuration and activity diagnostics. Only these final summaries are retained.

These short checks complete Step 9 on the local bench. They supplement the earlier
sustained qualifications and do not qualify two independent hosts or physical USB
unplug/reconnect recovery: RX streams stayed active during signal interruption.

### Connected-radio regression check

Both B205mini devices were checked on 2026-09-29 using the shared conducted bench
settings above. Each modulation ran a 30-second bidirectional workload of 256-byte
packets at five packets/second/direction, followed by a 17-second zero-TX-IQ
interruption and a separate follower-process restart. Hardware clock synchronization
and timed transmission were enabled; fixed hardware-slot scheduling remained off.

| Modulation | Delivery each way | Retries each way | Signal recovery | Delivery after follower readiness |
|---|---|---|---|---|
| BPSK | 150/150 | 0/0 | 1.131 s | 0.170 s |
| 2FSK | 150/150 | 0/0 | 1.134 s | 0.212 s |

Both checks passed with zero changed payloads, duplicate deliveries, buffer drops,
RX/TX stream errors, underflows, timeouts or late transmissions during the measured
traffic workload. Both directions delivered after each interruption and restart;
the follower entered recovery during each signal interruption. These short regression
checks supplement the earlier 30-minute qualification; they do not qualify independent
hosts, USB disconnection, MSK or GMSK. Only final summaries are retained here.

After the module cleanup on the same date, both BPSK and 2FSK repeated the
30-second, 256-byte, five-packets/second/direction workload on the same hardware
and settings. Each delivered **150/150 in both directions**, with **zero retries,
changed payloads, duplicates or stream errors**. These post-cleanup checks covered
traffic and timed transmission; the interruption/restart measurements above are
from the preceding regression run and were not repeated during this cleanup.

### Version 1.0.0 packaging verification — 2026-10-14

The release distribution is `packet-radio` and imports use `packet_radio`. The
namespace/command/metrics rename changes no protocol bytes or modem algorithms.
The wheel and source archive include the maintained guides, profiles and tools;
only the source archive includes the software tests.

A fresh isolated Python 3.13 environment installed the wheel with its runtime
dependencies. All seven console commands, protocol-v5 fixtures, all ten paired
profiles and signed 256-byte binary delivery in both directions for all five
modulations passed outside the checkout. The same installed checks passed in a
fresh system-site-packages Python 3.14/UHD environment. These software checks
provision temporary keys and use simulated samples; they do not qualify RF timing.

The installed wheel's bundled bench runner and profiles then passed 10-second,
256-byte, five-packets/second/direction runs on the same attenuated B205mini pair:

| Modulation | Delivery each way | Retries A/B | Stream errors | Clean shutdown |
|---|---|---|---|---|
| BPSK | 50/50 | 0/0 | 0 | Passed |
| 2FSK | 50/50 | 0/0 | 0 | Passed |
| MSK | 50/50 | 0/0 | 0 | Passed |
| GMSK | 50/50 | 0/0 | 0 | Passed |
| QPSK | 50/50 | 0/2 | 0 | Passed |

All runs had zero missing, changed or duplicate payloads and no retry exhaustion.
These are installation/streaming checks, not new sustained qualifications; the
longer measurements and Step 9 recovery checks above remain the qualification
evidence. Two-host operation remains deferred. Only final hardware summaries are
retained here.

Final renamed-source verification: **924 tests passed in 408.39 seconds**, with
**88.51%** combined coverage. Ruff formatting/lint, Mypy, protocol conformance,
package metadata checks and dependency audit passed. The audit found no known
vulnerabilities. The configured Python 3.10–3.14 CI matrix was not executed locally;
the full local suite used Python 3.13, with installed smoke checks on 3.13 and 3.14.

### Version 1.1 Step 2 — processing-layer regression

The refactor selects packet encoding/validation, burst protection/framing and link
behavior through explicit interfaces. The only production profile remains
`generic-v5`; `simple-frame-v1` is still specified only and rejected by the runtime.
Existing schema-1 profiles and the protocol-v5 fixture remained byte-for-byte
unchanged (21 files checked against the Step 1 baseline).

Signed-packet IQ for payload sizes 1/64/256/1024 across all five modulations matched
the packaged 1.0.0 implementation **sample-for-sample exactly** (20 comparisons),
using the same numerical environment. Existing conformance vectors passed. New
regressions exercise transform order/reversal, substituted sync/protection on all
five waveforms, selected codec dispatch and rejection before hardware access.

Conducted hardware regression used the same B205mini pair and attenuated wiring,
915 MHz, 2 Msps, 1 MHz bandwidth, TX 20 dB/RX 40 dB, with the unchanged explicit
bench profiles. Each run used 10 seconds of 256-byte traffic at five packets per
second per direction:

| Modulation | Delivery each way | Retries A/B | Stream errors | Clean shutdown |
|---|---|---|---|---|
| BPSK | 50/50 | 0/0 | 0 | Passed |
| 2FSK | 50/50 | 0/0 | 0 | Passed |
| MSK | 50/50 | 0/0 | 0 | Passed |
| GMSK | 50/50 | 0/0 | 0 | Passed |
| QPSK | 50/50 | 0/1 | 0 | Passed |

There were no missing, changed or duplicate payloads, retry exhaustion, stream
errors or shutdown errors. These are short generic-v5 regression checks, not
qualification of the new emulation format, hopping or independent hosts. Earlier
sustained and recovery results remain the evidence for those tested behaviors.

A development wheel installed outside the checkout passed all command entry
points, bundled paired profiles, conformance and signed simulated delivery for all
five waveforms. Package metadata, Ruff formatting/lint and Mypy checks passed.
The released 1.0.0 archives were preserved; the development build was kept temporary.

Final source verification: **937 tests passed in 425.89 seconds**, with **88.67%**
combined line/branch coverage. This includes 13 new processing-layer regressions.
The final full run followed the queue-interface cleanup; all targeted lifecycle,
protocol, replay, diagnostic and hardware-runner tests also passed.

### Version 1.1 Step 3 — configurable framing and protection

The Python framing API now implements the frozen `simple-frame-v1` byte layout
and 2FSK sample pipeline, with configurable preamble/sync, bit/byte order, fixed or
variable payload length, CRC-16/CCITT-FALSE, CRC-32/ISO-HDLC or no CRC. Protection
selects true no-FEC or extended Hamming/interleaving with optional Reed–Solomon.
Whitening, differential encoding and non-byte fields are explicitly unsupported;
none is required by the frozen initial profile. The application still uses
`generic-v5`; live ACK-free operation and YAML selection remain Steps 4–5.

The 184 new tests cover independent CRC/frame vectors, every single-bit corruption
of short protected-by-CRC frames, malformed lengths, truncated frames, fixed-size
framing, transform sizing and rejection of unsupported settings. Independent scalar
2FSK waveforms match transmitted IQ within 1e-5 and have exact sample counts for
1/64/256/1024-byte payloads. Receiver testing covers 96 combinations of those sizes,
carrier offsets -750/0/+750 Hz and all eight sample phases, with seed 1101 and
independent Gaussian noise of standard deviation 0.08 on each I/Q component.
This is a sample-amplitude test condition, not a calibrated SNR claim. Split-input,
invalid-candidate recovery, timestamp discontinuity and partial-frame expiry checks
also pass. Expiry occurs on the next feed call after 100 ms without additional
samples. Idle receive buffering is now bounded even after large input blocks.

Generic-v5 signed-packet waveforms remain exactly equal to the packaged 1.0.0
implementation for all five modulations and four payload sizes (20 comparisons).
The frozen wire fixture and 20 schema-1 profiles remain unchanged.

Conducted regression used the same attenuated B205mini pair, 915 MHz, 2 Msps,
1 MHz bandwidth, TX 20 dB/RX 40 dB and unchanged bench profiles. Each run used
10 seconds of 256-byte traffic at five packets/second/direction:

| Modulation | Delivery each way | Retries A/B | Stream errors | Clean shutdown |
|---|---|---|---|---|
| BPSK | 50/50 | 0/1 | 0 | Passed |
| 2FSK | 50/50 | 0/0 | 0 | Passed |
| MSK | 50/50 | 0/0 | 0 | Passed |
| GMSK | 50/50 | 0/0 | 0 | Passed |
| QPSK | 50/50 | 0/0 | 0 | Passed |

There were no missing, changed or duplicate payloads, retry exhaustion or shutdown
errors. These short generic-v5 checks do not qualify the new frame format over RF,
independent hosts or interoperability with a target radio.

A fresh development wheel installed outside the checkout passed all command entry
points, bundled paired-profile checks, signed simulated delivery on all five
waveforms and the documented simple-frame example. Package metadata checks,
Ruff formatting/lint, Mypy and protocol conformance passed. Released 1.0.0 archives
remain unchanged; development builds and raw measurement reports were temporary.

Final source verification: **1,121 tests passed in 421.65 seconds**, with **89.33%**
combined line/branch coverage. This includes 184 new framing and modem tests.

### Version 1.1 Step 4 — selectable ACK-free live modes

`SimpleProfile` now connects the configurable 2FSK frame pipeline to `RadioApp`.
Transmit-only, receive-only and bidirectional modes use a bounded FIFO with one
TX attempt per packet and no automatic ACKs, retries, SYNC beacons, peer acquisition
or identity/replay semantics. Validated preframed input retains its original body
bytes; output is always payload. Status distinguishes mode, local readiness,
receiver acquisition and delivery. Schema-1 YAML still selects generic-v5; reusable
profiles and simple-format recording/replay remain Step 5.

Software checks deliver 1,000 exact packets in each sequential direction, including
all byte values, repeated payloads, embedded acquisition patterns and seed-1101
random payloads. Additional tests cover real local UDP delivery of maximum-size
preframed input, direction restrictions, bounded queues including in-flight work,
TX failure without retry, loss/recovery, CRC rejection/recovery, timing lower bounds,
local health and rejection of incompatible settings before hardware access.

Initial hardware checks exposed errors in the final CRC bits and loss of maximum-
size frames. The simple-profile UHD path now transports 32 microseconds of zero-
valued idle before EOB so its TX pipeline can drain; this preserves the logical
waveform and adds no packet symbols. The receiver now retains a bounded block/idle
margin around the largest frame. Both fixes have software regression coverage.
Generic-v5 retains zero transport idle padding and unchanged logical waveforms.

Final short simple-format checks used the established attenuated B205mini pair
(3457464/3457480), 915 MHz, 2 Msps, 250 kbps, ±125 kHz deviation, 1 MHz bandwidth,
TX 20 dB/RX 40 dB. Each direction sent 150 256-byte packets at five/second, followed
by five boundary/repeated payloads, for about 31 seconds of offered traffic:

| Direction | Input | Exact delivery | RX-only TX samples | Minimum device-target idle gap |
|---|---|---|---|---|
| 3457464 → 3457480 | Payload | 155/155 | 0 | 162.545 ms |
| 3457480 → 3457464 | Preframed body | 155/155 | 0 | 161.493 ms |

Boundary payloads included 1, 64 and 1,024 bytes and two identical four-byte
payloads. No missing, changed or unintended duplicate deliveries occurred. There
were no ACKs, retries or extra control bursts. Independent phase-sign inspection of
submitted logical IQ verified the exact preamble/sync/body bytes and sample counts.
Gaps were measured between submitted device-time targets, not UDP send timestamps;
this is not an independent measurement of the RF envelope or minimum-gap saturation.

Both directions had zero new stream errors, overflows, underflows, dropped buffers
or late transmissions during the measured workload. Startup/warm-up occurred before
the counter baseline; a startup overflow indication was observed outside that
measurement interval. Devices closed cleanly and reopened for the reverse run.
These are short integration checks, not the contract's five-minute sustained,
one-minute boundary, physical interruption/recovery or spectral qualification.
Two-host and actual-target interoperability remain unclaimed.

Generic-v5 regression used the unchanged paired profiles, 10 seconds of 256-byte
traffic at five packets/second/direction on the same bench:

| Modulation | Delivery each way | Retries A/B | Stream errors | Clean shutdown |
|---|---|---|---|---|
| BPSK | 50/50 | 0/0 | 0 | Passed |
| 2FSK | 50/50 | 0/0 | 0 | Passed |
| MSK | 50/50 | 0/0 | 0 | Passed |
| GMSK | 50/50 | 0/0 | 0 | Passed |
| QPSK | 50/50 | 0/3 | 0 | Passed |

All five runs had zero missing, changed or duplicate payloads and no retry
exhaustion. Signed generic-v5 waveforms remain sample-for-sample identical to the
packaged 1.0.0 code across all five modulations and sizes 1/64/256/1024 (20 checks).
The frozen fixture and all 20 profiles remain byte-for-byte unchanged. Protocol
conformance, Ruff formatting/lint, Mypy and package metadata checks passed.

A fresh development wheel installed outside the checkout passed command entry
points, bundled paired profiles and signed simulated delivery on all five legacy
modulations. The documented simple-profile configuration also passed installed
maximum-size payload delivery with zero receiver transmissions. Released 1.0.0
archives remain unchanged; development builds and raw measurements are temporary.

Final source verification: **1,158 tests passed in 435.18 seconds**, with **89.94%**
combined line/branch coverage, including 37 new application, receiver-buffer and
UHD idle-flush regressions. All required formatting, lint, typing and conformance
checks passed.


### Version 1.1 Step 5 — reusable profiles and resolved capture/replay

Schema-2 node files now select a reusable versioned definition for generic-v5 or
simple-frame-v1. Waveform, fixed-frequency policy, framing, protection and link
behavior are separated from device/gain/port/key/recording and host timing settings.
Unknown keys, duplicate/merge YAML keys, invalid types, ambiguous overrides and
incompatible pair/layer combinations are rejected. Effective behavior overrides
produce a custom ID and unqualified status. Resolved previews expose complete
settings and a definition fingerprint while redacting key paths.

The existing schema-1 wire fixture and all 20 example/bench profiles remain
byte-for-byte unchanged. Their files continue to load without conversion. Signed
schema-2 generic-v5 delivery passed on all five modulations in simulation. All 20
signed generic-v5 sample arrays (five modulations × payload sizes 1/64/256/1024)
remain exactly equal to the packaged 1.0.0 implementation.

Application capture format 3 retains resolved profile identity, framing/protection,
waveform, frequency and operating/timing settings. Tests replay rotated IQ and frame
captures after deleting the source profile, including CRC-32, little bit/byte order,
fixed-length frames and Hamming/RS variants. All five generic-v5 waveforms also
pass resolved capture replay. Live simple application IQ/frame recording and the
replay CLI pass. Missing/conflicting metadata is rejected; legacy capture formats
1/2 still replay. This checks reconstruction, not RF retransmission of a capture.

Short schema-2 conducted checks used B205mini serials 3457464/3457480 on the
established crossed 30 dB attenuated paths: 915 MHz, 2 Msps, 250 kbps 2FSK,
±125 kHz deviation, 1 MHz bandwidth, TX 20 dB/RX 40 dB. Temporary host files selected
the shipped reusable definitions with local serials/gains. The simple check loaded
its processing chain directly from YAML, with no injected Python profile:

| Profile / direction | Offered traffic | Exact delivery | Retries | Stream errors |
|---|---|---|---|---|
| Simple A→B, payload input | 50 × 256 bytes at 5/s, plus five boundary/repeated payloads | 55/55 | None generated | 0 |
| Simple B→A, framed input | Same; radios closed/reopened for reverse direction | 55/55 | None generated | 0 |
| Generic-v5 A↔B, signed payloads | 10 seconds, 50 × 256 bytes at 5/s each way | 50/50 each way | 0/0 | 0 |

Simple receiver-only TX sample counts were zero, with no ACKs or other control
bursts. Submitted logical IQ bytes/sample counts matched the frozen format; minimum
device-target idle gaps were 160.671 ms forward and 163.524 ms reverse. No missing,
changed or unintended duplicate payloads occurred. Both checks shut down cleanly.
Stream-error deltas cover the workload after warm-up; simple startup again showed
an overflow indication outside that interval. Device-target gaps are not independent
RF-envelope measurements. These short checks do not extend sustained qualification,
minimum-gap capacity, physical recovery, two-host or target-radio interoperability
claims. Earlier all-five modulation hardware results remain the regression baseline.

Ruff formatting/lint, Mypy, protocol conformance and package metadata checks passed.
Only final measurement summaries are retained here; raw hardware reports and local
bench overlays were temporary. Released 1.0.0 archives remain unchanged.

A fresh development wheel installed outside the checkout passed all command entry
points, legacy paired profiles and signed simulated delivery on all five waveforms.
Both bundled schema-2 profile pairs validated and delivered maximum-size payloads.
Installed schema-2 live capture creation, IQ replay and the replay command also
passed. The installation and capture checks did not access SDR hardware.

Final source verification: **1,244 tests passed in 443.83 seconds**, with **90.06%**
combined line/branch coverage. This includes 86 new profile-composition, CLI,
compatibility, signed-delivery and resolved capture/replay tests.

## Version 1.1 Step 6 — simple 2FSK frequency hopping

The new schema-2 `simple-2fsk-hopping-v1` profile passed the defined **same-host**
conducted hopping workload. Table: **914 → 915 → 916 MHz**, cyclic, **1 hop/s**,
1-second dwell, 100 ms guards, POSIX epoch 0, external UTC synchronization. Both
applications used this workstation's clock; no on-air synchronization packets were
added. The two B205minis (3457464 and 3457480) retained the crossed TX/RX → 30 dB
attenuator → RX2 connections. Settings: UHD 4.9.0.1, 2 Msps, 1 MHz bandwidth,
250 kbit/s rectangular 2FSK, ±125 kHz deviation, TX 20 dB/RX 40 dB, background
decoding and 10 ms hardware-timed TX lead. Simple framing/protection bytes remain
unchanged.

| Conducted check | Result |
|---|---|
| 300 seconds, 256-byte packets, 1/s each direction, separated within each dwell | **300/300 exact each way** |
| 60 seconds cycling 1/64/1024-byte packets, 20 of each size per direction | **60/60 exact each way** |
| 1024-byte bursts offered near the dwell end, too late to finish before the guard | **60/60 rejected locally; no RF submission or retry** |
| Receiver held on 920 MHz for three seconds, then normal schedule resumed | Three deliberately lost packets; next offered post-restoration traffic delivered; **3/3 exact each way**, three skipped slots reported by the paused peer |
| Total intended received workload | **363/363 exact per receiver**, no changed or unintended duplicate payloads |
| Stream errors after warm-up; missed steady-state slots/tune deadlines | **0 / 0** on both radios |
| ACKs, synchronization beacons, retries | None generated |

Retuning measurements include host locking, continuous-RX stop/drain, both LO tunes
and RX restart. Serial 3457464: min/p95/max **18.544/30.401/56.908 ms**; serial
3457480: **18.471/30.431/51.398 ms**. Latest restart completion was **59.268 ms**
and **53.294 ms** after the dwell boundary, respectively. A further 100 ms
monotonic guard suppresses TX and decoding after every tune. First observed
successful TX target phases were **211.097 ms** (A) and **511.172 ms** (B); first
host payload delivery phases were **226.735 ms** at B and **527.915 ms** at A.
These successful cross-radio payloads establish usable RX/TX at the tested phases;
tune-call duration alone was not used as evidence of RF readiness.

An initial startup overflow indication occurred before the measured baseline.
Error deltas above cover the workload after warm-up. The regression suite also ran
at reduced CPU priority during the sustained check. Device targets are mapped to
host UTC using adjacent host/device clock reads; these are not independent RF
edge/settling or phase-coherence measurements. The supported envelope is the
measured table and runtime settings, not arbitrary tables, rates, host loads or
RFIC fast-lock capability. Validation rejects dwell below 1 s and guard below
100 ms. Slower dwells and ping-pong sequences are configurable/software-tested,
but have no separate conducted qualification.

The off-channel interruption was induced through tuning, without cable removal or
USB disconnection. This does not claim USB unplug/reconnect recovery. This hopping
workload is separate from the fixed simple-format 5-packet/s/17-second-interruption
acceptance contract; those broader fixed-profile gates are not marked complete by
this run. Independent-host timing and actual-target-radio interoperability remain
unqualified. Raw hopping IQ capture is rejected until discontinuity metadata exists;
decoded-frame capture/replay preserves the full resolved schedule.

A separate 60-second probe reversed the device order and offered the first
packet at dwell phase 150 ms. It delivered **60/60 sustained packets and 69/69
including boundaries/recovery in each direction**, with zero new stream errors
or missed steady-state tune deadlines. Serial 3457480 retune min/p95/max was
**18.272/29.346/31.041 ms**, and serial 3457464 was
**19.364/30.377/32.793 ms**. First buffered RX blocks after restart arrived at host
phases **28.257–37.058 ms** and **28.852–39.191 ms**, respectively. Those blocks
include idle/noise and are discarded during the post-tune guard; their arrival
alone does not establish usable RF. Earliest successful timed TX from 3457480 was
**161.291 ms**, with earliest validated host payload delivery at 3457464 at
**177.153 ms**. This is a tested usable timing bound, not the minimum physical LO
settling time. Six more deliberately crossing bursts were rejected, and recovery
again succeeded without retries. Both device orders closed and reopened successfully.

Software checks preserve all 20 legacy example/bench YAML files and the frozen
protocol-v5 fixture byte-for-byte. All **20/20 signed waveform arrays** (five
modulations × 1/64/256/1024-byte payloads) match the frozen 1.0.0 implementation
exactly. New tests cover future epochs, cycle/ping-pong positions, invalid tables
and rates, maximum-packet fit, modulation overrunning a dwell, selected-burst drops,
missed complete cycles, clock reversals, post-tune guards, synchronized decoder
reset/output flushing, hopping frame replay and the CLI. An installed development
wheel passed all command entry points, all five legacy waveform delivery checks,
and generic/simple/hopping schema-2 profile checks. The released 1.0.0 archives
were not replaced.

Short fixed-frequency hardware regressions also passed at 915 MHz. Simple frames
sent 10 × 256-byte packets at 5/s plus five boundary/repeated payloads:
**15/15 exact each direction**, no control/retry traffic, RX-only TX samples zero,
and zero new stream errors. Logical transmitted bytes/sample counts were exact;
minimum device-target idle gaps were **161.595 ms** forward and **162.242 ms**
reverse. The signed generic-v5 two-process UDP check ran for 10 seconds at 5/s:
**50/50 exact each direction**, no retries, duplicates, changed packets or new
stream errors, and clean shutdown. These short checks establish regression evidence,
not additional sustained fixed-frequency qualification.

Final source verification: **1,274 tests passed in 452.40 seconds**, with **90.14%**
combined line/branch coverage. This includes 30 new hopping/decoder tests. Ruff
formatting/lint, Mypy (45 source files), protocol conformance, frozen-file checks
and development-package metadata checks passed. Only final measured summaries are
retained here; temporary conducted reports and IQ comparison arrays were removed.

## Version 1.1 Step 7 — bounded rate and receiver reference set

No target/model has been selected. Step 7 assessed and exercised simple-frame 2FSK
at **100, 250 and 500 kbit/s**, all at **2 Msps**, with integral samples/symbol
20/8/4 and deviation ±50/125/250 kHz. The new 100/500 kbit/s reusable profiles keep
915 MHz fixed frequency and the frozen simple framing/CRC. RF bandwidth is 1 MHz
at 100 kbit/s and 1.5 MHz at 500 kbit/s. No production modem or framing algorithm
was changed. New rates use the already-supported exact rate path.

Independent reference IQ integrates the two tones at receiver sample times and
uses standard-library CRC, without calling the production waveform/envelope or
resampling its IQ. All 1/64/256/1024-byte clean arrays match production TX within
**1e-5 absolute complex-sample error**, with exact counts and logical maximum-burst
airtimes **85.120/34.048/17.024 ms** at 100/250/500 kbit/s. There is no resampling
or filter delay; the separate backend zero-idle tail remains 32 µs.

The tested receiver envelope passed separate and combined **±10 ppm sample-clock
error**, **±750 Hz carrier offset**, and independent Gaussian I/Q noise with
standard deviation **0.04**, seed 1101. All tested payloads decoded exactly. Arrival
timestamp error was at most **500 ns**. Checks include fractional onset, every
integer start phase at the negative-error corner, split input blocks and timestamp
reset. This is sample-domain characterization, not calibrated RF SNR or a claim
about physical clock accuracy.

A wider exploratory sweep used a 1024-byte ascending-byte payload, +750 Hz offset,
0.35-sample onset and the same noise seed. Results below are observations of those
specific cases, not acceptance guarantees outside ±10 ppm:

| Bit rate | -100 ppm | -50 ppm | -20 ppm | -10/0/+10 ppm | +20 ppm | +50 ppm | +100 ppm |
|---|---|---|---|---|---|---|---|
| 100 kbit/s | Fail | Pass | Pass | Pass | Pass | Pass | Fail |
| 250 kbit/s | Fail | Fail | Fail | Pass | Pass | Pass | Fail |
| 500 kbit/s | Fail | Pass | Pass | Pass | Pass | Pass | Fail |

The receiver does not track symbol-clock drift within a burst. A target needing
larger clock errors or longer packets must add timing recovery. Resampling is
explicitly deferred because the reference rates are integral; 19,200 bit/s at
2 Msps remains rejected rather than rounded. Target-specific polarity/mapping,
shaping, ramps and phase continuity also remain deferred pending a reference.
This is a bounded rate milestone, not arbitrary-rate or target interoperability
support. Independent-host operation and hopping at the two new rates are unqualified.

Conducted rate checks used UHD 4.9.0.1 and the same two B205minis, crossed through
30 dB attenuators, TX 20 dB/RX 40 dB. Each direction offered 300 × 256-byte packets
at 5/s (one minute), then 1/64/1024-byte boundaries and two identical 4-byte payloads.
Forward input used payloads; reverse input used preframed bodies. Radios closed and
reopened between directions. These short checks do not establish sustained
qualification, maximum throughput, minimum-gap capacity or independent-host behavior.

| Rate / direction | Exact delivery | CPU seconds / elapsed seconds | Minimum device-target idle gap | RX decoder p95 / max processing | RX decoder realtime utilization |
|---|---|---|---|---|---|
| 100 kbit/s, 3457464 → 3457480 | **305/305** | 24.641 / 60.834 | 104.002 ms | 0.293 / 8.918 ms | 0.0784 |
| 100 kbit/s, 3457480 → 3457464 | **305/305** | 21.451 / 60.833 | 109.823 ms | 0.349 / 10.649 ms | 0.0745 |
| 500 kbit/s, 3457464 → 3457480 | **305/305** | 21.291 / 60.823 | 179.409 ms | 0.437 / 3.997 ms | 0.0676 |
| 500 kbit/s, 3457480 → 3457464 | **305/305** | 19.294 / 60.823 | 180.316 ms | 0.075 / 4.812 ms | 0.0397 |

All four runs had **zero new stream errors, buffer drops, late TX, retries, changed
payloads or unintended duplicates** during the workload. No ACK/control bursts were
generated; RX-only TX sample counts were zero. Submitted 2FSK bytes and sample
counts matched the reference framing at the configured samples/symbol. Minimum
idle gaps exceeded the configured 2 ms; these are device-target timing measurements,
not independently measured RF-envelope edges. Startup overflow indications preceded
the warm-up baseline and are excluded from the workload error deltas.

CPU is aggregate process time for both radios and the check's instrumentation,
not total workstation load. Decoder utilization is processing seconds divided by
received sample duration over the decoder lifetime, including startup and idle.
Processing p95/max are the final rolling window of at most 2,048 decoded IQ blocks,
not whole-run percentiles. Peak processing time can exceed
one input block's duration; the streaming buffers absorb those bursts. Sampled
post-step decoder output queue depth was zero in all four runs, with no output drops;
this is not an atomic high-water measurement. These observations support the tested
5-packet/s workload and do not justify a decoder/language rewrite or a higher-capacity
claim. No production receiver/modulator changes were needed for this reference set.

**409 affected tests passed in 13.05 seconds**, including **118 new rate/capture
cases**. The checks cover independent waveform/timing references, the receiver
impairment envelope, paired configuration, IQ/frame replay at the new rates, and
existing simple-link/hopping/CLI behavior. The full Step 6 suite remains the last
whole-repository coverage baseline; it was not rerun for this profiles/tests/tooling
change. Ruff formatting/lint, Mypy, protocol conformance and frozen fixture/profile
hash checks passed. The 20 legacy example/bench profiles remain unchanged.

A fresh development wheel installed outside the checkout passed every command entry
point, signed simulated delivery on all five legacy modulations, and maximum-payload
checks for generic/simple/hopping plus both new rate profile pairs. Package metadata
checks passed. Released 1.0.0 archives remain unchanged; development builds stayed
under `/tmp`. Temporary conducted reports and local bench overlays were removed;
only the final measurement summaries above are retained.

## Version 1.1 Step 8 — project-reference fidelity

`tools/fidelity_check.py` now checks every shipped emulation definition against
independent bytes and analytic two-tone IQ, rather than accepting a TX/RX round trip
alone. Simple frames use independent length/CRC bytes; generic-v5 uses all three
frozen signed DATA/ACK/SYNC wire and protected-envelope vectors. The rate reference
helper is shared with the impairment tests, not with production modem code. No
production waveform, packet or receiver algorithm changed in this step.

Tolerances were recorded in `CONFIGURATION.md` before the comparisons: exact bytes,
bits and sample count; relative rate error ≤1e-12; complex sample error ≤1e-5;
unit-envelope error ≤1e-6; mean tone-frequency error ≤1 Hz. Spectra use a rectangular
whole-burst FFT, zero-padded to a power of two at least four times burst length,
normalized to unit total power. Central 99% edges must agree within one FFT bin;
integrated absolute normalized power difference must be ≤1e-5.

All **19 independent waveform comparisons passed**:

| Definition | Reference bursts | Maximum complex sample error | Maximum mean tone error | Digital central 99% occupied bandwidth range | Maximum normalized spectral L1 error |
|---|---|---|---|---|---|
| Simple 250 kbit/s | 1/64/256/1024-byte payloads | 1.14e-13 | 0.0156 Hz | 508,422.852–561,759.949 Hz | 2.91e-17 |
| Simple 100 kbit/s | Same four sizes | 1.14e-13 | 0.00781 Hz | 200,988.770–214,069.366 Hz | 3.42e-17 |
| Simple 500 kbit/s | Same four sizes | 1.14e-13 | 0 Hz | 1,163,940.430–1,379,882.812 Hz | 4.14e-25 |
| Hopping 250 kbit/s | Same four sizes, baseband waveform | 1.14e-13 | 0.0156 Hz | 508,422.852–561,759.949 Hz | 2.91e-17 |
| Generic-v5 250 kbit/s | Frozen DATA/ACK/SYNC | 2.27e-13 | 0.00781 Hz | 533,752.441–539,367.676 Hz | 2.60e-18 |

These bandwidth ranges describe the tested byte patterns, not a universal spectral
mask or calibrated RF measurement. Simple payloads are ascending bytes modulo 256;
the hopping waveform comparison does not independently measure an LO hop. Exact
sample counts, tone polarity, derived rates, independent decode and all spectral
edge tolerances also passed. Mutation tests reject changed polarity, phase,
amplitude, tone frequency and truncated IQ; known-tone and scale-invariance tests
check the spectrum measurement helper.

Receiver tests retain the Step 7 envelope (±10 ppm sample-clock error, ±750 Hz
carrier offset, I/Q component noise standard deviation 0.04, seed 1101), including
fractional/integer onset and arbitrary blocks. New dropped-block tests remove four
symbols from the body at each rate and 1/64/256/1024-byte size, both with and without
timestamps. No spliced/altered payload is delivered; the following intact packet
is recovered, with timestamp error ≤500 ns when timestamps are present. New malformed
CRC, illegal length and truncated-frame cases also recover to the following packet.
Existing deterministic loss tests verify reliable-mode retransmission after ACK loss
and duplicate suppression; ACK-free tests verify intentional loss without retries.
Application minimum-gap/turnaround and hop admission/clock-recovery tests pass.

New conducted checks used the same B205mini serials 3457464/3457480 and crossed
30 dB attenuated paths, TX 20 dB/RX 40 dB, 915 MHz, 2 Msps and the reference rate
profiles. Each directional run offered 10 × 256-byte packets at 5/s plus five
boundary/repeated payloads, then muted one TX burst to zero IQ during a 17-second
signal outage. After restoration, one new recovery packet was offered. Each run
requires that packet within five seconds, without retransmitting the muted packet.
Forward input was payload bytes; reverse input was preframed bodies.

| Simple rate | A→B exact / recovery | B→A exact / recovery | Intentional muted packets |
|---|---|---|---|
| 100 kbit/s | **16/16**, 25.550 ms | **16/16**, 28.587 ms | One per direction, not retried |
| 250 kbit/s | **16/16**, 23.581 ms | **16/16**, 21.307 ms | One per direction, not retried |
| 500 kbit/s | **16/16**, 19.766 ms | **16/16**, 18.988 ms | One per direction, not retried |

All six simple runs had zero new stream errors/buffer drops/late TX after warm-up,
zero retries/control bursts, exact submitted bytes/sample counts, and RX-only TX
sample counts of zero. Device-target idle gaps remained above 2 ms. The observed
recovery delays are host delivery times after restoring nonzero TX IQ, not minimum
RF turnaround times. These checks supplement earlier delivery measurements; they
do not complete the separate five-minute fixed-profile sustained qualification.
Startup overflow indications were outside the measurement baseline. Muting IQ is
a signal-loss test, not cable/USB unplug-reconnect recovery.

A legacy signed generic-v5 2FSK bench regression delivered **50/50 packets each way**
in 10 seconds at 5/s, then recovered both directions **1.178 s** after a 17-second
zero-IQ outage. The follower entered recovery and reacquired. The traffic interval
had no retries; through recovery/shutdown, the leader retried twice and the follower
suppressed two duplicate frames. Post-warm-up stream-error/buffer-drop/late-TX deltas
were zero through shutdown. Startup RX buffer-drop counts (823/887) were already
present before the workload baseline and did not increase. This uses the existing
schema-1 bench timing settings and is separate from the reusable-profile check.

The reusable **schema-2 generic-2fsk-v5 profile** was then checked directly with
local serial/gain settings and its own portable timing defaults. It delivered
**50/50 packets each direction** in 10 seconds at 5/s, then recovered both directions
**1.413 s** after a 17-second zero-IQ outage. The follower entered recovery.
Post-warm-up stream-error, buffer-drop and late-TX deltas remained zero through
shutdown; startup buffer-drop/overflow baselines were also zero. There were two
leader retries during recovery and one follower duplicate suppression; no duplicates
were delivered in the audited traffic interval. Both devices shut down cleanly.
This directly covers the shipped reliable profile rather than inferring its timing
from the legacy bench configuration.

The previously measured Step 6 hopping table/rate and recovery remain the conducted
hop evidence; Step 8 reruns its scheduler, guard, missed-slot, stale-output and
whole-burst admission tests. The offline hopping comparison covers each burst's
baseband waveform, not calibrated LO timing. No new hopping rate is qualified here.

Fidelity status is deliberately separated:

| Behavior | Status and evidence |
|---|---|
| Project-defined bytes, polarity, rates, burst length, clean IQ | Passed independent reference checks for all five shipped emulation definitions |
| Digital occupied spectrum | Passed the declared normalized FFT comparison for 19 reference bursts |
| Receiver impairment/recovery envelope | Passed deterministic sample-domain and conducted checks described above |
| Application gap/turnaround and hop rules | Passed deterministic timing tests; conducted hop evidence retained from Step 6 |
| Calibrated RF spectrum, power, physical minimum turnaround/LO settling | Unqualified; digital/host timing is not substituted for these measurements |
| Custom framing/protection/rate variants | Software tests only unless a separate measured result is explicitly recorded |
| Independent-host timing and actual-target interoperability | Unqualified; no target/model/reference equipment has been selected |

**563 affected tests passed in 19.64 seconds**, including **48 new fidelity cases**.
Ruff formatting/lint, Mypy, frozen-file hashes and protocol conformance passed. A
fresh development wheel passed installed entry points, all five legacy waveform
checks, every reusable profile pair and the independent fidelity checker outside
the checkout. Full-suite coverage remains the earlier Step 6 baseline; this step
changes validation tools/tests, not production radio behavior.

Only final measurement summaries are retained here. Temporary conducted reports,
bench overlays and offline-check output were removed. Independent reference code
and the existing frozen protocol fixture remain reproducible test inputs. Released
1.0.0 archives were not replaced; development package checks used `/tmp` artifacts.


## Version 1.1.0 release acceptance — Step 9

Local wheel and source archive: `dist/packet_radio-1.1.0-py3-none-any.whl`
and `dist/packet_radio-1.1.0.tar.gz`. Package metadata and Python version constants
agree on **1.1.0**. The original 1.0.0 archives retain their SHA-256 hashes.
No public registry publication was performed.

- **1,440 tests passed in 427.48 seconds (7 minutes 7 seconds)** on Python 3.13.
  Combined line/branch coverage: **90.14%**; statements **92.67%** (4,425/4,775),
  branches **82.74%** (1,352/1,634). The configured minimum is 75% combined.
- Ruff formatting/lint, Mypy (45 production files), protocol-v5 conformance,
  and frozen fixture/all 20 legacy-config hashes passed.
- `pip-audit .` reported **no known vulnerabilities** in the resolved project
  dependency graph. Fresh-wheel `pip check` found no broken requirements.
- Wheel and sdist build and Twine metadata checks passed. CI selects the 1.1.0
  wheel explicitly so preserved older wheels do not conflict during installation.
- A fresh Python 3.13 environment outside the checkout passed all seven command
  help checks, bundled fixture/fidelity checks, all legacy profile validation and
  signed packet exchange for BPSK, 2FSK, MSK, GMSK and QPSK.
- All five schema-2 pairs passed installed CLI resolved validation and simulated
  payload delivery: generic-v5, simple 250 kbit/s, simple 100 kbit/s, simple
  500 kbit/s and hopping. Installed API and CLI replay recovered exact binary
  payloads from fixed-profile IQ and frames, and hopping decoded-frame captures.
  Generic capture checks use unsigned test packets; signed delivery is checked
  separately. Temporary keys and captures are removed by the checker.

Fresh-install runtime dependencies were cryptography 50.0.2, numpy 2.5.4,
PyYAML 6.0.3 and reedsolo 1.7.0 (with cffi 2.1.1 and pycparser 3.11).
Python 3.10–3.14 remains the configured CI matrix; this local release check does
not claim a fresh run on every interpreter. UHD remains host-provided.

This release step changes versioning, packaging verification and documentation;
production radio behavior is unchanged. Step 8's conducted generic-v5 regression
and short fixed-simple recovery runs, and Step 6's measured hopping qualification,
remain the applicable hardware evidence. No additional RF test was run for Step 9.
Durations and exact settings remain recorded in those summaries above. Independent
computers, commercial-target interoperability, calibrated RF fidelity, wider clock
tracking and non-integral resampling remain unqualified or explicitly deferred.
The fixed-simple short runs do not establish five-minute sustained qualification.


## B205mini comparison baseline before X310 migration

This is a short, repeatable characterization of the current 1.1 runtime, including
unsuccessful overload workloads. It is not a maximum-throughput determination or
a replacement for the earlier sustained qualifications. No radio algorithms or
bench configurations were changed. The simple-link diagnostic gained same-host
injection-to-delivery latency measurements for this comparison.

Hardware: A = B205mini **3457464**, B = B205mini **3457480**, crossed TX/RX →
30 dB attenuator → RX2. Both enumerated at USB 3, 5 Gbit/s, on bus 006 of the same
workstation. Host: Intel Core Ultra 7 265HX, 20 logical CPUs, Linux
7.0.0-38-generic x86_64. UHD 4.9.0.1-1ubuntu1, Python 3.14.4, NumPy 2.3.5,
using `/tmp/packet-radio-v1-uhd/bin/python` and `PYTHONPATH=.`.

Fixed-frequency runs: 915 MHz, 2 Msps, TX gain 20 dB, RX gain 40 dB;
1 MHz requested bandwidth except simple 500 kbit/s at 1.5 MHz. UHD readbacks
for the generic runs confirmed the requested rates, gains, bandwidth and frequency.
Generic settings are the unchanged `configs/bench/peer-{a,b}-{modulation}.yaml`:
250 kbit/s for BPSK/2FSK/MSK/GMSK, 500 kbit/s QPSK with RS parity 16.
Simple profiles use their supplied 100/250/500 kbit/s definitions, with only
hardware serial/gain overlays. All RF runs were sequential on this host.

### Reliable generic-v5 workload

Each modulation: **30 seconds**, 256-byte payloads, **5 packets/s/direction**
(10.24 kbit/s offered application payload each way), then a five-second drain.
All delivered **150/150 each way**, with no duplicate or altered application payloads.
A separate **10-second, 50-packet echo** workload measured round-trip latency.
Recovery follows **17 seconds of zero-valued TX IQ** with RX streams kept active;
it is not USB unplug/replug recovery. CPU values cover each process during the
30-second workload plus five-second drain, excluding startup and outage.

Direction pairs below are **A→B / B→A**; CPU pairs are **process A / process B**.

| Modulation | One-way p95 (ms) | Echo RTT p95 (ms) | CPU seconds A/B | Both-way recovery (s) |
|---|---:|---:|---:|---:|
| BPSK | 196.692 / 200.188 | 293.737 | 10.712 / 11.221 | 1.040 |
| 2FSK | 243.520 / 194.821 | 294.573 | 10.301 / 10.137 | 1.116 |
| MSK | 203.522 / 194.227 | 291.578 | 10.009 / 10.374 | 1.122 |
| GMSK | 209.410 / 198.662 | 299.373 | 9.713 / 10.015 | 1.046 |
| QPSK | 331.514 / 257.906 | 934.022 | 11.257 / 11.229 | 1.146 |

No new stream errors, buffer drops, late transmissions, transmit errors, queue drops
or shutdown errors occurred from the normal-workload baseline through recovery and
shutdown. Startup was excluded from that claim: the counters below were already
present at baseline. Acquisition is time from both hardware instances being ready
to application receipt, not total device-opening time. Decoder statistics are
rolling processing diagnostics, not whole-packet latency.

| Modulation | Acquisition at A/B (ms) | Startup RX buffer drops A/B | Decoder p95 at A/B (ms) | Decoder max at A/B (ms) |
|---|---:|---:|---:|---:|
| BPSK | 49.035 / 176.383 | 1003 / 949 | 0.058 / 0.114 | 0.611 / 12.764 |
| 2FSK | 158.905 / 32.120 | 1200 / 953 | 0.115 / 0.118 | 1.079 / 5.313 |
| MSK | 196.150 / 87.931 | 1183 / 947 | 0.110 / 0.108 | 1.579 / 5.770 |
| GMSK | 42.750 / 174.483 | 732 / 951 | 0.059 / 0.062 | 0.813 / 5.536 |
| QPSK | 212.247 / 120.973 | 1206 / 934 | 0.131 / 0.064 | 1.127 / 6.675 |

### Maximum-payload loading limit

The heavier workload offered **25 × 1,024-byte packets per direction over five
seconds** (5 packets/s/direction), followed by five seconds to drain. **All five
modulations failed complete delivery within that observation window**, with queued
packets remaining. Counts below are received by the deadline, not measured RF packet
loss. Received payloads were exact and unique. Their multi-second latency exposes
queueing under the current link policy; replacing hardware alone has not been
shown to remove this limit. Do not treat the tool's delivered-bytes divided by
five seconds as sustainable throughput, since delivery extends into the drain.

A separate lower-load check offered **10 maximum-size packets per direction over
10 seconds** at 1 packet/s/direction, plus a five-second drain. All delivered 10/10
each way. Retry pairs below are process A/B during each workload and drain.

| Modulation | Heavy delivered A→B / B→A (of 25) | Heavy p95 A→B / B→A (s) | Heavy retries A/B | Low-load p95 A→B / B→A (ms) | Low-load retries A/B |
|---|---:|---:|---:|---:|---:|
| BPSK | 18 / 18 | 6.205 / 6.222 | 0 / 2 | 419.892 / 714.716 | 0 / 0 |
| 2FSK | 17 / 19 | 5.973 / 6.027 | 0 / 0 | 469.153 / 462.661 | 0 / 0 |
| MSK | 19 / 21 | 6.165 / 5.346 | 0 / 0 | 496.827 / 724.988 | 0 / 0 |
| GMSK | 17 / 21 | 6.490 / 5.367 | 0 / 0 | 1912.413 / 1342.926 | 0 / 0 |
| QPSK | 22 / 16 | 5.161 / 6.655 | 2 / 5 | 1680.627 / 1682.016 | 2 / 2 |

### ACK-free simple 2FSK

Each rate runs the directions sequentially: 150 × 256-byte packets at 5 packets/s
(about 30 seconds), then five boundary/repeated payloads (1, 64, 1,024 bytes,
plus two identical payloads), one intentionally muted packet during a 17-second
outage, and one fresh recovery packet. Expected successful count: **156 each way**;
the deliberately muted packet must not arrive or retry. A→B uses payload input;
B→A uses preframed input. Timing hooks verify exact application delivery and
submitted waveform bytes/sample counts. Latency statistics use only the 150 steady
packets. CPU totals include both local app threads, boundaries and outage, unlike
the generic two-process workload CPU metric; compare only like-for-like runs.

| Rate (kbit/s) | Direction | Exact delivery | Steady latency min/p95/max (ms) | CPU / elapsed (s) | Recovery (ms) | Decoder p95/max (ms) | Min device-target gap (ms) |
|---|---|---:|---:|---:|---:|---:|---:|
| 100 | A→B | 156/156 | 42.718/49.744/50.969 | 16.585 / 47.867 | 28.476 | 0.411/5.094 | 106.298 |
| 100 | B→A | 156/156 | 42.017/49.002/49.907 | 16.511 / 47.862 | 28.232 | 0.336/5.170 | 107.362 |
| 250 | A→B | 156/156 | 27.262/31.745/32.248 | 17.399 / 47.860 | 21.008 | 0.327/3.206 | 160.490 |
| 250 | B→A | 156/156 | 27.123/31.271/32.862 | 19.668 / 47.857 | 22.724 | 0.381/3.566 | 162.968 |
| 500 | A→B | 156/156 | 19.741/25.702/26.140 | 14.915 / 47.849 | 21.075 | 0.087/2.761 | 179.480 |
| 500 | B→A | 156/156 | 21.568/25.294/26.250 | 18.217 / 47.855 | 21.215 | 0.119/3.257 | 182.358 |

All simple runs passed no-ACK/no-retry behavior, zero RX-only transmitted samples,
zero new stream errors after warmup, close/reopen and clean shutdown. Startup
UHD overflow indications were outside the measured baseline. Decoder queue depths
are sampled after app steps, not an atomic high-water mark. The device-target gap
is scheduled burst spacing at this offered load, not minimum physical turnaround.

### Reusable schema-2 generic-v5 cross-check

The paired `configs/v1_1/peer-{a,b}-generic.yaml` profiles, with serial/gain
overlays, delivered **50/50 packets each way** over 10 seconds at 5 packets/s,
256 bytes each. P95 latency: **51.882 ms A→B / 61.767 ms B→A**. Process CPU
seconds over workload plus five-second drain: **3.497 A / 4.480 B**. No duplicates,
altered payloads, retries or new stream errors occurred in the measured workload.
These profiles use different scheduling/ACK settings from the schema-1 bench
profiles; their lower latency is not a hardware improvement. Keep both baselines
separate when comparing the X310. Signal recovery for this definition retains the
Step 8 measurement rather than repeating it in this cross-check.

### Hopping timing

Same-host external-UTC schedule: **914 → 915 → 916 MHz**, 1 hop/s, 100 ms guard,
250 kbit/s simple 2FSK, 2 Msps. **60 seconds** of one 256-byte packet per direction
per dwell, followed by **six seconds** of 1/64/1,024-byte boundary payloads and
six deliberately rejected crossing bursts, then **three seconds off-channel** on
B and three recovery dwells. Both received **60/60 steady packets and 69/69 total
expected packets**. There were zero steady missed hops/deadlines, zero new stream
errors, and no ACK/SYNC/retry traffic. A rejected all six deliberately crossing
bursts. Recovery-phase missed-slot counters were A=1, B=3; these are reported
separately from the zero-miss steady interval.

| Device | Retune min/p95/max (ms) | Latest restart phase (ms) | First buffered RX block phase min/max (ms) | Earliest successful TX target phase (ms) |
|---|---:|---:|---:|---:|
| A | 18.399 / 29.624 / 31.199 | 34.366 | 28.932 / 37.543 | 211.605 |
| B | 19.556 / 29.674 / 30.177 | 32.965 | 28.875 / 36.425 | 511.288 |

Phases are within the one-second dwell; directional traffic was intentionally
submitted at phases 0.2 and 0.5 seconds. These observations measure the host-driven
stop/drain/tune/restart path, not minimum LO settling or maximum hopping speed.
The existing 1-hop/s qualification limit is unchanged.

### Reproducing the comparison

Use the same host, runtime dependencies, packet sizes, offered loads, scheduling,
FEC, rate definitions and observation windows after adapting device/channel
ownership for the X310. Record its daughterboards, Ethernet link, actual UHD
settings and shared-clock arrangement. Gain numbers on different RF hardware do
not establish equal conducted power. Shared-clock X310 measurements must not be
presented as independent-clock/two-host qualification.

Run these commands sequentially, using the UHD Python with `PYTHONPATH=.`:

```bash
# Repeat for bpsk, 2fsk, msk, gmsk, qpsk:
python tools/uhd_link_check.py --modulation bpsk --duration 30 --interrupt-seconds 17
python tools/uhd_link_check.py --modulation bpsk --duration 10 --echo
python tools/uhd_link_check.py --modulation bpsk --duration 5 --payload 1024
python tools/uhd_link_check.py --modulation bpsk --duration 10 --payload 1024 --rate 1
# Use schema-2 node copies with absolute profile paths, bench serials and gains;
# repeat simple, simple-100k and simple-500k, then generic:
python tools/simple_link_check.py --profiles /tmp/a-simple.yaml /tmp/b-simple.yaml --count 150 --interval .2 --interrupt-seconds 17
python tools/uhd_link_check.py --profiles /tmp/a-generic.yaml /tmp/b-generic.yaml --duration 10
python tools/hopping_check.py --seconds 60
```

Heavy maximum-payload runs intentionally retain nonzero check exits as baseline
loading failures. Other runs passed. Generic normal-traffic runs had zero retries;
QPSK echo had one follower retry. No new stream/queue/shutdown errors occurred in
any generic run after its baseline, including the overloaded runs. All simple
sampled decoder queue maxima were zero; this does not imply the queue was always
empty between samples. Ruff formatting/lint passed for the diagnostic change,
and its new timing measurements ran successfully in all six simple directions.
No additional status files or raw qualification JSON are retained.

Limits: single-run short-workload measurements, no confidence intervals, no
maximum USB/Ethernet streaming-rate sweep, no calibrated RF power/spectrum/BER
measurement, no over-air or independent-computer qualification. The X310 has not
yet been tested; these numbers establish the B205 reference only. Released wheel
and source archives remain unchanged.

Benchmark start (UTC): **2026-10-14T18:59:14+00:00**. Runtime-source SHA-256
(sorted `packet_radio/**/*.py`, each relative path + NUL + bytes + NUL):
`7a010083af74573ddd3e3bf2e326a2c415db34a4280b7c32b9c2a3d9c10c9ace`.


## X310 connection recovery and hardware identification

X310 serial **3267DE0**, revision 12, became reachable at **192.168.10.2** after
loading the installed `usrp_x310_fpga_HG.bit` temporarily over USB JTAG. Before
that load, the FPGA reported completed startup without a configuration CRC error,
but the Ethernet connection produced no received packets. The previous image's
exact identity was not established; it is not proven to have been XG or corrupt.

UHD 4.9.0.1 successfully probed both **UBX-160 v2** transceiver daughterboards:
A serial **325AEBD**, B serial **325AF46**. Reported FPGA version **39.3**, FPGA
hash **8e780be**, firmware **6.1**; no GPSDO detected. Both daughterboards report
10 MHz–6 GHz tuning and fixed 160 MHz analog bandwidth. Transport is currently
**1-Gigabit Ethernet**, port 0. No RF transmission or X310 performance benchmark
has been performed. The existing application's bandwidth checks and shared-device
ownership still need adaptation before treating this as a qualified replacement.

The prior flash image was successfully backed up to
`/home/dev5/Documents/Surrogate-baselines/x310-3267DE0-before-hg-recovery.bit`
(SHA-256 `448d7906f3c932ea2b5f2f62da4ed3ebe544fee9b52c40d7bb8fcfb31d8bd0d0`).
UHD then completed writing the working HG image to flash successfully. Source:
`/usr/share/uhd/images/usrp_x310_fpga_HG.bit`, SHA-256
`f895f432e870165cd79a85070581553a685a23ff9658ac1293cd9a1576962446`.
**Cold-start verification passed** after the user power-cycled the X310 with
USB JTAG disconnected. UHD discovery identified serial 3267DE0 with HG, and
`uhd_usrp_probe` exited successfully with both UBX-160 v2 daughterboards, FPGA
39.3/hash 8e780be and firmware 6.1. The host retained 192.168.10.1/24. UHD still
reported a host UDP send-buffer limit (1,048,576 bytes versus 2,453,333 requested);
streaming performance has not yet been tested.

Host NetworkManager profile `X310 direct` sets `enp132s0` to 192.168.10.1/24,
MTU 1500 inherited from the interface, no default route, IPv6 disabled, automatic
connection priority 50. The original `netplan-enp132s0` profile remains available;
Wi-Fi retains the default route. The USB JTAG target was closed after temporary
programming, and the verification restart was performed without USB JTAG attached.

## X310 two-channel comparison — 2026-10-14

Measured on X310 **3267DE0**, UBX-160 v2 boards **325AEBD** (A/channel 0) and
**325AF46** (B/channel 1), HG FPGA, **1 Gb Ethernet**, 192.168.10.2. The user
confirmed crossed TX/RX-to-other-RX2 cables with **30 dB attenuation in each path**.
The host, UHD/Python/NumPy versions, waveform definitions, offered loads and drain
windows match the B205 baseline above. Both channels run at **915 MHz, 2 Msps,
TX gain 0 dB, RX gain 0 dB**. The fixed UBX analog bandwidth reads **160 MHz**;
requested waveform bandwidth remains 1 MHz (1.5 MHz for simple 500 kbit/s).
These gains are not calibrated to the B205's conducted power.

This implementation uses one `MultiUSRP` with two independently buffered channel
streams and a single UTC clock initialized before reception. Generic applications
run in two threads in one process, whereas the B205 generic baseline used two
processes. CPU below is the **whole shared process**, including both endpoints
and the UDP driver; it must not be doubled. These architectural and clock
changes prevent attributing every difference to the SDR model alone.

Initial diagnostic runs found clipping at TX 10/RX 30 dB and a decoder buffering
bug: a slowly decaying signal tail could evict a maximum packet's preamble before
searching it. The decoder now searches before trimming a full live-burst buffer.
A deterministic regression fails with the old logic and passes with the fix;
the captured 1,024-byte packet also decodes in streaming chunks after the fix.
The final runs below use the fix, no experimental extra TX padding, and 0/0 dB
gains. A captured 0/0 dB receive burst peaked near 0.037 in normalized IQ magnitude.
The B205 has not been remeasured with this decoder change.

### Reliable generic-v5 results

Same 30-second, 150 × 256-byte, 5-packet/s/direction workload plus five-second
drain. Direction pairs are **A→B / B→A**. Latency percentiles include only packets
received before the deadline; partial-delivery runs are not latency successes.
The B205 delivered 150/150 each way for every modulation with roughly
194–332 ms one-way p95 under this workload.

| Modulation | Delivered each way (of 150) | One-way p95 (ms) | Shared-process CPU (s) | Fresh-packet recovery after 17 s mute |
|---|---:|---:|---:|---:|
| BPSK | 95 / 90 | 15152.946 / 15841.597 | 21.032 | Not within 5 s; prior backlog remained |
| 2FSK | 150 / 150 | 863.177 / 278.460 | 20.814 | 1.132 s |
| MSK | 150 / 150 | 3890.225 / 3241.987 | 20.546 | 1.263 s |
| GMSK | 150 / 150 | 1615.150 / 703.301 | 21.037 | 1.353 s |
| QPSK | 149 / 150 | 5069.919 / 3916.460 | 22.249 | 1.122 s |

Normal-window retries A/B: BPSK 8/14, 2FSK 2/0, MSK 0/8 (one follower retry
exhaustion), GMSK 0/0, QPSK 0/0. MSK recorded **34 late TX reports on A** during
that window. The other normal windows had no measured UHD stream errors.
After the normal window, during outage/recovery/shutdown, A recorded one TX
underrun each for BPSK, 2FSK and MSK; 2FSK also recorded one late TX report.
Those runs therefore are **not clean overall stream passes**, even when packet
recovery succeeded. QPSK's 149/150 result meets the tool's 99% delivery threshold
but does not match the B205's exact delivery. BPSK's recovery result is confounded
by its existing queue; it is not evidence of a failed hardware reconnection.

Separate ten-second echo workload: 50 submitted packets, five-second drain.
RTT p95 is measured at A from the subset of returned echoes. The benchmark's
RTT requirement is ≤1 second.

| Modulation | Echoes returned to A (of 50) | RTT p95 (ms) | B205 RTT p95 (ms) |
|---|---:|---:|---:|
| BPSK | 37 | 7213.895 | 293.737 |
| 2FSK | 50 | 504.502 | 294.573 |
| MSK | 50 | 496.012 | 291.578 |
| GMSK | 50 | 3737.055 | 299.373 |
| QPSK | 50 | 2485.071 | 934.022 |

GMSK echo recorded 33 late TX reports on A, four follower retries and one retry
exhaustion. BPSK echo had 8/1 retries; other echo runs had zero retries and stream
errors. No delivered generic payload was duplicated or altered.

### Maximum-payload loading

Heavy: 25 × 1,024-byte packets per direction over five seconds plus five-second
drain. Lower load: ten packets per direction over ten seconds at 1 packet/s,
plus five-second drain. Counts reflect delivery by that deadline, including
queued backlog; they are not a raw RF loss measurement or a sustainable-rate
measurement. All heavy runs failed complete delivery on both hardware setups.

| Modulation | Heavy delivered (of 25), A→B / B→A | Heavy p95 (s) | Low-load delivered (of 10) | Low-load p95 (ms) |
|---|---:|---:|---:|---:|
| BPSK | 8 / 8 | 8.551 / 6.216 | 9 / 10 | 6337.101 / 5626.286 |
| 2FSK | 8 / 9 | 7.342 / 7.980 | 10 / 10 | 1959.042 / 4625.073 |
| MSK | 7 / 11 | 7.950 / 7.772 | 10 / 10 | 3676.850 / 2336.261 |
| GMSK | 9 / 10 | 8.289 / 7.988 | 10 / 10 | 2583.372 / 2325.801 |
| QPSK | 22 / 22 | 5.524 / 5.111 | 10 / 10 | 351.971 / 302.737 |

Heavy/low-load retries were zero. Additional transport failures: 2FSK lower-load
A had one underrun and one late TX; MSK lower-load A had two underruns. QPSK heavy
B had two underruns in the window and one more before shutdown. No new RX
overflows, RX buffer drops, RX stream errors, decoder-output drops, TX timeouts
or TX stream errors occurred after the generic workload baselines. Shutdown
error counters were zero throughout. The checker now includes transport errors
after the workload window in its overall verdict, so recovery errors cannot be
hidden by an earlier clean stream snapshot.

QPSK at the lower load is a measured improvement: about 303–352 ms one-way p95
versus about 1,681–1,682 ms on the B205 baseline, with exact delivery. That result
does not outweigh the inconsistent normal-load and echo behavior across modes.

### ACK-free simple 2FSK results

Same 150 steady packets at 5 packets/s, five boundary/repeat packets, one muted
packet during a 17-second outage, and one recovery packet. Each direction
passed **156/156 exact deliveries**, exact submitted waveform bytes/sample counts,
no ACK/retry traffic, zero RX-only transmitted samples, no new stream errors,
close/reopen and clean shutdown. Direction A→B uses payload input and B→A uses
preframed input. CPU includes both app threads, boundary checks and the outage.

| Rate (kbit/s) | Direction | Latency min/p95/max (ms) | B205 p95 (ms) | CPU / elapsed (s) | Recovery (ms) | Decoder p95/max (ms) | Min target gap (ms) |
|---|---|---:|---:|---:|---:|---:|---:|
| 100 | A→B | 51.784/61.460/62.722 | 49.744 | 20.785 / 47.908 | 48.469 | 0.115/10.007 | 105.398 |
| 100 | B→A | 53.338/60.383/61.913 | 49.002 | 19.760 / 47.901 | 48.308 | 0.374/6.972 | 108.167 |
| 250 | A→B | 27.555/34.889/37.181 | 31.745 | 18.465 / 47.899 | 39.327 | 0.172/6.845 | 159.468 |
| 250 | B→A | 27.423/34.983/38.352 | 31.271 | 19.408 / 47.884 | 40.506 | 0.109/7.340 | 162.391 |
| 500 | A→B | 26.180/34.043/35.778 | 25.702 | 19.953 / 47.886 | 36.448 | 0.387/5.285 | 178.062 |
| 500 | B→A | 25.205/33.718/35.409 | 25.294 | 19.424 / 47.881 | 36.712 | 0.134/5.027 | 181.629 |

All sampled decoder queue maxima were zero; these are sampled depths, not atomic
high-water marks. Minimum target gaps reflect this offered load, not the hardware's
minimum possible turnaround. The X310 did not improve simple-profile p95 latency
at any of these rates.

### Reusable schema-2 generic-v5 cross-check

The same ten-second, 50-packet/direction schema-2 generic profile delivered
50/50 A→B and 50/50 B→A. One-way p95 was
**66.937 / 65.702 ms**, compared with **51.882 / 61.767 ms** on B205.
Shared-process CPU was **9.333 s**
over the 15-second workload-plus-drain window; B205 process CPU was 3.497/4.480 s.

No duplicate or altered payloads, retries, new stream errors, or shutdown errors
were observed. This profile has a different link schedule from the legacy bench
profiles above; its latency must be compared with the matching B205 schema-2
result, not with a different modulation or scheduling policy.


### Hopping and transport

The matched simple-2FSK hopping workload used 914/915/916 MHz, one hop/s,
100 ms boundary guards, 60 steady dwells, six boundary checks, three off-channel
interruption dwells and three recovery dwells. Both directions delivered
**60/60 steady and 69/69 total exact packets**, with no stream errors, missed
steady hops/deadlines or ACK/SYNC/retry traffic. A rejected all six intentionally
late boundary bursts. Recovery missed-slot counters were A=0/B=3, the latter
reflecting the deliberate pause of B.

| Metric | X310 A / B | B205 A / B |
|---|---:|---:|
| Retune min (ms) | 12.000 / 12.263 | 18.399 / 19.556 |
| Retune p95 (ms) | 23.910 / 23.720 | 29.624 / 29.674 |
| Retune max (ms) | 24.534 / 24.795 | 31.199 / 30.177 |
| Latest restart phase (ms into dwell) | 27.814 / 27.789 | 34.366 / 32.965 |
| First buffered RX block phase min–max (ms) | 15.675–30.842 / 15.946–30.980 | 28.932–37.543 / 28.875–36.425 |
| Earliest successful TX target phase (ms) | 212.012 / 512.154 | 211.605 / 511.288 |

X310 retunes were about 19–20% faster by p95 in this short run. The target phases
are imposed by the test schedule, not measurements of minimum physical turnaround
or maximum hopping rate. No higher hop-rate envelope was qualified.

A separate **10-second UHD C++ transport check** exercised simultaneous RX and TX
on both channels at 2 Msps per channel, with separate streamers and fc32/sc16
formats. It received 40,998,320 and submitted 40,248,208 aggregate samples,
with **zero dropped samples, overruns, underruns, TX/RX sequence errors, late
commands or TX/RX timeouts**. Counts include the tool's start/stop boundaries;
this is continuous sample transport, not packet decoding or a maximum-rate sweep.

```bash
/usr/libexec/uhd/examples/benchmark_rate \
  --args type=x300,addr=192.168.10.2 --channels 0,1 --multi_streamer \
  --rx_rate 2000000 --tx_rate 2000000 --duration 10 \
  --overrun-threshold 0 --underrun-threshold 0 --drop-threshold 0 --seq-threshold 0
```

UHD warned that the host TX socket buffer remained 1,048,576 bytes versus its
2,453,333-byte request. Host kernel buffer limits were not changed. The clean
continuous transport check demonstrates this setup can carry the tested sample
rates; it does not establish the cause of the application burst-timing failures.

### Comparison decision and reproduction

**The X310 is not an overall speed/reliability upgrade for this application's
current workload.** The B205 reference had lower normal reliable-link latency
and cleaner burst timing; it also had lower simple-profile p95 at every tested
bit rate. X310 advantages measured here are shorter retunes and lower-load QPSK
latency. Simple profiles, the schema-2 generic profile and one-hop/s hopping all
worked on both X310 channels. Retain the B205 results as the reference and treat
the shared X310 reliable-link path as experimental until its timing failures and
backlog are resolved. Investigate burst submission/deadline margins and the legacy
link schedule before raising bit rates or assuming Ethernet bandwidth is the cause.

Repeat the B205 recipes above with `--shared-device --profiles NODE_A NODE_B`
in place of `--modulation MODE`. For schema 1, copy each bench node and change
only `radio.device` to `{type: x300, addr: 192.168.10.2}`, `radio.channel` to 0/1,
and `radio.tx_gain_db`/`rx_gain_db` to zero. For schema 2, make the equivalent
`hardware.device`, `channel`, `tx_gain`, `rx_gain` edits and preserve the referenced
profile (use an absolute path if placing the node copy in `/tmp`). The supplied
`configs/v1_1/peer-{a,b}-x310-simple.yaml` pair provides the 250 kbit/s example.
For hopping use `python tools/hopping_check.py --shared-address 192.168.10.2
--seconds 60` on one command line. All comparison workloads ran sequentially without the
software suite competing for CPU. Temporary overlays, raw IQ, logs and JSON were
used for analysis; only final summaries are retained in the repository.

These are short single-run conducted measurements, without confidence intervals,
calibrated RF power, spectrum/BER qualification, independent oscillators,
independent computers, over-air propagation, 10 Gb Ethernet or a maximum sample-rate
sweep. Shared clocks and a single process are part of the X310 comparison, not
proof of performance between separate radios in the field.

Final runtime-source SHA-256 (same method as B205):
`c69ad250f1184446f1c1c12527f8490147ff5e5606c11a1c30cbbff6d5794a18`. Released 1.0.0/1.1.0 archives were not rebuilt.

The public paired launcher was also checked with the supplied X310 node profiles:
three real UDP payloads each way arrived exactly, followed by SIGTERM, both
endpoints closing, and process exit 0. This smoke check is separate from the
performance measurements.

Software validation after these changes: **1,457 tests passed in 445.71 seconds**;
combined line/branch coverage **90.23%**. Ruff lint and formatting passed, mypy
passed for all 46 runtime source files, and `git diff --check` passed. Regression
coverage includes shared ownership/clock protection, peer failure cleanup,
fixed analog bandwidth, the long signal-tail decoder failure, paired CLI status
options, cooperative benchmark shutdown and post-workload transport failures.


## X310 Step 1 timing investigation — 2026-10-15

Short diagnostic runs used X310 3267DE0 at 192.168.10.2, the existing crossed
30 dB attenuated paths, shared channels 0/1, 915 MHz, 2 Msps, 0/0 dB TX/RX gain,
and legacy authenticated generic-v5 bench profiles at 250 kbit/s. Only device,
channel and gains were adapted from the B205 profiles. TDD period was 180 ms,
guard 2 ms, ACK timeout 250 ms and timed-data lead 10 ms. Each run submitted
75 × 256-byte payloads per direction over 15 seconds, followed by a 5-second
drain. No interruption/recovery or full qualification matrix was run.

Rows are in execution order. Delivery and p95 pairs are **A→B / B→A**.
Latency describes only delivered packets, so incomplete BPSK runs fail delivery.
CPU is for the entire shared process during workload plus drain.

| Run | Diagnostics | Delivered (of 75 each) | One-way p95 (ms) | CPU (s) | Workload stream result |
|---|---|---:|---:|---:|---|
| bpsk-off | off | 53 / 52 | 9005.446 / 9450.573 | 11.985 | Zero stream errors |
| bpsk-on | on | 57 / 55 | 8275.479 / 8099.302 | 12.409 | Zero stream errors |
| msk-on | on | 75 / 75 | 692.818 / 373.068 | 11.873 | A: 1 underrun, 1 UHD late event |
| bpsk-on-repeat | on | 56 / 56 | 8666.077 / 8574.250 | 12.334 | Zero stream errors |
| bpsk-off-repeat | off | 52 / 46 | 9282.511 / 10207.455 | 12.185 | Zero stream errors |

All five runs shut down cleanly. There were no workload RX overflows, RX buffer
drops, decoder output drops, changed payloads or duplicate UDP deliveries.
No additional post-workload stream errors were reported. BPSK retry counts A/B,
in row order excluding MSK, were 3/9, 6/7, 4/9 and 7/16; no retry exhaustion.
The UHD socket-buffer warning remains (requested 2,453,333 bytes, actual
1,048,576); no host transport settings changed.

### BPSK contributors

The first instrumented run measured these **workload-window means**, calculated
from before/after aggregate counts and means:

| Metric | Channel A | Channel B |
|---|---:|---:|
| Encoding | 0.076 ms | 0.069 ms |
| Modulation | 1.311 ms | 1.605 ms |
| UHD submission including EOB | 4.057 ms | 3.687 ms |
| Data send calls per attempt | 85.436 | 87.500 |
| UHD completion wait | 19.498 ms | 19.924 ms |
| Radio transmit call | 26.125 ms | 26.617 ms |
| Recognized decoder batch queue residence | 5.649 ms | 5.455 ms |
| Latest TX completion to accepted ACK | 199.423 ms | 202.944 ms |
| Enqueue to accepted ACK, including queue residence/retries | 4207.162 ms | 3914.270 ms |

At the deadline, 19/20 admitted data packets remained pending (queue depth
includes in-flight data), with oldest pending ages 8.800/9.003 seconds.
The repeat instrumented run again left 20/19 pending and had approximately
197–213 ms mean completion-to-ACK intervals. This reproduces backlog without
stream loss, rather than establishing loss of every missing UDP packet.

RX decode calls reached lifetime maxima of 199/132 ms in the first run and
189/143 ms in the repeat, including startup. These maxima are not workload-only
percentiles and do not prove every slow decode produced a valid packet.
Combined with one in-flight data packet per peer and 180 ms TDD scheduling,
the measurements point to receive/ACK turnaround and waiting for transmission
opportunities as contributors to service falling below the offered 5 packets/s.
Milliseconds of encoding/submission alone do not explain multi-second queue
ages. More detailed receive and scheduling work remains in Steps 3–5.

There were 44–131 UHD data send calls per attempt, with full chunks of just
364 samples. Step 2 therefore has a measurable batching opportunity, but reducing
send-call overhead alone is not established as a complete backlog fix.

### Late transmission and measurement overhead

MSK delivered every payload but failed clean-stream acceptance. Channel A had
one asynchronous UHD late event and one underrun during the workload, with
**zero host-side late rejections**. B had neither. First-submission hardware lead
on A reached a lifetime minimum of 0.928 ms, versus 6.887 ms workload mean;
submission reached 6.908 ms lifetime maximum. Positive lead at first submission
does not guarantee timely delivery of the entire burst. These aggregates identify
tight margins and fragmented submission as candidates; they do not associate
the minimum with a particular error or prove that both errors belong to one
burst. Error-specific correlation and deadline budgeting remain Step 3 work.

The two BPSK diagnostics-on runs averaged 12.372 CPU seconds versus 12.085
with diagnostics off, about 2.4% higher observed shared-process CPU. Delivery
also varied, so this is a coarse overhead check, not an isolated causal estimate.
Diagnostics remain opt-in; use the same setting for subsequent comparisons.
Neither setting eliminated backlog, and instrumentation is not a performance fix.

**Decision:** the short reproducer and timing baseline are sufficient to begin
Step 2's offline batching work and subsequent hardware comparison. Step 1
measurement gate is complete; performance acceptance is not. No radio defaults,
modulation algorithms, queue policy or transmission scheduling were changed.

A diagnostic-only follow-up fixed empty reliable queues reporting a few
microseconds of age from two separate host-clock reads. An advancing-clock
regression and the related timing/reliability tests passed (23 tests); this
does not change packet timing. Temporary benchmark JSON/logs and generated
profiles were removed after extracting these summaries.


## X310 Step 2 transmit batching experiment — 2026-10-15

**Decision: retain one transport packet per send as the runtime default.**
Larger batches reduced Python send-call count and submission duration, but the
third bounded BPSK repeat produced 46 asynchronous UHD late events. The batching
implementation remains available through the benchmark's explicit
`--shared-device --tx-batch-packets 16` option; it is not a qualified default.
Step 2 implementation/experiments are finished, but its clean-stream acceptance
gate remains open for Step 3's deadline investigation. B205 physical revalidation
was not performed, and its submission size remains unchanged.

The installed UHD 4.9 header and
[UHD send contract](https://files.ettus.com/manual_archive/v4.9.0.0/html/classuhd_1_1tx__streamer.html)
allow internal fragmentation of larger host buffers. Compared one-packet,
16-packet (5,824 samples) and whole-burst host submissions on the same X310/UBX,
915 MHz, 2 Msps, 0/0 dB gains and crossed 30 dB paths as Step 1. All runs enabled
timing diagnostics. Scheduling, modulation, payload bytes and existing idle
samples were unchanged. No competing test suite ran during RF measurements.

Normal rows used 15 seconds, 75 × 256-byte packets each way, 5 packets/s and
5 seconds drain. The maximum-payload row used 10 seconds, ten × 1,024-byte
packets each way, 1 packet/s and the same drain. Rows are execution order;
direction pairs are **A→B / B→A**. Incomplete runs fail delivery; their latency
percentiles only describe packets delivered before the deadline.

| Run | Batch size | Delivery | One-way p95 (ms) | Shared CPU (s) | Workload stream errors |
|---|---|---:|---:|---:|---|
| bpsk-single | 1 packet | 56 / 54 of 75 | 8128.131 / 8700.015 | 12.404 | A: 1 tx_underflows; A: 1 timed_transmit_late |
| bpsk-bounded | 16 packets | 48 / 53 of 75 | 9538.086 / 9169.492 | 12.254 | None |
| bpsk-whole | whole burst | 53 / 52 of 75 | 9079.913 / 9146.030 | 12.422 | None |
| msk-bounded | 16 packets | 75 / 75 of 75 | 1237.908 / 283.990 | 11.805 | None |
| bpsk-max | 16 packets | 10 / 10 of 10 | 4714.993 / 4532.672 | 7.786 | None |
| bpsk-bounded-repeat2 | 16 packets | 53 / 54 of 75 | 8804.676 / 8833.367 | 12.364 | None |
| bpsk-bounded-repeat3 | 16 packets | 41 / 42 of 75 | 7312.681 / 9354.422 | 11.958 | A: 46 timed_transmit_late |

All seven runs shut down cleanly and reported no additional post-workload stream
errors, RX drops/overflows, changed payloads or duplicate UDP deliveries. The
last BPSK run also had two retry exhaustions on B. Its 46 late reports were UHD
events on A, not host-side late rejections, and must not be interpreted as 46
distinct failed bursts: fragmented transmissions can generate multiple events.
The original-size control also had an underrun and a late event, so this
experiment does not establish that batching caused the repeat failure.

### Submission measurements and interpretation

Values below are workload-window means derived from before/after aggregate
counts and means; pairs are channel A/B and include data/control bursts.

| BPSK case | Data send calls per attempt | UHD submission including EOB (ms) | Total UHD burst call (ms) |
|---|---:|---:|---:|
| bpsk-single | 84.150 / 88.213 | 4.328 / 3.978 | 23.091 / 23.879 |
| bpsk-bounded | 5.762 / 6.158 | 1.994 / 2.121 | 23.014 / 24.323 |
| bpsk-whole | 1.000 / 1.000 | 1.629 / 1.478 | 22.906 / 24.201 |

The 16-packet candidate used 3–9 data calls per normal burst instead of 44–131;
maximum-sized BPSK packets used up to 26 calls. First bounded submission means
were roughly half the one-packet control. Whole-burst submission reduced this
further, but neither approach established a packet-latency benefit or resolved
backlog. Total burst calls stayed around 23–25 ms because earlier submission
leaves more time waiting for the scheduled transmission/completion. CPU results
do not establish a substantial overall saving. This is why the default was
not promoted based on send-call timing alone.

MSK's bounded run delivered 75/75 each way with no stream errors, but A→B p95
was 1.238 seconds. The low-load maximum-payload BPSK run delivered 10/10 each
way without stream errors, still with 4.5–4.7-second p95 latency. Both are
useful preservation checks, not broad performance qualification.

The implementation retains an owned IQ copy, partial-send offsets, first-transfer
timestamp/start flags, a separate empty EOB, per-burst deadline checks and
asynchronous error reporting. The per-packet UHD timeout is divided across a
batch's fragments to avoid multiplying the remaining blocking-time budget.
Tests cover sample ownership, tiny/large and boundary-sized buffers, partial
acceptance across batches, exceptions, invalid counts, deadline expiry, metadata,
idle samples and the experimental runner option. Final targeted validation:
112 tests, Ruff lint/format and mypy passed.

**Next:** investigate hardware submission deadlines and late-event circumstances
in Step 3, retaining this failed repeat as evidence. Retest both submission
sizes under the resulting timing fix before adopting batching. Other modulations,
higher loads and B205 hardware remain outside this narrow qualification pass.
Temporary reports, generated profiles and experiment wrappers were removed after
summarizing; no new status file or permanent JSON was retained.


## X310 Step 3 transmission deadlines — 2026-10-15

**Step 3's scoped X310 timing checks passed:** ten short BPSK/MSK runs reported
zero host-late rejections, UHD late events, underruns, TX timeouts, TX stream
errors or shutdown errors from startup through shutdown. BPSK delivery at
5 packets/s still backlogs; this is not full radio qualification or a throughput
fix. The one-packet runtime submission default remains unchanged.

Implemented:
- Copy/validate IQ and drain stale asynchronous events before choosing an ordinary
  data target. One serialized device-time read selects/checks it; redundant reads
  no longer consume its lead. Explicit sync timestamps are never shifted.
- Drain stale reports together (bounded to 256 events), counting every error
  before raising one aggregate failure. This prevents a queued error train from
  requiring one application attempt per error. An endless event source remains
  a reported failure rather than an unbounded loop.
- Add `link.hardware_min_tx_lead_seconds` (default zero for compatibility).
  The application and adapter reject insufficient lead before IQ submission,
  safely requeue the packet, and regenerate sync timestamps on the next attempt.
  A distinct exception distinguishes an unsent deferral from an ambiguous
  post-submission failure. Margin rejections and deferrals have separate counters.
- Include transport idle samples in TDD whole-burst admission, matching the
  existing hopping admission accounting. Add bounded failure-attempt timing
  summaries; delayed UHD events still cannot guarantee physical burst identity.

The first two runs kept the prior 10 ms data/10 ms sync budget, with no minimum
margin, to isolate the preparation changes. The remaining eight used **10 ms
data, 20 ms sync and 2 ms minimum remaining lead**. Sync alone gets more time
because its target must be encoded before modulation. These settings are saved
in `configs/bench/peer-{a,b}-x310-bpsk.yaml`; original B205 profiles and global
lead defaults are unchanged.

Same X310 3267DE0, shared channels 0/1, UBX-160, 1 Gb Ethernet, 915 MHz, 2 Msps,
250 kbit/s, gains 0/0 dB and crossed 30 dB attenuated paths. The legacy generic-v5
TDD period remains 180 ms, guard 2 ms, ACK timeout 250 ms. All measurements used
diagnostics on; software tests ran only after RF workloads stopped.

### Delivery, timing and recovery

Rows are execution order; direction pairs are **A→B / B→A**. Normal traffic is
15 seconds, 75 × 256-byte packets each way at 5 packets/s, plus 5 seconds drain.
Max payload is ten × 1,024 bytes at 1 packet/s over 10 seconds with the same
drain. Recovery-only traffic is five × 256 bytes at 1 packet/s, followed by drain
and a 17-second zero-IQ mute. The combined default-size run applies that mute
after the maximum-payload workload. The follower entered recovery before restore
in both mute runs. Percentiles only describe packets delivered by the deadline.

| Run | Host batch | Delivered | One-way p95 (ms) | Shared CPU during traffic/drain (s) | Both directions recovered after restore |
|---|---|---:|---:|---:|---:|
| bpsk-10ms | 16 packets | 58 / 59 of 75 | 8434.664 / 8046.313 | 12.280 | — |
| msk-10ms | 16 packets | 75 / 75 of 75 | 769.370 / 530.117 | 12.169 | — |
| bpsk-margin1 | 16 packets | 56 / 60 of 75 | 8558.306 / 7309.582 | 12.112 | — |
| msk-margin | 16 packets | 75 / 75 of 75 | 354.644 / 365.430 | 11.836 | — |
| bpsk-margin2 | 16 packets | 62 / 67 of 75 | 7552.635 / 6636.597 | 12.306 | — |
| bpsk-max | 16 packets | 10 / 10 of 10 | 2957.151 / 3200.237 | 7.866 | — |
| bpsk-recovery | 16 packets | 5 / 5 of 5 | 294.343 / 213.493 | 5.431 | 2.298 s |
| bpsk-margin3 | 16 packets | 52 / 57 of 75 | 8919.190 / 8396.255 | 12.186 | — |
| bpsk-default | 1 packet | 58 / 55 of 75 | 8190.940 / 8512.982 | 12.222 | — |
| bpsk-default-max-recovery | 1 packet | 10 / 10 of 10 | 4720.798 / 3047.798 | 7.980 | 2.664 s |

All ten runs also had zero workload RX overflow/buffer/output drops and no
duplicate or changed UDP deliveries. There were no additional post-workload
stream errors. The eight margin-enabled runs had zero margin rejections or
deferrals throughout their lifetime, so the clean results were not obtained by
suppressing every attempted transmission. Deferral/error-train paths were
exercised in deterministic offline tests.

### Lead budget and latency tradeoff

After the preparation fix, the first BPSK run's mean first-send lead was
9.035/9.418 ms, versus 7.261/7.000 ms in Step 1's first instrumented run.
The first MSK run was also stream-clean, but its A-side lifetime minimum lead
was only 0.884 ms, motivating the separate sync budget.

With 20 ms sync and the 2 ms minimum, first-send lead lifetime minima across
both channels of the three normal bounded BPSK repeats ranged from 8.694 to
9.090 ms. The margin-enabled MSK minima were 8.881/8.952 ms. These minima include
startup; workload means use before/after aggregate count and mean differences.

BPSK normal bounded radio-transmit means were approximately 27.1–27.9 ms,
compared with roughly 25.8–27.7 ms in Step 2. The default-size control measured
27.511/27.768 ms. Moving ordinary target selection later restores lead consumed
by preparation and can therefore add host-observed wait; doubling the sync
budget adds up to 10 ms to that control packet's intended start. Ordinary data
lead was not increased. There is no claim of a causal end-to-end latency gain:
normal BPSK remained incomplete, and p95 fluctuated by seconds with backlog.

The default-size normal BPSK submission means were 3.830/3.932 ms versus roughly
2 ms for bounded submissions. Three bounded BPSK repeats were stream-clean after
these fixes, unlike Step 2's failed third repeat. This supports further batching
experiments under the corrected timing, but does not yet establish a substantial
application benefit or justify changing the runtime/B205 submission default.

Maximum-payload delivery was complete for both submission sizes. Recovery
succeeded with both sizes after an actual synchronization-loss interval.
These recovery checks intentionally started without prior backlog; they do not
establish five-second recovery under the overloaded 5-packet/s BPSK workload.

### Validation and remaining scope

711 targeted hardware-fake, protocol, decoder and integration tests passed;
six tests matching `thousand` were excluded from this development run.
Ruff lint/format, mypy (47 source files) and whitespace checks passed.
New regressions cover preparation-expired targets, one device-time read when
diagnostics are off, a 46-event stale train, an endless event source, failure
timing association, minimum-lead rejection, queue preservation after an unsent
deferral, invalid margin settings and TDD transport-idle accounting. Existing
hopping-boundary and scheduling tests passed.

Step 3's timing gate is complete for these profiles/workloads. Higher-rate
hopping, other modulation hardware runs, B205 hardware revalidation and the full
matrix remain later qualification work. General-purpose host scheduling is not
a hard real-time guarantee. Next is Step 4's receive/decoder latency work,
followed by the reliable-link waiting/backlog work. Temporary reports and
generated experiment profiles were removed; only the reusable paired X310
profiles and these final summaries remain.

## X310 Step 5 reliable-link waiting — 2026-10-15

Step 5's delivery/backlog gate passed with an **explicit unslotted reliable
policy**. Existing TDD profiles were preserved. This is a scheduling-policy
improvement for the crossed conducted setup, not evidence of compatible
half-duplex emulation or of X310 hardware superiority. Step 4 decoder work
remains pending. QPSK intermittently underflowed despite complete delivery;
these new profiles remain `unqualified` pending stream-stability and broader
acceptance checks.

### Setup and isolated policy comparison

Same X310 (3267DE0), two UBX-160 boards, shared device/clock, 1 Gb Ethernet and
crossed TX/RX → 30 dB → opposite RX2 connections. Both channels used 915 MHz,
2 Msps, 0 dB TX/RX gains and requested 1 MHz bandwidth (UBX actual 160 MHz).
UHD 4.9.0.1, Python 3.14.4, NumPy 2.3.5. No NIC/kernel/socket tuning was applied;
the existing 1 MiB granted socket-buffer warning remained. Runs were sequential,
with no concurrent software regression suite. Timing diagnostics were enabled.
Submission remained **one transport packet per call**, not experimental batching.

The first three experiments used identical BPSK waveforms at 250 kbit/s,
256-byte payloads, 5 packets/s/direction for 15 seconds plus five seconds drain.
Direction pairs throughout this section are **A→B / B→A**.

| Experiment | Delivered (of 75 each) | One-way p95 (ms) |
|---|---:|---:|
| Legacy X310 diagnostic settings, only TDD period/guard disabled; ACK timeout 250 ms | 75 / 75 | 83.285 / 85.265 |
| Same unslotted settings, only ACK timeout changed to 600 ms | 75 / 75 | 87.724 / 81.303 |
| Working schema-2 generic profile adapted to identical BPSK waveform and X310 channels | 75 / 75 | 85.816 / 78.683 |

All three had zero stream errors/retries and empty final queues. The schema-2
comparison used an explicit custom/unqualified waveform override and retained
its existing host timing/clock settings. The first experiment alone isolates
removal of the 180 ms TDD schedule: Step 3's otherwise corresponding BPSK
settings accumulated seconds of latency and missed the delivery deadline.
This supports slot waiting as the main backlog contributor in this workload.
Changing the ACK timeout alone did not reduce latency. The 600 ms value matches
the working generic policy and provides more return-path margin; it is not a
measured optimum. No retry count, stop-and-wait rule or queue capacity increase
was used to obtain these results.

### Reusable profiles and queue behavior

Added portable `generic-{bpsk,2fsk,msk,gmsk,qpsk}-unslotted-v5` profiles and
`configs/v1_1/peer-{a,b}-x310-generic.yaml`. They retain generic-v5 framing,
authentication, replay protection, duplicate suppression and one in-flight data
packet per peer. TDD is disabled explicitly in their distinct profile identities;
ACK timeout is 600 ms and maximum retries remains four. The node examples use
10 ms data/20 ms sync lead, a 2 ms minimum submission margin, queue capacity 256
and a five-second pending-data age limit. Both peers must select the same profile.

The optional `queue_max_age_seconds` is host policy, excluded from the portable
radio definition. Default `null` preserves existing behavior. Reliable queued
and unacknowledged data expire when the link processes them; ACK/SYNC controls
are preserved. ACK-free simple profiles only expire waiting data and remain
ACK-free. Already selected/submitted bursts are not interrupted. `queue_expired`
and oldest pending age are visible without enabling timing diagnostics. Expiry
of unacknowledged data does not prove it was lost over RF. No data expired in
these hardware checks; expiry/overload paths were verified offline.

### Normal traffic

30 seconds, 150 × 256 bytes per direction at 5 packets/s, five-second drain.
BPSK/2FSK/MSK/GMSK use 250 kbit/s; QPSK uses 500 kbit/s, matching the earlier
per-modulation baseline. Exact delivery was checked, rather than accepting the
benchmark tool's general 99% threshold.

| Modulation/run | Delivered (of 150 each) | One-way p95 (ms) | Stream result |
|---|---:|---:|---|
| BPSK | 150 / 150 | 84.118 / 82.738 | Clean |
| 2FSK | 150 / 150 | 66.572 / 65.615 | Clean |
| MSK | 150 / 150 | 72.740 / 72.597 | Clean |
| GMSK | 150 / 150 | 72.820 / 72.756 | Clean |
| QPSK first | 150 / 150 | 78.516 / 77.528 | Two B-side TX underflows; not a stream pass |
| QPSK repeat | 150 / 150 | 80.819 / 80.166 | Clean |

Every run ended with empty data queues, zero expired packets, zero retries,
zero duplicate/changed deliveries and clean shutdown. The clean QPSK repeat
does not erase its first failure. Normal p95 is below the historical B205
legacy-TDD range of roughly 194–332 ms, but this comparison changes scheduling
policy and uses one shared host/clock. B205 must be remeasured with the same
revised policy before attributing the gain to hardware.

### Maximum payload and short capacity check

All payloads were 1,024 bytes. Low load: ten seconds, ten packets per direction
at 1 packet/s, plus five-second drain. Heavy load: five seconds, 25 packets per
direction at 5 packets/s, plus five-second drain.

| Modulation | Low-load delivery (of 10 each) | Low-load p95 (ms) | Heavy delivery (of 25 each) | Heavy p95 (ms) | Stream result |
|---|---:|---:|---:|---:|---|
| BPSK | 10 / 10 | 147.548 / 139.598 | 25 / 25 | 142.502 / 154.083 | Both clean |
| 2FSK | 10 / 10 | 126.693 / 126.079 | 25 / 25 | 127.597 / 128.077 | Both clean |
| MSK | 10 / 10 | 135.467 / 132.861 | 25 / 25 | 130.155 / 132.355 | Both clean |
| GMSK | 10 / 10 | 138.615 / 136.666 | 25 / 25 | 135.842 / 135.191 | Both clean |
| QPSK | 10 / 10 | 162.998 / 164.151 | 25 / 25 | 146.030 / 166.321 | Low load: one B-side underflow; heavy: two A-side underflows |

All ten runs had empty final data queues, zero expired packets, no retries,
no duplicate/changed deliveries and clean shutdown. Across all 19 Step 5 runs,
there were no UHD late events or additional post-workload stream errors. The
only recorded stream failures were the five QPSK TX underflows listed above.
The four other modulations sustained this short heavier check. QPSK's complete
delivery does not satisfy a zero-stream-error gate. These five-second bursts
do not establish sustained maximum capacity or long-duration stability.

### Reproduction and remaining scope

Use `tools/uhd_link_check.py --shared-device --profiles NODE_A NODE_B
--timing-diagnostics` with the supplied X310 generic node pair. For each waveform,
select its matching unslotted portable profile in both node files. Use
`--duration 30` for normal traffic; `--duration 10 --rate 1 --payload 1024`
for low-load maximum payload; `--duration 5 --rate 5 --payload 1024` for heavy.
The benchmark creates temporary authentication keys. Application operation
requires the documented configured keys. No permanent report output is required.

The implementation and Step 5 delivery/backlog checks are complete. QPSK stream
stability remains unresolved; investigate submission/host jitter rather than
promote these profiles as new qualified defaults. Step 4 receive/decoder work,
matched B205 revalidation, independent computers, over-the-air self-interference,
hopping, recovery and the full Step 9 acceptance matrix remain outside this pass.

Software validation: **725 tests passed in 45.66 seconds**, with six long tests
matching `thousand` excluded. Coverage includes queue expiry while blocked,
control preservation, expired-ACK handling, unchanged unlimited-age behavior,
ACK-free expiry, config validation and portable-profile separation. Ruff lint
and formatting passed (113 files), mypy passed (47 runtime files), and diff
whitespace checks passed. Temporary experiment profiles, logs and JSON reports
were removed after summarizing; only reusable profiles and final documentation
remain. Release archives were not rebuilt.

## X310 Step 4 receive and decoder latency — 2026-10-15

### Measured cause and receive-only change

The live receiver previously accumulated most above-noise bursts until an idle
block arrived. This avoided repeated partial-frame searches, but front-end tails
could defer recognition after a complete packet was present. New optional
measurements separate host IQ-queue residence, decoder-output queue residence,
and the sample duration buffered beyond a decoded frame's consumed end. The
last metric is not a host duration and can include block rounding, shaping tails
or following frames. No subtraction mixes host and hardware clock domains.

In the matched simple-radio baseline, average host IQ-queue residence was
0.080–0.230 ms, and decoder-output maximum residence was 0.084–0.336 ms.
Buffered-tail p95 was 15.674–23.212 ms. This supports investigating packet-end
waiting rather than enlarging queues. RX block size remains 4,096 samples and
queue capacity remains 256 blocks; no block-size sweep or host tuning was needed
to demonstrate the improvement below.

2FSK, MSK and GMSK now use plausible preamble/length candidates to schedule a
search when enough body samples and the known shaping delay are available.
Without a usable length, live searches are spaced by 4 ms of new IQ or a larger
read block; idle/full-buffer checks retain priority. Candidate payload validation
is unchanged. Reset and timestamp gaps clear pending search hints. Buffer bounds
and the existing search-before-trim regression for a long maximum-packet tail
are preserved. Transmitted samples, framing, profile identities and link policies
are unchanged. BPSK/QPSK retain their previous live search policy; this work does
not investigate or resolve QPSK TX underflows.

### Setup and matched short comparison

Same crossed 30 dB conducted X310/UBX-160 setup and shared clock/device as Step 5:
915 MHz, 2 Msps/channel, 0 dB TX/RX gains, 1 Gb Ethernet, UHD 4.9.0.1,
Python 3.14.4 and NumPy 2.3.5. Original one-transport-packet TX submissions and
10 ms data lead were preserved. Frozen simple 2FSK profiles at 100/250/500 kbit/s
were loaded through temporary node copies of the supplied X310 pair. No
kernel/NIC tuning, extra TX padding, carrier threshold changes or IQ captures.
Hardware runs were sequential with the software suite idle.

Both baseline and early-search comparisons offered 50 × 256-byte packets at
5 packets/s in each physical direction, tested sequentially, followed by five
boundary/repeated-payload packets and one fresh packet after a one-second mute.
Each run delivered **56/56 exactly**, with no control/retry traffic, no stream
errors, and clean close/reopen. Channel 0→1 used payload input; 1→0 used
preframed input. Submitted bytes, sample counts and minimum 2 ms target gaps
were checked. Timing diagnostics were enabled in both versions.
Direction pairs below are **A→B / B→A**.

| Simple rate | Baseline one-way p95 (ms) | Early-search one-way p95 (ms) | Baseline buffered-tail p95 (ms) | Early buffered-tail p95 (ms) |
|---|---:|---:|---:|---:|
| 100 kbit/s | 64.653 / 61.501 | 45.138 / 43.901 | 23.212 / 21.162 | 1.951 / 2.035 |
| 250 kbit/s | 33.983 / 35.310 | 27.877 / 27.382 | 18.380 / 17.843 | 3.390 / 3.163 |
| 500 kbit/s | 35.042 / 34.221 | 21.401 / 22.744 | 15.674 / 15.949 | 1.375 / 2.006 |

The packet latency columns cover only steady 256-byte traffic; buffered-tail
metrics include boundary/recovery frames too. These are short paired observations,
not confidence intervals. Whole-process CPU over each roughly 11.9-second check
changed from 5.327/5.074 to 5.150/5.373 seconds at 100 kbit/s, 4.667/5.134 to
5.377/4.786 at 250 kbit/s, and 5.023/4.866 to 5.175/4.583 at 500 kbit/s.
There is no consistent CPU saving claim: earlier correlation trades some work
for lower delivery latency. Decoder real-time utilization in the early checks
remained approximately 0.060–0.106. Host queue mean residence remained below
0.14 ms. Detailed diagnostics incur bounded clock/aggregation overhead.

### Full simple-radio delivery gate

150 steady packets at 5 packets/s, followed by one-byte, 64-byte, 1,024-byte,
two identical payloads and one recovery packet after a one-second muted burst.
Each direction independently checked exact packet contents, submitted waveform
bytes/sample counts, no implicit ACK/retry traffic, receive-only TX silence,
minimum burst spacing and clean shutdown.

| Rate | Delivered each direction | One-way p95 (ms) | Recovery (ms) | Minimum device-target gap (ms) |
|---|---:|---:|---:|---:|
| 100 kbit/s | 156 / 156 | 45.314 / 44.922 | 23.029 / 22.121 | 103.590 / 106.577 |
| 250 kbit/s | 156 / 156 | 27.324 / 26.663 | 21.526 / 22.018 | 161.410 / 161.228 |
| 500 kbit/s | 156 / 156 | 22.740 / 22.607 | 18.626 / 16.785 | 178.633 / 179.679 |

All six directional checks passed with zero measured stream errors, RX buffer
losses and decoder-output drops. Final buffered-tail p95 was 1.331–2.029 ms;
host IQ-queue mean was 0.025–0.169 ms and decoder-output maximum residence was
0.112–0.705 ms. Full-check decoder real-time utilization was 0.059–0.108.
This verifies lower end-to-end latency with exact delivery, not merely faster
decoder calls. The 1,024-byte boundary packets passed at every rate and direction.

### Reliable-link regression on the affected receiver family

Existing Step 5 unslotted profiles, without additional timing or link changes.
Normal workload: 30 seconds, 150 × 256-byte packets each way at 5 packets/s
plus five-second drain. Maximum payload: ten seconds, ten × 1,024 bytes each
way at 1 packet/s plus five-second drain. Both directions run concurrently.

| Modulation | Normal delivered (of 150 each) | Normal p95 (ms) | Maximum delivered (of 10 each) | Maximum p95 (ms) |
|---|---:|---:|---:|---:|
| 2FSK | 150 / 150 | 54.949 / 54.584 | 10 / 10 | 115.890 / 118.351 |
| MSK | 150 / 150 | 54.270 / 54.538 | 10 / 10 | 114.464 / 117.230 |
| GMSK | 150 / 150 | 54.671 / 55.452 | 10 / 10 | 117.730 / 120.110 |

All six runs had exact delivery, empty final data queues, no expired packets,
no retries or stream errors, and clean shutdown with no additional post-workload
stream errors. Step 5 normal p95 was approximately 66 ms for 2FSK and 73 ms
for MSK/GMSK; this pass measured approximately 54–55 ms. This comparison uses
the same explicit unslotted policy, unlike comparisons against legacy TDD.
The five-second heavy capacity test, hopping and reliable-link mute recovery
were not repeated here. BPSK/QPSK RF tests and the deferred QPSK underflow
investigation are not part of this pass.

### Diagnostics-off check, validation and scope

A final simple 250 kbit/s check with diagnostics disabled delivered 56/56 each
way with zero stream errors, exact bytes/sample counts and clean shutdown.
One-way p95 was 27.414/26.646 ms, versus 27.877/27.382 ms in the matched
instrumented early-search check. Whole-process CPU was 4.904/5.071 seconds
over roughly 11.85 seconds. This confirms the improvement is present during
normal operation; these short samples do not quantify a precise instrumentation
overhead. No IQ or per-packet traces were retained.

**1,514 tests passed in 73.27 seconds**, with six long tests matching `thousand`
excluded. The run included all modem, hardware-fake, protocol and integration
tests outside that exclusion. New regressions cover independent low-amplitude
reference frames at all three simple rates, 256/1,024-byte early delivery before
a long tail, bounded search/buffer behavior, timestamp/reset hint invalidation,
noise and CRC corruption followed by valid data, maximum plus following frames
for 2FSK/MSK/GMSK, and host queue age without rewriting RF timestamps. Existing
long-tail, fragmented-input, shaping and impairment cases passed. Ruff lint and
format checks passed (113 files), mypy passed (47 runtime files), and diff
whitespace checks passed.

Step 4 is complete for its stated receive-latency/delivery gate. To reproduce,
run `tools/simple_link_check.py --shared-device --profiles NODE_A NODE_B
--count 150 --interrupt-seconds 1 --timing-diagnostics` with the supplied X310
simple nodes selecting each frozen rate profile. Use count 50 for the short
comparison and omit `--timing-diagnostics` for normal-operation verification.
The reliable checks use Step 5's generic X310 profiles and benchmark recipe.
The new receive logic applies to B205 too, but B205 hardware was not remeasured.
Independent-host/over-the-air performance, sustained capacity and the full
Step 9 matrix remain outside this short conducted pass. New unslotted profiles
remain unqualified. QPSK's previously recorded transmit underflows remain
unresolved and are the next investigation. Temporary node copies, logs and
JSON were removed; reusable profiles and release archives were not changed.

## X310 QPSK underflow investigation — 2026-10-15

### Reproduction and targeted mitigation

The Step 5 QPSK underflow was reproduced after Step 4, without changing QPSK's
receiver, waveform or link policy. Same crossed 30 dB X310/UBX-160 setup,
915 MHz, two channels at 2 Msps, 500 kbit/s QPSK, 0 dB gains, shared host/clock,
1 Gb Ethernet, UHD 4.9.0.1 and the explicit unslotted generic-v5 profile.
Data/sync leads remained 10/20 ms with a 2 ms minimum margin, ACK timeout
600 ms and pending-data age limit five seconds. No NIC/kernel tuning or TX
padding changes. RF tests ran sequentially with no competing software suite.

The one-packet baseline delivered every payload but recorded four B-side TX
underflows in the low-load maximum-payload run and two in the heavy run. The
normal run was clean, confirming that the issue is intermittent and complete
packet delivery alone is not a stream pass.

On the failing maximum-payload attempt, first-send lead was 9.089 ms,
submission lasted 20.765 ms and the underflow attempt lasted 20.832 ms. The
longest observed send call on that endpoint took 11.395 ms. Concurrent QPSK
decode calls reached approximately 119–120 ms. These observations are consistent
with interrupted host sample submission under concurrent processing; they do
not establish that the Python GIL, rather than another host scheduling delay,
caused every event.

Bounded **16-transport-packet UHD submissions** eliminated observed underflows
in the pilot and three repeated matrices below. The maximum data-send calls
per normal attempt fell from 66 to 5, and per maximum-payload attempt from 201
to 13. On this stream that is a 5,824-sample host batch instead of 364 samples.
Maximum-payload lifetime mean submission time changed from 2.517/3.255 ms in
the baseline to 1.311/1.187 ms in the pilot. Those means include startup/control
traffic, so they are not per-payload timing estimates. The improvement is stream
continuity; no large packet-latency reduction or universal optimal batch size
is claimed.

### Application support and unchanged radio contract

Added `hardware_tx_batch_packets` to host link/runtime configuration, integer
1..256, default 1. The application configures its UHD backend before TX starts,
and reports both packet count and sample limit in actual hardware settings.
The benchmark override now follows the same application configuration path.
The new `configs/v1_1/peer-{a,b}-x310-qpsk.yaml` pair explicitly selects 16 with
the existing unslotted QPSK profile. Existing generic/simple/B205 examples remain
at one packet; selecting QPSK in an older file alone does not activate this fix.

This is a host/device setting, excluded from portable profile identity. It does
not change transmitted samples, framing, QPSK parameters, authentication,
replay/duplicate protection, ACK/TDD policy, RF gains or timing targets. Existing
owned-buffer, partial-send, deadline, first-fragment timestamp, EOB and async
error handling are retained. No error counters are suppressed. Configuration
is applied at startup; this is not a live stream reconfiguration operation.

### Matched workloads and repeated results

Normal: 30 seconds, 150 × 256-byte packets/direction at 5 packets/s plus five
seconds drain. Maximum: ten seconds, ten × 1,024-byte packets/direction at
1 packet/s plus five seconds drain. Heavy: five seconds, 25 × 1,024-byte
packets/direction at 5 packets/s plus five seconds drain. Timing diagnostics
were enabled. Direction pairs are **A→B / B→A**.

| Workload | Baseline delivery | Baseline p95 (ms) | Baseline stream errors | 16-packet pilot delivery | Pilot p95 (ms) |
|---|---:|---:|---|---:|---:|
| Normal | 150 / 150 | 81.506 / 85.304 | None | 150 / 150 | 83.232 / 81.986 |
| Maximum | 10 / 10 | 189.514 / 192.786 | Four B-side TX underflows | 10 / 10 | 175.577 / 179.365 |
| Heavy | 25 / 25 | 160.608 / 166.125 | Two B-side TX underflows | 25 / 25 | 146.812 / 156.697 |

The pilot used the benchmark batch override. The following runs used the new
node configuration through the ordinary application path, with **no benchmark
batch override**. Actual settings confirmed 16 packets/5,824 samples on both
channels in every run.

| Repeat | Normal delivery | Normal p95 (ms) | Maximum delivery | Maximum p95 (ms) | Heavy delivery | Heavy p95 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 150 / 150 | 78.929 / 77.637 | 10 / 10 | 174.698 / 181.773 | 25 / 25 | 146.607 / 146.673 |
| 2 | 150 / 150 | 76.887 / 74.785 | 10 / 10 | 156.832 / 159.554 | 25 / 25 | 151.930 / 179.084 |
| 3 | 150 / 150 | 76.996 / 77.362 | 10 / 10 | 166.533 / 161.000 | 25 / 25 | 130.659 / 166.034 |

All twelve batched pilot/repeat runs had exact delivery, zero stream errors,
zero retries, empty final data queues, no expired packets and clean shutdown
with no additional post-workload stream errors. Baseline queues were also empty;
queue expiry did not manufacture the improvement. Historical failures remain
reported above and in Step 5. Three short repeats do not establish long-duration
stability or maximum sustainable throughput.

### Normal operation, interruption recovery and scope

With diagnostics disabled and the new QPSK node files loaded directly, normal
traffic delivered 150/150 each way at p95 **78.057/79.737 ms**, with zero stream
errors and clean shutdown. A separate instrumented low-load maximum-payload
run delivered 10/10 each way at p95 **148.335/150.762 ms**, then muted both TX
paths for 17 seconds. The follower entered recovery before restoration, and
fresh packets reached both directions within **1.090 seconds** after restoration.
There was one A-side retry during recovery, no retry exhaustion or expired data,
and no stream/timing/shutdown errors during or after recovery.

The observed QPSK underflow is mitigated for this explicit conducted X310
configuration. All fourteen batched runs (pilot, three repeated matrices,
diagnostics-off and interruption recovery) were stream-clean. No claim is made
that the issue is impossible under arbitrary host load, different sample rates,
other hardware, different QPSK profiles or over-the-air operation. Portable
profile qualification metadata remains unchanged; this does not complete the
full Step 9 matrix or qualify new global defaults. B205 batching was not tested.

To reproduce the normal check, use:

```sh
python tools/uhd_link_check.py --shared-device \
  --profiles configs/v1_1/peer-a-x310-qpsk.yaml configs/v1_1/peer-b-x310-qpsk.yaml \
  --duration 30 --timing-diagnostics
```

Use `--duration 10 --rate 1 --payload 1024` for low-load maximum payload,
`--duration 5 --rate 5 --payload 1024` for heavy traffic, and add
`--interrupt-seconds 17` for recovery. The benchmark generates temporary keys;
the application uses the same node files with its normal configured keys.
Do not pass a batch override when validating the supplied node configuration.

Software validation: **1,528 tests passed in 72.99 seconds**, with six long
tests matching `thousand` excluded. New regressions verify valid/invalid batch
settings, host-policy separation from portable identity, backend startup
propagation, both-node benchmark overrides and preservation of data/metadata.
Existing partial-transfer, deadline, ownership, async-error and shutdown tests
also passed. Ruff lint/format checks passed (113 files), mypy passed (47 runtime
files) and diff whitespace checks passed. Temporary benchmark logs, JSON and
node copies were removed after summarizing. The two reusable QPSK node files
remain; release archives were not rebuilt.

## X310 Step 6 shared-channel execution — 2026-10-15

### Measurements and decision

Retained the existing shared device and application/RX/decoder thread model.
No multiprocessing, IPC path, additional `MultiUSRP` owner, streamer owner,
clock reset, queue-size or waveform change was introduced. Measurements found
avoidable CPU work in status collection rather than evidence that a new
execution architecture was necessary at the tested load.

Added opt-in wall/thread-CPU scopes for application steps, modem feed,
decoder-status collection and RX read/copy/enqueue. Added explicit acquisition
and hold timing for the UHD control lock, decoder state lock and RX worker I/O
lock. Observations are recorded after releasing the measured lock. Instrumentation
is disabled by default and performs no scope clock reads when disabled. Elapsed
minus thread CPU is not a GIL-wait measurement; lock acquisition duration can
include scheduling delay. Scope times overlap (application step includes status
collection) and must not be summed as independent CPU totals. RX/device timestamps
remain separate from these host timing clocks.

Baseline measurements showed that `DecoderWorker.diagnostics()` sorted its
2,048-sample timing window on every application poll, under the decoder state
lock. Workload-only mean decoder-status CPU was approximately **0.194–0.247 ms
per call**, a substantial fraction of the application step's CPU. Explicit
steady-workload device-control acquisition was approximately a microsecond on
average; lifetime maxima include startup tuning and must not be interpreted as
steady lock contention alone. Receive and decoder stages remain independently
owned and buffered.

The worker now maintains a sorted view incrementally alongside the existing
arrival-order window. It removes exactly one expired value and inserts each new
observation under the same state lock. Status reads obtain the exact existing
p95/max without sorting. The last-2,048 window, quantile index, duplicate-value
handling, current counters, timestamps and buffering semantics are unchanged.
Memory remains bounded; there is no stale-cache interval. This avoids repeated
work while retaining the simpler execution model.

### Matched hardware comparisons

Same conducted X310/UBX-160, crossed 30 dB paths, 915 MHz, 2 Msps/channel,
0 dB gains, 1 Gb Ethernet, UHD 4.9.0.1, Python 3.14.4 and NumPy 2.3.5.
BPSK/2FSK use the Step 5 unslotted policy and one transport packet per send;
QPSK uses the validated 16-packet node configuration. Data/sync lead stays
10/20 ms and minimum margin 2 ms. No NIC/kernel or scheduling-affinity changes.
Hardware runs were sequential, without the software suite competing for CPU.

Each normal run offered 150 × 256-byte packets per direction over 30 seconds
at 5 packets/s, followed by five seconds drain. All rows below delivered
**150/150 each way**, with zero stream errors/retries, no expired data, empty
final queues, clean shutdown and no additional post-workload stream errors.
CPU is the whole shared process over workload plus drain, counted once.
Direction pairs are **A→B / B→A**. Both versions include the same new diagnostic
scopes, so the comparison isolates rolling-statistics maintenance.

| Configuration | Version/repeat | Shared CPU (s) | One-way p95 (ms) |
|---|---|---:|---:|
| BPSK | Baseline | 26.914 | 84.959 / 84.677 |
| BPSK | Incremental 1 | 24.090 | 83.815 / 82.106 |
| BPSK | Incremental 2 | 23.362 | 80.604 / 82.496 |
| BPSK | Incremental 3 | 23.399 | 82.745 / 79.761 |
| 2FSK | Baseline | 24.341 | 54.037 / 54.951 |
| 2FSK | Incremental 1 | 21.807 | 53.890 / 53.982 |
| 2FSK | Incremental 2 | 22.313 | 54.571 / 54.174 |
| QPSK | Baseline | 25.093 | 78.414 / 80.685 |
| QPSK | Incremental 1 | 22.580 | 82.728 / 77.135 |
| QPSK | Incremental 2 | 21.819 | 75.775 / 79.264 |

Shared CPU fell approximately **8–13%** in the instrumented comparisons.
Packet latency remained in the same range; this is a CPU-cost improvement, not
a claim that every tail-latency observation improved. In the first optimized
BPSK run, status CPU was approximately 0.010 ms/call and application-step CPU
approximately 0.184–0.189 ms/call, compared with baseline 0.221–0.247 and
0.368–0.403 ms respectively. Thread CPU scopes cover only their documented
regions; whole-process CPU captures the overall cost, including maintenance
moved to the decoder worker.

Workload-only mean IQ-queue residence fell from 1.438/1.546 ms to
1.072/1.174 ms in the first BPSK comparison, and from 0.348/0.351 ms to
0.219/0.246 ms for 2FSK. QPSK fell from 1.242/1.278 ms to 0.924/1.213 ms.
Decoder-output residence varied within sub-millisecond means: some individual
values increased, so no universal jitter reduction is claimed. There was no
sustained queue growth or sample loss, and exact delivery was maintained.
These short repeats are observations without confidence intervals, not hard
real-time bounds or a new maximum-capacity qualification.

### Diagnostics-off and recovery confirmation

The matched BPSK normal workload with detailed diagnostics disabled used
**25.536 CPU seconds before and 22.528 after**, an approximately **11.8%**
reduction. Both runs delivered 150/150 each way without stream errors; p95
was 81.128/80.122 ms before and 80.845/77.651 ms after. This confirms the CPU
benefit in normal operation rather than only in the instrumented benchmark.
It is one paired observation, not a precise instrumentation-overhead estimate.

A separate QPSK check delivered ten 1,024-byte packets each way at 1 packet/s,
with p95 146.208/148.062 ms, then muted both TX paths for 17 seconds. The
follower entered recovery, and both directions delivered fresh packets within
**1.102 seconds** after restoration. One A-side retry occurred during recovery;
there was no retry exhaustion or data expiry. Stream checks, post-workload
checks and shutdown passed with zero errors, preserving the QPSK batching fix.

### Reproduction and scope

Use the existing `tools/uhd_link_check.py --shared-device --profiles NODE_A NODE_B
--duration 30 --timing-diagnostics` recipe with the X310 unslotted BPSK/2FSK
node pair and the dedicated 16-packet X310 QPSK pair. Omit the diagnostics flag
for normal-operation comparisons. QPSK maximum/recovery adds
`--duration 10 --rate 1 --payload 1024 --interrupt-seconds 17` instead of the
normal duration. Scope mean comparisons above use before/after count-weighted
means to exclude startup; rolling percentiles and lifetime maxima cannot be
subtracted. Baseline rows refer to the pre-incremental implementation, not a
new runtime configuration switch.

Step 6's CPU-cost gate is satisfied for these workloads. The shared ownership,
control serialization and thread execution model remain in place. There is no
measured justification here for introducing worker processes and IPC; a future
load that demonstrates a different bottleneck would require a new comparison.
MSK/GMSK physical RF were not separately repeated for this statistics-only
change; modem behavior is unchanged and covered by software regression tests.
B205 hardware, faster hopping, sustained maximum capacity, independent hosts,
over-the-air operation and the full Step 9 matrix remain outside this pass.

Software validation: **1,532 tests passed in 73.86 seconds**, with six long
`thousand` tests excluded. New tests verify exact rolling p95/max through
5,000 updates, duplicate removal and expired extrema; independent elapsed/CPU
clocks; disabled instrumentation without clock reads; and lock release on
exceptions with reentrant ownership preserved. Existing shared-owner,
peer-failure, timestamp, modem and shutdown regressions passed. Ruff lint and
formatting passed (113 files), mypy passed (47 runtime files), and diff
whitespace checks passed. Temporary profiles, logs and JSON were removed;
only final summaries and reusable instrumentation/code remain. No release
archives were rebuilt.

## X310 Step 7 faster hopping — 2026-10-15

**Outcome: 2 hops/s qualified for the documented same-host shared X310/UBX
simple-2FSK workload.** The original 1 hop/s profile remains unchanged. The new
`configs/profiles/simple-2fsk-x310-hopping-v1.yaml` and
`configs/v1_1/peer-{a,b}-x310-hopping.yaml` select a 500 ms dwell with 100 ms guards.
Schema-2 validation now permits dwell down to 500 ms, requiring explicit UHD
`type: x300` selection above 1 hop/s. Other hardware, intermediate rates, tables,
modulations and independent hosts require separate qualification.

Setup: X310 3267DE0 with UBX-160 channels 0/1, UHD 4.9.0.1, 1 Gb Ethernet at
192.168.10.2, one device owner and shared clock; crossed TX/RX-to-RX2 connections
with 30 dB attenuation in both directions; TX/RX gains 0 dB. The profile uses
2 Msps, 250 kbit/s 2FSK, ±125 kHz deviation, simple-frame-v1 without ACKs, a
914/915/916 MHz cycle, external host UTC, background decoding and 10 ms timed TX
lead. Requested bandwidth is 1 MHz; the UBX analog filter remains fixed at 160 MHz.
No NIC, kernel, FPGA, clock, buffer-size or channel-control concurrency changes.
The existing UDP send-buffer warning (1 MiB actual versus 2,453,333 bytes requested)
remained; it did not produce stream errors in these runs.

Each fast run included 60 seconds/120 dwells with one unique 256-byte packet per
direction per dwell, then 12 dwells of 1/64/1024-byte payloads. Packets were injected
at 200 ms on channel 0 and 300 ms on channel 1. Both channels additionally received
a deliberately too-late maximum packet at 380 ms in every boundary dwell. Peer B
was then paused and tuned to 920 MHz for three dwells, followed by three recovery
dwells. The device was closed and reopened between runs.

| Run | Steady exact per direction | Total exact per direction | Late rejections A/B | Stream errors | Missed steady hops/deadlines |
|---|---:|---:|---:|---:|---:|
| Original 1 hop/s, 60 seconds | 60/60 | 69/69 | 6/0 (original one-sided probe) | 0 | 0 |
| 2 hops/s, round 1 | 120/120 | 135/135 | 12/12 | 0 | 0 |
| 2 hops/s, round 2 | 120/120 | 135/135 | 12/12 | 0 | 0 |
| 2 hops/s, round 3 | 120/120 | 135/135 | 12/12 | 0 | 0 |
| Final 1 hop/s check, 6 seconds | 6/6 | 15/15 | 6/6 | 0 | 0 |

All fast runs recovered exact delivery in the first resumed dwell, with no ACKs
or retries. The intentional pause counted three skipped slots on B and none on A;
these were outside the steady window. Shutdown passed without errors each time.
All submitted bursts during steady, boundary and recovery phases passed the
frequency and complete-airtime guard checks, including the intentionally lost
interruption traffic. The 36 deliberately late packets on each channel were
rejected before transmission across the three runs.

### Transition and delivery measurements

Times below are milliseconds. Total tune duration includes buffered-I/O and shared
control waiting, stop/drain, RX/TX frequency calls/readbacks and restart. Decoder
and delivery values are **phases within a dwell**, not elapsed packet latency.
Statistics cover the steady interval; p95 and standard deviation are per run.

| Rate/run/channel | Total tune min/p95/max/std | First decoder-usable phase p95/max | Delivery phase p95/max/std |
|---|---|---|---|
| 1 hop/s / baseline / 0 | 12.283 / 23.425 / 24.187 / 5.033 | 127.621 / 128.143 | 528.338 / 528.909 / 1.035 |
| 1 hop/s / baseline / 1 | 12.351 / 23.513 / 23.614 / 5.095 | 127.322 / 128.096 | 228.623 / 230.115 / 1.006 |
| 2 hops/s / 1 / 0 | 12.146 / 23.417 / 24.067 / 5.024 | 127.223 / 127.549 | 328.282 / 329.352 / 1.003 |
| 2 hops/s / 1 / 1 | 12.115 / 23.565 / 23.928 / 5.042 | 127.225 / 128.061 | 228.264 / 230.265 / 1.002 |
| 2 hops/s / 2 / 0 | 11.901 / 23.558 / 23.849 / 5.138 | 127.594 / 128.000 | 328.989 / 330.571 / 1.089 |
| 2 hops/s / 2 / 1 | 12.020 / 23.261 / 24.178 / 4.852 | 127.132 / 127.892 | 228.908 / 230.210 / 1.092 |
| 2 hops/s / 3 / 0 | 12.223 / 23.325 / 23.792 / 4.988 | 127.151 / 128.029 | 328.566 / 329.236 / 1.060 |
| 2 hops/s / 3 / 1 | 12.054 / 23.586 / 24.073 / 5.113 | 127.065 / 128.005 | 228.578 / 229.614 / 0.931 |

Separate stages across the three fast runs (ranges are the range of per-run
statistics, not pooled percentiles):

| Stage | Channel 0 p95 range / worst / std range | Channel 1 p95 range / worst / std range |
|---|---|---|
| Stop and drain duration | 10.631–10.814 / 11.215 / 0.142–0.162 | 10.632–10.809 / 11.044 / 0.130–0.149 |
| RX/TX tune and readback calls | 0.296–0.303 / 0.339 / 0.021–0.023 | 0.298–0.301 / 0.355 / 0.018–0.026 |
| Restart command duration | 0.021–0.024 / 0.031 / 0.002 | 0.024–0.028 / 0.047 / 0.002–0.004 |

The latest restart completion phases were 27.170 ms on channel 0 and 27.150 ms on
channel 1. First buffered RX arrival ranged 15.264–29.974 ms and 15.445–29.778 ms,
respectively. The decoder's additional 100 ms settling wait made its latest first
usable block 128.029/128.061 ms. That left over 71 ms before the earliest scheduled
200 ms injection. A maximum frame occupies 34.048 ms; including 10 ms lead,
0.032 ms transport idle and 1 ms drain margin requires 45.080 ms of usable dwell.
The nominal window is 100–400 ms, with actual readiness later than 100 ms.
This qualification did not reduce guards or infer a rate from retune p95 alone.

First buffered samples and decoder-usable samples are distinct observations;
"usable" means admitted to decoding after the software settling wait. The RF
control-call duration does not measure PLL lock time. TX target checks map device
time through a bracketed host-time read; the worst bracket across fast runs was
1.251 ms, included at the trailing guard. Exact packet delivery and timestamp
bounds provide conducted application evidence, not independent RF-edge, phase
coherence or spectral-mask measurements. These short runs do not establish a
maximum sustained rate or a worst-case scheduling guarantee.

### Timed tuning decision and reproduction

The installed UHD 4.9.0.1 binding exposes `set_command_time`, `clear_command_time`,
`set_rx_freq` and `set_tx_freq`. The local `multi_usrp.hpp` documents command
backpressure and late commands executing on arrival. Ettus documents timed LO
tuning on X3x0/UBX in its [synchronization manual](https://files.ettus.com/manual/page_sync.html)
and command ordering in its [timed-command manual](https://files.ettus.com/manual/page_timedcmds.html).
These checks establish an API/platform path, not measured timed-retune behavior
in this application. The qualified implementation retains serialized immediate
stop/drain/tune/restart. Timed tuning, shorter guards and rates above 2 hops/s were
not enabled or physically qualified.

```bash
PYTHONPATH=. /tmp/packet-radio-v1-uhd/bin/python tools/hopping_check.py \
  --shared-address 192.168.10.2 --hop-rate 2 --seconds 60
```

Omitting `--hop-rate` retains the 1 hop/s workload. The benchmark now checks both
channels' late rejections, bounds TX clock-mapping uncertainty, reports separate
transition stages and first decoder-usable samples, and checks clean shutdown.
The measurement wrappers exist only in this benchmark; normal runtime timing,
stream ownership and waveform generation are unchanged.

The final short 1 hop/s check also passed recovery and clean shutdown using the
finished two-sided boundary probe. Its worst total tune was 23.629 ms and latest
first decoder-usable phase was 127.449 ms. Five conducted runs passed overall;
only the three 60-second fast runs establish the new 2 hop/s qualification.

Software validation: **1,540 tests passed in 72.09 seconds**, with six long
`thousand` tests excluded. Added checks cover the new profile pair and snapshot,
X300-series device restriction, actual maximum-frame airtime, both dwell guards,
fast-hop frequency progression, skipped-slot recovery with post-tune settling,
late-burst rejection without retry, a modulation operation that crosses a hop,
and invalid benchmark arguments before hardware access. Ruff lint/format passed
(113 files), mypy passed (47 runtime files), both new nodes passed the network
validator, and diff whitespace checks passed. Temporary logs and scripts were
removed after summarizing; no permanent JSON, IQ captures or extra status files
were retained. Release archives were not rebuilt.

## X310 Step 8 host transport — 2026-10-15

**Decision: retain the existing host/transport settings.** The conditional
transport assessment is complete; no buffer, NIC, kernel, scheduling, affinity or
10 Gb change met an adoption gate. Smaller buffers did not give a consistent
normal-traffic improvement, and maximum-payload QPSK produced an intermittent TX
underflow with both the 512 KiB candidate and the original 1 MiB allocation.
All application payloads arrived, but those two runs **failed** the zero-stream-error
gate. These failures reopen the maximum-payload QPSK acceptance question; the
later clean repeat does not erase them. Investigate burst-submission timing before
Step 9's final reference-radio acceptance.

### Environment and buffer observations

Same X310 3267DE0, UBX-160 channels 0/1, crossed 30 dB attenuated TX/RX-to-RX2 paths,
915 MHz, 0 dB TX/RX gains, one owner/clock, UHD 4.9.0.1 and 2 Msps/channel. QPSK uses
500 kbit/s, the existing unslotted profile and 16-packet TX batches (5824 samples
per host send at a 364-sample transport MTU), with 10 ms data and 20 ms sync lead.
Requested analog bandwidth remains 1 MHz, actual fixed UBX bandwidth 160 MHz.
The QPSK node files were copied temporarily with an absolute profile reference;
only `hardware.device.send_buff_size` differed. Device arguments must be strings.
An initial numeric test argument was rejected by configuration validation before
opening hardware, then corrected; it is not an RF trial.

Host path: `enp132s0`, MTU 1500, 1000 Mb/s full duplex, address 192.168.10.1 to
192.168.10.2. `rmem_max=50000000`, `wmem_max=1048576`, and both socket defaults
212992 bytes. The values remained unchanged. UHD's requested stream buffer is
2,453,333 bytes in each direction. Receive gets that capacity; send is capped at
1,048,576 bytes and emits the known warning. No administrator authentication was
available for a larger-than-cap test, so that comparison remains unmeasured;
no privileged or persistent host-setting change was made.

| UHD TX request | Granted TX bytes | Observed Linux stream `tb` | Decision |
|---|---:|---:|---|
| Default 2,453,333 | 1,048,576 | 2,097,152 | Retain existing setting; warning remains |
| 262,144 | 262,144 | 524,288 | Pilot only; below UHD's reported 307,200-byte recommendation |
| 524,288 | 524,288 | 1,048,576 | Not adopted; mixed latency and one maximum-payload underflow |

Stream `rb` stayed 4,906,666 throughout. Linux reports doubled buffer accounting
through `ss`; these numbers are capacities, not queue residence. Control-port
sockets had separate defaults. See [Linux socket accounting](https://man7.org/linux/man-pages/man7/socket.7.html)
and [UHD transport parameters](https://files.ettus.com/manual/page_transport.html).

### Packet workload results

Normal traffic: 30 seconds, 150 × 256-byte packets per direction at 5 packets/s,
then five seconds drain. Maximum payload: 10 seconds, 10 × 1024-byte packets per
direction at 1 packet/s, then five seconds drain. Diagnostics were enabled in all
runs; CPU is the entire shared process over workload plus drain, counted once.
Runs were sequential with fresh device opens and no concurrent software suite.
A separate observer sampled sockets and host counters once per second in every
application run. Its CPU is outside the reported radio-process total. Latency
pairs are **A→B / B→A**, underflow pairs are **channel A / channel B**. Labels below
identify the executed trials; the buffer column records the actual requested
setting, including the change from the 256 KiB pilot to 512 KiB.

| Trial | TX buffer request (bytes unless noted) | Exact per direction | One-way p95 (ms) | Shared CPU (s) | TX underflows A/B |
|---|---|---:|---|---:|---|
| normal-default-1 | default (1 MiB granted) | 150/150 | 77.558 / 76.359 | 22.805 | 0/0 |
| normal-small-1 | 262144 | 150/150 | 79.912 / 77.853 | 22.603 | 0/0 |
| normal-small-2 | 524288 | 150/150 | 76.578 / 79.638 | 22.345 | 0/0 |
| normal-default-2 | default (1 MiB granted) | 150/150 | 73.505 / 72.845 | 21.745 | 0/0 |
| maximum-default | default (1 MiB granted) | 10/10 | 143.747 / 146.348 | 7.897 | 0/0 |
| maximum-small | 524288 | 10/10 | 129.586 / 125.541 | 7.738 | 0/0 |
| normal-512-repeat | 524288 | 150/150 | 74.869 / 75.537 | 21.555 | 0/0 |
| maximum-default-2 | default (1 MiB granted) | 10/10 | 126.027 / 138.371 | 8.485 | 0/0 |
| maximum-512-2 | 524288 | 10/10 | 123.692 / 124.092 | 7.796 | 0/0 |
| maximum-512-3 | 524288 | 10/10 | 165.270 / 173.126 | 8.258 | 0/1 |
| maximum-default-3 | default (1 MiB granted) | 10/10 | 204.236 / 172.742 | 8.296 | 1/0 |
| maximum-default-4 | default (1 MiB granted) | 10/10 | 145.974 / 147.392 | 8.455 | 0/0 |

All 12 application trials delivered exact payloads with no duplicates or unexpected
payloads: 820/820 per direction in total. Every run ended with empty queues, zero
expiry, no retries, clean shutdown and no additional post-workload stream errors.
Ten runs passed the stream gate; two failed with one TX underflow each. No RX
stream errors, overflow, buffer drops, TX timeouts or timed-late reports occurred.
Thus exact delivery alone must not be reported as full acceptance.

The two normal baseline runs span 72.845–77.558 ms p95 and 21.745–22.805 CPU seconds;
the two 512 KiB normal runs span 74.869–79.638 ms and 21.555–22.345 CPU seconds.
That overlap does not establish a repeatable latency/CPU improvement. The first
two smaller-buffer maximum-payload runs looked faster, prompting a third run
with the candidate first; that run failed, and the subsequent baseline also
failed. A final baseline repeat passed. No setting is promoted from this evidence.
With ten packets per direction, the benchmark's empirical p95 is the largest
observed latency, so the short maximum-payload results are particularly sensitive
to a single stall.

The failed 512 KiB channel-B attempt recorded 20.191 ms elapsed with 9.456 ms initial
hardware lead; its longest submission was 20.145 ms and longest individual send
call 19.135 ms. The failed baseline channel-A attempt recorded 19.857 ms elapsed
with 9.418 ms initial lead, a 19.806 ms longest submission and 10.997 ms longest
send call. These diagnostics point toward intermittent burst-submission delay,
not an established buffer-capacity fix. They do not distinguish host scheduling,
Python execution and driver blocking, nor identify the exact frame that underflowed.
The control/ACK path is included in the workload. Preserve these failures when
comparing future changes.

### Continuous transport and NIC counter investigation

Two separate ten-second UHD C++ runs exercised simultaneous RX/TX on both channels
at 2 Msps each, separate streamers, fc32 CPU samples and sc16 over the wire:

| Transport setup | Aggregate RX samples | Aggregate TX samples | Stream errors | NIC RX-length error increment |
|---|---:|---:|---:|---:|
| Existing automatic frame discovery | 41,003,838 | 40,248,208 | 0 | 6 during initialization |
| Discovery capped to 1472 bytes in both directions | 41,007,335 | 40,248,208 | 0 | 0 |

Both reported zero dropped samples, overruns, underruns, TX/RX sequence errors,
late commands and TX/RX timeouts. Counts include tool start/stop boundaries.
The uncapped run used the same resulting 1472-byte frame size as the capped run;
only the discovery ceiling differed. Neither test changed the NIC MTU.

In every application run, `rx_length_errors` and `rx_errors` increased by six in
the first approximately one second of initialization, then stayed stable through
traffic. All host UDP receive/send-buffer, input, checksum and memory-error deltas
were zero; sampled per-socket drop counters stayed zero. Global UDP `NoPorts`
counts did sometimes increase during the process lifetime and are not equivalent
to buffer errors or attributable solely to this application. No claim is made
that every host networking counter stayed unchanged.

The matching [UHD 4.9 X300 Ethernet source](https://github.com/EttusResearch/uhd/blob/v4.9.0.1/host/lib/usrp/x300/x300_eth_mgr.cpp)
probes frame sizes up to a default ceiling or the user's requested frame size.
The capped control test supports initialization probes as the explanation for
the observed six-count increment. It does not justify ignoring new errors during
streaming. No frame-size override was adopted for cosmetic counter cleanup.

At this rate, sc16 sample payload is 128 Mbit/s per direction before overhead.
The clean continuous tests demonstrate the current workload, not maximum link
capacity or a cure for packet-submission stalls. No evidence here supports a
10 Gb upgrade, RX buffer enlargement, NIC offload/coalescing changes or CPU affinity
as the solution to the intermittent underflow.

### Reproduction and remaining acceptance work

Use `tools/uhd_link_check.py --shared-device --profiles NODE_A NODE_B --duration 30
--rate 5 --payload 256 --timing-diagnostics` on the original QPSK nodes and temporary
copies with a string-valued `hardware.device.send_buff_size: '524288'` on both
nodes. For the lower-load maximum-payload comparison, use `--duration 10 --rate 1
--payload 1024`. Close the device between runs. No permanent JSON is required.
The [setup guide](docs/UHD_SETUP.md#x310-host-transport-assessment) documents socket
inspection, per-process rollback and a reversible administrator-only larger-cap
experiment if future evidence warrants it.

```bash
/usr/libexec/uhd/examples/benchmark_rate \
  --args type=x300,addr=192.168.10.2 --channels 0,1 --multi_streamer \
  --rx_rate 2000000 --tx_rate 2000000 --duration 10 \
  --overrun-threshold 0 --underrun-threshold 0 --drop-threshold 0 --seq-threshold 0
```

For the discovery control, add `recv_frame_size=1472,send_frame_size=1472` to
`--args`. Restore original arguments after the test; no system setting is altered.

Step 8 did not change runtime code, benchmark tools, profiles or host settings.
The Step 7 software result (1540 passed, six long tests excluded) remains the
latest regression run; it was not rerun for these documentation-only changes.
Documentation whitespace and local links were checked. Temporary profiles,
socket traces, logs, reports and downloaded source were removed after summarizing.
The intermittent maximum-payload QPSK underflow is unresolved and must be addressed
before claiming Step 9/reference-radio acceptance. Larger kernel caps, higher
sample rates, other modulations under altered buffers and independent hosts were
not qualified by this assessment.


## X310 burst-submission timing follow-up — 2026-10-15

**Decision:** carry EOB on the final IQ submission and promote the paired X310
QPSK nodes from 16 to 256 transport packets per host call. Keep other nodes at
one packet and retain the existing host/network settings. This addresses two
reproduced starvation windows; it is not a hard real-time scheduling guarantee
or completion of Step 9's full reference-radio comparison.

### Findings and controlled reproduction

The old adapter returned to Python after sending the last IQ, then made a separate
zero-sample EOB call. A scheduling pause in that gap could leave the FPGA waiting
for samples after all payload IQ had arrived. With 16-packet batches, even a
7,936-sample QPSK ACK required two Python-to-UHD calls, exposing a second gap
between IQ submissions. Packet delivery alone did not detect either fault.

Controlled tests inserted the same 20 ms pause after a send returned, once per
peer during the workload. All four runs delivered 10/10 maximum-size payloads
in each direction; stream counters distinguished the failures:

| Pause location | Adapter / QPSK batch | Underflows A/B | Late TX A/B |
|---|---|---:|---:|
| After final IQ, before Python resumes | Separate EOB / 16 | 1 / 1 | 0 / 0 |
| After final IQ, before Python resumes | EOB on final IQ / 16 | 0 / 0 | 0 / 0 |
| After first IQ call, before Python resumes | EOB on final IQ / 16 | 1 / 1 | 1 / 1 |
| After first IQ call, before Python resumes | EOB on final IQ / 256 | 0 / 0 | 0 / 0 |

The 16-packet first call carried 5,824 ACK samples without EOB; the 256-packet
call carried the entire 7,936-sample ACK with EOB before the pause. These are
causal fault-injection checks, not latency benchmarks. An earlier startup-only
injection also reproduced ACK underflows, outside the benchmark's steady-state
counter baseline. A natural 30-second baseline trace delivered 30/30 each without
an underflow; it does not identify which frame caused every historical Step 8
failure or establish the OS-level reason for those pauses.

**Failed intermediate fix retained:** EOB-on-data alone, still at batch 16,
passed a 150/150 normal run and a 10/10 maximum-payload run, but the subsequent
2.5 packets/s maximum-payload run delivered 25/25 each with one underflow and
one late event on B. Its failing attempt lasted 16.693 ms, with 9.507 ms initial
lead and a 15.899 ms largest send call. This ruled out adopting only the EOB fix.

### Adopted implementation

The final IQ call carries EOB; an empty EOB remains only for failed-transfer
cleanup. Partial acceptance advances by accepted samples, preserving the owned
buffer and first-call timestamp/SOB semantics. UHD applies EOB only to the final
transport fragment of a fully accepted call; the unsent remainder can retain
EOB after a partial return. This was checked against UHD 4.9.0.1's
[TX streamer implementation](https://github.com/EttusResearch/uhd/blob/v4.9.0.1/host/lib/include/uhdlib/transport/tx_streamer_impl.hpp)
and [legacy send handler](https://github.com/EttusResearch/uhd/blob/v4.9.0.1/host/lib/transport/super_send_packet_handler.hpp).

The 256-packet cap is 93,184 samples at the measured 364-sample transport MTU.
It fits qualified QPSK ACK (7,936), SYNC (8,448) and authenticated 1,024-byte
DATA (72,960) in one Python-to-UHD call. UHD still fragments internally. The
logical waveform, explicit transport idle, framing, bit rate, gain and 10/20 ms
data/sync lead are unchanged. Normal success no longer sends the old empty-EOB
transport packet (which UHD implemented using a dummy zero sample).

### Conducted qualification

One shared X310 (3267DE0), two UBX-160 channels, crossed TX/RX-to-RX2 paths with
30 dB attenuation, 0 dB gains, 915 MHz, 2 Msps and 1 Gb Ethernet. Requested
1 MHz analog bandwidth remains coerced to 160 MHz. UHD 4.9.0.1, Python 3.14.4,
NumPy 2.3.5. TX socket buffer still requests 2,453,333 bytes and receives
1,048,576; no kernel, NIC, affinity or socket changes were adopted. RF workloads
ran sequentially without the software suite competing for CPU. Ordinary runs
use five seconds of drain time; CPU covers workload plus drain. Latencies below
are same-host application p95, listed A→B then B→A.

| Workload | Exact payloads each direction | p95 A→B / B→A (ms) | Shared CPU (s) |
|---|---:|---:|---:|
| QPSK normal, repeat 1 | 150/150 | 82.71 / 77.42 | 22.815 |
| QPSK heavy, repeat 1 | 25/25 | 158.72 / 143.30 | 9.402 |
| QPSK normal, repeat 2 | 150/150 | 81.18 / 82.31 | 23.088 |
| QPSK heavy, repeat 2 | 25/25 | 137.52 / 139.40 | 8.655 |
| QPSK normal, repeat 3 | 150/150 | 77.16 / 78.42 | 22.131 |
| QPSK heavy, repeat 3 | 25/25 | 143.14 / 162.04 | 8.793 |
| QPSK maximum + recovery | 10/10 | 125.79 / 122.40 | 8.070 |
| BPSK smoke | 50/50 | 86.27 / 85.41 | 9.708 |
| 2FSK smoke | 50/50 | 53.02 / 54.72 | 8.974 |
| MSK smoke | 50/50 | 53.87 / 54.35 | 9.060 |
| GMSK smoke | 50/50 | 56.24 / 56.05 | 9.475 |
| QPSK heavy, diagnostics off | 25/25 | 164.24 / 167.78 | 7.671 |

Normal QPSK: 30 seconds, 5 packets/s, 256-byte payloads. Heavy QPSK: 10 seconds,
2.5 packets/s, 1,024 bytes. Maximum/recovery: 10 seconds, 1 packet/s, 1,024 bytes,
then 17 seconds of muted TX; the follower entered recovery and both directions
resumed within 1.077 seconds after restoration. Other modulation smoke checks:
10 seconds, 5 packets/s, 256 bytes, default batch 1. Diagnostics were enabled
except for the final heavy run, which used the promoted checked-in QPSK nodes.

Every final run had exact delivery, no duplicates or altered payloads, zero
underflows, late TX, TX timeouts, RX overflow/drop/stream errors, decoder drops
or workload retries. Queues and outstanding ACKs drained without expiry;
post-workload stream checks and clean shutdown passed. These short conducted
checks do not qualify B205 hardware, independent hosts, arbitrary new profiles,
or the full Step 9 matrix.

Additional compatibility checks passed with batch 1:

- Simple 250 kbit/s 2FSK: 26/26 exact in each direction, including boundary and
  repeated payloads, a 0.5-second mute, device close/reopen and clean shutdown.
  Submitted byte/sample counts matched, the RX-only peer transmitted zero samples,
  and stream errors were zero. Steady p95 latency A→B/B→A was 27.120/26.082 ms;
  recovery after restore was 20.771/20.804 ms.
- Simple 2FSK at 2 hops/s: six seconds of steady traffic, 12/12 steady and 27/27
  total exact in each direction, 12 deliberate boundary rejections per channel,
  zero steady missed hops/tune deadlines or stream errors, successful off-channel
  interruption recovery and shutdown. This is a short compatibility check, not
  a replacement for Step 7's 60-second qualification or Step 9's final matrix.

Software validation: `pytest -q -k 'not thousand'` passed **1,543 tests**, with
six long thousand-packet acceptance cases deliberately deselected (72.84 s).
The adapter/shared-device subset passed 76 tests, including new regressions for
partial final-batch acceptance, timeout cleanup and hardware draining the final
IQ before Python resumes. Ruff lint and formatting passed (113 files), mypy
passed (47 source files), and `git diff --check` passed. Temporary fault-injection
scripts, per-call traces and benchmark JSON/logs were removed after recording
these final summaries; no new permanent status artifacts were introduced.


## X310 Step 9 qualification closeout — 2026-10-15

**Initial acceptance decision: full reference-radio adoption was withheld.** The
[PSK follow-up below](#x310-psk-echo-acceptance-follow-up--2026-10-15) resolves this
blocker within the documented conducted scope. The original results follow. Normal
traffic,
maximum payloads and recovery passed for all five modulations, but BPSK and QPSK failed
the one-second echo-p95 gate in all three attempts each. All echoes arrived; retries
caused long delays, and one QPSK repeat expired nine pending reliability records. The
zero-underflow result does not make these latency failures a pass. The summaries below
preserve every failed attempt. No waveform/profile defaults or qualification labels were
promoted, and existing release archives were preserved.

Conducted setup: X310 3267DE0, UBX-160 channels 0/1, crossed TX/RX through 30 dB
attenuation to opposite RX2; 915 MHz, 0 dB TX/RX gains, 2 Msps, 1 Gb Ethernet.
Generic-v5 uses the unslotted schema-2 profiles, 600 ms ACK timeout, 10/20 ms data/SYNC
lead and 2 ms minimum lead. QPSK uses batch 256; other modulations use batch 1.
BPSK/2FSK/MSK/GMSK are 250 kbit/s; QPSK is 500 kbit/s with RS parity 16. Requested 1 MHz
bandwidth is coerced to the fixed 160 MHz analog filter (simple 500 kbit/s requests 1.5
MHz). UHD 4.9.0.1 / Python 3.14.4 / NumPy 2.3.5. No host/network tuning or production-
code changes were made for this qualification.

RF workloads ran sequentially, with the software suite run afterward. Generic tests use
temporary signed identities, real localhost UDP and a shared device/clock. Timing
diagnostics are disabled. All generic delivery counts include a five-second drain; CPU
is shared-process CPU during workload plus drain, counted once. Direction pairs are A→B
/ B→A, counter pairs A / B. These are same-host conducted measurements, not independent-
host or target-radio interoperability tests.

### Normal reliable traffic

Three 30-second repeats per modulation, 256-byte payloads, 5 packets/s/direction, 150
packets each way per repeat. Ranges show the three observed measurements, not confidence
intervals.

| Mode | Exact delivery each direction | p95 A→B range (ms) | p95 B→A range (ms) | Shared CPU range (s) | Clean streams / shutdown |
|---|---|---:|---:|---:|---|
| BPSK | 450/450; 450/450 | 78.892–80.420 | 78.163–80.111 | 22.130–22.686 | Pass |
| 2FSK | 450/450; 450/450 | 53.023–53.508 | 53.124–53.472 | 20.895–21.383 | Pass |
| MSK | 450/450; 450/450 | 53.499–53.955 | 53.259–53.726 | 21.441–21.889 | Pass |
| GMSK | 450/450; 450/450 | 53.688–54.781 | 53.705–54.463 | 21.768–22.221 | Pass |
| QPSK | 450/450; 450/450 | 79.571–80.579 | 77.233–79.419 | 20.854–21.159 | Pass |

### Echo

Ten seconds at 5 probes/s, 256 bytes, 50 expected echoes, with five seconds to drain.
Acceptance requires all echoes and p95 ≤1 second. Historical B205 numbers used the
legacy scheduling policy and earlier software; they are context, not a matched revised-
code hardware comparison.

| Mode/run | Returned / expected | RTT p95 (ms) | Historical B205 p95 (ms) | Retries A/B | Stream/shutdown clean | Latency gate |
|---|---:|---:|---:|---|---|---|
| bpsk-echo | 50/50 | 1378.009 | 293.737 | 3 / 2 | Pass | Fail |
| bpsk-echo-repeat-2 | 50/50 | 2016.599 | 293.737 | 4 / 5 | Pass | Fail |
| bpsk-echo-repeat-3 | 50/50 | 2286.720 | 293.737 | 4 / 4 | Pass | Fail |
| 2fsk-echo | 50/50 | 132.487 | 294.573 | 0 / 0 | Pass | Pass |
| msk-echo | 50/50 | 139.531 | 291.578 | 0 / 0 | Pass | Pass |
| gmsk-echo | 50/50 | 120.547 | 299.373 | 0 / 0 | Pass | Pass |
| qpsk-echo | 50/50 | 2357.324 | 934.022 | 9 / 7 | Pass | Fail |
| qpsk-echo-repeat-2 | 50/50 | 5180.091 | 934.022 | 12 / 1 | Pass | Fail |
| qpsk-echo-repeat-3 | 50/50 | 3114.219 | 934.022 | 10 / 3 | Pass | Fail |

### Maximum payload and recovery

Low load: 10 seconds at 1 packet/s, 1,024 bytes, then five-second drain. Afterward both
TX paths are muted for 17 seconds while RX remains active; fresh packets test
restoration. High load reproduces the original comparison: 25 packets over **five
seconds at 5 packets/s**, plus five-second drain. An additional intermediate load uses
25 packets over **ten seconds at 2.5 packets/s**. Counts are delivery by deadline, not
RF-loss estimates or sustainable throughput.

| Mode/workload | Exact received A→B / B→A | p95 A→B / B→A (ms) | Retries A/B | Stream/shutdown clean |
|---|---|---|---|---|
| BPSK / max | 10 / 10 of 10 | 137.955 / 142.233 | 0 / 0 | Pass |
| BPSK / heavy | 25 / 25 of 25 | 145.571 / 141.769 | 0 / 0 | Pass |
| BPSK / original-heavy | 25 / 25 of 25 | 145.997 / 148.756 | 0 / 0 | Pass |
| 2FSK / max | 10 / 10 of 10 | 114.461 / 117.868 | 0 / 0 | Pass |
| 2FSK / heavy | 25 / 25 of 25 | 114.627 / 139.861 | 0 / 0 | Pass |
| 2FSK / original-heavy | 25 / 25 of 25 | 117.043 / 135.085 | 0 / 0 | Pass |
| MSK / max | 10 / 10 of 10 | 119.689 / 121.360 | 0 / 0 | Pass |
| MSK / heavy | 25 / 25 of 25 | 116.103 / 140.450 | 0 / 0 | Pass |
| MSK / original-heavy | 25 / 25 of 25 | 118.437 / 135.561 | 0 / 0 | Pass |
| GMSK / max | 10 / 10 of 10 | 115.527 / 120.615 | 0 / 0 | Pass |
| GMSK / heavy | 25 / 25 of 25 | 119.688 / 126.496 | 0 / 0 | Pass |
| GMSK / original-heavy | 25 / 25 of 25 | 118.319 / 137.217 | 0 / 0 | Pass |
| QPSK / max | 10 / 10 of 10 | 153.245 / 125.366 | 0 / 0 | Pass |
| QPSK / heavy | 25 / 25 of 25 | 155.891 / 177.483 | 0 / 0 | Pass |
| QPSK / original-heavy | 25 / 25 of 25 | 137.690 / 142.986 | 0 / 0 | Pass |

| Mode | Recovery after restore (s) | Both directions delivered | Follower entered recovery |
|---|---:|---|---|
| BPSK | 1.124 | Pass | Pass |
| 2FSK | 1.073 | Pass | Pass |
| MSK | 1.085 | Pass | Pass |
| GMSK | 1.089 | Pass | Pass |
| QPSK | 1.116 | Pass | Pass |

### Counter and backlog audit

All 39 generic runs delivered exact, unique workload payloads and had zero absolute
stream/transmit/shutdown error counters at baseline, workload end and shutdown. Queues
and outstanding ACKs were empty at workload end. Normal and maximum-payload workloads
had zero retries or expiry; the six PSK echo failures had the retries listed above. QPSK
echo repeat 2 recorded **nine expirations on A**, even though all echoes eventually
arrived. Empty final queues therefore do not establish a clean reliability result. No
other workload-end queue drops, expiry, retry exhaustion or endpoint errors were
observed.

The echo runs show a reproducible PSK reliability/latency problem without UHD underflow
or late-TX evidence. They do not identify its root cause. Follow-up should correlate
DATA/ACK transmit and receive times, receiver recognition and retry/expiry decisions
during reflected traffic before changing timeouts or loosening the gate. Failed attempts
must remain in the comparison.

### Simple 2FSK rate qualification

Each rate ran 150 steady 256-byte packets at 5 packets/s, five boundary/repeated
payloads, one deliberately muted packet during a 17-second outage and one fresh recovery
packet. The 156 successful packets per direction exclude the intentional loss.
Directions ran sequentially, using payload input forward and preframed input in reverse.
All passed exact submitted byte/sample counts, zero RX-only transmitted samples, no
ACK/SYNC/retry traffic, zero measured stream errors, close/reopen and clean shutdown.
CPU covers both local app threads during steady traffic, boundaries and outage; do not
compare it directly with the generic workload CPU. Historical B205 p95 is shown for
context; the software has changed since that run.

| Rate (kbit/s) | Direction | Exact delivery | Latency min/p95/max (ms) | Historical B205 p95 (ms) | CPU / elapsed (s) | Recovery (ms) | Minimum device-target gap (ms) |
|---|---|---:|---|---:|---|---:|---:|
| 100 | A→B | 156/156 | 39.447/44.912/47.840 | 49.744 | 17.253 / 47.861 | 23.614 | 107.597 |
| 100 | B→A | 156/156 | 39.547/44.089/45.250 | 49.002 | 17.027 / 47.844 | 21.651 | 108.771 |
| 250 | A→B | 156/156 | 22.979/28.147/29.943 | 31.745 | 17.141 / 47.859 | 21.044 | 159.164 |
| 250 | B→A | 156/156 | 23.058/26.368/27.849 | 31.271 | 17.034 / 47.843 | 18.874 | 162.835 |
| 500 | A→B | 156/156 | 17.027/22.449/22.897 | 25.702 | 16.010 / 47.850 | 20.799 | 179.562 |
| 500 | B→A | 156/156 | 18.409/21.615/22.225 | 25.294 | 15.896 / 47.843 | 19.999 | 181.362 |

### Hopping and continuous transport

Both hopping runs used simple 250 kbit/s 2FSK, 914/915/916 MHz, 100 ms guards, 60
seconds of steady traffic, deliberate boundary violations and an off-channel
interruption. They passed exact delivery, zero stream errors/missed steady hops or tune
deadlines, expected boundary rejection, recovery and shutdown, with no ACK/SYNC/retry
traffic. The deliberate follower pause recorded three recovery missed slots on B and
zero on A in both runs.

| Rate | Steady / total exact, each direction | Boundary rejections A/B | Tune p95 A/B (ms) | Tune max A/B (ms) |
|---|---|---|---|---|
| 1 hop/s | 60/60; 69/69 | 6 / 6 | 23.435 / 23.230 | 23.682 / 23.604 |
| 2 hops/s | 120/120; 135/135 | 12 / 12 | 23.278 / 23.518 | 23.588 / 23.834 |

The historical B205 1-hop/s retune p95 was 29.624/29.674 ms. Current X310 measurements
retain the shorter retune result; neither this comparison nor same-host hopping
qualifies independent clocks or a target radio's hop protocol.

The separate UHD C++ `benchmark_rate` test used both channels simultaneously,
independent streamers, RX/TX at 2 Msps for ten seconds. It received 40,998,072 samples
and transmitted 40,248,208 across the channels, including startup/stop boundaries.
Dropped samples, overruns, underruns, RX/TX sequence errors, late commands and RX/TX
timeouts were all zero. The TX socket-buffer warning remains: 2,453,333 bytes requested,
1,048,576 granted. No kernel or NIC tuning was applied.

### Comparison and scope

Normal-load delivery/backlog and latency improved substantially from the original X310
measurement: all modes now deliver 150/150 each way per repeat, with p95 approximately
53–81 ms. The original X310 BPSK delivered 95/90 and QPSK 149/150 in that window. The
original heavy 25-packet workload had substantial backlog; all current modes deliver
25/25 each way with no retries. The matched offered loads support an
application/configuration improvement, not a hardware-only claim: the revised unslotted
link policy, receive path and submission behavior differ.

Simple-frame p95 is lower than the recorded B205 result at all three rates, but those
B205 measurements predate the receive/submission changes. Shared-process normal CPU is
roughly 21–23 seconds; do not duplicate it per channel or infer a hardware advantage
from differing execution policies. PSK echo remains worse than its historical B205
context and exceeds the explicit acceptance gate.

Retain the X310 as the current development bench with the scoped passing workloads; do
not declare it the universally better or fully accepted reference radio. The B205s were
not connected during this closeout, so revised-code B205 qualification remains deferred.
Independent hosts/oscillators, calibrated RF power/spectrum, over-the-air behavior,
arbitrary new profiles and actual-target interoperability remain outside these conducted
checks. No release archives were rebuilt.

### Paired CLI lifecycle and probe correction

Actual `python -m packet_radio --config NODE_A --peer-config NODE_B` checks
passed for authenticated QPSK and ACK-free simple 250 kbit/s 2FSK. Each used
10 seconds at 5 packets/s with 256-byte payloads and a five-second drain:
50/50 exact, unique payloads in each direction, zero measured stream/endpoint
errors, SIGTERM shutdown exit 0 and successful device reopen between modes.
These are two local UDP probes against one paired process, not two physical hosts.

Two initial QPSK launcher/probe attempts stopped **before workload traffic** with
an effective-configuration mismatch; both radio processes shut down with exit 0.
The UDP checker still compared bare configuration against runtime status that
also includes `packet_format` and the resolved `processing` description. It now
resolves and compares those fields too, retaining strict mismatch rejection.
The temporary harness also matches its status-output setting to the launched
configuration. Neither initial attempt is counted as a passing traffic run.

The tool correction changes no radio waveform or runtime scheduling. Three
regression cases verify actual generic/simple application snapshots and reject
a wrong processing description before opening UDP sockets. All 16 probe tests
passed, followed by the two successful hardware CLI checks above.

### Final software verification and retained artifacts

The full `pytest -q` suite passed **1,552 tests in 324.10 seconds**, with no
long-test exclusions. Ruff lint and formatting passed (113 files), mypy passed
for all 47 runtime source files, and `git diff --check` passed. The software suite
ran after the RF checks; it did not compete with their workload measurements.
The UDP probe correction and its regression tests are the only code changes
from this closeout; no radio-runtime change or release rebuild was made.

Final summaries remain in this document, with acceptance status in
`docs/X310_IMPROVEMENT_PLAN.md` and the setup guide updated. Temporary benchmark
JSON/logs, generated node files, scripts and signing/replay material were removed.
All radio processes were closed. At this checkpoint, full X310 reference adoption
remained withheld
pending the PSK echo fix and its acceptance reruns; revised-code B205 hardware
comparison remains deferred until those devices are available.

## X310 PSK echo acceptance follow-up — 2026-10-15

**Outcome: the BPSK/QPSK echo blocker is resolved on the documented conducted
X310 bench.** All 16 final affected-workload checks passed with unchanged traffic,
one-second echo limit, ACK timeout and hardware settings. Together with the
preceding Step 9 FSK/simple/hopping/transport/CLI results, this closes the scoped
X310 functional acceptance matrix. It does not establish hardware superiority
over B205s or qualification of independent hosts or target-radio interoperability.
The earlier failures above remain part of the record.

### Cause and receive-path correction

Both PSK receivers could select a later, stronger preamble before an earlier
valid frame in the same IQ buffer. Consuming the later frame discarded the
earlier ACK or DATA, causing retries and long echo delays despite clean UHD
counters. A diagnostic QPSK baseline reproduced 4.930 seconds echo p95.
Replaying identical captured IQ with the corrected search recovered an additional
earlier frame in eight of 118 buffers. The old search failed 28 of 32 new
coalesced-frame regression cases; the corrected search passed all 32.

Both receivers now group timing/alignment hypotheses by arrival and rank quality
within each group. BPSK also uses length-aware live searches to avoid waiting for
a long energy tail. It waits for the receive shaping tail and two symbols of live
lookahead before committing a frame or failure, allowing neighboring alignments
to finish. Opposite-polarity correlation reuse, exact float64 correlation sums and
skipping provably impossible constant decisions limit the added search cost.
QPSK retains its previous live-search scheduling. Transmitted waveforms, packet
formats, FEC, authentication, profiles, gains, batching and timeouts are unchanged.

Intermediate BPSK results are retained to distinguish ordering correctness from
latency acceptance: grouping alone returned all 50 echoes without retries but
had 3.559-second p95; polarity reuse alone still took 1.968 seconds. Faster
correlation/pruning produced 0.915/0.821-second pilots, but a diagnostics-enabled
repeat still failed at 1.155 seconds. Its receive-buffer tail p95 was about
56 ms. Early length-aware search reduced tail p95 to about 2 ms and echo p95 to
0.524 seconds, but produced one FEC/invalid-frame event and one retry. The final
lookahead corrected that premature failure; its diagnostics-enabled pilot returned
50/50 at 0.147 seconds with no invalid frames, retries or stream errors. A QPSK
ordering-fix pilot also returned 50/50 at 0.147 seconds without retries/errors.
These development pilots are separate from the diagnostics-disabled acceptance
repeats below; the earlier failed implementations are not accepted settings.

### Final hardware repeats

Same X310 3267DE0/UBX-160, crossed 30 dB paths, 915 MHz, 0 dB gains, 2 Msps and
1 Gb Ethernet as Step 9. Generic-v5 unslotted BPSK is 250 kbit/s; QPSK is
500 kbit/s with RS parity 16. ACK timeout remains 600 ms, TX leads 10/20 ms,
and host batch sizes BPSK 1 / QPSK 256. UHD 4.9.0.1, Python 3.14.4 and NumPy
2.3.5. RF runs were sequential; software tests ran afterward. Diagnostics were
disabled for all 16 final runs. Every run passed exact, unique delivery, zero
invalid/FEC-uncorrectable packets, retries, expiry, drops, exhausted retries,
stream/timing/endpoint errors and pending DATA/ACK queues at the workload
snapshot. Recovery and shutdown checks also passed without stream errors.

Echo repeats: ten seconds, 5 probes/s, 256 bytes, 50 echoes, five-second drain.

| Mode | Returned, each of three runs | Echo p95 by run (ms) | Shared CPU by run (s) | One-second gate |
|---|---|---|---|---|
| BPSK | 50/50 | 129.241 / 125.160 / 128.924 | 11.763 / 10.034 / 10.784 | Pass 3/3 |
| QPSK | 50/50 | 138.677 / 136.504 / 140.314 | 9.035 / 8.277 / 8.941 | Pass 3/3 |

Normal repeats: 30 seconds, 5 packets/s/direction, 256 bytes, five-second drain.
Each run delivered 150/150 each way, totaling 450/450 per direction per mode.
Ranges are observed repeat ranges, not confidence intervals.

| Mode | p95 A→B range (ms) | p95 B→A range (ms) | Shared CPU range (s) |
|---|---:|---:|---:|
| BPSK | 58.014–60.498 | 58.433–60.276 | 29.959–30.375 |
| QPSK | 60.823–63.838 | 62.249–64.163 | 21.781–22.288 |

Maximum payload remains 1,024 bytes. Lower load is ten seconds at 1 packet/s;
heavy load is five seconds at 5 packets/s. Both have a five-second drain.

| Mode/workload | Exact delivery each way | p95 A→B / B→A (ms) | Shared CPU (s) |
|---|---|---|---:|
| BPSK / lower load | 10/10 | 135.450 / 134.006 | 8.540 |
| BPSK / heavy | 25/25 | 120.559 / 131.648 | 7.413 |
| QPSK / lower load | 10/10 | 121.118 / 108.897 | 6.812 |
| QPSK / heavy | 25/25 | 114.291 / 108.886 | 6.173 |

After each lower-load run, both transmit paths were muted for 17 seconds while RX
continued. The follower entered recovery; fresh traffic arrived both ways after
restoration in 1.107 seconds for BPSK and 1.099 seconds for QPSK. Shared CPU above
covers workload and drain only, counted once for both endpoints.

### Comparison, tradeoff and scope

Echo p95 improves from the failed Step 9 ranges of 1.378–2.287 seconds (BPSK)
and 2.357–5.180 seconds (QPSK), with retries now zero. Historical B205 echo p95
was 293.737 ms for BPSK and 934.022 ms for QPSK; those runs used earlier software
and link scheduling, so they do not establish a hardware-only advantage.

Normal p95 improves from approximately 78–80 ms for BPSK and 77–81 ms for QPSK.
BPSK shared CPU rises from 22.130–22.686 seconds to 29.959–30.375 seconds over
the same 35-second workload/drain window (roughly 85–87% of one CPU core).
QPSK rises slightly from 20.854–21.159 to 21.781–22.288 seconds. The additional
BPSK work is an explicit latency/correctness tradeoff, not a CPU optimization
claim; higher-rate or weaker-host capacity requires separate measurement.

Unchanged FSK modes, simple 2FSK, hopping, continuous UHD transport and paired CLI
retain the preceding Step 9 hardware evidence; they were not rerun in this PSK
follow-up. The X310 is accepted as the development reference for that documented
same-host conducted envelope. Revised-code B205 comparison, independent radios
and clocks, calibrated RF/spectrum, over-the-air behavior and arbitrary target
profiles remain unqualified. Host/network settings, profile qualification labels
and release archives remain unchanged. The existing TX socket-buffer warning
remains (2,453,333 bytes requested; 1,048,576 granted).

### Software verification and retained artifacts

The full suite passed **1,600 tests in 304.84 seconds**, with no exclusions.
This includes 48 new parametrized PSK regression cases covering coalesced frames,
arrival timestamps, fragmented live input, pulse shaping, long energy tails and
partial final symbols. The existing idle-history test now supplies the short
trailing samples that continuous live RX provides for timing lookahead. The
842-test modem subset also passed. Ruff lint/format passed for 113 files, mypy
passed for all 47 runtime files, and whitespace checks passed.

Final summaries remain here, with acceptance status and receive behavior updated
in the existing X310 plan and UHD setup guide. Temporary IQ, JSON reports, traces,
logs, generated nodes and investigation scripts were removed after analysis.
All benchmark/radio processes were closed; no additional status files or release
artifacts were created.

## B205mini revised-code comparison — 2026-10-15

**Decision: retain the X310 as the current reference for the documented full
conducted workload matrix.** The same revised software gives the B205s competitive
steady FSK latency, but their PSK retries, QPSK delivery/overflow failures and
reliable-link recovery failures prevent equivalent acceptance. These results
compare complete tested setups, not isolated RF hardware capability; the B205s
have separate clocks and USB transports, unlike the shared-clock X310.

### Setup and workload matching

B205mini A **3457464**, B **3457480**, both USB 3 at 5 Gbit/s on bus 006 of the
same workstation, crossed TX/RX → 30 dB attenuation → opposite RX2. UHD
4.9.0.1-1ubuntu1 / Python 3.14.4 / NumPy 2.3.5. Actual settings confirmed
915 MHz, 2 Msps, TX 20 dB/RX 40 dB and 1 MHz bandwidth; simple 500 kbit/s uses
1.5 MHz. These are the previously tested B205 gain settings; X310 used 0/0 dB
and its fixed 160 MHz analog filter. Equal numeric gains would not establish
equal RF levels across these different front ends.

Temporary schema-2 node overlays selected the same generic unslotted-v5 profiles
and host settings as the final X310 runs, substituting B205 serials/channel 0
and the gains above. Rates: 250 kbit/s BPSK/2FSK/MSK/GMSK; 500 kbit/s QPSK with
RS parity 16. ACK timeout 600 ms, DATA/SYNC lead 10/20 ms, minimum lead 2 ms,
queue age five seconds; QPSK batch 256, other modes batch 1. No radio code,
waveform, timeout, gain or host tuning changes were made during the comparison.
The X310 stayed disconnected; its final recorded results provide the comparison.

All RF workloads ran sequentially without software tests competing for CPU.
Generic traffic used temporary signing identities and real localhost UDP, with
one process per B205. This remains one-host testing, not two-computer acceptance.
The 34 generic runs comprise three normal repeats per mode, three echo repeats
for each PSK mode and one for each FSK mode, plus lower/heavy maximum-payload
checks for every mode. Every attempted run is retained below, including failures.
The tool's combined delivery/latency/stream/recovery gate passed 28 runs and
failed six: two QPSK echoes, both QPSK maximum-payload runs, and the MSK/GMSK
recovery runs. Passing that gate does not imply zero rejected candidates or
retries; those counters are reported separately below.

### Normal and echo traffic

Normal: 30 seconds, 5 packets/s/direction, 256 bytes, five-second drain.
All five modes delivered **150/150 exact, unique packets each way in each of
three repeats**, totaling 450/450 per direction per mode. Ranges are observed
repeat ranges. Counter pairs are A/B; latency direction pairs are A→B/B→A.
CPU is the sum of both B205 worker processes during workload plus drain; it
excludes the parent traffic driver, while X310's shared-process CPU includes its
driver. These differing execution models limit CPU-only hardware conclusions.

| Mode | B205 p95 A→B range (ms) | B205 p95 B→A range (ms) | Latest X310 p95 across directions/repeats (ms) | B205 combined worker CPU (s) |
|---|---:|---:|---:|---:|
| BPSK | 54.211–311.554 | 54.075–558.340 | 58.014–60.498 | 21.162–22.191 |
| 2FSK | 48.101–48.755 | 48.220–49.751 | 53.023–53.508 | 16.683–17.696 |
| MSK | 48.348–49.254 | 48.801–51.927 | 53.259–53.955 | 16.344–17.900 |
| GMSK | 49.350–50.824 | 49.298–50.523 | 53.688–54.781 | 16.742–18.873 |
| QPSK | 521.313–525.907 | 47.754–48.760 | 60.823–64.163 | 16.462–17.698 |

BPSK normal workload retries were 0/0, 0/2 and 3/0; invalid/FEC-uncorrectable
candidate counts were 10/11, 20/13 and 8/16. QPSK retries were 0/3, 0/4 and 0/4,
with invalid counts 3/0, 4/0 and 4/0. All FSK-family normal repeats had zero
workload retries and invalid frames. Normal runs had no new stream errors,
queue expiry, exhausted retries or pending DATA/ACK queues at the snapshot.
Rejected candidates alone are not a measured packet-loss count.

Echo: ten seconds, 5 probes/s, 256 bytes, 50 echoes, five-second drain.
All returned 50/50; the unchanged latency gate is p95 ≤1 second.

| Mode/run | B205 echo p95 (ms) | Retries A/B | Invalid candidates A/B | Latency gate |
|---|---:|---|---|---|
| BPSK / 1 | 123.767 | 0 / 0 | 4 / 7 | Pass |
| BPSK / 2 | 121.994 | 0 / 0 | 5 / 2 | Pass |
| BPSK / 3 | 122.534 | 0 / 0 | 2 / 3 | Pass |
| 2FSK / 1 | 116.419 | 0 / 0 | 0 / 0 | Pass |
| MSK / 1 | 115.528 | 0 / 0 | 0 / 0 | Pass |
| GMSK / 1 | 117.458 | 0 / 0 | 0 / 0 | Pass |
| QPSK / 1 | 587.139 | 1 / 1 | 1 / 1 | Pass |
| QPSK / 2 | 1089.720 | 0 / 2 | 2 / 0 | Fail |
| QPSK / 3 | 1487.900 | 1 / 4 | 4 / 1 | Fail |

Latest X310 echo p95: BPSK 125–129 ms, QPSK 137–140 ms; 2FSK/MSK/GMSK
132.487/139.531/120.547 ms. All had zero retries and passed. B205 QPSK failed
two of three echo repeats despite complete delivery. All B205 echo runs had
clean measured UHD stream counters, no queue expiry and no pending DATA/ACK
queues at the end of the workload.

### Maximum payload and reliable-link recovery

Payload 1,024 bytes: lower load is ten seconds at 1 packet/s/direction; heavy
load is five seconds at 5 packets/s/direction. Each has a five-second drain.
Counts are delivery by deadline, not RF-loss estimates or sustainable capacity.

| Mode/load | Received A→B / B→A | p95 A→B / B→A (ms) | Workload retries A/B |
|---|---|---|---|
| BPSK / lower | 10/10 / 10/10 | 805.891 / 803.123 | 1 / 1 |
| BPSK / heavy | 25/25 / 25/25 | 140.790 / 1263.831 | 2 / 0 |
| 2FSK / lower | 10/10 / 10/10 | 107.006 / 108.035 | 0 / 0 |
| 2FSK / heavy | 25/25 / 25/25 | 106.702 / 107.771 | 0 / 0 |
| MSK / lower | 10/10 / 10/10 | 105.135 / 108.867 | 0 / 0 |
| MSK / heavy | 25/25 / 25/25 | 105.956 / 107.658 | 0 / 0 |
| GMSK / lower | 10/10 / 10/10 | 110.167 / 108.445 | 0 / 0 |
| GMSK / heavy | 25/25 / 25/25 | 108.725 / 110.320 | 0 / 0 |
| QPSK / lower | 9/10 / 10/10 | 2477.393 / 100.582 | 0 / 13 |
| QPSK / heavy | 21/25 / 25/25 | 4901.931 / 1813.676 | 3 / 8 |

Only QPSK recorded new UHD errors: five RX overflows at A in the lower-load
run and one at A in the heavy run. Its lower-load snapshot recorded one retry
exhaustion at B; the heavy snapshot recorded four expired queue entries at B.
Other modes had complete maximum-payload delivery and no workload stream errors.
X310 passed both loads in all modes without retries or stream errors.

Each lower-load run then muted both TX paths for 17 seconds while RX continued.
The follower entered recovery in every case. Fresh packets were allowed five
seconds after restore; these outcomes are separate from steady-workload delivery.

| Mode | Both directions recovered within five seconds | Time (s) | Additional retries A/B from workload snapshot through shutdown | Additional exhausted retries A/B | Additional expired entries A/B |
|---|---|---:|---|---|---|
| BPSK | Pass | 1.192 | 1 / 0 | 0 / 0 | 0 / 0 |
| 2FSK | Pass | 3.131 | 4 / 0 | 0 / 0 | 0 / 0 |
| MSK | Fail | 5.002 (deadline) | 4 / 0 | 1 / 0 | 0 / 1 |
| GMSK | Fail | 5.002 (deadline) | 4 / 0 | 1 / 0 | 0 / 1 |
| QPSK | Fail | 5.000 (deadline) | 4 / 0 | 1 / 0 | 0 / 1 |

Thus clean steady FSK traffic does **not** imply reliable-link recovery passed:
MSK and GMSK failed, as did QPSK. 2FSK recovered but required four additional
retries; BPSK recovered with one additional retry/invalid candidate. No new
stream errors appeared after the workload snapshots. Device shutdown completed
in all 34 runs, including failed traffic/recovery cases. X310's corresponding
recovery checks all passed; its PSK follow-up measured 1.107/1.099 seconds for
BPSK/QPSK without retries.

Startup is not included in zero-workload-error claims. Before measurement,
RX software-buffer drops ranged **722–1,166 at A / 838–955 at B** across the
34 runs, with 0–1 retries and 0–1 decoder timestamp discontinuities per endpoint.
Startup invalid frames and UHD RX overflows were zero. This retains the earlier
B205 startup-drop limitation rather than concealing it in steady-state deltas.

### ACK-free simple 2FSK

At each rate, each physical direction sent 150 steady 256-byte packets at
5 packets/s, five boundary/repeated payloads and one fresh recovery packet
after a 17-second outage. The deliberately muted packet did not arrive or retry.
All six direction/rate runs passed **156/156 exact**, exact submitted IQ
bytes/sample counts, no ACK/SYNC/retry traffic, zero RX-only TX samples and
zero measured stream errors, including clean close/reopen and shutdown.
A→B used payload input; B→A used preframed input. CPU includes both app threads,
boundaries and outage and is not comparable to the generic workload CPU window.
Startup overflow indications occurred outside the measured baseline.

| Rate (kbit/s) | Direction | B205 steady min/p95/max (ms) | X310 p95 (ms) | B205 CPU / elapsed (s) | Recovery (ms) | Minimum device-target gap (ms) |
|---|---|---|---:|---|---:|---:|
| 100 | A→B | 41.357/45.614/47.101 | 44.912 | 12.752 / 47.851 | 23.252 | 105.267 |
| 100 | B→A | 40.491/44.515/46.842 | 44.089 | 11.414 / 47.846 | 22.771 | 108.035 |
| 250 | A→B | 23.855/27.976/30.907 | 28.147 | 13.591 / 47.847 | 19.132 | 161.965 |
| 250 | B→A | 23.442/27.624/28.973 | 27.849 | 14.949 / 47.846 | 21.883 | 161.774 |
| 500 | A→B | 17.664/22.145/22.649 | 22.449 | 14.289 / 47.850 | 21.810 | 179.468 |
| 500 | B→A | 17.403/22.540/23.094 | 21.615 | 12.415 / 47.844 | 20.731 | 179.969 |

These short simple-radio measurements are similar across the two setups;
the small latency differences do not support a blanket hardware winner.
The reliable-link recovery failures above do not occur in these ACK-free tests.

### Hopping and continuous USB transport

Simple 250 kbit/s 2FSK hopping at 914/915/916 MHz, 100 ms guards and **1 hop/s
for 60 seconds** passed 60/60 steady and 69/69 total exact packets each way.
Both endpoints recorded six expected boundary rejections, zero stream errors
and zero missed steady hops/tune deadlines. The deliberately paused follower
recorded three recovery missed slots; the leader recorded zero. Off-channel
recovery, no ACK/SYNC/retry traffic and shutdown passed.

| Device | Retune min/p95/max (ms) | Retune standard deviation (ms) | Maximum TX clock-mapping bracket (ms) |
|---|---|---:|---:|
| B205 A / 3457464 | 18.444 / 29.562 / 29.966 | 3.269 | 0.443 |
| B205 B / 3457480 | 18.910 / 29.819 / 30.278 | 3.050 | 0.433 |

X310's matching one-hop/s retune p95 was 23.435/23.230 ms, shorter than these
B205 measurements. The tool currently restricts the separate two-hop/s profile
to the shared X310; no B205 two-hop/s capability limit is inferred from that
software restriction. This is same-host UTC hopping, not independent-host timing
or compatibility with an external radio's hop protocol.

Two UHD C++ `benchmark_rate` processes ran concurrently, one per B205, each
receiving and transmitting continuously at **2 Msps for ten seconds**:

| Device | Received samples | Transmitted samples | Dropped/overrun/underrun/sequence/late/timeout counters |
|---|---:|---:|---|
| 3457464 | 20,047,424 | 20,057,280 | All zero |
| 3457480 | 20,047,414 | 20,057,280 | All zero |

Both processes exited successfully. This demonstrates that the tested USB setup
can sustain the raw transport workload; it does not waive QPSK application RX
overflows or identify their cause. X310 also passed its corresponding continuous
transport check. No USB, kernel, NIC or host scheduling tuning was applied.

### Disposition and retained artifacts

The B205 comparison that was previously deferred is now complete, with failures
preserved. Keep X310 as the reference for the documented conducted matrix:
its PSK delivery/latency and reliable-link recovery are more consistent, and
retune p95 is shorter. B205 steady FSK and simple-radio latency remain competitive;
the result is not a claim that every workload runs faster on X310.

B205 follow-up should isolate PSK candidate rejection and long-frame RX overflows,
then investigate reliable-link recovery across independent devices/clocks. The
present measurements do not prove whether RF gain, oscillator differences,
decoder behavior or scheduling causes each failure. The matching profile and
load comparisons are useful, but different front ends, gains, clocks and process
models prevent attributing every difference to hardware alone.

No runtime code, permanent node configurations, qualification labels or release
archives changed during this benchmark. The preceding 1,600-test software result
remains the code-verification baseline; the suite was not rerun for these
hardware measurements and Markdown-only updates. Final report counts, failure
counters, recovery outcomes, shutdown results and whitespace were checked.
The existing plan and UHD guide now reflect the completed comparison. Temporary
JSON, logs, generated node overlays and benchmark scripts were removed after
retaining these summaries. All radio/benchmark processes were closed.


## Terminal interface X310 qualification (2026-10-20)

**Disposition: terminal interface accepted for the conducted X310 scope below.**
All five generic modulations, custom simple 2FSK and phase-coordinated hopping
passed matching CLI and terminal-UI workloads. This completes Step 9 of the
terminal-interface plan. Arbitrary arrival timing in the existing ACK-free
hopping mode retains the explicit boundary-drop behavior documented below.

### Setup and matching

X310 `3267DE0` at `192.168.10.2`, two UBX-160 channels, each TX/RX crossed through
30 dB attenuation into the other channel's RX2. Both channels used 2 Msps,
TX/RX gains 0 dB, and 915 MHz for fixed-frequency traffic. UHD 4.9.0.1,
Python 3.14.4, NumPy 2.5.4 and Textual 8.2.8. UBX read back its fixed 160 MHz
analog bandwidth for the requested 1 MHz; no host/NIC/socket-buffer tuning changed.

Generic modes used `generic-*-unslotted-v5` definitions and the X310 node settings,
with temporary signed identities and persisted replay state shared by each matched
CLI/UI pair. QPSK used the existing 500 kbit/s, 256-packet host batching node;
the other generic modes used 250 kbit/s and the existing default batching.
Custom simple 2FSK used sync `d391d391` and CRC32-ISO-HDLC, retaining the 32-byte
`55` preamble, two-byte variable length, no FEC and 125 kHz deviation.
Hopping used the existing X310 profile: 914/915/916 MHz, 2 hops/s, 100 ms guards
and 10 ms timed TX lead. All compared effective-configuration hashes matched.

Each mode ran headless CLI first and UI second, sequentially. The UI used its
normal terminal driver in a 120×45 pseudoterminal, with automated keyboard/dialog
input and F12 visible throughout traffic; this was not Textual's headless test
driver. UDP probes ran in separate processes in both controls. Earlier exploratory
BPSK/2FSK probes shared the UI process and were excluded from this comparison
because rendering could delay probe timestamps. The corrected matched runs below
retain the same payload sizes, rates, drain windows and acceptance gates.

Each positive run offered ten seconds at 5 packets/s/direction, 256 bytes, then
a separate ten-second 5-probe/s echo/reflector workload. Each phase had a
five-second drain. Every one of the 14 runs delivered **50/50 exact packets in
each direction and 50/50 exact echoes**, with no duplicate/unexpected payloads.
The unchanged echo gate was p95 ≤1 second. No new stream errors, transmit drops,
queue drops/expiry, retries or retry exhaustion occurred in these positive windows;
all final payload and ACK queues were empty. Every radio and terminal process
exited 0. No software tests competed for CPU during RF measurements.

### Matched results

| Mode | CLI echo p95 (ms) | UI echo p95 (ms) | Shared radio CPU, CLI / UI (s) | UI process CPU (s) |
|---|---:|---:|---:|---:|
| BPSK, 250 kbit/s | 141.500 | 121.705 | 21.930 / 22.630 | 3.007 |
| 2FSK, 250 kbit/s | 115.085 | 116.291 | 16.920 / 18.060 | 3.081 |
| MSK, 250 kbit/s | 114.389 | 116.769 | 17.860 / 18.360 | 3.122 |
| GMSK, 250 kbit/s | 117.782 | 119.635 | 17.870 / 18.260 | 3.203 |
| QPSK, 500 kbit/s | 149.361 | 141.642 | 18.480 / 19.000 | 2.925 |
| Custom simple 2FSK, 250 kbit/s | 57.322 | 58.729 | 14.530 / 15.730 | 3.000 |
| Hopping simple 2FSK, 250 kbit/s | 257.006 | 255.154 | 14.800 / 15.470 | 3.560 |

CPU includes the offered-load, coordinated-start and drain window: approximately
33.06 seconds for fixed cases and 34 seconds for hopping. Shared-radio CPU is
counted once for both channels. UI-process CPU includes rendering and automation;
probe CPU is separate. The UI added about 3–3.6 CPU seconds in these windows.
The largest observed UI echo-p95 increase was 2.380 ms (MSK). No comparison
crossed the investigation threshold of an increase greater than both 20 ms and
20% of its matching CLI p95. These are short acceptance runs at the stated loads.

Every UI run produced 67–68 matching live snapshot observations without stale
status. At this sampling rate, at most one queued payload and one pending ACK
were observed; final queues were empty. Hopping status showed all three active
frequencies on both channels, changing delivery counters and RF/payload activity.

### Hopping boundary behavior and diagnostics

An unaligned headless echo attempt delivered **40/50**, with ten selected bursts
dropped at node A before RF submission. Its normal traffic still delivered 50/50
both ways. This failed exact delivery despite a passing 193.719 ms echo p95 and
zero stream errors. The cause was the existing whole-burst admission contract,
not the UI: a selected burst that cannot finish before the trailing guard is
counted as `hop_burst_rejections`/`transmit_drops` and is not retried on another hop.

The positive hopping comparison kept the same 5 packets/s, 256 bytes, duration
and gates. Both traffic and echo start times were coordinated to 25 ms after a
500 ms dwell boundary, allowing each selected burst its full airtime and timed
lead before the trailing guard. No guard, lead, queue or on-air behavior changed.
It passed all 50-packet gates. Both controls had one initial tune-deadline count
per node from startup during a dwell; neither accumulated further tune-deadline
misses or missed slots during measured traffic.

A separate UI boundary test started 90 ms after a dwell boundary and offered
five packets each way at the same rate. Each node rejected exactly one selected
burst: **4/5 delivered, one admission rejection and one TX drop per node**, as
expected for this negative test. F12 displayed those counts and the hop-window
explanation while the radio remained operational. The UI now also displays hop
slot/count, retune duration and missed-slot/tune-deadline counters for hopping
profiles. Unsynchronized application arrivals are not qualified for lossless
hopping by these results.

The existing UDP probe originally rejected hopping before sending because it read
the fixed-only center-frequency property. It now compares the status gauge against
the first scheduled frequency and still checks the entire effective configuration.
Tests cover actual hopping snapshots, exact traffic/echo over loopback and rejection
of a changed hop table sharing the same first frequency. This changes the test
adapter, not radio framing, admission or synchronization.

### Lifecycle, verification and retained artifacts

Every UI case reopened saved settings, launched a shared pair, displayed live
status, stopped and quit. The custom-framing case then edited both nodes from
915 to 916 MHz in the editor, saved/reopened them and restarted with a new run ID.
Both hardware readbacks showed 916 MHz; five fresh payloads in each direction
arrived exactly, followed by a clean Ctrl+C stop. Source profile qualification
labels were not broadened; edited combinations remain custom outside this scope.

The Step 8 full-suite baseline remains 1,809 passes and 89.55% coverage. After the
probe correction and conditional hopping display, all 37 affected probe, monitor
and run-UI checks passed on Python 3.10.21 and 3.14.4; lint, formatting and type
checks passed. No B205, independent-host, calibrated RF-mask or commercial-radio
interoperability qualification is implied. Final summaries are retained here;
temporary reports, signed keys, replay state, terminal capture and generated node
files are removed after closeout. Release archives are refreshed with the final
interface, diagnostics, guides and this summary.


## X310 experimental hop-rate ceiling — 2026-10-20

**Outcome: 3.5 hops/s was the highest tested rate to pass every check, including
three independently opened 60-second runs.** Rates of 3.55 and above failed this
workload. This measures the present software/configuration envelope, not the
X310's absolute hardware maximum. The normal GUI/profile limit remains 2 hops/s;
these measurements do not automatically qualify arbitrary user configurations.

Setup: detected X310 serial 3267DE0 at 192.168.10.2, shared UBX-160 channels 0/1,
UHD 4.9.0.1, same host, existing crossed attenuated RF paths. Retained the X310
hopping profile's 914/915/916 MHz cycle, 250 kbit/s 2FSK, 2 Msps, 0 dB gains,
100 ms post-tune/trailing guards and 10 ms timed TX lead. No NIC/kernel/FPGA or
production timing changes. UHD again reported its existing 1 MiB send-buffer
limit; the passing runs recorded no stream errors.

`tools/hopping_check.py --experimental-rate` overrides only the in-memory dwell
after normal application initialization; it does not relax saved-profile
validation. Both directions queue one unique 1024-byte payload at 100 ms into each
dwell. Production readiness and whole-burst admission determine actual transmit
timing. This early-queued maximum-packet workload differs from the historical
256-byte workload injected at 200/300 ms. Following the steady phase, checks cover
1/64/1024-byte payloads, deliberately late maximum packets, a three-dwell
interruption and recovery, frequency/whole-burst timestamp bounds, and shutdown.
Each steady payload must arrive exactly and within its intended dwell.

| Rate (hops/s) | Dwell (ms) | Short-screen steady RX channel 0 / channel 1 | Result |
|---|---:|---|---|
| 2 | 500.000 | 24/24 / 24/24 | All checks passed |
| 3 | 333.333 | 36/36 / 36/36 | All checks passed |
| 3.5 | 285.714 | 42/42 / 42/42 | All checks passed; repeated below |
| 3.55 | 281.690 | 43/43 / 43/43 | Failed subsequent mixed-size boundary delivery |
| 3.6 | 277.778 | 43/44 / 44/44 | One steady burst rejected on TX channel 1 |
| 3.7 | 270.270 | 20/45 / 25/45 | 20/25 steady burst rejections on TX channels 0/1 |
| 4 | 250.000 | 0/48 / 0/48 | All maximum-size steady bursts rejected |

Short screens requested 12 seconds, rounded up to whole dwells. Failed screens
are not credited with passing the later recovery/boundary gates. An initial
2-hop/s early-queue run passed steady/boundary delivery but exposed the probe's
recovery timing assumption: the interrupted receiver resumes 20 ms into a dwell.
The experimental recovery packets now wait until 160 ms to allow its full
retune/settle period; the completed screens and repeats use this correction.

| 3.5 hops/s run | Steady exact RX per direction | Total exact RX per direction | Deliberate late rejections per channel | Stream errors / missed steady hops / missed tune deadlines |
|---|---|---|---|---|
| 60 seconds, reopen 1 | 210/210 | 234/234 | 21 | 0 / 0 / 0 |
| 60 seconds, reopen 2 | 210/210 | 234/234 | 21 | 0 / 0 / 0 |
| 60 seconds, reopen 3 | 210/210 | 234/234 | 21 | 0 / 0 / 0 |

Across repeats: 702/702 total exact packets per direction, including 630/630
maximum-size steady packets; recovery succeeded in the first resumed dwell;
all transmitted bursts passed timestamp/frequency guard checks and every close
was clean. The intentional interruption accounts for three skipped slots on
channel 1 per run, outside the steady interval. No ACKs or retries were used.
Worst observed total tune was 24.331 ms, decoder-ready phase 128.751 ms, and
host/device clock-mapping bracket 1.306 ms. These are finite-run observations,
not guaranteed worst-case bounds or independently measured RF edges.

The earlier 3.64 hops/s estimate was a timing-budget estimate without extra
scheduling margin. At 3.5 hops/s the usable window can accommodate this workload;
nearer the estimate, modulation/host scheduling variation consumes the remaining
margin and admission rejects packets. Treat 3.5 as the highest passing tested
point, not an exact universal threshold. Other payload sizes, bit rates,
modulations, hop tables, guards, loads and hosts need separate measurements.
Faster operation requires a separate investigation of smaller guards, timed
retuning or digital frequency shifts; this sweep did not test those designs.

Reproduce a repeated-run workload from the repository root (each invocation
opens and closes the device):

```bash
PYTHONPATH=. .venv-uhd/bin/python tools/hopping_check.py \
  --shared-address 192.168.10.2 --experimental-rate 3.5 --seconds 60
```

Hopping, scheduler and configuration regression checks: **134 passed**. Tool/test
lint and formatting checks also passed. Only this final summary is retained;
temporary per-run logs are removed.
