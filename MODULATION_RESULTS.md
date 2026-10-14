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
