# Configuration schema 1

Profiles are loaded at startup; both peers must be restarted with matching waveform
settings. `schema_version: 1` is mandatory. Unversioned/legacy YAML and unknown
sections/fields are rejected with a migration message. There is no silent fallback
to another modem or rate. The configuration schema version is independent of packet
protocol **5**, which remains unchanged.

Start from the maintained examples in `configs/examples/`. The `configs/bench/peer-*-bpsk.yaml`
profiles contain the measured local serial numbers, distinct UDP/status ports and
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

Schema 1 exposes a fixed center frequency. Hop tables, hop rate, live switching and
arbitrary packet formats remain future-version work. The internal frequency
scheduler and injected simulation APIs are retained for their existing tests.

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
validation and replay use that factory. `Modem` defines modulation, streaming and
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

## Endpoint matching and release support

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
