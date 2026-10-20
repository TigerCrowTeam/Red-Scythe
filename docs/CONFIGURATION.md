# Configuration schema 1

Profiles are loaded at startup; both peers must be restarted with matching waveform
settings. `schema_version: 1` is mandatory. Unversioned/legacy YAML and unknown
sections/fields are rejected with a migration message. There is no silent fallback
to another modem or rate. The configuration schema version is independent of packet
protocol **5**, which remains unchanged.

Start from the maintained examples in `configs/examples/`. The B205 `configs/bench/peer-a-bpsk.yaml` and
`peer-b-bpsk.yaml` profiles contain the measured local serial numbers, distinct UDP/status ports and
TX 20 dB/RX 40 dB settings for the confirmed 30 dB attenuated paths. Generic profiles
retain conservative gains; they are not validated RF operating profiles.

## Sections

| Section | Meaning |
|---|---|
| `schema_version` | Exactly integer `1` |
| `node_id`, `peer_id`, `timing_role` | Two mutually addressed identities, one leader and one follower |
| `radio` | UHD selection, channel, required center frequency/rates/gains/antenna ports |
| `modem` | Waveform, nominal on-air bit rate, shaping and decoder settings |
| `error_correction` | Hamming/interleaving, repetition, optional Reed–Solomon |
| `packet` | Built-in `generic-v5` format and payload limit |
| `endpoint` | Local UDP bind/delivery addresses and polling bound |
| `link` | Reliability, queue admission, carrier sensing and optional timing |
| `authentication` | Ed25519 keys and persisted replay state |
| `recording`, `status`, `log_level` | Capture, diagnostics and logging |

An illustrative waveform portion (merge with a complete peer profile):

```yaml
schema_version: 1
radio:
  device: {type: b200, serial: "3457464"}
  channel: 0
  center_frequency_hz: 915000000
  sample_rate_hz: 2000000
  bandwidth_hz: 1000000
  tx_gain_db: 20
  rx_gain_db: 40
  tx_antenna: TX/RX
  rx_antenna: RX2
modem:
  modulation: bpsk
  bit_rate_bps: 250000
  rrc_rolloff: 0.0
  rrc_span_symbols: 8
  decoder_worker_enabled: true
error_correction:
  scheme: extended-hamming-8-4
  interleaver_depth: 16
  bit_repetitions: 1
  rs_parity_bytes: 0
packet:
  format: generic-v5
  max_payload_bytes: 1024
```

Frequency, sample rate, bandwidth, gains and antenna ports are required in `radio`.
Device argument values are strings; quote serial numbers. `channel` defaults to 0;
open retry defaults remain five attempts, one second apart. `modem.modulation` and
`modem.bit_rate_bps` are required. Other illustrated modem/FEC/packet values are
defaults, except the background decoder defaults off. BPSK, QPSK, 2FSK, MSK and GMSK are implemented.

Schema 1 exposes a fixed center frequency and generic-v5 packets. Schema 2 adds
configurable simple framing and hop tables, described below. Live configuration
switching remains unsupported.

## Rate definition

`bit_rate_bps` is the nominal channel bit rate. It does not represent delivered
application throughput. Preamble, packet headers, signatures, CRC, error correction,
repetition, retransmissions and receive/idle periods reduce useful throughput.

- `symbol_rate_baud = bit_rate_bps / bits_per_symbol`
- `samples_per_symbol = sample_rate_hz / symbol_rate_baud`
- BPSK, 2FSK, MSK and GMSK carry 1 bit/symbol; QPSK carries 2.

An integral samples-per-symbol value of at least 2 is required; the program never
silently resamples or rounds an unsupported combination. At 2 Msps:

| Channel bit rate | BPSK samples/symbol | QPSK samples/symbol |
|---|---:|---:|
| 125,000 bps | 16 | 32 |
| 250,000 bps | 8 | 16 |
| 500,000 bps | 4 | 8 |

Migrated QPSK examples retain **500,000 bps**, preserving their previous 8 samples
per symbol. BPSK examples retain 250,000 bps. Repetition changes airtime, not the
configured symbol clock. QPSK currently requires `bit_repetitions: 1`.
The paired `configs/bench/peer-*-qpsk.yaml` profiles also use 500,000 bps at
2 Msps, with rectangular pulses. They select the same two B205 serials, UDP ports,
gains and TDD period as the other bench profiles, with background decoding enabled.
QPSK bench profiles use 20 ms DATA lead, 30 ms SYNC lead and a 600 ms ACK
timeout to accommodate host scheduling and decoding. Both QPSK example and bench pairs enable `rs_parity_bytes: 16`
for outer Reed–Solomon protection. Set this field identically on both peers;
older QPSK profiles used zero, so update both ends together. Existing captures
retain their recorded parity setting and remain replayable.
Stop other radio processes before using them. A signed maximum-size QPSK burst
occupies 39.040 ms at this profile. See `MODULATION_RESULTS.md` for measured scope.

For BPSK/QPSK, RRC rolloff zero selects rectangular pulses. Nonzero rolloff enables the existing
root-raised-cosine shaping and matched receive filter. The rolloff must be 0..1 and
the span an even integer of at least 2. Reed–Solomon parity is off at 0; supported
values are even integers through 254, subject to burst-size and airtime limits.

## Validation and airtime

```bash
python -m packet_radio.cli.validate configs/bench/peer-a-bpsk.yaml configs/bench/peer-b-bpsk.yaml
python -m packet_radio.cli.validate configs/bench/peer-a-bpsk.yaml --hardware
```

Offline validation reports center frequency, bit/symbol rates, derived sampling
and maximum packet airtime. Paired validation checks waveform, timing, identities,
payload limits and authentication enablement. Hardware validation uses UHD ranges
and actual readbacks, without sending samples. It remains necessary even when the
arithmetic rate combination is valid.

Airtime starts from the complete packet size, including a 64-byte signature when
authentication is configured. It includes the Hamming-protected length, interleaver
padding, parity on **every** RS block, waveform training/sync/length, repeated bits,
and the complete transmit filter tail. At the signed BPSK bench defaults, a
1,024-byte payload occupies **72.960 ms**. The receive matched-filter delay is not
additional transmitted airtime.

The existing `link.queue_size` bound includes queued DATA and packets awaiting ACK.
Optional `link.queue_airtime_seconds` additionally bounds the sum of one complete
burst per admitted DATA packet. It includes awaiting-ACK packets, releases capacity
on acknowledgement/exhaustion, rejects newest input when full, and excludes the
separately bounded control queue. It is not a wall-clock latency promise or a retry
reservation. Omit it to preserve count-only admission. A budget smaller than the
maximum payload burst is rejected at startup. `queue_airtime_seconds` is exposed as
a status gauge even when this optional limit is disabled.

A maximum packet that cannot fit a configured TDD window is rejected before opening
the radio. Per-opportunity admission still checks the current phase and full burst
size at runtime. Passing these checks does not qualify experimental scheduled TDD
or guarantee the peer can decode under a particular RF impairment.

## Migrating pre-schema YAML

| Old setting | Schema 1 setting |
|---|---|
| No version | Add `schema_version: 1` |
| `frequency.frequencies_hz: [F]` | `radio.center_frequency_hz: F` |
| `sdr.sample_rate`, `sdr.bandwidth` | `radio.sample_rate_hz`, `radio.bandwidth_hz` |
| `sdr.tx_gain`, `sdr.rx_gain` | `radio.tx_gain_db`, `radio.rx_gain_db` |
| `sdr.device`, channel, antenna and retry settings | Same field names under `radio` |
| `sdr.modulation`, shaping and decoder settings | Same field names under `modem` |
| `sdr.samples_per_symbol: S` | `modem.bit_rate_bps = sample_rate × bits_per_symbol / S` |
| `sdr.fec_interleaver_depth` | `error_correction.interleaver_depth` |
| `sdr.bit_repetitions`, `sdr.rs_parity_bytes` | Same field names under `error_correction` |
| `endpoint.max_payload_bytes` | `packet.max_payload_bytes` |

Remove the old `sdr` and `frequency` YAML sections. A multi-frequency profile cannot
be migrated into fixed-frequency schema 1 without selecting a single channel.
Authentication paths, endpoint addresses, status, recording and reliability fields
otherwise retain their meanings. The resolved Python `SDRConfig`/`FrequencyConfig`
objects remain available to existing injected simulations and adapter code; they
are not the YAML schema.

## Modem and replay contracts

`packet_radio/modems/registry.py` owns waveform definitions and construction. Live TX, live RX,
validation and replay use that factory through `protocol/registry.py`, which selects
the packet codec, link behavior and burst/protection composition. Only generic-v5
is currently registered; the Step 2 refactor does not add schema options. `Modem` defines modulation, streaming and
timestamped receive, full sample/byte counts and `reset()`; FEC diagnostic counters
remain cumulative across reset. A reset discards partial frames, burst acquisition,
noise history and timestamp history while retaining the configured waveform.

Timestamped receive returns frame-start timestamps when available; gaps larger than
the modem's sample tolerance discard partial acquisition history. Untimed replay
returns decoded bytes without inventing hardware timestamps. BPSK and QPSK have
separate preambles and acquisition implementations; future waveforms must define
their own burst format rather than inherit PSK acquisition by assumption.

New capture manifests use capture format **2**, identifying configuration schema,
packet/burst versions, bit/symbol/sample rates, shaping, FEC, authentication, IQ dtype
and center frequency when known. Replay validates those settings and rejects unknown
waveforms or conflicting rates. Existing capture format 1 files with protocol 5
remain readable using their original settings. Captures containing older packet
protocols remain unsupported. The capture format change does not change radio bytes.

The modem factory also accepts an optional packet-candidate validator. Live radio
and IQ replay supply the protocol CRC check; raw modem use can omit it. This lets
acquisition try another timing/phase candidate after an FEC miscorrection while
keeping packet encoding and waveform generation separate. The packet decoder still
performs all protocol, CRC and authentication checks before payload delivery.

## 2FSK settings

Select the paired `configs/examples/peer-*-2fsk.yaml` examples or the local conducted
`configs/bench/peer-*-2fsk.yaml` profiles. The latter select the same serials, ports, gains
and timing as the BPSK bench profiles, so stop other radio processes first.

```yaml
modem:
  modulation: 2fsk
  bit_rate_bps: 250000
  frequency_deviation_hz: 125000
  decoder_worker_enabled: true
```

`frequency_deviation_hz` is required for 2FSK and rejected for other modulations.
It is the positive distance of each tone from the center, not the full tone spacing.
Both peers must match. The first implementation is continuous-phase rectangular
2FSK, without RRC or Gaussian shaping. It requires `bit_repetitions: 1`,
`rrc_rolloff: 0`, integral samples/symbol >=4, and deviation strictly between zero
and sample_rate/4. RF bandwidth must be at least `2*deviation + 2*bit_rate` and
no greater than sample rate. This margin does not imply a rectangular waveform has
finite spectral support. At 500 kbit/s with 250 kHz deviation, choose at least
1.5 MHz bandwidth; the 250 kbit/s examples use 1 MHz.

Captures store the deviation and `2fsk-burst-v1` identifier. Replay requires the
recorded deviation; it does not infer it from bit rate. Complete sample counts
include all training, envelope and protection bytes; 2FSK has no filter tail.
See [modulation results](../MODULATION_RESULTS.md) for receiver assumptions,
frozen test conditions and qualification status.

The 2FSK receiver acquires integer symbol timing independently for each burst and
estimates frequency midpoint from training. It does not provide fractional
resampling or continuous sample-clock drift tracking. The adjacent-sample phase
discriminator follows the relation in the
[GNU Radio reference](https://www.gnuradio.org/doc/doxygen/classgr_1_1analog_1_1quadrature__demod__cf.html).

## MSK settings

Select `modem.modulation: msk` and set `bit_rate_bps`. Do not provide
`frequency_deviation_hz`: MSK derives it as bit rate / 4 (62,500 Hz at 250 kbit/s),
and rejects even an override equal to the derived value. At least four integral
samples per bit are required. Bit repetition must be 1 and RRC rolloff must be 0.
The initial profile uses 2 Msps, 250 kbit/s and 1 MHz RF bandwidth. Validation
requires bandwidth >= 2.5 * bit rate as a conservative receiver-bandwidth policy,
not a claim that the waveform has finite spectral support. The 500 kbit/s software
characterization therefore needs at least 1.25 MHz bandwidth in a complete profile.

Paired profiles are `configs/examples/peer-*-msk.yaml` and
`configs/bench/peer-*-msk.yaml`. Hardware qualification status is recorded only in
`MODULATION_RESULTS.md`. Capture manifests use `msk-burst-v1` and retain a null
frequency-deviation override; replay derives the value from the stored rate.

## GMSK settings

Select `modulation: gmsk` and explicitly provide `gaussian_bt` (0.3 or 0.5) and
`gaussian_span_symbols` (integer 4 or 6). These fields are rejected for other
modulations. Example and bench pairs use BT=0.3, span=4 and 250 kbit/s; alternative
shaping profiles are software-characterized until separately measured on hardware.

Like MSK, GMSK derives its frequency scale as bit rate / 4. Omit
`frequency_deviation_hz`; repetition must be 1, RRC rolloff must be 0, and at least
four integral samples per bit are required. The conservative bandwidth policy
remains >= 2.5 * bit rate. Both peers must match BT and span, as well as rates
and the other existing waveform settings. Changing either requires a coordinated
restart. The full Gaussian tail counts toward queue airtime and TDD admission.

```yaml
modem:
  modulation: gmsk
  bit_rate_bps: 250000
  gaussian_bt: 0.3
  gaussian_span_symbols: 4
```

GMSK captures declare `gmsk-burst-v1` and include both Gaussian fields. Replay
rejects missing or unsupported shaping settings instead of guessing. Existing
non-GMSK capture manifests remain compatible and do not gain Gaussian fields.

## Schema-1 endpoint matching and retained release support

Use a complete matching pair, then run `packet-radio-validate` on both files.
The following must match: center frequency, sample rate, bandwidth, modulation,
bit rate, shaping (RRC or Gaussian), 2FSK deviation, all FEC/interleaving/repetition
settings, payload limit, authentication enablement, and all `link.tdd_*` and
`link.hardware_*` settings. IDs must point to each other, with one leader and one
follower. Each endpoint needs its own private key and trusts both public identities.

Device serials, gains, antenna ports, local UDP/status addresses and file paths can
differ. Use unique UDP input/output and status ports when both instances share a
host. Keep the supplied retry/queue defaults unless intentionally tuning them.
Key/replay/recording paths are relative to the process working directory, not YAML
location. Profile validation cannot establish that keys exist or that RF hardware
can sustain an otherwise valid configuration.

| Parameter | Accepted configuration | Measured release profile |
|---|---|---|
| Packet format/payload | `generic-v5`, 1–1024 bytes | 1/64/256/1024-byte workloads |
| Center frequency | Positive; must pass the selected UHD device range check | 915 MHz, fixed |
| Sample rate/bandwidth | Positive; bandwidth ≤ sample rate; UHD readback validation required | 2 Msps / 1 MHz |
| Channel bit rate | Positive; integral samples/symbol; waveform and airtime limits apply | 250 kbps, QPSK 500 kbps |
| Samples/symbol | BPSK/QPSK ≥2; 2FSK/MSK/GMSK ≥4 | 8 for all supplied bench waveforms |
| 2FSK deviation | 0 < deviation < sample rate / 4; bandwidth ≥2×deviation + 2×bit rate | ±125 kHz |
| MSK/GMSK deviation | Derived bit rate / 4; bandwidth ≥2.5×bit rate | ±62.5 kHz |
| GMSK shaping | BT 0.3 or 0.5; span 4 or 6 symbols | BT 0.3, span 4 |
| PSK shaping | RRC rolloff 0..1; even span ≥2 | Rectangular (rolloff 0) |
| RS parity | 0 or even values 2..254, subject to burst/airtime limits | 0; QPSK 16 |
| Hardware/host | Other UHD devices/hosts require separate validation | Two B205mini, one Ubuntu 26.04.1 workstation, UHD 4.9.0.1, Python 3.14.4 |

These are validation constraints, not promises of RF performance throughout the
range. The measured profiles and workload-specific latency/throughput are in
`MODULATION_RESULTS.md`. Nominal bit rate is not UDP goodput. Generic examples
use conservative gains and simpler scheduling, and are templates rather than
measured bench profiles. Independent-host operation, over-air range, hopping,
live reconfiguration, arbitrary framing, automatic USB hotplug recovery and fixed
hardware-slot scheduling are not qualified for version 1.0.0. QPSK is maintained
and locally tested but is outside the first-version modulation focus.

## Upgrading to packet-radio 1.0.0

The distribution is now `packet-radio`, the import namespace is `packet_radio`,
and commands are `packet-radio`, `packet-radio-payload`, `packet-radio-status`,
`packet-radio-validate`, `packet-radio-replay`, `packet-radio-keys` and
`packet-radio-conformance`. Old command/import aliases are not installed.
Prometheus metric names now start with `packet_radio_`; update dashboards that
used `telemetry_radio_`. HTTP paths and JSON status field names are unchanged.

Stop both applications, preserve their keys and replay-state files, and create a
fresh virtual environment using the appropriate UHD-compatible interpreter.
Install the local wheel, copy the supplied profiles into a writable directory,
then migrate configuration using the table above. Update service commands and
Python imports from `telemetry-radio` / `telemetry_radio` to the new names.
Validate both profiles, confirm each hardware selection, and restart both peers.
Avoid retaining an older editable installation in the new environment.

This packaging rename does not change protocol 5 bytes, signatures, burst formats,
configuration schema 1, or capture formats. Existing schema-1 profiles, keys and
replay state remain valid. Protocols before 5 are incompatible and rejected; both
peers must use protocol 5. Capture formats 1 and 2 containing protocol 5 remain
readable. QPSK users moving from older RS=0 profiles must set the same parity on
both ends; the supplied release profiles use RS=16. Keep the original recorded
settings when replaying old captures.

## Version 1.1: configurable framing API

Step 3 implements `packet_radio.protocol.simple.FrameSettings`, `SimpleFrameCodec`
and `make_frame_modem`. This is a Python API for codec/waveform work, not a new
schema-1 YAML option. Schema-1 YAML still registers only `generic-v5`. Step 4 adds an explicit live
Python profile selection below; Step 5 adds schema-2 reusable profiles further below.
The [frozen simple 2FSK contract](EMULATION_PROFILE.md) is reproduced by the defaults.
Changing those defaults produces a custom variant, not the frozen profile.

```python
from packet_radio.protocol.simple import SimpleFrameCodec, make_frame_modem

codec = SimpleFrameCodec()
tx = make_frame_modem()
rx = make_frame_modem()
frame = codec.encode(bytes.fromhex("000102ff"))
assert frame.hex() == "0004000102ffc8b4"
samples = tx.modulate(frame)
assert len(samples) == 2816
assert [codec.decode(wire) for wire in rx.feed(samples)] == [bytes.fromhex("000102ff")]
```

The codec returns length + payload + CRC; the modem adds the acquisition prefix
and selected protection. Pass encoded frames to `modulate`, not application
payloads. Modem receive returns decoded frame bytes for `codec.decode`, which
returns the payload. No generic-v5 headers, signatures or control frames are added.
This API does not open an SDR, provide link scheduling, or create capture manifests.
Schema-2 application captures retain this pipeline in capture format 3; existing
generic-v5 captures remain readable.

Supported `FrameSettings` fields:

| Setting | Supported values / meaning |
|---|---|
| `preamble` | 1–64 bytes, default `b"\x55"` |
| `preamble_repetitions` | 1–256, expanded preamble at most 256 bytes; default 32 |
| `sync` | 2–16 bytes, default `a7c35a9e`; full acquisition prefix must contain both bit values |
| `bit_order` | `big` = MSB first; `little` = LSB first, applied to every transmitted byte |
| `length_bytes` | 1, 2 or 4 for variable payload; 0 for fixed payload |
| `length_byte_order` | `big` or `little`; length counts application payload bytes only |
| `fixed_payload_bytes` | `None` for variable length; otherwise an exact size within the payload limit, with `length_bytes=0` |
| `max_payload_bytes` | 1–1024; variable limit must fit the length field (one byte permits at most 255) |
| `crc` | `crc16-ccitt-false` (default), `crc32-iso-hdlc`, or `none` |
| `crc_byte_order` | `big` (default) or `little` |
| `fec` | `none` (default) or `extended-hamming-8-4` |
| `interleaver_depth` | 1–256 with Hamming; unused default 16 required for no-FEC |
| `rs_parity_bytes` | 0 or even 2–254 with Hamming; must be 0 for no-FEC |
| `whitening`, `differential` | Only `none` / `False`; other values rejected |

CRC coverage is the transmitted length bytes followed by payload, before FEC.
For fixed-size frames there are no length bytes, so only payload is covered.
Preamble, sync and the CRC itself are excluded. CRC parameters are explicit:

| Choice | Width | Polynomial (leading term implicit) | Initial register | Reflected input/output | Final XOR | `123456789` check |
|---|---:|---|---|---|---|---|
| `crc16-ccitt-false` | 16 | `0x1021` | `0xffff` | false / false | `0` | `0x29b1` |
| `crc32-iso-hdlc` | 32 | `0x04c11db7` | `0xffffffff` | true / true | `0xffffffff` | `0xcbf43926` |

With no-FEC, bytes remain unchanged. With Hamming, protect payload + CRC using the
existing internal length/Hamming/interleaver encoder, then optional RS. The outer
payload-length field remains unprotected so acquisition can determine the coded
body size. CRC still detects alteration of that field. The transmit order is:
length/payload/CRC → protect body only → prepend preamble/sync → serialize bits →
2FSK. Receive reverses these operations and restores the outer length before
packet CRC validation. `rs_parity_bytes=0` does not disable Hamming when selected.

No CRC means no integrity check; only framing/length validation remains. It is a
custom option and not the frozen reference profile's acceptance configuration.
Likewise, configurable values are not a claim of RF qualification for every
combination. This frame pipeline currently supports **2FSK only**. Attempts to
use its envelope with BPSK/QPSK/MSK/GMSK fail rather than misencode it.

The factory defaults to 2 Msps, 250 kbps and ±125 kHz deviation. Positive finite
rates, integral samples/bit ≥4 and deviation below sample rate/4 are required.
RF bandwidth/gain and hardware-range validation remain the application's concern
in the live profile.

Receive state is bounded by the largest configured burst plus a capped idle/block
margin (at most one additional maximum burst). This retains the full preamble of
maximum-size frames when live blocks also contain idle samples. Malformed lengths are
rejected before frame allocation, bad CRC candidates are discarded, timestamp
discontinuities reset partial acquisition, and 100 ms without additional RX
samples abandons a partial frame on the next feed call. Offline replay can supply
an injected monotonic `clock` if processing pauses should not be treated as a live
stream interruption. No wall-clock timer thread is created by the modem.

Whitening, differential encoding and non-byte-aligned fields are not required by
the first reference profile and are not implemented. Unsupported options fail;
the API never silently pads a partial byte or invents a whitening seed. Add these
only with a selected target's exact bit-level contract and independent vectors.


## Version 1.1: live simple link modes

Step 4 integrates `SimpleProfile` into `RadioApp` through its explicit `processing`
argument. Existing schema-1 files and the `packet-radio` CLI still select generic-v5.
Schema-2 YAML and capture format 3 now provide this selection and replay without
source edits; see the reusable-profile section below. Recording with a manually
injected simple profile on a schema-1 configuration remains rejected because it
lacks a resolved simple definition.

For the established conducted bench, a standalone transmitter can be constructed
as follows. `app.run()` uses the normal UDP endpoint and status services; interrupting
it closes the devices through its `finally` block.

```python
from packet_radio.app import RadioApp
from packet_radio.config import (
    EndpointConfig,
    FrequencyConfig,
    LinkConfig,
    RadioConfig,
    SDRConfig,
)
from packet_radio.protocol.simple_link import SimpleLinkSettings, SimpleProfile

config = RadioConfig(
    node_id=1,
    peer_id=2,
    timing_role="leader",
    sdr=SDRConfig(
        device={"type": "b200", "serial": "3457464"},
        modulation="2fsk",
        sample_rate=2_000_000,
        bandwidth=1_000_000,
        samples_per_symbol=8,
        frequency_deviation_hz=125_000,
        tx_gain=20,
        rx_gain=40,
        tx_antenna="TX/RX",
        rx_antenna="RX2",
        decoder_worker_enabled=True,
    ),
    frequency=FrequencyConfig((915_000_000,), epoch=0, guard_seconds=0),
    endpoint=EndpointConfig(bind_port=50000, destination_port=50001),
    link=LinkConfig(hardware_timed_io=True, max_retries=0),
)
profile = SimpleProfile(link=SimpleLinkSettings(operation="transmit-only"))
app = RadioApp(config, processing=profile)
app.run()
```

For the receiver, use serial `3457480`, distinct local ports (for example 50010/50011),
node/peer 2/1, and `operation="receive-only"`. UDP input to the transmitter becomes
UDP output at the receiver's destination port. Node IDs and the legacy timing role
are local configuration labels here; no addresses or role announcements are added
on air. `operation="bidirectional"` permits both directions but supplies no collision
avoidance. Initial conducted checks use sequential directions.

| `SimpleLinkSettings` field | Behavior |
|---|---|
| `operation` | `transmit-only`, `receive-only`, or `bidirectional` (default) |
| `input_kind` | `payload` (default), or `frame` for validated length/payload/CRC bytes |
| `minimum_gap_seconds` | At least 0.002; minimum idle time following a completed burst |
| `minimum_interval_seconds` | Nonnegative; optional minimum spacing, default 0. Conservatively waits this interval after completion, so start-to-start spacing also includes airtime and host/USB overhead |
| `turnaround_seconds` | Nonnegative; minimum host delay from validated RX delivery to the next TX, default 0.002 |

Receive windows are continuous in receive-only/bidirectional mode. Transmission is
permitted without acquiring a peer. This injected API requires fixed frequency;
select a schema-2 hopping definition for the UTC schedule described below.
Synchronized TDD windows, Gaussian shaping and bit repetitions are rejected. These delays are lower bounds,
not exact repetition rates, guaranteed response times or RF turnaround measurements.
A whole burst is submitted once; neither loss nor a local TX failure causes a retry.

`input_kind="frame"` accepts the **body before protection**: configured length,
payload and CRC, excluding preamble/sync, FEC and IQ. The body must pass length/CRC
validation before admission. It is not wrapped in a second length field or CRC.
The modem adds acquisition bytes and applies the selected protection once. Output
is always the decoded payload, even when the transmitter uses framed input. The
UDP size limit accounts for frame overhead; `endpoint.max_payload_bytes` still
limits the contained payload. Fixed-length settings require that endpoint limit
to equal the configured fixed size. This is not an arbitrary unchecked-byte or
raw-IQ transmit interface.

The FIFO rejects newest input at its packet-count or airtime limit, including a
selected in-flight packet. `queue_drops`, `invalid_payloads`, `mode_rejections` and
`transmit_drops` distinguish capacity, malformed input, receive-only input and
failed TX submissions. Received equal payloads are delivered separately. Candidate
rejections after waveform acquisition increment `candidate_rejections` and
`invalid_packets`; they are not interpreted as authenticated peer failures.

Status exposes the resolved processing settings, `operation`, `input_kind` and
`requires_peer=false`; `synchronized` is null because this mode has no synchronization
protocol. `/healthz` checks local readiness, so an idle or receive-only node can be
healthy without an active peer. Receiver acquisition and application delivery are
separate activity indicators. There is no identity-bearing peer traffic to mark
`peer_active`. CRC provides corruption detection, not authentication or replay
protection. Supplying authentication keys/replay state is rejected; generic-v5
security and health semantics remain unchanged.

On UHD, the simple profile submits 32 microseconds of **zero-valued idle** after
its logical waveform, before ending the stream burst (64 samples at 2 Msps). This
allows the TX pipeline to drain without losing the final CRC bits. These zeros
belong to the existing interburst idle; no extra symbols or packet bytes are added.
Codec sample counts/airtime describe the logical waveform. UHD completion includes
the idle flush, and the next burst still waits the configured minimum gap.
Hardware status reports `tx_idle_samples`; the generic-v5 default remains zero.

Run the short conducted integration check from the checkout with the UHD-enabled
Python environment:

```bash
PYTHONPATH=. /tmp/packet-radio-v1-uhd/bin/python tools/simple_link_check.py
```

It sends 150 256-byte packets at five/second plus five boundary/repeated payloads,
then reopens both radios and reverses direction using framed input. It checks exact
payloads, zero RX-only TX samples, absence of control traffic, stream errors, logical
IQ bytes/sample counts and gaps between submitted device-time targets. It prints
summaries only. These short checks do not replace the contract's five-minute and
one-minute qualification workloads or independently measured RF timing.


## Version 1.1: reusable profiles (schema 2)

Schema 2 separates a reusable **radio definition** from a **node file**. Both
`packet-radio --config` and `packet-radio-validate` accept schema 1 and schema 2.
The existing 20 schema-1 example/bench files remain unchanged and do not need
conversion. Their framing, authentication, default values and working-directory
path interpretation remain the same.

Start with these paired node files:

| Format | Reusable definition | Node examples |
|---|---|---|
| Simple ACK-free 2FSK | `configs/profiles/simple-2fsk-v1.yaml` | `configs/v1_1/peer-a-simple.yaml`, `peer-b-simple.yaml` |
| Reliable generic-v5 2FSK | `configs/profiles/generic-2fsk-v5.yaml` | `configs/v1_1/peer-a-generic.yaml`, `peer-b-generic.yaml` |

Set each node's UHD serial, gains and local ports for the intended hardware.
The examples use zero gains and do not select either bench serial automatically.
Both use existing UHD and modem implementations. The generic definition can select
any of the five existing modulations when its waveform settings are compatible;
the simple format currently supports only 2FSK.

```bash
packet-radio-validate configs/v1_1/peer-a-simple.yaml configs/v1_1/peer-b-simple.yaml
packet-radio-validate configs/v1_1/peer-a-simple.yaml --resolved
packet-radio --config configs/v1_1/peer-a-simple.yaml
```

The resolved preview is JSON printed to stdout; no result file is created. It
contains the full effective configuration, selected processing settings and a
SHA-256 fingerprint of the normalized definition. Key/replay-state paths are
redacted. `--resolved` and `--hardware` are separate checks.

A node file contains:

```yaml
schema_version: 2
profile: ../profiles/simple-2fsk-v1.yaml
node_id: 1
peer_id: 2
timing_role: leader
operation: transmit-only
input_kind: payload
hardware:
  device: {type: b200, serial: "3457464"}
  tx_gain: 20
  rx_gain: 40
  tx_antenna: TX/RX
  rx_antenna: RX2
runtime:
  decoder_worker_enabled: true
  hardware_timed_io: true
  hardware_timed_tx_lead_seconds: 0.01
  queue_size: 256
endpoint:
  bind_host: 127.0.0.1
  bind_port: 50000
  destination_host: 127.0.0.1
  destination_port: 50001
status:
  output: text
  http_port: 8765
```

The serial/gains above are the established attenuated bench values. For the second
node, select the other serial, distinct ports and receive-only operation. The
complete examples include both nodes. Generic-v5 uses bidirectional payload input
and its normal key files. The CLI still requires generic-v5 authentication unless
`--allow-unauthenticated` is explicit. Selecting the simple format explicitly
selects its unsigned contract, so that flag is not required; keys/replay protection
cannot be attached to simple frames.

The reusable file uses `profile_version: 1`, independently of node schema version:

| Section | Ownership and required choices |
|---|---|
| Metadata | `id`, positive integer `revision`, nonempty `reference`, `qualification`, and nonempty `limitations` |
| `waveform` | `modulation`, `sample_rate_hz`, `bandwidth_hz`, `bit_rate_bps`; compatible deviation/shaping fields from the existing modem settings |
| `frequency` | Explicit `mode: fixed` and `center_frequency_hz`, or the schema-2 hopping contract below |
| `packet` | Explicit `format` and `max_payload_bytes`; simple formats additionally accept `frame` settings |
| `protection` | Explicit `scheme` (`none` or `extended-hamming-8-4`), plus interleaver depth, repetitions and RS parity |
| `link` | Explicit `mode: reliable` for generic-v5 or `mode: ack-free` for simple frames; corresponding portable timing/reliability settings |

Simple `packet.frame` accepts the framing table above, excluding `max_payload_bytes`
(which belongs in `packet`) and FEC/interleaver/RS choices (which belong in
`protection`). Preamble and sync are quoted hexadecimal strings. Other frame defaults
are the documented `FrameSettings` defaults, frozen for profile version 1 and
expanded in the resolved preview/capture. Generic-v5 rejects a custom `frame` block
and requires Hamming; zero RS parity still does not disable its Hamming coding.

Portable reliable-link settings are the existing ACK/retry/backoff/duplicate and
TDD policy fields, plus `hardware_clock_sync` and `hardware_slot_scheduling`.
Simple links accept only the three minimum gap/interval/turnaround settings listed
above. Node `operation` and `input_kind` select direction and local input semantics;
they do not alter the shared frame layout. Simple receive-only health does not
require an active peer. Pair validation requires at least one usable TX→RX direction
and matching frame/protection/waveform/frequency settings, without requiring opposite
legacy timing roles or identical local timing margins. Two bidirectional simple
nodes are compatible but have no collision avoidance or retry guarantee.

`hardware` holds device arguments, gains, antennas, channel and open-retry settings.
`runtime` holds decoder enablement/output capacity, queue capacities/airtime limit,
`hardware_timed_io`, hardware lead/lookahead/submission margins and
`tdd_receive_delay_compensation_seconds`. Those fields are rejected inside the
shared definition, as are device serials, endpoint addresses, key paths and recording
paths. Conversely, wire settings cannot be hidden in `runtime`.

Schema-2 profile paths, key paths, replay-state paths and recording directories are
resolved relative to the **node file**, even when launched from another directory.
There is no environment-variable expansion, implicit search path or recursive
profile inheritance. Schema-1 paths keep their prior working-directory semantics.

### Composition and overrides

Resolution has one deterministic order: read the named definition → validate and
expand its versioned defaults → apply explicit node `overrides` → validate the
result → combine disjoint host settings. Duplicate YAML keys, merge keys (`<<`),
unknown fields, missing mandatory sections, nonfinite values and conflicting layer
combinations fail. There are no implicit overrides between sections.

`overrides` can replace known leaf values inside `waveform`, `frequency`, `packet`,
`protection` and `link`; mappings recurse. Metadata
cannot be overridden. Changing the packet format or link mode requires selecting
another profile file, not a partial structural override. For example:

```yaml
overrides:
  frequency:
    center_frequency_hz: 916000000
  packet:
    frame:
      crc: crc32-iso-hdlc
```

Any effective change appends `-custom` to the definition ID and resets qualification
to `unqualified`; its resolved fingerprint changes. A no-op override preserves
identity. Qualification labels (`unqualified`, `software-verified`, `short-conducted`,
`qualified`) are authored provenance, not automatically inferred performance claims.
Use the reference and limitations with the final measurements; changing a serial or
host gain does not extend qualification to untested hardware. The schema is bounded
configuration support, not universal interoperability.

### Migration and capture replay

No migration is required to keep using schema 1. To adopt schema 2, copy one of the
new node examples, select a matching reusable definition, move device/gain/antenna
settings to `hardware`, local scheduling margins and decoder/queue settings to
`runtime`, and keep endpoints/keys/status/recording in the node. Compare resolved
previews and validate the pair before use. Account for the new node-relative path
rules. Schema 1 cannot select `simple-frame-v1` and is never silently reinterpreted.

Application recordings now use **capture format 3**. The manifest embeds the full
resolved configuration and processing descriptor, including profile identity,
waveform, framing, protection, operating/input modes, frequency policy, host timing
settings and UHD idle-flush count. It includes a consistency checksum, with key paths
redacted; this checksum is not authentication. Replay validates redundant fields and
reconstructs the embedded processing chain without reopening external profile files.
Missing or conflicting settings fail rather than defaulting to generic-v5.

```bash
packet-radio-replay /path/to/capture-session --source iq
packet-radio-replay /path/to/capture-session --source frames
```

Simple replay descriptions contain payload bytes and a format identifier, without
invented generic source/sequence fields. Generic authenticated captures still require
trusted `--public-key` arguments. Offline simple IQ replay disables the host pause
timeout so pausing iteration does not discard a partial frame. Captures describe RX
samples/frames; they do not retransmit RF or reproduce a host's transmit schedule.
Legacy capture formats **1 and 2 remain readable**, and the standalone legacy
`CaptureRecorder` API still creates format 2 when no resolved configuration is
supplied. Live simple recording requires schema 2. Captures are optional operational
data; final qualification summaries still belong only in `MODULATION_RESULTS.md`.

## Version 1.1 frequency hopping

Select `configs/v1_1/peer-a-hopping.yaml` and `peer-b-hopping.yaml`. They reference
`configs/profiles/simple-2fsk-hopping-v1.yaml`; set local serials, gains and ports
as for the fixed simple profile. Schema 1 and `generic-v5` remain fixed-frequency
configuration choices. The hopping profile keeps the simple 2FSK frame bytes,
250 kbit/s, 2 Msps, ±125 kHz deviation and no ACK/SYNC/retry traffic.

```yaml
frequency:
  mode: hopping
  frequencies_hz: [914000000, 915000000, 916000000]
  dwell_seconds: 1.0
  guard_seconds: 0.1
  epoch: 0
  synchronization: external-utc
  sequence_mode: cycle
  repeated_frequencies: reject
```

All fields above are required. The table contains 2–256 unique, finite positive
frequencies, additionally checked against the opened UHD device. `cycle` visits
entries in order and wraps; `ping_pong` visits 0,1,2,1,0,... for a three-entry table.
Repeated entries are rejected, including nonadjacent duplicates. Sequence position
is `floor((UTC - epoch) / dwell_seconds)` modulo the sequence length. Epoch is a
nonnegative POSIX timestamp in seconds; transmission is inhibited before it.
Both peers must use the same table, sequence, epoch, dwell and guard.

Hop rate is `1 / dwell_seconds`. Schema-2 profiles reject dwell below **0.5 s**,
guard below **100 ms**, and guards occupying the whole dwell. Rates above 1 hop/s
also require an explicit UHD `type: x300` device selection. These are conservative
host stop/tune/restart limits, not the maximum capability of the hardware.
The original profile and node examples retain **1 hop/s**. The separate
`simple-2fsk-x310-hopping-v1.yaml` profile and
`peer-{a,b}-x310-hopping.yaml` nodes select **2 hops/s**, with 100 ms guards,
250 kbit/s 2FSK, 2 Msps, 10 ms timed TX lead and the same 914/915/916 MHz cycle.
Run those nodes together through `packet-radio --config NODE_A --peer-config NODE_B` on the shared
X310; set the address for your device. Qualification applies to the measured
same-host X310/UBX arrangement, not all X300-series boards or separate hosts.
Slower/intermediate settings, ping-pong, other tables and behavior overrides need
their own qualification. Overrides receive a custom ID and `unqualified` metadata.
See the exact measurement scope in [MODULATION_RESULTS.md](../MODULATION_RESULTS.md).

At 2 hops/s the nominal TX window is 100–400 ms of each 500 ms dwell; actual
readiness is later than 100 ms because the post-tune settling wait starts after
retuning completes. The conducted workload injects a packet at 200 ms on channel 0
and 300 ms on channel 1. A maximum frame occupies 34.048 ms, plus 10 ms timed lead,
32 microseconds transport idle and a 1 ms drain margin. Whole-burst admission
still rejects packets too late to fit before the trailing guard; it does not defer
these packets to the next hop. Neither the guard nor the timed lead was reduced.

`external-utc` means the host clocks supply the schedule. The measured arrangement
uses one workstation, so both applications share a clock. Separate computers need
externally disciplined UTC clocks and a measured relative error small enough to fit
the guard budget; two-host qualification is deferred. There is no on-air clock
acquisition or drift correction, no claim of phase coherence, and no automatic
proof of clock accuracy. The simple profile's `synchronized` status remains null.

Startup tunes directly to the channel selected by current UTC. Each change pauses
decoding, stops/drains UHD receive, tunes RX and TX, restarts receive, flushes queued
samples and old decoder output, and resets partial-frame state. After tuning, both
TX and decoding wait a further guard duration on a monotonic clock. The end-of-dwell
guard suppresses TX. Lost signal does not stop the schedule; the next correct-channel
frame can reacquire without any control traffic. A delayed application skips expired
slots, tunes directly to the current slot and reports missed slots. Even a delay of
one complete table cycle resets reception. Backward wall-clock changes are counted
and force retuning; applications using unsynchronized clocks cannot recover through
this mode alone.

A selected burst must finish before the end guard, including timed-TX lead, backend
zero-sample tail and a 1 ms drain margin. Admission is checked again after encoding
and modulation. A crossing burst is dropped once and counted as
`hop_burst_rejections`/`transmit_drops`; it is neither truncated nor retried on another
channel. Packets waiting while TX is disabled remain queued until an opportunity;
that is distinct from a selected burst failing admission. The maximum configured
payload must also fit a dwell before a device is opened. This is host-scheduled,
soft real-time operation: arbitrary OS stalls are outside the timing guarantee.

Status includes `active_frequency_hz`, `frequency_slot`, `hop_retune_ms`,
`hop_slots_missed`, `hop_clock_reversals`, `hop_tune_deadlines_missed`, and
`hop_burst_rejections`. The tune deadline counter means tuning finished outside the
start guard (startup during a dwell can legitimately increment it). Retune duration
includes receive draining/restart; it is not an RF settling measurement.

Decoded-frame recording/replay retains the resolved hop definition. Hopping raw IQ
recording is rejected until captures can preserve retune discontinuities and channel
segments. Fixed-frequency IQ capture remains available.

Ettus documents that AD93xx tuning is subject to RFIC scheduling and is not
necessarily deterministic even with timed commands. See
[Ettus AN-883, AD93xx caveats](https://kb.ettus.com/Timed_Commands_in_UHD#Timed_commands_on_AD93xx-based_devices).
Hardware-timed TX is retained, but hardware-timed LO tuning is not claimed here.
Digital-only frequency offsets or RFIC fast-lock tuning would need separate backend
work, bandwidth checks and conducted measurements; they are not enabled by accepting
a faster hop-rate value.

For the established **crossed 30 dB attenuated bench only**, run:

```bash
PYTHONPATH=. /tmp/packet-radio-v1-uhd/bin/python tools/hopping_check.py --seconds 300
```

The tool uses the two documented serials (override with `--serials A B`), TX 20 dB
and RX 40 dB. It prints summaries without retaining JSON or IQ. Its hopping-specific
acceptance is 300 seconds with one 256-byte packet per second in each direction,
separated in each dwell, then 60 seconds alternating 1/64/1024-byte payloads and
attempting one deliberately crossing burst per second. It requires exact delivery,
zero new stream errors, no missed steady-state tune deadlines, rejection of every
crossing burst, and recovery after a three-second off-channel interruption. This is
a distinct hopping workload; it does not replace the fixed simple profile's 5-packet/s
and 17-second recovery acceptance gates in `EMULATION_PROFILE.md`. Timing observations
use host clocks and device timestamps, not an independent RF analyzer.

## Step 7 reference rates

No target radio has been selected. Step 7 therefore defines a bounded, project-owned
rate reference set rather than assuming a target's rates, shaping or timing rules.
The new fixed-frequency simple 2FSK definitions and node pairs are:

| Profile | Bit/symbol rate | Sample rate | Samples/symbol | Deviation | RX/TX bandwidth | Maximum logical burst (1024-byte payload) |
|---|---|---|---|---|---|---|
| `simple-2fsk-100k-v1` | 100 kbit/s, 100 kbaud | 2 Msps | 20 | ±50 kHz | 1 MHz | 85.120 ms |
| `simple-2fsk-v1` | 250 kbit/s, 250 kbaud | 2 Msps | 8 | ±125 kHz | 1 MHz | 34.048 ms |
| `simple-2fsk-500k-v1` | 500 kbit/s, 500 kbaud | 2 Msps | 4 | ±250 kHz | 1.5 MHz | 17.024 ms |

Start with `configs/v1_1/peer-{a,b}-simple-100k.yaml` or
`peer-{a,b}-simple-500k.yaml`, set local device serials/gains/UDP ports, and validate
both peers together. These profiles retain the same simple frame layout and CRC,
rectangular continuous-phase 2FSK, bit 1 = upper tone, bit 0 = lower tone, and
per-burst phase reset. The two new rates do not change the existing 250 kbit/s
profile or automatically qualify hopping at a different rate.

Logical burst duration is `(payload_bytes + 40) * 8 / bit_rate_bps`; there is no
filter/resampler delay. The UHD backend additionally appends 32 microseconds of
zero-valued idle to drain the FIFO. Admission and accounting continue to use exact
sample counts; timed-TX lead and configured gap are additional scheduling time.
Raw IQ and frame captures preserve the rate and replay with the correct modem.

**Resampling decision:** all three reference rates are integral at 2 Msps, so a
resampler is unnecessary for this set and remains explicitly deferred. A rate such
as 19,200 bit/s at 2 Msps is still rejected before hardware opens. A future target
requiring a non-integral ratio must add and qualify a resampler, including filter
delay, stream history, timestamp conversion and exact output-count rules. This
step does not silently round requested rates or claim arbitrary-rate support.

**Receiver timing envelope:** independent continuous-phase reference IQ exercises
1/64/256/1024-byte payloads at each rate, separate and combined receiver sample-clock
errors of ±10 ppm and carrier offsets of ±750 Hz, with independent Gaussian I/Q
noise of standard deviation 0.04 per component on unit-amplitude samples. Seed
1101 makes the cases repeatable. Positive clock error means more receiver samples
per transmitted symbol; tone phase is integrated at those sample times. Carrier
offset is applied independently, rather than substituting it for timing drift.
Tests include fractional burst onset, all integer sample-start phases at the
negative clock-error corner, arbitrary block sizes and timestamp discontinuities.
They require exact decoded bytes and arrival timestamp error no greater than one
sample (500 ns); clean TX arrays match the independent phase reference within
1e-5 absolute complex-sample error.

This is a tested sample-domain envelope, not a calibrated RF SNR or oscillator
specification. The receiver acquires symbol timing from the preamble and does not
track clock drift within a packet. Exploratory maximum-payload cases at ±100 ppm
fail, and some intermediate cases fail at 250 kbit/s. Clock tracking is therefore
explicitly deferred, not implicitly available. A target requiring a wider clock
error or longer burst must reopen that work. Likewise, alternative tone polarity,
symbol mapping, shaping, burst ramps and cross-burst phase continuity are deferred
until a target provides a reference and numerical acceptance limits.

The conducted rate check uses the existing attenuated B205mini bench and runs one
minute per direction at five 256-byte packets/second plus boundary/repeated payloads.
It verifies submitted bytes/sample counts, timed burst gaps, exact delivery, stream
errors, process CPU, decoder processing time and sampled queue depth. Results and
limits are in `MODULATION_RESULTS.md`. These short rate checks do not replace the
broader five-minute fixed-profile qualification contract or establish peak capacity.
CPU numbers include the check's instrumentation; sampled queue depth is not an
atomic queue high-water counter. No language rewrite or decoder optimization is
justified solely by the new configurable rates.

## Step 8 fidelity acceptance

The fidelity reference is the project-defined simple framing contract at
100/250/500 kbit/s (including the 250 kbit/s hopping profile) and the frozen
protocol-v5 fixture for the generic 2FSK profile. No commercial target is implied.
The following tolerances are fixed before running the new fidelity comparisons:

- Packet bytes and independently recovered modulation bits: exact equality.
- Logical burst length: exact sample count; derived bit rate: relative error ≤1e-12.
- Clean complex TX samples: maximum absolute error ≤1e-5 against independently
  integrated tone phase. Unit envelope error: ≤1e-6; mean tone-frequency error: ≤1 Hz.
- Digital occupied spectrum: rectangular whole-burst FFT, zero-padded to the next
  power of two at least four times the sample count. Normalize each power spectrum
  to unit total power. Central 99% occupied-band edges (0.5%/99.5% cumulative power)
  must differ from the independent reference by no more than one FFT bin; integrated
  absolute normalized spectral-power error must be ≤1e-5. This is a digital IQ
  comparison, not a target spectral mask or calibrated conducted RF measurement.
- Receiver tests retain Step 7's ±10 ppm clock error, ±750 Hz offset, component noise
  standard deviation 0.04 and seed 1101. Missing timestamped blocks must not splice
  partial packets across a gap; bad CRC/length frames must not deliver altered bytes,
  and a following intact packet must decode exactly.
- Simple link tests require the configured minimum gap/turnaround on the application
  clock. Physical minimum RF turnaround is unmeasured. Hop timing retains Step 6's
  1-second dwell, 100 ms guards, complete-burst admission and recorded conducted limits.
- Conducted simple recovery mutes TX IQ for 17 seconds, intentionally loses one
  offered packet without retry, then requires the next intact packet within 5 seconds.
  Reliable-mode retry behavior is checked independently in deterministic loss tests;
  conducted reliable recovery is labeled separately from the ACK-free loss case.

Independent-host timing, calibrated RF spectrum/power, custom profile variants and
actual-target interoperability remain unqualified. Optional reference vectors are
inputs, not measurement reports; only final results belong in `MODULATION_RESULTS.md`.

Run the offline reference check from the checkout:

```bash
PYTHONPATH=. python tools/fidelity_check.py
```

It validates all five shipped emulation definitions and prints one summary per
profile; it opens no SDR and writes no result files. Installed packages include the
same tool under `share/packet-radio/tools`, and installation checks execute it there.
Its spectrum measurements cover logical, unfiltered baseband bursts; hardware
filters, images, leakage, absolute frequency/power and target spectral masks need
separate equipment/reference measurements. See the fidelity status matrix in
`MODULATION_RESULTS.md` before interpreting a passing result as RF interoperability.

On the established attenuated bench, `tools/simple_link_check.py` accepts
`--interrupt-seconds 17` to test intentional no-retry loss and recovery in both
directions at the selected reference rate. Use local schema-2 profiles with the
correct serials/gains; the source examples deliberately do not select bench devices.
The generic conducted checker retains its own reliable-mode recovery path. Neither
script treats successful local submission as proof of RF delivery.


## Upgrading from 1.0.0 to 1.1.0

The distribution remains `packet-radio`, imports remain `packet_radio`, and all
seven console commands retain their names. Install `packet_radio-1.1.0-py3-none-any.whl`
in a fresh UHD-compatible environment. Stop the old processes first, preserve
keys and replay state, and copy the bundled `configs` tree into writable storage.
Do not overwrite customized node files with examples.

Existing schema-1 configurations and generic-v5 wire bytes remain compatible;
no conversion or key rotation is required. Legacy capture formats 1 and 2 remain
readable. Capture format 3 includes the resolved profile so replay does not depend
on the original YAML. Recordings made by 1.1 should be replayed with 1.1.

For new profiles, keep the relative `configs/v1_1` and `configs/profiles` layout.
Choose a matching pair below, set each `hardware.device.serial` and appropriate
gains, and validate both nodes before starting. Schema-2 file paths resolve from
the node YAML directory; schema-1 paths retain working-directory semantics.

| Node pair in `configs/v1_1/` | Behavior and qualification scope |
|---|---|
| `peer-{a,b}-generic.yaml` | Reliable authenticated generic-v5 2FSK; short conducted regression |
| `peer-{a,b}-simple.yaml` | ACK-free simple 2FSK, 250 kbit/s; short conducted checks |
| `peer-{a,b}-simple-100k.yaml` | Simple 2FSK, 100 kbit/s; short conducted checks |
| `peer-{a,b}-simple-500k.yaml` | Simple 2FSK, 500 kbit/s; short conducted checks |
| `peer-{a,b}-hopping.yaml` | Simple 2FSK, 250 kbit/s, measured 914/915/916 MHz cycle at 1 hop/s on one host |
| `peer-{a,b}-x310-hopping.yaml` | Shared X310/UBX channels 0/1, simple 2FSK at 2 hops/s with 100 ms guards |

```bash
packet-radio-validate configs/v1_1/peer-a-simple.yaml configs/v1_1/peer-b-simple.yaml --resolved
packet-radio --config configs/v1_1/peer-a-simple.yaml
# In another terminal, after configuring the second SDR:
packet-radio --config configs/v1_1/peer-b-simple.yaml
```

The simple pair supplies `transmit-only` and `receive-only` examples. Set
`operation: bidirectional` on both for two-way ACK-free operation. Keep generic-v5
in bidirectional payload mode and provision keys as described in the program guide.
`input_kind: frame` is available only for simple frames; input must contain a valid
complete configured frame. Normal applications should use `input_kind: payload`.

Configuration is loaded at startup. Matching custom overrides are permitted but
marked unqualified. Simple mode does not provide authentication, retransmission or
duplicate suppression. Hopping requires externally aligned UTC and permits decoded
frame captures only; raw IQ hopping capture is rejected. Two-host hopping, arbitrary
fast hopping, non-integral resampling, calibrated RF fidelity and interoperability
with a real target radio remain unqualified or deferred. The short fixed-simple
checks do not establish sustained five-minute qualification. Consult
[final results](../MODULATION_RESULTS.md) for exact durations and measured limits.

### Shared X310 channels

`packet-radio --config NODE_A --peer-config NODE_B` runs two compatible profiles
on one X310 in a single process. Set both `hardware.device` mappings to the same
`type: x300` and address, and select `hardware.channel: 0` and `1`. In schema 1,
these fields are under `radio`. Use distinct local UDP bind and HTTP status ports.
The board clock is initialized once before streaming; neither endpoint resets it.
See [UHD setup](UHD_SETUP.md#two-endpoints-on-one-x310) for conducted examples and
fixed UBX analog-bandwidth behavior. This shares the reference clock and host;
it does not emulate two independent radio oscillators or computers.

## Timed-transmission submission margin

`link.hardware_timed_tx_lead_seconds` budgets ordinary timed data after waveform
and adapter preparation. `link.hardware_sync_tx_lead_seconds` budgets sync
packets before encoding because their authenticated payload contains the intended
airtime. These are separate settings.

`link.hardware_min_tx_lead_seconds` is the minimum remaining device-time lead
accepted for submission. Its default is zero for compatibility; it must be
nonnegative and strictly below both configured TX leads. A positive value causes
an insufficient-lead packet to be deferred before any IQ is sent. Data remains
queued; synchronization packets are rebuilt with a fresh target on their next
attempt. It does not silently move the target of an already-encoded sync packet.

The conducted X310 diagnostic profiles
`configs/bench/peer-{a,b}-x310-bpsk.yaml` use 10 ms ordinary lead, 20 ms sync lead
and 2 ms minimum remaining lead, with TX/RX gains 0 dB. These legacy reliable-link
profiles are for timing investigation; BPSK backlog remains a separate issue.
The original B205 profiles and global lead defaults are unchanged.

## Pending-data age limit

`link.queue_max_age_seconds` (schema 1) or
`runtime.queue_max_age_seconds` (schema 2) optionally abandons stale pending
application data. The default `null` preserves the existing unlimited-age
behavior. Values must be finite and positive. Queue count and airtime limits
still apply; enabling an age limit does not enlarge either capacity.

The reliable link expires queued and unacknowledged data before selection,
admission and ACK matching. Its control queue is preserved, so expiry cannot
remove ACK/SYNC traffic needed by the peer. The simple link expires waiting
data without adding acknowledgements or retries. A burst already selected or
submitted is not interrupted, and expiry runs when the application processes
the link; this is not a hard real-time transmission cutoff.

`queue_expired` counts abandoned pending data separately from capacity
`queue_drops` and `retry_exhausted`. An expired unacknowledged packet may already
have reached the peer; this counter does not prove RF loss. Queue age is now
visible in regular status without enabling timing diagnostics.
`oldest_pending_packet_age_ms` includes control traffic for the reliable link,
so an old control packet can exceed the configured data-age limit.

The new `configs/v1_1/peer-{a,b}-x310-generic.yaml` pair selects the explicit
`generic-bpsk-unslotted-v5` profile and a five-second host data-age limit.
Its 600 ms ACK timeout and absence of TDD slots belong to the portable profile;
host queue capacity/age and UHD lead budgets belong to runtime settings.
Choose the corresponding `generic-{2fsk,msk,gmsk,qpsk}-unslotted-v5.yaml`
profile on both nodes to select another waveform. Existing TDD and frozen
generic/simple profiles are preserved.

These new profiles remain marked `unqualified`: short conducted delivery checks
do not establish sustained capacity or a clean stream under every workload.
See the Step 5 measurements in [MODULATION_RESULTS.md](../MODULATION_RESULTS.md).

## UHD host transmit batching

`link.hardware_tx_batch_packets` (schema 1) or
`runtime.hardware_tx_batch_packets` (schema 2) controls the maximum number of
UHD transport packets submitted per host call. It is an integer in 1..256,
defaulting to 1 for backward compatibility. The application applies it before
TX starts. It does not alter packet bytes, waveform samples, RF timing targets,
ACK/TDD policy or portable-profile identity. Partial acceptance and burst
completion/error handling still apply. The actual sample limit is the count
multiplied by the streamer's transport packet size, reported in hardware status.

The paired `configs/v1_1/peer-{a,b}-x310-qpsk.yaml` examples use 256 for the
conducted X310 QPSK underflow mitigation. Other existing examples remain at 1.
This setting is a host/device tuning choice, not evidence that all hardware or
emulation profiles should use 256. See [UHD_SETUP.md](UHD_SETUP.md#uhd-transmit-batching)
and the final QPSK measurements in [MODULATION_RESULTS.md](../MODULATION_RESULTS.md).
