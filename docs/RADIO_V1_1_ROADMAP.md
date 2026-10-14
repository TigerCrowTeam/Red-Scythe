# Packet Radio 1.1 roadmap

Status: planned; implementation has not started. Version 1.0.0 remains the packaged
and measured baseline. This document owns the 1.1 plan and acceptance criteria;
final measured results stay in [MODULATION_RESULTS.md](../MODULATION_RESULTS.md).
Do not create per-step status documents or retain temporary qualification JSON.

## Objective and scope

Make the radio configurable enough to reproduce selected radios' waveform,
packet structure and transmission behavior. Preserve the working generic packet
radio as a selectable `generic-v5` profile, using the existing direct UHD backend
and BPSK, 2FSK, MSK, GMSK and retained QPSK implementations.

There are two distinct acceptance levels:

- **Behavior emulation:** our SDR pair reproduces a documented waveform, framing
  and timing profile, checked against independent references.
- **Interoperability:** an SDR exchanges correct packets with an actual target
  radio. Passing tests between our SDRs does not establish this level.

No target radio/model has been selected yet. Begin with a deliberately simple,
fixed-frequency emulation profile to prove the architecture. Select a real target
before adding target-specific encoding, timing or hardware requirements. Do not
claim universal compatibility or invent unspecified target behavior.

Continue using the two B205mini devices and confirmed attenuated conducted setup.
Two-computer physical qualification remains deferred until explicitly resumed.
Startup-loaded configuration is sufficient; a GUI and live waveform switching are
not required for 1.1. Version 1.1 does not automatically imply a new on-air protocol.

## Step 1 — Define the emulation contract

- [ ] Specify the first reference profile: begin with fixed-frequency 2FSK, an
      explicit preamble/sync, variable payload length and CRC, without mandatory
      FEC, signatures, ACKs or synchronization beacons.
- [ ] Freeze its exact bit order, field widths, byte order, CRC parameters/coverage,
      payload limits and burst timing before implementation. Give it a distinct
      format identifier; it must not be mistaken for `generic-v5`.
- [ ] Define the initial acceptance level as behavior emulation between our SDRs.
      Record a named target, specification revision and evidence source when one
      is chosen; direct interoperability then gets an additional acceptance gate.
- [ ] List target properties as implemented, approximated, unsupported or unknown.
      Unknown details must not silently use generic-v5 defaults.
- [ ] Preserve the existing generic-v5 wire fixtures and qualified profiles as the
      regression baseline.

**Completion gate:** the first profile has an unambiguous byte/bit layout and
transmission contract that can be implemented and tested independently. Selection
of a commercial target must not block the generic architectural work below.

## Step 2 — Separate the configurable processing layers

Primary areas: `app.py`, `reception.py`, `transmission.py`, `protocol/`,
`modems/base.py`, `modems/burst.py` and `modems/registry.py`.

- [ ] Define small interfaces for packet encoding/decoding, frame validation,
      protection/bit transforms, burst framing and link behavior.
- [ ] Make the order of CRC, whitening, FEC, interleaving and other transforms
      explicit in a profile; different radios may require different orders.
- [ ] Inject the selected components into the application and modem factory;
      remove direct generic-v5 assumptions from the common processing path.
- [ ] Make receive-candidate validation belong to the selected packet format,
      replacing the unconditional generic-v5 CRC validator.
- [ ] Keep exact encoded length, transmitted sample count and airtime available
      for queue admission and scheduling across all selected components.
- [ ] Retain the current behavior as an implementation of these interfaces.
      Preserve generic-v5 signatures, replay protection and reliability defaults.

**Completion gate:** generic-v5 passes its frozen wire vectors, all five modulation
regressions and lifecycle tests through the new interfaces with unchanged wire
bytes. Unsupported layer combinations fail before opening an SDR.

## Step 3 — Add configurable framing and protection

- [ ] Support configurable preamble bytes/repetitions, sync word, bit order,
      payload length encoding and fixed-length or variable-length framing.
- [ ] Add selectable CRC implementations with explicit initialization, polynomial,
      reflection, final XOR, output order and coverage. Implement only the initial
      required variants, verified with independent vectors.
- [ ] Add a true no-FEC path; retain Hamming/interleaving and optional Reed–Solomon
      as named protection choices. RS parity zero alone is not no-FEC.
- [ ] Add optional whitening and differential encoding when specified by the
      initial/selected target profile, including reset/seed and transform order.
- [ ] Handle bit-level framing where required without silently padding bits into
      a different over-air layout.
- [ ] Validate lengths before allocation and reject malformed, truncated or
      incompatible frames with bounded receive buffering.
- [ ] Document the supported subset and reject unsupported format options.

**Completion gate:** the new simple profile encodes and decodes independent
reference vectors, including split input blocks and malformed frames. TX samples,
RX acquisition and airtime calculations all use the same selected framing settings.

## Step 4 — Make link behavior selectable

- [ ] Provide transmit-only, receive-only and bidirectional operation.
- [ ] Provide a packet mode without automatic ACK/retry traffic or generic SYNC
      beacons, alongside the existing reliable generic-v5 mode.
- [ ] Separate transmission permission from peer synchronization so a receive-only
      node or unsynchronized simple transmitter does not require generic beacons.
- [ ] Make supported burst intervals, turnaround delays and transmit/receive windows
      explicit; retain bounded queues and observable drops.
- [ ] Allow target-defined frame bytes to enter the selected burst pipeline when
      requested. Distinguish payload input from already-framed input to avoid
      adding headers or CRC twice. Raw IQ transmission is a separate future feature.
- [ ] Make health/status reporting appropriate to the mode: receive-only health
      must not require outgoing ACKs, and idle TX-only operation need not have a peer.
- [ ] Keep security semantics explicit per format. A format without signatures
      does not imply that generic-v5 authentication has been disabled globally.

**Completion gate:** two SDRs exchange the simple profile's exact payloads without
extra generic-v5 headers or control traffic. Receive-only mode emits no TX samples;
transmit-only operation does not wait for peer acquisition.

## Step 5 — Introduce reusable emulation profiles

Primary areas: `config.py`, `configs/`, validation CLI and capture/replay metadata.

- [ ] Introduce a versioned schema that selects waveform, packet format, protection,
      link mode and frequency behavior explicitly.
- [ ] Separate reusable radio-behavior settings from local device serials, gains,
      ports, keys, recording paths and host-specific timing margins.
- [ ] Define deterministic composition and override rules; reject unknown keys,
      incompatible combinations and ambiguous overrides.
- [ ] Supply paired generic-v5 and simple-emulation examples with a resolved
      configuration preview and a compatibility check appropriate to each mode.
- [ ] Give each emulation profile an identifier/revision and record its reference,
      approximation limits and qualification status.
- [ ] Update captures/replay to retain the exact resolved format, transforms and
      timing settings; preserve replay of existing supported captures.
- [ ] Document migration from schema 1, including whether it remains directly
      readable or requires an explicit conversion. Never reinterpret old profiles.

**Completion gate:** switching between generic-v5 and the new emulation profile
requires configuration changes only. Validation and replay reconstruct the same
processing chain without source edits or hidden defaults.

**First usable 1.1 milestone:** Steps 1–5 produce two genuinely different packet-radio
profiles on the existing modulations. Complete this before expanding target scope.

## Step 6 — Implement and qualify frequency hopping

Primary areas: `scheduler.py`, `clock_sync.py`, `hardware/uhd.py` and configuration.

- [ ] Expose a validated frequency table and deterministic sequence, with dwell
      duration or hop rate, guard interval and epoch/synchronization policy.
- [ ] Define whether repeated frequencies are allowed and how sequence position,
      startup acquisition, missed hops and recovery work.
- [ ] Define whether a burst must fit within one dwell; initially reject bursts
      that would cross a hop rather than truncating or silently delaying them.
- [ ] Design synchronization for the selected emulation mode without introducing
      unsolicited generic-v5 beacons into a target waveform.
- [ ] Characterize the current stop/tune/restart path, buffer flushing, RF settling
      and first usable RX/TX samples. Host tune-call duration alone is insufficient.
- [ ] Evaluate hardware-timed tuning or other tuning strategies only against actual
      B205mini/UHD capability and measurements; do not promise a hop-rate range yet.
- [ ] Test acquisition, sustained hopping, dwell timing, missed deadlines and
      interrupted-path recovery at each claimed operating profile.

**Completion gate:** a documented hop table/rate passes measured timing and packet
checks on the conducted bench. Unsupported rates fail validation. Fixed-frequency
behavior remains unchanged. If hardware limits prevent a target's hopping behavior,
record that limit rather than claiming target compatibility.

## Step 7 — Extend rate and receiver flexibility where needed

- [ ] Compare the selected target's exact bit/symbol rates against the existing
      integral-samples-per-symbol constraint.
- [ ] Add a resampling stage for required non-integral combinations, with explicit
      delay, filter behavior, timestamp mapping and sample-count rules.
- [ ] Characterize sample-clock mismatch separately from carrier-frequency offset;
      add timing tracking where the target's burst lengths require it.
- [ ] Make target-required symbol mapping/polarity and pulse shaping selectable.
      Add burst ramps or phase-continuity behavior only with a defined reference.
- [ ] Measure CPU load, decoder queue growth and scheduling margins at the new
      rates before choosing optimizations or changing implementation language.

**Completion gate:** required rates pass independent waveform and impaired-receiver
checks with correct airtime/timestamps. Hardware-supported claims have measured
streaming evidence. Features unnecessary for the selected target can be explicitly
deferred; broader rate support is not a universal compatibility claim.

## Step 8 — Verify emulation fidelity

- [ ] Use independent packet vectors and waveform references for each emulation
      profile; avoid relying only on our own transmitter/receiver round trips.
- [ ] Compare decoded bits/bytes, burst duration, symbol rate, deviation or mapping,
      occupied spectrum and applicable turnaround/hop timing against the reference.
- [ ] Add repeatable tests for noise, frequency offset, sample-clock error, timing
      phase, malformed frames and dropped sample blocks, with fixed seeds.
- [ ] Define numerical tolerances before tuning implementations to pass them.
      State the noise/power normalization; do not label raw amplitude as calibrated SNR.
- [ ] Run conducted packet delivery and recovery checks, distinguishing intentional
      no-retry loss from reliable-mode retransmission behavior.
- [ ] When an actual target becomes available, verify traffic in each supported
      direction and its control behavior. Label SDR-only evidence separately.
- [ ] Retain optional reference captures/vectors as test inputs when needed;
      retain only final measurement summaries in `MODULATION_RESULTS.md`.

**Completion gate:** each claimed emulation behavior has a reference, tolerance and
passing result. Partial matches are labeled explicitly. Independent-host and
actual-target qualification remain unclaimed unless those setups were tested.

## Step 9 — Document and package version 1.1

- [ ] Update operating guides, protocol descriptions, parameter limits and migration
      instructions to match the final supported profiles.
- [ ] Include examples for generic-v5, the first emulation profile, supported
      operating modes and any qualified hopping configuration.
- [ ] Update package version and installed-release checks together when producing
      the 1.1 release; preserve the `packet-radio`/`packet_radio` names.
- [ ] Verify installed wheels outside the checkout, including bundled resources,
      profile validation, capture/replay and packet exchange for both formats.
- [ ] Run format/lint/type checks, the software suite with coverage, conformance,
      dependency audit and package checks. Keep applicable generic-v5 hardware
      regressions; do not repeat long runs without a relevant change or concern.
- [ ] Publish final summaries in the existing results document, with actual test
      durations and explicit target/hardware/profile limitations.

**Completion gate:** the installable release reproduces the supported profiles
from documented configuration, with no source editing required.

## Acceptance policy and sequencing

Complete Steps 1–5 in order. Step 8's reference checks accompany each implementation
step; they are not postponed until the end. Prioritize Steps 6 and 7 according to
the selected target after the first usable milestone. Hopping is planned work, but
its supported rate must be established experimentally.

Use exact binary-payload correctness and zero duplicate application deliveries
where the mode promises duplicate suppression. Preserve generic-v5's existing
reliability contract. For new modes, freeze delivery, latency and timing thresholds
against their own intended behavior; an ACK-free mode must not inherit ACK tests.

Default to short conducted acceptance runs: five-minute sustained traffic,
one-minute boundary checks, and targeted startup/shutdown/recovery checks. For
comparable generic-v5 runs, retain five 256-byte packets/second/direction and the
existing delivery/error thresholds. Adapt offered load and payload boundaries to
each emulation profile's actual capacity, documenting those choices before testing.
Do not automatically require 30-minute tests.

Keep unsupported features explicit: no universal radio emulation, no automatic
protocol discovery, no assumed proprietary-format interoperability, and no promise
of live reconfiguration or arbitrary hop rates. Select a concrete target and extend
only the behavior needed to reproduce it accurately.
