# Generic Packet Protocol 5

`generic-v5` is the supported packet format. Configuration
schema 1 and capture format 2 do not change these on-air bytes.

All multibyte integers use network byte order. A packet comprises:

| Field | Bytes | Meaning |
|---|---:|---|
| Magic | 2 | ASCII `TR` |
| Version | 1 | `5` |
| Kind | 1 | DATA=0, ACK=1, SYNC=2; other values rejected |
| Source | 1 | Sender node ID |
| Destination | 1 | Receiver node ID; 0 reserved for synchronization broadcast |
| Session | 8 | Sender boot/session identity |
| Sequence | 4 | Sender packet sequence, shared across DATA/ACK/SYNC |
| Payload length | 2 | Bytes in payload |
| Payload | variable | Kind-specific bytes |
| Signature | 64 when enabled | Ed25519 over header and payload |
| CRC | 4 | `zlib.crc32(header + payload + signature)`, unsigned |

Authentication enablement must match between peers. No encryption is applied.

DATA carries one opaque application payload (1–1024 bytes in the application;
the low-level codec retains its existing 4096-byte allocation limit). ACK carries
12 bytes: the acknowledged DATA sender's uint64 session followed by its uint32
sequence. The ACK header's sequence belongs to the ACK sender, so ACK traffic
cannot collide with its DATA/SYNC replay window. A stale ACK for another local
session cannot clear an in-flight packet.

SYNC carries an 8-byte network-order IEEE-754 double containing leader time in
seconds, optionally followed by an 8-byte hardware transmit timestamp in
nanoseconds. Only the configured timing leader may synchronize the follower.

New packet sequences are assigned on first transmission, not input admission, so
queue overflow and a large backlog do not advance synchronization packets beyond
the replay window of unsent data. Retransmissions retain identity and bytes;
successfully acknowledged data is not retransmitted. ACKs for repeated DATA receive
fresh sender identities when sent.

## Burst framing and modulation

Packet encoding (`protocol/framing.py`), protection (`modems/burst.py` and
`modems/fec.py`), the byte envelope (`modems/burst.py`) and waveform implementations
(`modems/bpsk.py`, `modems/qpsk.py`, `modems/fsk.py`, `modems/msk.py`) are separate interfaces.
Protect the complete packet, including its signature and CRC, in this order:

1. Prepend its two-byte big-endian length.
2. Encode each high nibble then low nibble with extended Hamming (8,4).
3. Pad with encoded zero nibbles to a multiple of the interleaver depth; transpose
   each depth-by-8 bit matrix to interleave its codeword bits.
4. If enabled, apply Reed–Solomon through `RSCodec`: each block carries at most
   `255 - rs_parity_bytes` data bytes and adds the configured parity count.

The envelope is training bytes, sync `55 55 55 55 d3 91`, a two-byte big-endian
protected-byte count, and exactly that many protected bytes. Its length excludes
training, sync and the length field itself. Bit order is MSB first. An independent
byte-envelope decoder rejects truncation and trailing bytes; streaming modems
consume consecutive bursts independently.

| Waveform / burst format | Training | Mapping |
|---|---|---|
| BPSK / `bpsk-burst-v1` | 32 bytes of `55` | 0 → −1, 1 → +1 |
| QPSK / `qpsk-burst-v1` | `d37a49c5a612f08e5b31e4972cd068bf91e6235dac7840f7b2c9540e6fa1833d` | 00 → (1+j), 01 → (−1+j), 10 → (1−j), 11 → (−1−j), all divided by √2 |

BPSK supports odd bit repetition across the entire envelope, including training.
The configured QPSK path requires repetition 1. Rectangular pulses repeat each
symbol for the derived samples-per-symbol count. Optional RRC shaping includes
its full transmit convolution tail in sample counts and airtime.

BPSK uses its alternating training and sync correlation to acquire timing and
phase/frequency. QPSK uses its distinct training correlation and constellation
recovery, with decision-directed payload tracking as a fallback after the initial
phase/frequency fit. The fallback first tracks only the length field and waits
for the complete packet before tracking the payload, avoiding repeated carrier
loops over partially buffered packets. Both accept arbitrary sample
block boundaries and validate the protected length before decoding. A new waveform
must specify and test its own acquisition strategy.

The shared modem contract provides exact transmitted sample counts, streaming
receive, reset and optional decoded frame-start timestamps. Reset clears acquisition
and timestamp history while preserving cumulative FEC diagnostics. Timestamped
receive drops partial acquisition across discontinuities; timestamps are receiver
estimates, not a claim of calibrated RF timing accuracy. See [configuration and
replay contracts](CONFIGURATION.md) for rates, queue limits and capture metadata.

Golden signed DATA, ACK, and SYNC vectors are in `fixtures/protocol_v5.json`.
The v5 wire references were constructed independently of `RadioPacket.encode`
using explicit struct packing, Ed25519 signatures, and CRC, then checked by the
conformance command. FEC references use the existing tested FEC encoder.
Older protocol versions are explicitly rejected; only v5 golden vectors are
maintained in this product.

Receive candidate selection uses the packet CRC to try alternate timing or
phase candidates when FEC produces an apparently decodable but corrupted packet.
If no better candidate exists, the rejected bytes remain available for packet-layer
error reporting. This does not replace length/version checks, signatures or replay
protection, and does not alter the transmitted waveform or protocol version.

## 2FSK burst v1

`2fsk-burst-v1` uses the same byte envelope and 32-byte `55` training as BPSK,
with an independent FSK mapping. Zero selects −`frequency_deviation_hz`, one
selects +`frequency_deviation_hz`, relative to center. Bytes are MSB first. Phase
accumulates continuously across symbols, starting at zero immediately before the
first sample of each burst. Frequency pulses are rectangular, with no filter tail.
The receiver integrates adjacent-sample phase differences, searches integer symbol
timing phases and estimates the decision midpoint from the known preamble.
CRC-guided candidate selection, packet authentication and replay checks apply.
Only the waveform changes; DATA, ACK and SYNC retain protocol-v5 wire bytes.

## MSK burst v1

`msk-burst-v1` uses the same protected bytes, MSB-first envelope and 32-byte `55`
training as 2FSK. This is direct binary continuous-phase MSK, without an additional
differential encoder. With bit rate Rb and bit duration T = 1/Rb, deviation is
fixed at delta_f = Rb/4 and modulation index h = 2*delta_f/Rb = 0.5. Zero selects
-delta_f and one selects +delta_f, relative to the center frequency. Each bit
changes phase by -pi/2 or +pi/2; phase remains continuous across bit boundaries.
These relationships agree with the rectangular single-symbol CPM definition in
[GNU Radio's CPM reference](https://www.gnuradio.org/doc/doxygen-3.6/classdigital__cpmmod__bc.html)
and the deviation rule in [TI's radio user guide](https://www.ti.com/mx/lit/pdf/swru295).

For sign a[k] in {-1,+1}, the phase within bit k is
`phi(k*T + u) = phi(k*T) + a[k]*pi*u/(2*T)`, for 0 <= u <= T.
The sample convention evaluates u at 1/Fs through T, including each bit's right
boundary. Initial phase is zero immediately before the first sample of each burst;
phase is not carried across separate bursts. There is no amplitude ramp, Gaussian
filter, RRC shaping or transmit tail. The unit-magnitude samples end at the final
bit boundary; silence between bursts is handled by the radio. Burst-edge spectral
splatter is not eliminated by this profile.

For P protected bytes, the transmitted sample count is exactly
`(32 + 6 + 2 + P) * 8 * samples_per_symbol`, including preamble and length.
The shared streaming FSK receiver estimates frequency from adjacent-sample phase
differences, integrates over candidate bit windows, searches every integer sample
phase, and estimates the threshold from known preamble tones. Acquisition repeats
for each burst. It is a noncoherent discriminator receiver, not an optimal coherent
MSK detector; continuous sample-clock drift tracking is not claimed.

The Step 7 characterization was fixed before receiver tuning: unit-magnitude IQ,
independent Gaussian noise with standard deviation 0.08 on each I/Q component,
seed 7007, offsets -750/0/+750 Hz, rates 125/250/500 kbit/s at 2 Msps, and payloads
1/64/256/1024 bytes. Additional noiseless independent-reference checks use all
eight sample phases at 250 kbit/s and offsets +/-15,625 Hz. These are explicit
sample impairments, not calibrated RF SNR or Eb/N0 claims.

## GMSK burst v1

`gmsk-burst-v1` uses the same MSB-first protected packet envelope as MSK, with
the 32-byte pseudorandom training sequence used by QPSK:
`d37a49c5a612f08e5b31e4972cd068bf91e6235dac7840f7b2c9540e6fa1833d`.
This avoids concentrating the Gaussian-shaped training signal at carrier DC.
Bits directly select signs -1/+1; there is no additional differential encoder. The modulation index remains h=0.5, with the steady-state
frequency scale Rb/4. Gaussian filtering smooths the frequency pulses before
phase integration, so an individual bit's phase contribution spreads over
neighboring bit intervals.

The supported Gaussian BT values are 0.3 and 0.5; Gaussian impulse-response spans
are 4 and 6 bit intervals. The initial conducted profile is BT=0.3, span=4,
250 kbit/s at 2 Msps. BT uses the one-sided 3 dB bandwidth B and bit interval T.
With time expressed in bit intervals, sigma = sqrt(ln(2))/(2*pi*BT). Sample
`exp(-t*t/(2*sigma*sigma))` from -span/2 to +span/2 inclusive, at 1/sps spacing,
and normalize the tap sum to one. This is the impulse-response convention in
[MathWorks' Gaussian FIR reference](https://www.mathworks.com/help/signal/ref/gaussdesign.html).
The filter has `span*sps + 1` taps; its span describes the Gaussian impulse
response, not the longer result of convolving it with a rectangular bit pulse.

Repeat each bit sign for sps samples, convolve with the Gaussian taps in full
mode with zero state outside the burst, multiply by Rb/4, and integrate phase
at 2*pi/Fs radians per Hz per sample. Phase starts at zero immediately before
the first sample of each burst. Filter state and phase reset between bursts.
All `span*sps` tail samples are transmitted. With P protected bytes, sample
count is `(32 + 6 + 2 + P)*8*sps + span*sps`. Group delay is `span*sps/2` samples.
At the default profile this adds 16 microseconds to the burst and 8 microseconds
of group delay; the signed maximum-payload burst is 72.976 ms. There is no
amplitude ramp; Gaussian frequency shaping does not eliminate burst-edge splatter.

The noncoherent receiver estimates frequency from adjacent-sample phase
differences and integrates over candidate symbol windows. It scores acquisition
against a Gaussian-shaped preamble template, estimates frequency offset and
searches integer sample phases. Startup and trailing template edges are excluded
from fitting because of reset state and unknown following length bits. The
receiver waits for the filter tail and subtracts group delay from the reported
burst-start timestamp. It resets acquisition on sample discontinuities. This
receiver is not a coherent/Viterbi detector and does not claim continuous
sample-clock drift tracking.

The Step 8 software matrix was fixed before receiver tuning: seed 8008,
unit-magnitude IQ, independent Gaussian noise standard deviation 0.08 per I/Q
component, offsets -750/0/+750 Hz, rates 125/250/500 kbit/s at 2 Msps, and payloads
1/64/256/1024 bytes, across both BT values and both spans. Independent scalar
normal-density convolution and phase integration check actual transmitted samples.
Additional noiseless reference tests cover all eight sample phases and offsets
+/-15,625 Hz at 250 kbit/s, including group-delay-corrected timestamps. Noise
amplitude is not a calibrated RF SNR or Eb/N0 specification.
