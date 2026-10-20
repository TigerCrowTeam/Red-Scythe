# Packet Radio 1.1 roadmap

Status: Steps 1–9 complete for the documented project reference profiles and explicit deferrals.
Simple-frame 2FSK hopping is qualified on the same-host bench; new fixed reference
rates have short conducted evidence.
Schema-2 YAML selects reliable generic-v5 or ACK-free simple frames, with resolved
capture/replay. The documented hopping table runs at 1 hop/s; faster rates are rejected.
Schema 1 remains supported. Version 1.1.0 is packaged locally; the original 1.0.0
archives are preserved. This document owns the 1.1 plan and acceptance criteria;
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

- [x] Specify the first reference profile: begin with fixed-frequency 2FSK, an
      explicit preamble/sync, variable payload length and CRC, without mandatory
      FEC, signatures, ACKs or synchronization beacons.
- [x] Freeze its exact bit order, field widths, byte order, CRC parameters/coverage,
      payload limits and burst timing before implementation. Give it a distinct
      format identifier; it must not be mistaken for `generic-v5`.
- [x] Define the initial acceptance level as behavior emulation between our SDRs.
      Record a named target, specification revision and evidence source when one
      is chosen; direct interoperability then gets an additional acceptance gate.
- [x] List target properties as implemented, approximated, unsupported or unknown.
      Unknown details must not silently use generic-v5 defaults.
- [x] Preserve the existing generic-v5 wire fixtures and qualified profiles as the
      regression baseline.

**Completion gate:** the first profile has an unambiguous byte/bit layout and
transmission contract that can be implemented and tested independently. Selection
of a commercial target must not block the generic architectural work below.

The frozen [simple 2FSK contract](EMULATION_PROFILE.md) defines `simple-2fsk-v1`
and `simple-frame-v1`, independent byte references, timing and acceptance gates.
Step 3 implements the frame codec and 2FSK sample pipeline; application link modes
and configuration selection remain Steps 4–5.

## Step 2 — Separate the configurable processing layers

Primary areas: `app.py`, `reception.py`, `transmission.py`, `protocol/`,
`modems/base.py`, `modems/burst.py` and `modems/registry.py`.

- [x] Define small interfaces for packet encoding/decoding, frame validation,
      protection/bit transforms, burst framing and link behavior.
- [x] Make the order of CRC, whitening, FEC, interleaving and other transforms
      explicit in a profile; different radios may require different orders.
- [x] Inject the selected components into the application and modem factory;
      remove direct generic-v5 assumptions from the common processing path.
- [x] Make receive-candidate validation belong to the selected packet format,
      replacing the unconditional generic-v5 CRC validator.
- [x] Keep exact encoded length, transmitted sample count and airtime available
      for queue admission and scheduling across all selected components.
- [x] Retain the current behavior as an implementation of these interfaces.
      Preserve generic-v5 signatures, replay protection and reliability defaults.

**Completion gate:** generic-v5 passes its frozen wire vectors, all five modulation
regressions and lifecycle tests through the new interfaces with unchanged wire
bytes. Unsupported layer combinations fail before opening an SDR.

Step 2 verification: 937 tests passed, 88.67% combined coverage, frozen generic-v5
wire fixtures and profiles unchanged, and 20 sample arrays identical to the 1.0.0
wheel. All five short conducted modem checks and clean-wheel installation checks
passed. Architecture details are in `PROGRAM_GUIDE.md`; final measurements remain
in `MODULATION_RESULTS.md`. No new YAML format or link-mode options are enabled.

## Step 3 — Add configurable framing and protection

- [x] Support configurable preamble bytes/repetitions, sync word, bit order,
      payload length encoding and fixed-length or variable-length framing.
- [x] Add selectable CRC implementations with explicit initialization, polynomial,
      reflection, final XOR, output order and coverage. Implement only the initial
      required variants, verified with independent vectors.
- [x] Add a true no-FEC path; retain Hamming/interleaving and optional Reed–Solomon
      as named protection choices. RS parity zero alone is not no-FEC.
- [x] Assess optional whitening and differential encoding against the frozen profile.
      Neither is required; both are explicitly rejected. Implementation, seed/reset
      and transform-order definitions remain deferred until a target requires them.
- [x] Support selectable bit order for the byte-aligned frozen profile. Non-byte
      fields are not required and are rejected rather than silently padded; support
      remains deferred until a target requires it.
- [x] Validate lengths before allocation and reject malformed, truncated or
      incompatible frames with bounded receive buffering.
- [x] Document the supported subset and reject unsupported format options.

**Completion gate:** the new simple profile encodes and decodes independent
reference vectors, including split input blocks and malformed frames. TX samples,
RX acquisition and airtime calculations all use the same selected framing settings.

Step 3 provides `FrameSettings`, `SimpleFrameCodec` and `make_frame_modem` as a
Python API. It supports CRC-16/CCITT-FALSE, CRC-32/ISO-HDLC or no CRC, a true no-FEC
path, and Hamming/interleaving with optional Reed–Solomon. The new format currently
supports 2FSK only. The application still selects only `generic-v5`: ACK-free link
behavior and reusable YAML profiles belong to Steps 4–5. See the
[framing API](CONFIGURATION.md#version-11-configurable-framing-api) for
settings, transform order and limits; final checks are in `MODULATION_RESULTS.md`.

Step 3 verification: 1,121 tests passed with 89.33% combined coverage, including
184 new framing/modem cases. Independent frame and waveform references, exact
generic-v5 waveform comparisons, all five short conducted regressions and clean
installed-package checks passed.

## Step 4 — Make link behavior selectable

- [x] Provide transmit-only, receive-only and bidirectional operation.
- [x] Provide a packet mode without automatic ACK/retry traffic or generic SYNC
      beacons, alongside the existing reliable generic-v5 mode.
- [x] Separate transmission permission from peer synchronization so a receive-only
      node or unsynchronized simple transmitter does not require generic beacons.
- [x] Make supported burst intervals, turnaround delays and transmit/receive windows
      explicit; retain bounded queues and observable drops.
- [x] Allow target-defined frame bytes to enter the selected burst pipeline when
      requested. Distinguish payload input from already-framed input to avoid
      adding headers or CRC twice. Raw IQ transmission is a separate future feature.
- [x] Make health/status reporting appropriate to the mode: receive-only health
      must not require outgoing ACKs, and idle TX-only operation need not have a peer.
- [x] Keep security semantics explicit per format. A format without signatures
      does not imply that generic-v5 authentication has been disabled globally.

**Completion gate:** two SDRs exchange the simple profile's exact payloads without
extra generic-v5 headers or control traffic. Receive-only mode emits no TX samples;
transmit-only operation does not wait for peer acquisition.

Step 4 provides `SimpleProfile` / `SimpleLinkSettings` through the explicit
`RadioApp(..., processing=...)` Python API. Receive windows are continuous;
transmission is unscheduled, with minimum idle/interval/turnaround delays and no
collision avoidance. Preframed input means validated body bytes before protection,
not arbitrary IQ. Unsupported synchronization, hopping, authentication and capture
combinations fail before opening hardware. Schema-1 CLI/YAML selection is unchanged.
See the [live API](CONFIGURATION.md#version-11-live-simple-link-modes).

Short conducted checks delivered 155/155 exact packets in each sequential direction
(payload input forward, preframed input in reverse), with zero receive-only TX samples
and no control traffic. All five generic-v5 short regressions also passed. These
checks do not replace sustained qualification or independent RF timing measurements;
final evidence and limitations are in `MODULATION_RESULTS.md`.

Step 4 final verification: **1,158 tests passed**, **89.94% combined line/branch
coverage**, clean installed-wheel checks and unchanged generic-v5 reference samples.

## Step 5 — Introduce reusable emulation profiles

Primary areas: `config.py`, `configs/`, validation CLI and capture/replay metadata.

- [x] Introduce a versioned schema that selects waveform, packet format, protection,
      link mode and frequency behavior explicitly.
- [x] Separate reusable radio-behavior settings from local device serials, gains,
      ports, keys, recording paths and host-specific timing margins.
- [x] Define deterministic composition and override rules; reject unknown keys,
      incompatible combinations and ambiguous overrides.
- [x] Supply paired generic-v5 and simple-emulation examples with a resolved
      configuration preview and a compatibility check appropriate to each mode.
- [x] Give each emulation profile an identifier/revision and record its reference,
      approximation limits and qualification status.
- [x] Update captures/replay to retain the exact resolved format, transforms and
      timing settings; preserve replay of existing supported captures.
- [x] Document migration from schema 1, including whether it remains directly
      readable or requires an explicit conversion. Never reinterpret old profiles.

**Completion gate:** switching between generic-v5 and the new emulation profile
requires configuration changes only. Validation and replay reconstruct the same
processing chain without source edits or hidden defaults.

**First usable 1.1 milestone:** Steps 1–5 produce two genuinely different packet-radio
profiles on the existing modulations. Complete this before expanding target scope.

Step 5 implements schema-2 node files referencing versioned definitions in
`configs/profiles/`, with paired examples in `configs/v1_1/`. Explicit leaf
composition, variant identity/qualification handling, resolved previews and
mode-aware pair checks select either generic-v5 or simple frames without source
changes. Schema 1 remains directly readable with its prior defaults and paths.

Application capture format 3 embeds the resolved definition, processing and local
timing configuration; replay does not depend on external profile files. Capture
formats 1/2 remain readable. Frame/IQ replay tests cover custom CRC, bit order,
fixed-length and FEC variants, plus all five generic-v5 waveforms. Short schema-2
conducted checks passed for both the simple and reliable 2FSK profiles. Final
software counts and measurement limits are recorded in `MODULATION_RESULTS.md`.

Step 5 final verification: **1,244 tests passed**, **90.06% combined line/branch
coverage**, both schema-2 conducted checks and clean installed-package/replay checks.

## Step 6 — Implement and qualify frequency hopping

Primary areas: `scheduler.py`, `clock_sync.py`, `hardware/uhd.py` and configuration.

- [x] Expose a validated frequency table and deterministic sequence, with dwell
      duration or hop rate, guard interval and epoch/synchronization policy.
- [x] Define whether repeated frequencies are allowed and how sequence position,
      startup acquisition, missed hops and recovery work.
- [x] Define whether a burst must fit within one dwell; initially reject bursts
      that would cross a hop rather than truncating or silently delaying them.
- [x] Design synchronization for the selected emulation mode without introducing
      unsolicited generic-v5 beacons into a target waveform.
- [x] Characterize the current stop/tune/restart path, buffer flushing, RF settling
      and first usable RX/TX samples. Host tune-call duration alone is insufficient.
- [x] Evaluate hardware-timed tuning or other tuning strategies only against actual
      B205mini/UHD capability and measurements; do not promise a hop-rate range yet.
- [x] Test acquisition, sustained hopping, dwell timing, missed deadlines and
      interrupted-path recovery at each claimed operating profile.

**Completion gate:** a documented hop table/rate passes measured timing and packet
checks on the conducted bench. Unsupported rates fail validation. Fixed-frequency
behavior remains unchanged. If hardware limits prevent a target's hopping behavior,
record that limit rather than claiming target compatibility.

Step 6 implements the schema-2 `simple-2fsk-hopping-v1` definition and paired node
examples. A shared host UTC schedule cycles through 914/915/916 MHz with 1 s dwells
and 100 ms guards. The 300-second bidirectional conducted workload, 60-second mixed
payload/boundary checks and off-channel recovery passed. Faster-than-1-hop/s settings
are rejected; other tables/rates and independent hosts remain unqualified.

Decoder reset/output flushing is serialized with tuning. Late selected bursts drop
without retries; missed slots and clock reversals are observable. Frame capture
retains the schedule; hopping IQ capture is explicitly rejected pending discontinuity
metadata. Hardware-timed LO determinism is not claimed. See `CONFIGURATION.md` for
the complete timing contract and `MODULATION_RESULTS.md` for measurements and limits.

Step 6 final verification: **1,274 tests passed**, **90.14% combined coverage**,
all conducted hopping checks, fixed-frequency regressions and installed-wheel checks.

## Step 7 — Extend rate and receiver flexibility where needed

No target radio has been selected. This step uses a bounded reference set of
100/250/500 kbit/s simple 2FSK at 2 Msps; it does not infer target-specific behavior.

- [x] Compare reference rates against integral samples/symbol: 20/8/4 respectively.
      Add reusable 100/500 kbit/s definitions and paired host examples.
- [x] Assess resampling: explicitly deferred because this reference set needs none.
      Non-integral ratios remain rejected. A target requiring them must add filter
      delay, history, timestamp conversion and output-count tests before support.
- [x] Characterize sample-clock mismatch separately from carrier offset. Independent
      IQ passes the documented ±10 ppm, ±750 Hz, noise and maximum-payload cases.
      Wider-error failures are recorded. In-burst tracking is explicitly deferred
      until a target requires a wider envelope or longer packets.
- [x] Assess symbol mapping/polarity and pulse shaping: retain the defined two-tone
      mapping and rectangular pulses. Alternative mapping, shaping, ramps and
      cross-burst phase continuity are explicitly deferred pending a target reference.
- [x] Measure process CPU, decoder processing/queue observations and timed-burst gaps
      on both new rates before considering optimization or implementation-language changes.

**Completion gate:** reference rates pass independent waveform and impaired-receiver
checks with correct airtime/timestamps. Each new rate has measured streaming evidence.
Explicitly deferred features are not claimed as supported; broader rate support is
not a universal compatibility claim.

Step 7 verification: **409 affected tests passed**, including 118 new rate/capture
cases. At both 100 and 500 kbit/s, the conducted check delivered **305/305 exact
packets each direction** with zero new stream errors/retries. These are one-minute
rate checks plus boundary/repeated payloads, not five-minute fixed-profile
qualification. The generic waveforms, fixed 250 kbit/s profile and hopping profile
are unchanged. Resampling, drift tracking and target-specific shaping remain
explicit deferrals. See `CONFIGURATION.md` for the reference contract and
`MODULATION_RESULTS.md` for measurements, failure boundaries and performance scope.

## Step 8 — Verify emulation fidelity

- [x] Use independent packet vectors and waveform references for each emulation
      profile; avoid relying only on our own transmitter/receiver round trips.
- [x] Compare decoded bits/bytes, burst duration, symbol rate, deviation or mapping,
      occupied spectrum and applicable turnaround/hop timing against the reference.
- [x] Add repeatable tests for noise, frequency offset, sample-clock error, timing
      phase, malformed frames and dropped sample blocks, with fixed seeds.
- [x] Define numerical tolerances before tuning implementations to pass them.
      State the noise/power normalization; do not label raw amplitude as calibrated SNR.
- [x] Run conducted packet delivery and recovery checks, distinguishing intentional
      no-retry loss from reliable-mode retransmission behavior.
- [x] Label the evidence as project-reference/SDR-only. Actual-target verification
      is explicitly deferred: no target/model has been selected. Before claiming
      interoperability, test each supported direction and its control behavior.
- [x] Retain optional reference captures/vectors as test inputs when needed;
      retain only final measurement summaries in `MODULATION_RESULTS.md`.

**Completion gate:** each claimed emulation behavior has a reference, tolerance and
passing result. Partial matches are labeled explicitly. Independent-host and
actual-target qualification remain unclaimed unless those setups were tested.

Step 8 verification: **563 affected tests passed**, including 48 new fidelity
cases. All **19 independent waveform/spectrum comparisons** passed the predeclared
tolerances. Every fixed simple rate passed a 17-second intentional-loss recovery
check in both directions, without retries. The reusable generic profile delivered
50/50 packets each way and recovered both directions within 1.414 seconds; its
reliable retries are recorded separately. Step 6 remains the conducted hop evidence.

`tools/fidelity_check.py` runs offline in the checkout and installed package.
`CONFIGURATION.md` fixes the numerical acceptance contract. The status matrix and
final measurements in `MODULATION_RESULTS.md` explicitly separate passing digital
fidelity from unqualified calibrated RF spectrum/power, physical minimum turnaround,
independent-host timing and actual-target interoperability. This step adds validation
coverage and tools; it does not alter the production waveform or receiver algorithms.

## Step 9 — Document and package version 1.1

- [x] Update operating guides, protocol descriptions, parameter limits and migration
      instructions to match the final supported profiles.
- [x] Include examples for generic-v5, the first emulation profile, supported
      operating modes and any qualified hopping configuration.
- [x] Update package version and installed-release checks together when producing
      the 1.1 release; preserve the `packet-radio`/`packet_radio` names.
- [x] Verify installed wheels outside the checkout, including bundled resources,
      profile validation, capture/replay and packet exchange for both formats.
- [x] Run format/lint/type checks, the software suite with coverage, conformance,
      dependency audit and package checks. Keep applicable generic-v5 hardware
      regressions; do not repeat long runs without a relevant change or concern.
- [x] Publish final summaries in the existing results document, with actual test
      durations and explicit target/hardware/profile limitations.

**Completion gate passed:** the installed 1.1.0 wheel reproduces all five reusable
profile pairs, validates their bundled YAML, exchanges packets for both formats,
and replays fixed-profile IQ/frames and hopping frames outside the checkout.
All 1,440 software tests passed with 90.14% combined line/branch coverage.
Final checks and qualification limits are recorded in [MODULATION_RESULTS.md](../MODULATION_RESULTS.md).

## Acceptance policy and sequencing

Complete Steps 1–5 in order. Step 8's reference checks accompany each implementation
step; they are not postponed until the end. Prioritize Steps 6 and 7 according to
the selected target after the first usable milestone. The supported hopping rate is limited to the experimentally qualified profile;
new schedules require their own measurements.

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
