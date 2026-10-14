# Radio V1 requirements

Release contract for [the roadmap](RADIO_V1_ROADMAP.md).
Implemented and measured status is recorded in [modulation results](../MODULATION_RESULTS.md).
Two-computer hardware qualification is deferred by user request.

## Confirmed product direction

- Two computers similar to the current workstation, each connected to a B205 SDR.
- Workstation-based operation with opaque application payloads.
- Direct Ettus UHD hardware access.
- Modulation milestones: BPSK, then 2FSK, then MSK, then GMSK.
- Preserve QPSK and its regression coverage without making improvements to it a
  focus of this release.
- One documented packet format initially.
- UDP packet input/output, as explained and accepted in the project discussion.
- Configurable operating parameters, normally kept unchanged for a release/profile.
  No live reconfiguration interface is required initially.
- Both SDRs are available on the local workstation for conducted tests.
  Qualification with one radio on each of two computers remains deferred.

The user delegated initial rates, payload limits, and performance targets to the
implementation. The choices below establish those engineering defaults. They can
be revised explicitly in this document, with reasons, rather than silently relaxed
after a failing test.

## What using the radio means

Each computer runs the radio application. Another local program supplies a packet
of bytes to it through UDP. The receiving radio delivers those same bytes to a
program on its computer through UDP. Internet access is not required.

```text
Local program A ↔ Radio application A ↔ B205
                                        ↕ RF
Local program B ↔ Radio application B ↔ B205
```

This version is a digital packet radio. It does not implement a microphone/speaker
voice path or automatically carry all of a computer's network traffic.

### Local payload interface

- Bind the radio's application input to `127.0.0.1:50000` by default.
- Deliver received payloads to `127.0.0.1:50001` by default.
- Make both addresses and ports configurable; identical defaults work on separate
  computers. Tests with multiple instances on one computer use distinct ports.
- One input datagram represents one application packet. Preserve its contents and
  boundary exactly; do not interpret it as text or inspect application message types.
- Initially accept 1–1024 payload bytes. Reject empty or oversized datagrams with
  an explicit counter and diagnostic. Do not truncate or fragment them silently.
- Use a bounded FIFO queue, initially 256 packets. When full, reject the newest
  incoming packet and count it. Never replace an older packet based on its contents.
- Include a command-line payload generator/receiver so the user can exercise the
  radio without writing another application.
- UDP submission alone does not promise remote delivery. Expose local drops,
  exhausted retries, and successful receives through status and test output.

## Initial operating profile

| Setting | Initial choice |
|---|---|
| Backend | Direct UHD, initially through Python bindings |
| Topology | Two peers; endpoint IDs 1 and 2 |
| Duplex | Alternating transmit/receive windows; one explicit timing leader |
| Frequency mode | Fixed frequency for the modulation milestones |
| Center frequency | Required `center_frequency_hz` in each hardware profile; no implicit transmit frequency |
| Initial bit rate | 250,000 nominal on-air bits per second |
| Additional software rate checks | 125,000 and 500,000 bits per second |
| Initial sample rate | 2,000,000 samples per second, subject to device validation |
| Payload maximum | 1024 bytes |
| Reliability | Unicast ACK/retry with duplicate suppression; initial retry limit 4 |
| Error correction | Existing Hamming/interleaving baseline; optional Reed–Solomon, off in the first baseline |
| Authentication | Preserve per-peer Ed25519 authentication and replay protection in the default profile |
| Configuration | Versioned YAML loaded at startup; coordinated restart for waveform changes |

The rate is the nominal bit rate carried by the modulation, before subtracting
packet overhead, error correction, retransmissions, and idle/receive windows.
Delivered application throughput is a separate measurement. Reject unsupported
rate/sample-rate combinations rather than silently resampling or changing the rate.

Center frequency, gain, antenna ports, and RF bandwidth must be explicit in hardware
test profiles. Generic profiles use conservative startup gains; tested bench
settings are documented in the results. Capability validation compares requested and
actual hardware settings.

The selected transmit windows must accommodate the complete largest encoded packet,
including authentication, FEC, preamble, and shaping tails. Calculate and validate
that constraint before selecting a TDD period and ACK timeout.

## Packet-format contract

Use the documented generic protocol-v5 packet format. Change its version if the
wire contract changes; preserve its golden vectors as regression references.

The new format must specify:

- Protocol identifier and version, packet kind, source and destination endpoint IDs.
- Session identity, sequence number, payload length, and opaque payload bytes.
- DATA, ACK, and timing synchronization behavior.
- Exact byte order, field widths, maximum sizes, CRC algorithm/coverage, signature
  coverage, and ACK matching across sessions.
- Length validation, unknown-version rejection, authentication failure handling,
  replay rejection, and duplicate suppression.
- A distinct burst envelope for acquisition and length recovery, defined for each
  waveform where needed.

The byte layout and golden vectors are documented in `docs/PROTOCOL.md`.
Arbitrary layouts and format editors are deferred.

## Acceptance targets

These are initial engineering targets, not advertised capabilities. Apply the
same workload to each release-focus modulation. Record actual settings, test seed,
payload IDs, timing, errors, and results with each run.

### Software gates

- Exact packet round trips for 1, 64, 256, and 1024 bytes, including zero-valued
  bytes, all byte values, and deterministic pseudorandom payloads.
- Reject empty and oversized application datagrams, malformed packet lengths,
  incompatible versions, invalid signatures, and replayed data.
- Clean channel: 100% delivery without altered or duplicated payloads for 1,000
  packets in each direction at a load below the configured link capacity.
- Exercise split receive blocks, idle intervals, varied burst-start sample phase,
  and separate transmit and receive modem instances.
- ACK-loss test: suppress the first ACK for each of 100 packets. All 100 arrive
  exactly once at the application, with retries observed and bounded.
- Overload test: exceed queue capacity; memory remains bounded, the documented
  drop policy applies, counters increase, and service resumes after load falls.
- Disconnect/reacquisition test: recover packet delivery within 5 seconds of
  restoring the simulated channel under otherwise clean conditions.
- Preserve existing BPSK/QPSK waveform impairment tests. For each new modem, add
  independent waveform checks and seeded noise/frequency-offset characterization.
  Record the noise definition and offset range; self-loopback alone is insufficient.

Noise/offset performance thresholds beyond the clean-channel gates are set at the
start of each modem milestone, before implementation tuning. Do not label a tested
noise amplitude as an RF SNR or an Eb/N0 value without defining its calculation.

### Hardware gates

Use a conducted attenuated setup on the two target computers. Record the cable
path, attenuation, frequency, gains, bandwidth, sample rate, UHD version, and device
identities. Determine the appropriate power/attenuation settings before transmitting;
do not directly connect an unattenuated transmitter to a receiver.

| Measurement | Initial acceptance target |
|---|---|
| Bidirectional steady workload | 256-byte payloads, 5 packets/second in each direction |
| Duration | 30 minutes after acquisition, per release-focus modulation |
| Delivery | At least 99% of submitted test packets per direction |
| Delivered payload correctness | Zero changed payloads and zero duplicate deliveries |
| Goodput at that workload | At least 10,137.6 payload bits/second per direction (99% of 10,240) |
| Round-trip latency | p95 no greater than 1 second for successfully completed echo probes |
| First peer acquisition | Within 10 seconds after both processes report hardware ready |
| Reacquisition | Within 5 seconds after restoring a temporarily interrupted RF path |
| Stream operation | No new overflow, underflow, or late-command errors during the steady run |
| Boundary payload sizes | Separate 1-, 64-, and 1024-byte runs, 1 packet/second/direction for 5 minutes, same delivery/correctness targets |

Measure latency using round-trip probes and one computer's monotonic clock; do not
subtract unsynchronized host timestamps. Run echo latency probes separately from
the bidirectional steady-workload run. Include an application delivery grace period
of 5 seconds after injection stops, and count still-missing packets as lost.

Acquisition and interrupted-path recovery are separate tests from the uninterrupted
30-minute run. Define hardware-ready as completed device setup with receive
streaming started; this keeps driver startup time separate from peer acquisition.
The two-node timing scheme must acquire without relying on manually aligned host clocks.

Additional rates are characterization profiles until they pass their own hardware
checks. Configurability is not a promise that every rate/gain/frequency combination
has been validated.


## Step 8 validation-duration adjustment

At the user's request on 2026-09-29, shorten the GMSK iteration's conducted checks
to save time: five minutes at five 256-byte packets/second/direction, one minute
each for 1/64/1024-byte payloads at one packet/second/direction, and separate
60-probe echo and 17-second signal-interruption/restart checks. Keep the existing
delivery, correctness, stream-error and latency/recovery thresholds unchanged.
The full software suite remains required. These short-run results must be labeled
with their actual durations and must not be presented as the earlier modulations'
30-minute sustained/five-minute boundary qualification. The longer workload above
remains the reference for a comparable sustained qualification.

The subsequently requested QPSK validation uses the same shortened hardware
workloads and unchanged thresholds. Its maintained bench profile is 500 kbit/s
at 2 Msps, with rectangular pulses, 16 Reed–Solomon parity bytes per block and
20 ms DATA/30 ms SYNC lead and a 600 ms ACK timeout. This extends retained QPSK validation without
changing the original BPSK → 2FSK → MSK → GMSK milestone order.
