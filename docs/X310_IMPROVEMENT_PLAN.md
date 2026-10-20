# X310 software improvement plan

**Current status (2026-10-15):** The PSK echo follow-up passed all 16 affected
hardware checks, closing the documented same-host conducted acceptance matrix
with the preceding Step 9 evidence. The X310 is the development reference within
that tested envelope. Revised-code B205 comparison has now exposed PSK and
reliable-link recovery failures, supporting that scoped reference choice.
Independent-host/target interoperability remains unqualified. The
initial Step 9 failures and subsequent correction are preserved below.

## Objective

Improve packet latency, reliable delivery and timed transmission on the X310,
while preserving configurable modulation, bit rate, framing and hopping behavior.
Keep BPSK, 2FSK, MSK, GMSK and QPSK available. Preserve the direct UHD backend and
working B205 support.

This implementation sequence includes scoped progress checkpoints; it is not
a promise that the X310 will outperform the B205s. Final measurements belong in
[MODULATION_RESULTS.md](../MODULATION_RESULTS.md). Keep this as the single X310
improvement plan; do not create separate progress documents for each step.

## Starting point

The [October 14 comparison](../MODULATION_RESULTS.md#x310-two-channel-comparison--2026-10-14)
used one X310 with two UBX-160 boards, 1 Gb Ethernet, 2 Msps per channel and crossed
30 dB attenuated connections. Both endpoints shared one device and clock.

| Finding | What it means for this work |
|---|---|
| UHD continuous RX/TX passed on both channels at 2 Msps | Investigate application burst timing before assuming insufficient Ethernet throughput. |
| Legacy BPSK delivered 95/150 and 90/150 within the normal test window | Separate queue delay, receive delay and ACK scheduling to locate the backlog. |
| MSK normal traffic recorded 34 late TX reports; other runs had underruns or late TX | Improve submission deadlines and determine where host-side delays occur. |
| Simple 2FSK passed at 100/250/500 kbit/s, but latency exceeded B205 results | Preserve these working profiles and use them to isolate transport and decoder costs. |
| The newer schema-2 generic profile passed at roughly 66–67 ms p95 | Compare link policies explicitly; the older reliable-link schedule is not representative of every profile. |
| Retune p95 was about 24 ms versus about 30 ms on B205 | Preserve this improvement before qualifying faster hopping. |

Already implemented: shared device ownership, independent channel streamers,
one-time clock initialization, fixed UBX analog-bandwidth handling, paired CLI
launching, and a decoder fix that searches a full buffer before discarding a
maximum-sized packet's preamble. Those are foundations, not new work items.

## Step 1 — Measure where packet time is spent

**Priority: first.** Establish which delays actually need optimization.

- Add optional, low-overhead timing diagnostics for payload enqueue, packet
  selection, encoding/modulation, intended hardware TX time, first UHD submission,
  burst completion, received frame recognition, ACK handling and payload delivery.
- Record queue depth and oldest-packet age, TX samples per send call, send-call
  count, submission duration and remaining lead time at submission.
- Separate application rejection of an already-late target from UHD-reported late
  events, underruns and ordinary retransmissions.
- Keep host monotonic timing and hardware timestamps explicitly distinguished.
  Do not subtract timestamps from different clocks without a measured mapping.
- Make detailed tracing opt-in. Retain aggregate summaries rather than permanent
  per-packet logs; compare tracing enabled/disabled to check measurement overhead.

Primary files: `packet_radio/statistics.py`, `transmission.py`, `reception.py`,
`hardware/uhd.py`, `protocol/reliability.py`, and `tools/uhd_link_check.py`.

**Completion check:** explain the main contributors to BPSK backlog and capture
the circumstances around late TX/underrun events. Establish a reproducible short
workload before changing scheduling or buffering behavior.

### Step 1 implementation status

Offline implementation is in place: opt-in bounded timing summaries, separate
host/UHD late-event counters, decoder-output queue timing, reliable-link ACK
intervals, and CLI/benchmark switches. Metric definitions and clock domains are
documented in [UHD_SETUP.md](UHD_SETUP.md#optional-packet-timing-diagnostics).
No packet traces, IQ captures or permanent benchmark JSON are created by this
instrumentation.

Offline validation covers bounded concurrent aggregation, disabled clock reads,
bidirectional simulated delivery, retry/ACK intervals, partial UHD sends, separate
late-event sources, decoder clock domains and CLI propagation. The broader
hardware-fake/protocol/integration/decoder run passed 683 tests; its one outdated
launcher assertion was updated for the new optional argument and the affected
lifecycle/diagnostics group then passed all 15 tests. Six long tests matching
`thousand` were excluded from this development run. Ruff lint/format, mypy and
diff whitespace checks passed. No physical SDR was accessed.

**Hardware gate completed, 2026-10-15:** five short runs compared BPSK diagnostics
off/on twice and reproduced an MSK underrun/UHD late event. See the
[Step 1 results](../MODULATION_RESULTS.md#x310-step-1-timing-investigation--2026-10-15).
Backlog persists with zero BPSK stream errors; receive/ACK turnaround and TDD
waiting warrant follow-up. Small UHD chunks require 44–131 calls per burst,
supporting Step 2's batching experiment. Exact per-error causality is not yet
established, and no performance fix or new default is qualified.

Step 2's implementation and experimental outcome are recorded below. The X310
remains available; no repeat power-on request is needed while it stays connected.

For each subsequent step, implement and test what can be verified offline first,
then request hardware only for its measurement/acceptance gate. Hardware-dependent
claims and qualified defaults must wait for actual measurements.

## Step 2 — Reduce UHD transmit-call overhead

**Priority: high.** Test larger host-side submissions instead of one Python call
per transport-sized chunk.

- Verify the installed UHD send/fragmentation contract, then compare the current
  approach with bounded multi-packet submissions and whole-burst submissions.
- Preserve partial-send handling, deadlines, first-fragment timestamps, start/end
  of burst flags and asynchronous completion/error reporting.
- Preserve buffer ownership while UHD accesses samples. Reduce allocations or
  copies only where ownership remains safe across threads.
- Keep waveform samples and documented zero-valued idle unchanged. Transport
  batching must not silently change packet bytes, modulation or on-air timing.
- Keep one caller per TX streamer and independent RX draining on both channels.

Primary file: `packet_radio/hardware/uhd.py`.

**Completion check:** fewer Python send calls and lower measured submission cost,
without new underruns, late TX, partial-transfer bugs or sample changes. Verify
short and maximum-sized bursts plus partial-send/error paths. Recheck B205 behavior
when hardware is available. Reject batching changes that only move delays elsewhere.

### Step 2 outcome — initial acceptance failed; follow-up below

Implemented bounded multi-packet submissions with partial-send, ownership,
metadata, EOB, timeout and asynchronous error handling preserved. Seven short
hardware runs compared one packet, 16 packets and whole bursts, included three
bounded BPSK repeats, MSK and maximum-sized BPSK packets.

**Step 2 decision (historical): do not promote batching yet.** Although 16-packet batching roughly halved
submission duration and reduced normal bursts to 3–9 data calls, it did not
resolve backlog. The third repeat recorded 46 UHD late events and two retry
exhaustions. Runtime/B205 defaults remain one packet per send. The benchmark-only
`--shared-device --tx-batch-packets 16` option preserves the experiment for
Step 3. Offline validation passed 112 targeted tests plus lint/format/type checks.

See [Step 2 results](../MODULATION_RESULTS.md#x310-step-2-transmit-batching-experiment--2026-10-15).
The clean-stream acceptance gate and B205 hardware recheck remain pending;
revisit batching after deadline work rather than claim a latency improvement
from moving time into the completion wait.

**Step 3 follow-up:** three bounded BPSK repeats plus MSK, maximum payload and
recovery checks are now stream-clean with corrected target preparation and the
explicit X310 timing budgets. This clears the observed timing failure for those
workloads. At that checkpoint batching remained benchmark-only: BPSK backlog and B205
revalidation were unresolved, and faster submission alone was not a latency gain.
The QPSK follow-up below now provides an explicit host opt-in after repeated
stream-clean checks; the global one-packet default is preserved.

## Step 3 — Make timed transmission deadlines reliable

**Priority: high.** Use Step 1 measurements to budget time before a burst starts.

- Prepare waveform data before choosing a target time where the selected profile
  permits it. Preserve timestamp semantics for synchronization packets.
- Separate waveform preparation time, hardware submission lead and slot/guard
  requirements. Avoid repeatedly selecting targets that cannot still be met.
- Derive conservative lead-time requirements from measured submission jitter;
  expose validated settings where hardware-specific tuning is needed.
- Check the complete burst, transport idle and guard requirements against the
  remaining slot or dwell. Defer or reject an infeasible burst explicitly.
- Review shared control-lock hold time and unnecessary device-time reads while
  preserving serialized device control and the shared-clock protection.
- If increasing lead time prevents errors, measure the added packet latency too.
  Do not hide timing failures by making every transmission unnecessarily late.

Primary files: `packet_radio/transmission.py`, `hardware/uhd.py`,
`hardware/shared.py`, `config.py` and `scheduler.py`.

**Completion check:** zero late TX reports, underruns or shutdown errors across
normal, maximum-payload and recovery workloads, with valid slot/dwell boundaries
and an explicitly measured latency tradeoff.

### Step 3 outcome — scoped timing gate passed, 2026-10-15

Ordinary target selection now follows IQ preparation and stale-event draining,
with redundant clock reads removed. Stale errors are counted/drained as a group.
A configurable minimum remaining lead safely defers unsent packets, preserving
sync timestamp semantics. TDD admission now includes transport idle samples.

Ten short runs, including three bounded BPSK repeats, MSK, maximum payloads and
17-second mute/recovery checks, reported zero late events, underruns or shutdown
errors. Both one-packet and 16-packet submissions were checked. Reusable X310
diagnostic profiles use 10 ms data lead, 20 ms sync lead and a 2 ms minimum margin.
711 targeted tests plus lint/format/type/whitespace checks passed.

See [Step 3 results](../MODULATION_RESULTS.md#x310-step-3-transmission-deadlines--2026-10-15)
for the measured latency cost, delivery failures under BPSK overload and the exact
qualification scope. B205 hardware, other modulation hardware runs and higher-rate
hopping remain later checks. Global lead and submission defaults are unchanged.

**At the Step 3 checkpoint:** BPSK backlog remained despite clean stream timing.
Step 5 investigated that backlog; Step 4 receive/decoder work is now completed
below. No full-radio performance acceptance is claimed.

## Step 4 — Reduce receive and decoder delay

**Priority: high after transmission timing is stable.** Determine how much latency
comes from RX buffering, decoder work and waiting for a burst to end.

- Measure receive-block age, decoder queue age and time spent waiting for idle
  after the logical packet has arrived.
- Evaluate block sizes and queue limits against both latency and processing cost.
  Larger queues can conceal backlog; smaller queues can drop useful samples.
- Investigate earlier recognition of complete packets and robust handling of
  decaying signal tails, guided by captures and generated regression cases.
- Preserve bounded memory, hardware timestamps, maximum-size packet acquisition,
  and handling of noise, fragmented input and back-to-back bursts.
- Preserve the existing long-tail regression. Do not assume all modulation types
  have the same burst envelope or can use an identical energy threshold.

Primary files: `packet_radio/hardware/uhd.py`, `modems/decoder.py`,
`modems/base.py` and `reception.py`.

**Completion check:** lower end-to-end latency, not merely shorter decoder calls,
with exact delivery at all three simple rates and no new false packets, buffer
losses or maximum-payload failures.

### Step 4 outcome — receive-latency gate passed, 2026-10-15

Optional host IQ-queue and decoded-frame trailing-sample measurements identified
burst-end waiting as a useful optimization target. Average host IQ-queue delay
was below 0.24 ms in the matched baseline; buffered-tail p95 reached 15.7–23.2 ms.
2FSK/MSK/GMSK now schedule live searches using plausible packet lengths while
preserving payload validation, shaping delay, timestamps, bounded memory and
the long-tail search-before-trim protection. BPSK/QPSK live search behavior is
unchanged. No transmitted samples, portable profiles or link policies changed.
Queue/block sizes remain unchanged because measurements did not justify tuning.

All simple rates passed **156/156 each way**, including maximum-size packets,
repeated payloads, a one-second mute/recovery and clean close/reopen. Final p95
was approximately 45 ms at 100 kbit/s, 27 ms at 250 kbit/s and 23 ms at 500 kbit/s,
with zero measured stream errors/buffer losses. Matched short before/after runs
confirmed improvement at each rate; diagnostics-off 250 kbit/s remained exact
at approximately 27 ms p95. Reliable 2FSK/MSK/GMSK also passed 150/150 normal
and 10/10 maximum payloads each way with zero stream errors/retries or backlog.

**1,514 tests passed; six long thousand-packet tests were excluded.** Lint,
formatting, mypy and whitespace checks passed. Generated regressions cover
length-guided early recognition, long tails, maximum/following frames, timestamp
gaps, noise/corruption recovery and host/RF clock separation. See the
[Step 4 results](../MODULATION_RESULTS.md#x310-step-4-receive-and-decoder-latency--2026-10-15)
for matched workloads, CPU tradeoffs, timing definitions and scope. Temporary
benchmark files were removed; final summaries remain in the existing results file.

The subsequent QPSK investigation is recorded below. This receive-only pass
itself did not resolve TX underflows or qualify a global hardware/profile default.
B205 hardware and the complete Step 9 matrix remain later checks.

## Step 5 — Remove unnecessary reliable-link waiting

**Priority: high.** Optimize the protocol schedule after transport and receive
measurements are understood.

- Compare legacy bench scheduling with the working schema-2 generic profile under
  the same waveform, payload and offered load. Identify which policy differences
  account for delivery delay.
- Review ACK placement, retransmission timeouts, burst limits, TDD windows, guard
  times and queue admission. Change one factor at a time.
- Base ACK timing on the actual receive/decode/return path. Avoid both premature
  retries and long idle waits while useful packets are queued.
- Bound queue age and expose overload clearly. Do not increase queue capacity
  merely to hide an unsustainable offered rate.
- Preserve authentication, replay protection, duplicate suppression and the
  selected profile's behavior. ACK-free profiles must remain ACK-free.
- Make policy changes explicit in configuration/profile identity where applicable;
  do not silently alter a frozen radio-emulation contract to improve a benchmark.

Primary files: `packet_radio/protocol/generic.py`, `protocol/reliability.py`,
`transmission.py`, `config.py`, `profiles.py` and the affected profile definitions.

**Completion check:** all five modulations deliver 150/150 each way in the matched
normal workload without growing backlog. Target the B205's matching per-modulation
latency before claiming an improvement. At lower load, deliver all ten maximum-size
packets each way. Treat the heavier 25-packet workload as a measured capacity limit
unless the revised implementation actually sustains it.

### Step 5 outcome — delivery/backlog gate passed; QPSK follow-up below

Implemented explicit unslotted generic-v5 profiles for all five modulations,
paired X310 node examples, optional pending-data age limits and always-visible
queue age/expiry status. Existing TDD and frozen profiles retain their behavior;
ACK-free profiles remain ACK-free. Removing TDD slot waiting alone eliminated
BPSK backlog in the matched short experiment. Increasing the ACK timeout alone
did not improve latency; the new profiles explicitly use the working schema-2
600 ms policy.

All five delivered 150/150 each way at normal load, 10/10 maximum payloads at
low load and 25/25 in the short heavier check. Final queues were empty and no
data expired. Normal p95 was approximately 66–84 ms. QPSK recorded five TX
underflows across three runs, despite exact delivery; its normal repeat was
clean. At this checkpoint the failures were unresolved, so the new profiles were `unqualified`
and are not promoted as runtime defaults. Four other modulations were stream-clean
through all three workloads. This is an explicit policy improvement, not proof
that X310 hardware outperforms B205 under an identical radio contract.

725 targeted hardware-fake, protocol, decoder and integration tests passed;
six long tests matching `thousand` were excluded. Ruff lint/format, mypy and
whitespace checks passed. Final per-modulation summaries are in the
[Step 5 results](../MODULATION_RESULTS.md#x310-step-5-reliable-link-waiting--2026-10-15).
Temporary reports were removed. Step 4 is now complete below its own heading;
the scoped QPSK submission follow-up below addresses the observed underflows. No independent-host,
over-the-air, B205 revalidation or full Step 9 qualification is claimed.

### QPSK follow-up — scoped underflow mitigation validated, 2026-10-15

Reproduced four TX underflows in the low-load maximum-payload baseline and two
in the heavy baseline, despite exact delivery. Submission stalls exceeded the
available lead on an affected attempt. Sixteen-packet host submissions then
passed the pilot and three repeated normal/maximum/heavy matrices with zero
underflows, late TX or other stream errors. The largest normal burst now needs
five host sends instead of 66; maximum payload needs 13 instead of 201.

Added startup host setting `hardware_tx_batch_packets` (1..256, default 1),
with explicit 16-packet paired X310 QPSK node files. It does not change waveform,
framing, profile identity, lead budgets or ACK/TDD policy. Existing node files
and B205 defaults remain at one. Diagnostics-off normal traffic also passed;
a 17-second interruption recovered both directions within 1.090 seconds with
one retry and no stream errors. This validates the observed underflow mitigation
for the tested setup, not arbitrary host load, rates or profiles.

1,528 regression tests passed; six long thousand-packet tests were excluded.
Lint, formatting, mypy and whitespace checks passed. Temporary reports were
removed after retaining final summaries.

See the [QPSK results](../MODULATION_RESULTS.md#x310-qpsk-underflow-investigation--2026-10-15)
for preserved baseline failures, all repeated measurements and validation.
The portable profiles remain unqualified pending broader acceptance. Step 6
remains conditional: investigate further contention only where measurements
justify it; the full Step 9/B205 comparison is still outstanding.

## Step 6 — Improve shared-channel execution only where measurements justify it

**Priority: conditional.** Investigate contention if Steps 2–5 leave unexplained
submission jitter or processing delays.

- Measure application, decoder, RX worker and control-lock contention separately.
- Reduce duplicated work and unnecessary serialization while keeping one device
  owner and one owner for each streamer.
- Consider separating CPU-heavy processing into worker processes only if profiling
  demonstrates a benefit. Include IPC, copying, timestamp preservation and shutdown
  behavior in that comparison.
- Do not open competing independent `MultiUSRP` instances for the same board.
- Keep paired startup, peer-failure propagation and coordinated shutdown tested.

Primary files: `packet_radio/hardware/shared.py`, `hardware/uhd.py`,
`modems/decoder.py` and `app.py`.

**Completion check:** demonstrated reduction in jitter or CPU cost without
additional queue delay, lost samples, clock resets or resource leaks. Retain the
simpler execution model if additional processes do not improve measured results.

### Step 6 outcome — CPU-cost gate passed, 2026-10-15

Measured application, decoder feed/status, RX worker and explicit control/state
lock costs independently with optional wall/thread-CPU and acquisition/hold
summaries. Repeated sorting of the decoder's 2,048-entry timing window dominated
its status-read CPU; steady device-control lock acquisition was small on average.
The decoder now maintains exact rolling order statistics incrementally under
the existing lock. No status staleness, waveform, queue-size, device-owner,
streamer-owner, clock or execution-process changes were introduced.

Matched BPSK, 2FSK and QPSK normal workloads passed 150/150 each way with zero
stream errors, no expiry or backlog and clean close/reopen. Two optimized rounds
across all three, plus a third BPSK confirmation, reduced whole-process CPU
approximately 8–13%. A diagnostics-off BPSK comparison reduced CPU 11.8% while
preserving exact delivery and comparable latency. QPSK maximum payload and a
17-second interruption also passed, recovering both directions within 1.102 s
with one retry and no stream errors. Queue residence did not show sustained
growth; individual sub-millisecond output-queue measurements varied, so no
universal jitter improvement is claimed.

1,532 regression tests passed, including ownership, paired peer failure,
shutdown, exact rolling-window eviction/duplicates and diagnostic clock/lock
semantics; six long thousand-packet tests were excluded. Lint, formatting,
mypy and whitespace checks passed. Final measurements and limitations are in
[Step 6 results](../MODULATION_RESULTS.md#x310-step-6-shared-channel-execution--2026-10-15).
Temporary logs and reports were removed.

**Decision:** retain the simpler shared-device/thread model. The measured CPU
saving does not require processes or IPC. Step 7 faster hopping is next;
existing hopping limits remain unchanged pending that separate qualification.
B205 physical validation and the full Step 9 comparison remain outstanding.

## Step 7 — Qualify faster hopping

**Priority: after fixed-frequency timing passes.** Build on the measured retune
improvement without treating a 24 ms p95 as a guaranteed maximum.

- Measure tune, stream restart, first valid RX samples and first successful packet
  separately on both channels. Include worst observed values and variation.
- Evaluate hardware-timed tuning or coordinated channel operations only after
  checking support and behavior on the installed UHD/UBX combination.
- Choose candidate dwell/guard values that accommodate complete packet airtime,
  measured transition time and margin. Test several hop frequencies, not one pair.
- Keep the current qualified 1 hop/s setting until higher rates pass boundary,
  late-burst rejection and interruption-recovery checks.
- Update validated configuration limits and profile documentation together when
  a higher hop rate is actually qualified.

Primary files: `packet_radio/scheduler.py`, `app.py`, `hardware/uhd.py`,
`config.py`, `profiles.py` and `tools/hopping_check.py`.

**Completion check:** exact delivery at each newly supported rate, zero missed
steady hops/deadlines, no transmission across forbidden boundaries, and successful
recovery. Do not infer a supported hop rate from retune latency alone.

### Step 7 outcome — completed, 2026-10-15

Three independently opened 60-second X310/UBX runs passed at **2 hops/s**, each
with 120/120 steady and 135/135 total exact packets per direction. Every run
rejected 12 deliberately late maximum packets per channel, recorded zero stream
errors and missed steady hops/deadlines, recovered in the first dwell after the
intentional off-channel pause, and closed cleanly. A 60-second original-rate
baseline and final short 1 hop/s check also passed.

The separate `simple-2fsk-x310-hopping-v1` profile uses 500 ms dwells, unchanged
100 ms guards and 10 ms timed TX lead. The original profile retains 1 hop/s.
Schema-2 validation permits the new dwell floor only with explicit X300-series
selection above 1 hop/s; qualification remains scoped to this shared X310/UBX,
same-host, three-frequency 2FSK workload. Other boards, rates, tables, modulation
types and independent hosts need their own qualification.

The benchmark separates stop/drain, tune/readback calls, restart, first buffered
RX, first decoder-usable samples and packet delivery, and accounts for measured
host/device mapping uncertainty in whole-burst boundary checks. Across the fast
runs, the worst total tune was 24.178 ms and latest decoder readiness was 128.061 ms
within a dwell, before the first 200 ms injection. Timing is application/device
metadata evidence, not an independent RF-boundary or PLL-lock measurement.

UHD timed-command API availability and documented X3x0/UBX support were checked.
The qualified path retains serialized stop/tune/restart; timed retunes and reduced
guards were not enabled or qualified. Final measurements, reproduction commands
and limitations are in [Step 7 results](../MODULATION_RESULTS.md#x310-step-7-faster-hopping--2026-10-15).

Software validation passed 1,540 tests (six long thousand-packet cases excluded),
lint/format, mypy, paired configuration validation and whitespace checks. New
regressions cover fast-hop admission, retune settling, skipped-slot recovery,
profile/device limits and invalid benchmark arguments. Temporary logs and scripts
were removed after recording the final summaries.

**Decision:** qualify the separate 2 hop/s profile and preserve the original
1 hop/s defaults. Step 8 is a conditional transport assessment; the unchanged
socket-buffer warning alone is not evidence that kernel/NIC changes are needed.
The full Step 9 matrix and B205 physical revalidation remain outstanding.

## Step 8 — Tune the host transport if it remains a measured constraint

**Priority: conditional; not a prerequisite for Steps 1–5.**

- Investigate the observed UDP socket-buffer warning and record actual requested
  versus granted sizes. Make host tuning explicit, documented and reversible.
- Compare buffer sizes using packet latency, CPU and stream errors, not throughput
  alone. Large buffers can increase latency.
- Investigate NIC settings, scheduling or CPU affinity only when diagnostics point
  to a specific host bottleneck; record any environmental changes with results.
- Consider 10 Gb Ethernet when a measured target sample rate or channel count
  exceeds the demonstrated capacity of the current connection. Requalify the
  required NIC, FPGA image, link and host settings as a separate change.

Primary locations: `docs/UHD_SETUP.md` and the hardware benchmark tools.

**Completion check:** reproducible improvement under the affected workload.
The existing clean two-channel 2 Msps transport run is not evidence that a
10 Gb upgrade or kernel tuning will fix reliable-link scheduling problems.

### Step 8 outcome — assessment complete; no tuning adopted, 2026-10-15

Measured UHD's 2,453,333-byte TX request against the existing 1,048,576-byte grant,
then compared temporary 256 KiB and 512 KiB per-device requests using QPSK normal
and maximum-payload traffic, CPU, latency, socket capacities and host counters.
The 256 KiB pilot was below UHD's reported minimum recommendation and was not
pursued. Two normal 512 KiB runs showed no consistent latency/CPU improvement.
The first two maximum-payload candidate runs looked faster; the third reported
one TX underflow. A baseline recheck also reported one underflow, followed by a
clean baseline repeat. All 12 application runs delivered every payload, but two
failed the zero-stream-error gate. Complete delivery is not full acceptance.

Both underflow attempts lasted roughly 20 ms despite about 9.4 ms initial TX lead.
This warrants a burst-submission timing investigation; buffer size is not an
established cause or fix. No changed configuration passed an adoption gate, so
runtime code, node profiles and host settings remain unchanged. Increasing the
kernel cap requires administrator authentication unavailable in this session;
that larger-buffer comparison was not performed or claimed as qualified.

Two ten-second continuous two-channel RX/TX tests at 2 Msps passed with zero UHD
errors. Six NIC RX-length errors occurred only during default frame discovery;
capping its probes at the already-used 1472-byte frame size eliminated that
startup increment. UDP buffer-error counters remained unchanged. These results
do not justify a NIC/MTU, scheduling-affinity or 10 Gb change. The capped discovery
setting was diagnostic only and was not adopted.

The setup guide now documents requested/granted sizes, Linux socket accounting,
read-only inspection, explicit per-device experiments and rollback. Final timing
and CPU tables, both failed runs, diagnostic evidence and limitations are in
[Step 8 results](../MODULATION_RESULTS.md#x310-step-8-host-transport--2026-10-15).
Temporary reports, scripts and traces were removed; only final summaries remain.
No runtime code changed, so the Step 7 regression result remains the latest
software baseline. Documentation links and whitespace were checked.

**Step 8 follow-up — burst timing, 2026-10-15:** completed the investigation
before final reference-radio acceptance. Controlled 20 ms pauses reproduced two
starvation gaps: returning to Python before a separate EOB, and between the two
16-packet submissions needed even for a QPSK ACK. EOB on final IQ closed the
first gap but failed a natural heavy run (one underflow and one late event on B).
The adopted fix combines final-IQ EOB with a 256-packet cap in the paired X310
QPSK nodes, fitting all qualified QPSK frames in one Python-to-UHD call. Other
node batches and host/network settings remain unchanged.

Three normal and three heavy QPSK repeats, maximum-payload recovery, a
diagnostics-off heavy run and short BPSK/2FSK/MSK/GMSK checks passed exact delivery
and zero stream-error gates. Simple framing and 2 hops/s compatibility also
passed. The reproduced gaps explain a failure mechanism, not the OS-level cause
of every historical delay. Physical B205 revalidation and the full Step 9 matrix
remain outstanding. Final evidence, including the failed intermediate fix, is in
[the burst timing results](../MODULATION_RESULTS.md#x310-burst-submission-timing-follow-up--2026-10-15).

Software verification passed 1,543 tests (six long thousand-packet cases
deselected), Ruff lint/format checks and mypy. Temporary diagnostic artifacts
were removed after recording the final summaries.

**At this checkpoint:** Step 9 was the next action; its outcome is recorded below.
No transport tuning or final X310 acceptance was claimed by this follow-up.

## Step 9 — Requalify and decide whether to adopt the X310 as the reference

Use short checks while developing. Run the complete comparison after the changes
are stable, with RF workloads sequential and without the software suite competing
for CPU. Preserve failures and identify results produced under changed settings.

| Check | Final comparison workload and gate |
|---|---|
| Normal reliable traffic | All five modulations; 30 s, 256-byte payloads, 5 packets/s/direction, 5 s drain; 150/150 exact each way, zero stream/timing errors. |
| Echo | All five modulations; 10 s, 50 packets; all echoes returned, p95 ≤1 s and compared with the matching B205 result. |
| Maximum payload | Repeat both existing 1,024-byte workloads; report backlog and delivery by deadline separately from RF loss. Lower-load delivery must be complete. |
| Signal recovery | Repeat the 17 s mute and fresh-packet check; include errors during recovery and shutdown. Diagnose pre-existing backlog separately. |
| Simple radio | 100/250/500 kbit/s; both directions, 150 steady packets plus boundary/repeat/recovery cases; 156/156 exact each way and zero stream errors. |
| Hopping | Existing 60 s, 1 hop/s comparison, then any separately proposed higher rates; preserve boundary rejection and recovery checks. |
| UHD transport | Both channels simultaneously RX/TX at 2 Msps; 10 s with zero transport errors. Repeat at higher rates only when those rates are being qualified. |
| Software and lifecycle | Relevant regression tests during development; full suite, lint, formatting and type checks at completion; paired CLI traffic and clean shutdown. |

For configurations proposed as the new default, repeat the key affected workloads
at least three times to check variability. No mandatory 30-minute run is required
for this improvement pass. Longer testing should address a specific unresolved
stability question.

Keep the historical B205 and X310 results. If a change affects both backends,
remeasure the B205 on the same revised code when available before attributing an
improvement to hardware. Compare matching packet formats, scheduling policies,
bit rates, offered loads and measurement windows. Report shared-process CPU once.

Write final per-modulation/profile summaries only to `MODULATION_RESULTS.md`;
remove temporary traces, IQ and JSON after analysis. Update the setup guide and
examples with qualified settings. Preserve the current release archives unless
a new release is separately requested.

**Adoption decision:** describe which workloads improved, stayed equivalent or
regressed. Removing timing failures and reliably matching the current reference
is the first target. Claim that X310 is better only for workloads supported by
repeatable measurements. Sharing one host and oscillator does not establish
performance between separate radios or interoperability with a target radio.


### Step 9 outcome — qualification run; full adoption withheld

The final hardware matrix was executed on the same-host conducted X310 setup.
Each modulation passed three 30-second normal-load repeats (150/150 each way
per repeat), low-load maximum payload with 17-second mute/recovery, the original
25-packet/five-second maximum-payload workload and an additional intermediate
load. All 39 generic runs delivered exact, unique payloads with zero stream errors
through shutdown. Normal/payload-load cases had no backlog, expiry or retries.

**Unresolved acceptance failure:** BPSK echo p95 was 1.378/2.017/2.287 seconds;
QPSK was 2.357/5.180/3.114 seconds, all above the one-second gate. Every attempt
returned 50/50 echoes, but retries occurred in all six, and one QPSK repeat expired
nine pending reliability records. 2FSK/MSK/GMSK echo passed at 0.132/0.140/0.121
seconds. Clean stream counters do not waive the echo/reliability failure.

Simple 2FSK at 100/250/500 kbit/s passed 156/156 exact in both directions,
including boundary/repeated payloads, 17-second interruption recovery and
close/reopen. Sixty-second hopping checks passed at both 1 and 2 hops/s,
including boundaries and recovery. Continuous two-channel RX/TX at 2 Msps
passed ten seconds with all transport error counters zero.

The paired CLI passed signed QPSK and simple 2FSK traffic with clean shutdown.
A stale processing-metadata comparison in the UDP probe was corrected and tested
against actual application snapshots; the radio runtime was unchanged.

Final comparisons, limitations and lifecycle/software verification are recorded
in [Step 9 results](../MODULATION_RESULTS.md#x310-step-9-qualification-closeout--2026-10-15).
The X310 remains usable for the scoped passing workloads; it is not promoted as
the fully accepted reference across all five modes. The B205s were unavailable,
so same-revision B205 comparison remains deferred. Independent hosts and actual
target-radio interoperability are also unqualified. Existing release archives,
profile qualification labels and host/network settings remain unchanged.

The full software suite passed **1,552 tests** with no long-test exclusions;
Ruff lint/format, mypy and whitespace checks passed. Temporary qualification
artifacts were removed after retaining final summaries.

**Next action at that checkpoint:** investigate BPSK/QPSK reflected-traffic
DATA/ACK recognition and retry/expiry timing, then rerun the failed echo gates with at least three repeats
per affected mode and relevant normal-load regressions. Keep the one-second
threshold and failed results. Do not close full acceptance by raising timeouts,
discarding failed runs or changing the offered workload without justification.

### Step 9 follow-up — PSK echo blocker resolved

The PSK receivers could consume a later stronger frame before an earlier valid
ACK/DATA in the same buffer. Arrival-ordered frame groups now preserve that
earlier frame while ranking timing hypotheses within each group. BPSK also
searches from the decoded length, with shaping-tail and live timing lookahead,
instead of waiting for the RF energy tail. No waveform, packet format, timeout,
load, gain or host batching settings changed.

Three echo repeats per mode returned 50/50 each: BPSK p95 125–129 ms and QPSK
137–140 ms, all below the unchanged one-second gate. Three normal repeats per
mode returned 150/150 each direction, followed by passing lower/heavy maximum
payload and 17-second mute/recovery checks. All 16 final runs had zero retries,
invalid packets, queue expiry/backlog and stream errors, including clean shutdown.
BPSK normal shared CPU increased to approximately 30 seconds per 35-second
workload/drain window; this is a documented latency/correctness tradeoff.

The unchanged FSK/simple/hopping/transport/paired-CLI evidence above, together
with these PSK reruns, closes the scoped conducted matrix. It does not qualify
independent hosts, arbitrary emulation profiles or actual target-radio behavior.
Same-revision B205 comparison remains deferred until those radios are available.
Existing qualification labels, release archives and host/network settings remain
unchanged. Final per-mode measurements and intermediate failures are in the
[PSK follow-up results](../MODULATION_RESULTS.md#x310-psk-echo-acceptance-follow-up--2026-10-15).

Final software verification passed **1,600 tests**, including 48 new PSK cases,
with no exclusions. Ruff lint/format, mypy and whitespace checks passed. Temporary
IQ, traces and benchmark reports were removed after retaining final summaries.

### Revised-code B205 comparison — remaining hardware comparison executed

The same revised radio code and matching unslotted profiles were measured on
both B205minis. All five modes delivered every packet in three normal repeats,
and steady 2FSK/MSK/GMSK traffic had competitive latency without retries or
invalid frames. BPSK normal/payload traffic needed retries; QPSK failed two of
three echo latency checks and both maximum-payload delivery/stream checks.
Reliable-link mute recovery failed for MSK, GMSK and QPSK. These failures remain
visible in the final summaries; complete steady delivery does not waive them.

Retain the X310 as the reference for the already-qualified conducted envelope.
This compares complete setups: B205s use independent clocks, separate USB
transports/processes and their own gain settings. It does not establish universal
hardware superiority or target-radio interoperability. The next B205 work is to
investigate PSK candidate rejection/overflow and reliable-link recovery before
claiming equivalent acceptance. See the
[B205 comparison](../MODULATION_RESULTS.md#b205mini-revised-code-comparison--2026-10-15)
for all workload, startup and recovery results.

All six B205 simple-radio direction/rate checks passed 156/156 exact, including
recovery. One-hop/s hopping and simultaneous two-device continuous RX/TX at
2 Msps also passed. Retune p95 was about 30 ms versus about 23 ms on X310.
This completes the deferred hardware comparison, with B205 failures retained;
no radio code or release artifacts were changed for this measurement pass.
