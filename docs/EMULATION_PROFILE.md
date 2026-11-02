# Simple 2FSK emulation contract

Contract revision: **1**, frozen for version 1.1.
Profile identifier: **`simple-2fsk-v1`**.
Packet/burst format identifier: **`simple-frame-v1`**.

Status in release 1.1.0: the codec/sample pipeline, ACK-free live modes, reusable
schema-2 YAML selection and resolved capture/replay are implemented. Short conducted
integration and independent fidelity checks pass; sustained fixed-profile RF
qualification remains pending. Schema-1 configuration still rejects these identifiers. This is a project-defined reference radio, not a commercial
radio protocol.
Its acceptance level is **behavior emulation between our two SDRs**.

This contract specifies one exact starting profile. Later configuration may expose
its components independently, but changed wire settings must not be described as
this frozen profile. A wire-incompatible revision needs a new format identifier.
The protocol-v5 codec and its fixtures are unchanged.

## Waveform and RF settings

| Property | Frozen reference value |
|---|---|
| Modulation | Binary continuous-phase FSK, rectangular frequency pulses |
| Center frequency | 915,000,000 Hz, fixed; conducted reference setup |
| Channel bit rate / symbol rate | 250,000 bit/s / 250,000 symbol/s |
| Sample rate | 2,000,000 complex samples/s; 8 samples/bit |
| Frequency deviation | ±125,000 Hz from center; full tone spacing 250,000 Hz |
| Bit mapping | 0 → negative deviation; 1 → positive deviation |
| Bit order | Most significant bit first in every byte |
| RF bandwidth request | 1,000,000 Hz |
| Baseband sample amplitude | Unit magnitude before UHD gain; not calibrated RF power |
| Phase | Continuous within each burst; reset to zero immediately before its first sample |
| Pulse shaping | No Gaussian/RRC filter, differential encoding or amplitude ramp |
| Burst tail | None; idle between bursts is zero-valued baseband signal |

For bits b[k], let a[k] = 2*b[k] - 1. For sample index n beginning at zero:

```text
phase[-1] = 0
phase[n] = phase[n-1] + 2*pi*125000/2000000 * a[floor(n/8)]
iq[n] = cos(phase[n]) + j*sin(phase[n])
```

The first sample is one sample period after the phase-zero boundary. The final
sample includes the final bit's right boundary. No samples precede the preamble
or follow the CRC inside the logical waveform. The UHD adapter explicitly transports
32 microseconds of the zero-valued interburst idle before ending its stream burst
to let the TX pipeline drain. This adds no data symbols or packet bytes; logical
waveform lengths below remain unchanged. Rectangular burst edges are intentional; no
spectral-mask compliance is claimed.

The hardware test path uses B205mini serials 3457464 and 3457480 with the confirmed
crossed TX/RX → 30 dB attenuation → RX2 paths. Start qualification from the measured
2 Msps, 1 MHz bandwidth, TX 20 dB / RX 40 dB bench settings. Device serials, gains
and local UDP ports are host settings, not bytes in the emulated protocol.

## Exact transmitted layout

A burst contains these bytes, with no additional generic-v5 or modem envelope:

| Offset in bytes | Field | Size | Value / interpretation |
|---|---|---|---|
| 0 | Preamble | 32 | `55` repeated 32 times |
| 32 | Sync word | 4 | `a7 c3 5a 9e` |
| 36 | Payload length | 2 | Unsigned big-endian integer N, payload bytes only |
| 38 | Payload | N | Opaque bytes, 1 ≤ N ≤ 1024 |
| 38 + N | CRC | 2 | Big-endian CRC of length bytes followed by payload |

There is no in-band format/version field. The selected local format plus its
preamble/sync identifies the receiver's intended interpretation; the sync word
is not authentication or a guarantee against arbitrary false matches. Automatic
format detection is not supported. Both endpoints must explicitly select the same
profile. The distinct sync and layout must not be routed through generic-v5 decoding.

There are no source/destination IDs, sessions, sequence numbers, signatures,
packet kinds, padding or escaping. Payload bytes may include the preamble/sync
pattern; the declared length determines the end of an acquired valid frame.

CRC definition:

- Width 16; polynomial `0x1021` (implicit x^16 term).
- Initial register `0xffff`; input and output reflection both false.
- Final XOR `0x0000`; no extra zero-byte processing after the input.
- Process bytes in order, XOR each byte into the high register byte, then perform
  eight MSB-first shift/XOR steps, masking to 16 bits after every step.
- Coverage is exactly the two transmitted length bytes plus N payload bytes.
  Preamble, sync and the CRC itself are excluded.
- Serialize the final register high byte first. The standalone algorithm check
  for ASCII `123456789` is `0x29b1`; that is not the frame CRC, which includes length.

Transmit processing order is length + payload → append CRC → prepend preamble/sync
→ serialize bytes MSB-first → 2FSK modulation. FEC, interleaving, repetition,
whitening, differential encoding, encryption and signing are all absent. Disabling
only Reed–Solomon in the existing modem does not implement this profile.

## Size and airtime

For payload length N:

```text
burst_bytes = N + 40
burst_bits = 8 * (N + 40)
burst_samples = 64 * (N + 40)
burst_seconds = 8 * (N + 40) / 250000
```

| Payload bytes | Burst samples | Burst duration |
|---|---:|---:|
| 1 | 2,624 | 1.312 ms |
| 64 | 6,656 | 3.328 ms |
| 256 | 18,944 | 9.472 ms |
| 1024 | 68,096 | 34.048 ms |

Minimum silence from the end of one transmitted burst to the start of the next
from the same node is **2 ms**. This is a minimum gap, not a fixed repetition rate
or guaranteed host turnaround latency. Bursts are never truncated to satisfy a
schedule. Future timing policies must use the full sample count above.

## Packet and link behavior

- One valid input UDP datagram becomes one transmitted burst. Accept 1–1024 bytes;
  reject empty/oversized input explicitly, without truncation or fragmentation.
- A bounded FIFO admits up to 256 packets, including one being transmitted.
  Reject newest input when full and expose the drop. Preserve admitted order.
- Transmit each admitted packet once. No automatic ACKs, retries, carrier-sense
  backoff, synchronization beacons or other over-air control traffic are generated.
  Report a local TX failure; do not silently retry it.
- Receive-only operation must submit no TX samples. TX-only operation must not
  depend on peer acquisition. Bidirectional operation may listen while idle, but
  this format provides no collision avoidance or synchronized transmit windows.
- Each successfully validated received burst produces one output UDP datagram with
  exactly its payload. Equal payloads in different bursts are delivered separately.
  There is no protocol-level duplicate suppression or replay protection.
- CRC failure, illegal length and truncated frames produce no payload output.
  Record errors and recover acquisition for subsequent valid frames. Repeated
  examination of the same samples must not deliver one burst more than once.
- Validate N before allocation; cap a candidate at the maximum frame size. Support
  arbitrary RX block boundaries. Discard incomplete candidates across sample
  discontinuities/reset. Abandon a stalled partial candidate after 100 ms without
  additional RX samples on the monotonic host clock; this timeout is a resource
  bound, not a target-radio timing claim.
- A successful UDP submission is not proof of RF delivery. A received payload is
  not authenticated, and a successful output send is not proof the consumer read it.

Initial physical acceptance runs are unidirectional, then repeated in reverse.
This avoids imposing generic-v5 clock synchronization on an ACK-free profile.
Simultaneous transmissions may collide; reliable bidirectional service is not an
acceptance claim for this initial mode.

## Independent byte references

Each complete burst is `55` × 32, then `a7c35a9e`, then the body below. Spaces are
for readability only. These reference CRCs were calculated with a scalar bitwise
implementation and independently checked with Python's `binascii.crc_hqx` using
initial value `0xffff`, without calling a production packet or modem codec.

| Payload | Body: length + payload + CRC |
|---|---|
| `00` | `0001 00 ffad` |
| `000102ff` | `0004 000102ff c8b4` |
| ASCII `123456789` | `0009 313233343536373839 c0c2` |
| Every byte `00` through `ff`, ascending | `0100` + the 256 payload bytes + `9332` |

These are contract vectors, not RF test results. Implementation must promote them
into automated conformance checks without regenerating expected bytes from the
implementation under test.

## Acceptance gates for implementation

1. Exact vectors and size/airtime calculations above pass. A separate scalar
   waveform generator matches emitted noiseless samples with maximum absolute
   complex-sample error ≤ 1e-5 for 1/64/256/1024-byte test payloads.
2. Separate TX and RX instances deliver 1,000 clean-channel packets per direction
   exactly, in sequential directional runs. Include all byte values, repeated
   identical payloads, embedded preamble/sync, and seeded pseudorandom bytes.
   Use seed 1101 for generated payloads.
3. For independent reference IQ, test all eight integer sample-start phases,
   arbitrary RX block boundaries, carrier offsets -750/0/+750 Hz and independent
   Gaussian I/Q noise with standard deviation 0.08 per component on unit-amplitude
   samples. Use seed 1101; each 1/64/256/1024-byte case must decode exactly.
   These are explicit sample-domain conditions, not calibrated RF SNR claims.
4. Reject invalid lengths, truncated frames and incorrect CRCs; regain acquisition
   for a following valid frame. For the short vectors, flip every individual bit
   in length/payload/CRC separately and ensure no altered payload is delivered.
   Verify no-FEC/no-signature behavior and absence of generic control frames.
5. Conducted runs: five minutes of 256-byte payloads at five packets/second, first
   A→B then B→A. Require ≥99% delivery per direction, zero changed payloads, zero
   unintended duplicate deliveries and zero new stream errors/overflows/underflows,
   dropped sample buffers or late transmissions. Do not require ACK/retry counters.
6. Boundary runs: one minute each at 1/64/1024 bytes and one packet/second in each
   sequential direction, using the same delivery/correctness/error thresholds.
   Use test-side identity tracking for one-byte payloads; do not change their wire
   format to add sequence numbers.
7. Receiver startup and recovery from a 17-second interrupted signal path must
   deliver the next offered test traffic within five seconds under clean conditions.
   Offer test packets at five/second after readiness/restoration. Packets transmitted
   during the interruption may be lost and are not retransmitted. Verify clean
   shutdown and subsequent reopening of both devices.
8. Capture and inspect transmitted bursts to confirm framing, frequency polarity,
   sample count and minimum idle gap. Hardware sample timing should be measured
   rather than inferred from UDP send timestamps. Frequency/power calibration and
   a target spectral mask are outside this reference profile's qualification.

These acceptance criteria require actual hardware checks; calculated payload
capacity is not proof of delivery or interoperability.

## Capability and target status

| Property | Current scope | Evidence / limit |
|---|---|---|
| Direct UHD and 2FSK waveform primitive | Implemented | Existing generic-v5 code and conducted measurements |
| This frame layout | Implemented | `protocol/simple.py`; explicit `SimpleProfile` application selection |
| ACK-free live link mode | Implemented; short conducted checks passed | Live modes and schema-2/capture-3 selection; sustained qualification pending |
| Approximation of another radio | None claimed | This is a project-defined reference profile |
| Named target and specification revision | Unknown / not selected | Record both before target-specific compatibility work |
| Target packet/timing/whitening/security behavior | Unknown | Do not substitute generic-v5 behavior for missing information |
| Hopping | Separate schema-2 simple-2fsk-hopping-v1 profile | See CONFIGURATION.md for its timing contract and separate conducted workload |
| Non-integral sample/bit-rate ratios | Explicitly deferred | Reference rates are integral; a target requiring resampling must reopen this work |
| Actual-target interoperability | Unverified | Requires independent target equipment/reference and separate tests |
| Two-computer physical qualification | Deferred | Initial acceptance uses one workstation and two SDRs |

To add a target, record its model/revision, specification or capture provenance,
exact implemented subset, known approximations, and numerical waveform/timing
acceptance limits. Keep separate statuses for reference emulation and actual-device
interoperability.
