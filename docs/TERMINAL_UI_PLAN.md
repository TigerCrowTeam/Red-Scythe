# Configurable radio terminal interface plan

**Status:** Steps 1–9 complete for the documented X310 scope. Updated 2026-10-20.

Build a keyboard-driven terminal user interface (TUI) for creating, editing,
validating, saving and running radio configurations. A user should not need to
edit YAML or Python to configure a supported radio. Selecting a modulation must
show **only parameters relevant to that modulation**, further filtered by packet
format, frequency mode, operating mode and hardware.

This is the next product-development plan. Keep the completed 1.1 and X310 plans
as history; this document does not change the package version or declare a release.
Final RF measurements continue to belong only in `MODULATION_RESULTS.md`.

## First usable product

The user can launch a proposed `packet-radio-ui` command, open an existing project
or create one, choose a modulation, set its applicable options, save a configuration,
start/stop the radio and watch live status. Offline editing works without UHD or
an SDR installed. Initial deployment target is the existing Linux workstation,
including keyboard operation over SSH. Existing headless commands stay available.

Support one local radio node, or the existing paired-channel X310 launcher. A
second physical host can receive an exported peer configuration and run its own
instance. Remote start/stop and a fleet manager are later work. B205 selection
remains supported, with its measured limitations clearly distinguished from the
X310 reference results; fixing those radio limitations is not a prerequisite for
building the editor.

Initial RF/configuration changes use **stop → edit/validate → start**. Saving
an edit never changes a running radio. Show saved, draft and running settings
separately. Hot modulation changes, live retuning and live changes to packet
framing require a separate runtime design and qualification effort.

## Existing code to build on

| Existing component | Use in the TUI |
|---|---|
| `packet_radio/config.py` | Configuration types, validation, peer compatibility and effective snapshots. |
| `packet_radio/profiles.py` | Schema-2 definitions, overrides, path resolution, fingerprints and qualification provenance. |
| `packet_radio/protocol/simple.py` | Supported custom framing and its validation. |
| `packet_radio/protocol/registry.py` | Resolve the selected processing pipeline and its description. |
| `packet_radio/cli/validate.py` | Existing software/hardware validation behavior and airtime reporting. |
| `packet_radio/__main__.py`, `app.py` | Headless startup, authentication requirements and shutdown behavior. |
| `packet_radio/hardware/shared.py` | One owner for two channels on a shared X310. |
| `packet_radio/status.py`, `statistics.py` | Live snapshots, health and counters; the HTTP service is currently read-only. |
| `configs/profiles/`, `configs/v1_1/` | Starting definitions and separate node/hardware examples. |

The interactive editor and offline draft service are implemented. File saving and
live control remain later steps. Editable source documents stay separate from
normalized configuration snapshots; those snapshots are not lossless editable YAML.

## Interface and parameter visibility

New project flow: **modulation → compatible packet format/preset → hardware/node
settings → applicable parameter sections → validate/save → run**. Opening a saved
project populates those selections from the file. Offer simple 2FSK as the starting
option for a customizable ACK-free radio; generic-v5 remains the available reliable
format for all five modulations.

Use a navigation list for Project, Radio, Frequency, Framing, Link, Hardware,
Local I/O, Advanced and Run/Status. Hide sections with no applicable editable
settings. Each field has a label, units, help, default, supported range/choices,
source (preset or override) and validation message. Advanced reveals additional
**applicable** controls; it must not reveal irrelevant controls from other modes.

### Modulation-specific controls

Bit rate, sample rate, RF bandwidth and the applicable frequency selection are
common controls. Display symbol rate, samples/symbol and maximum packet airtime
as derived values, never as contradictory independent inputs.

| Selected modulation | Additional editable waveform controls | Derived information / controls not shown |
|---|---|---|
| BPSK | Rectangular/RRC pulse shaping; RRC rolloff and span only with RRC selected; supported bit repetition under protection. | One bit/symbol. Hide deviation and Gaussian controls. |
| 2FSK | Frequency deviation, labeled as distance from center to either tone. | One bit/symbol. Hide RRC, Gaussian and repetition controls; current repetition is fixed at one. |
| MSK | No independent deviation or shaping choice. | One bit/symbol; deviation = bit rate / 4, read-only. Hide 2FSK deviation input, Gaussian and RRC controls. |
| GMSK | Gaussian BT and span. | One bit/symbol; deviation = bit rate / 4, read-only. Hide independent deviation, RRC and repetition controls. |
| QPSK | Rectangular/RRC pulse shaping; rolloff and span only for RRC. | Two bits/symbol. Hide deviation, Gaussian and repetition controls; repetition is fixed at one. |

Use the existing supported ranges: for example, GMSK currently accepts BT 0.3/0.5
and spans 4/6. Do not introduce arbitrary ranges or duplicate the modem's validation
logic in widgets. Keep QPSK available without making it the default modulation.

### Other dependencies that also filter the form

- **Packet format:** generic-v5 framing is fixed and uses reliable bidirectional
  payload operation. Show its format summary and supported payload/protection/link
  settings, not editable preamble/CRC/header fields. Simple-frame-v1 currently
  supports 2FSK and allows custom framing, ACK-free operation and payload/frame
  input. Do not offer simple BPSK/MSK/GMSK/QPSK until the backend supports them.
- **Frequency:** fixed mode shows center frequency. Hopping shows an ordered hop
  table, add/remove/reorder controls, hops/second, derived dwell, guards, sequence
  and UTC epoch/synchronization. Current schema-2 hopping requires simple 2FSK.
  Enforce the backend's dwell/guard constraints and distinguish measured 1-hop/s
  B205 and 1/2-hop/s X310 cases from merely valid configurations.
- **Framing:** fixed-length packets show exact payload size; variable-length
  packets show length-field size/order and maximum payload. CRC byte order appears
  only for a CRC with bytes. Show preamble hex/repetitions, sync hex and bit order
  only in custom framing. Unsupported whitening/differential transforms remain
  unavailable with a concise capability explanation, not nonfunctional toggles.
- **Protection:** show interleaving and Reed–Solomon controls only where supported
  by the selected FEC scheme. Keep canonical fixed values out of editable forms.
- **Link/operation:** ACK timeout/retries and reliable synchronization belong to
  generic-v5; gaps/interval/turnaround belong to the simple link. Show TX-specific
  controls only for transmitting operation and RX-specific controls only for
  receiving operation. Preserve backend-required defaults for inactive paths.
- **Hardware:** show B205 serial selection or X310 address/channel selection as
  applicable. Antenna/gain choices come from the device when probed, with manual
  offline entry available. Display requested versus actual/coerced settings.
- **Advanced:** applicable queue limits, timed submission, batching, decoder
  buffers, status, logging, recording and authentication settings. Key paths and
  provisioning use existing mechanisms; private key contents are never form data.

Changing modulation must immediately rebuild the applicable form, recompute
rates/airtime and validate the draft. Preserve compatible common values. Keep
optional per-modulation drafts in memory so switching back can recover edits,
but never serialize stale Gaussian/deviation/framing overrides into another mode.
Changing packet format requires selecting/cloning a compatible definition rather
than an invalid structural override. Explain incompatible settings being replaced
and allow cancel before discarding unsaved work. Imported invalid fields must be
reported rather than silently dropped. Required canonical defaults may still be
written by the existing normalizer even when not editable in the UI.

## Architecture choices

Use **Textual** as an optional UI dependency, with a proposed installation extra
`packet-radio[ui]` and separate `packet-radio-ui` entry point. Its
[headless testing support](https://textual.textualize.io/guide/testing/) fits
keyboard/navigation tests, and its [worker model](https://textual.textualize.io/guide/workers/)
can keep blocking discovery and status reads off the interface event loop.
Step 1 adds the optional `ui` extra with `textual>=8.2.8,<8.3`, verified on
Python 3.10 and the workstation's UHD-enabled Python 3.14. Textual remains
optional for headless operation. Step 3 implements the interactive application and
the `packet-radio-ui` command.

Keep radio processing outside the TUI process. A local controller launches the
existing module with the same interpreter and an argument list, without a shell.
A paired X310 uses one child with `--peer-config`, retaining shared device ownership.
The UI reads bounded status/log updates, not IQ or a copy of the DSP pipeline.
A device probe must not contend with a running owner. No automatic discovery at
import time and no radio startup simply from opening or saving the editor.

Suggested organization, created only as implementation needs it:

```text
packet_radio/
    configuration/       New document editing, field metadata and save services
    tui/
        app.py           Application shell and navigation
        screens/         Project, configuration and run/status views
        widgets/         Reusable unit, hex and hop-table editors
        controller.py    Owned child lifecycle and bounded status/log collection
    cli/ui.py            Optional dependency check and entry point
tests/
    configuration/       Document/validation/serialization behavior
    tui/                 Headless interaction and controller tests
```

Keep `config.py` and `profiles.py` public behavior stable. Extract shared helpers
only where the editor needs them; avoid a broad module move or a second set of
radio rules. The field catalog holds presentation/dependency metadata; core
validators remain authoritative.

## Implementation steps and completion checks

### Step 1 — Define the parameter catalog and dependencies

Inventory every schema-2 user setting against the actual parser, not just the
small example files. Map each to a form section, unit, editor, applicability rule,
validation path, persistence location and restart requirement. Mark each supported
field editable, derived/read-only, or deliberately deferred with a reason; no
supported parameter should disappear accidentally. Add profile/format/modulation
compatibility cases and the visibility table above as executable expectations.
Verify the Textual dependency/interpreter compatibility and add the optional extra.

**Complete when:** the catalog accounts for all user settings and offline tests
prove each modulation exposes only its relevant controls, including Advanced.
**Hardware:** not needed.

**Completed 2026-10-20:**

- [`configuration/catalog.py`](../packet_radio/configuration/catalog.py) inventories
  123 persisted fields and presentation controls, including explicit read-only
  and deferred entries. Each entry identifies its section, editor, units, help,
  applicability, validation path, persistence and restart behavior.
- Visibility filters cover all five modulations, RRC selection, packet format,
  fixed/hopping frequency, framing, FEC, TX/RX operation, hardware and dependent
  runtime settings. Advanced never exposes inapplicable fields. Whitening and
  differential encoding remain explicitly deferred because their configurable
  transforms are not implemented.
- Parser-owned field inventories in `profiles.py` let tests detect missing
  catalog entries without changing configuration acceptance. Default and constraint
  hints describe the form; existing validators remain authoritative. Draft loading,
  effective defaults and derived calculations belong to Step 2.
- The optional `ui` dependency uses
  [Textual 8.2.8](https://pypi.org/project/textual/8.2.8/). CI now checks this
  extra on Python 3.10 and 3.14, including a headless keyboard/focus smoke test.
- Verification: 181 configuration, profile and CLI tests passed, with the optional
  Textual test skipped in the base environment. All 40 catalog/dependency tests
  passed separately on Python 3.10.21 and Python 3.14.4 with Textual installed.
  The latter interpreter also imported UHD 4.9.0.1. Ruff, formatting, mypy and
  the wheel build passed; wheel metadata confirms Textual is optional and the
  catalog is packaged. No SDR was opened or used.

This step delivered the tested catalog and dependency foundation. Editable drafts
are implemented in Step 2 below; interactive screens are implemented in Step 3.

### Step 2 — Add a reusable draft and validation service

Separate editable source documents from resolved `RadioConfig`. Load a schema-2
node plus its referenced profile, track changes and run existing validators on
in-memory drafts with the correct file-relative base path. Add structured field
errors where needed without changing CLI acceptance rules. Derive rate, deviation,
airtime and profile fingerprint using existing calculations. Validate imported
peer configurations with the same network rules used by the CLI.

Every numeric input must reject nonfinite values and enforce integer requirements
where applicable. Expose inclusive/exclusive minimums, maximums, allowed discrete
values and step constraints as structured field limits. The catalog already
contains fixed schema bounds; resolve dependent bounds from the current draft
and device capabilities, not arbitrary universal RF limits. Check sample rate
against bit/symbol rate, modulation bandwidth/deviation, payload airtime against
dwell/TDD windows, and hop rate against device support. Revalidate dependencies
after every relevant edit. Keep core validators authoritative.

Frequency, sample rate, bandwidth, gain and channel limits must use the selected
UHD device/channel capabilities in Step 5. Offline editing must clearly identify
unverified hardware limits; a missing bound never means unlimited hardware.
Valid settings alone cannot guarantee RF delivery or host throughput.

**Complete when:** valid/invalid drafts produce the same decisions as headless
validation, including nonfinite numbers, fractional samples/symbol, duplicate hop
frequencies, incompatible formats and maximum bursts that do not fit a dwell.
**Hardware:** not needed.

**Completed 2026-10-20:**

- [`configuration/draft.py`](../packet_radio/configuration/draft.py) provides
  `RadioDraft.load`, isolated source copies, explicit set/delete operations,
  dirty tracking, reset and atomic profile selection. Selecting another profile
  clears old overrides. Incomplete values can remain in a draft for correction.
- Validation resolves an in-memory profile through `profiles.load_v2`, then runs
  the same `validate_config` checks as file-based startup. Relative key/recording
  paths retain the node file's base directory. Validation does not reread source
  files, write files, load keys, open sockets or access UHD.
- Reports contain the resolved configuration, effective defaults, rate/timing
  calculations, maximum encoded packet airtime and profile fingerprint. Profile
  edits resolve as unqualified; source defaults and provenance are not overwritten.
  Failed validation returns no usable configuration or derived results. Known
  error messages map to field paths; ambiguous errors remain form-level with the
  original message. The authoritative validator reports its first failure.
- `limits` exposes fixed bounds and dependent software bounds for rates, bandwidth,
  deviation and hop rate, including modem sample-ratio and hop-airtime constraints.
  Invalid drafts fall back to static hints; a range alone never establishes
  validity. `validate_pair` applies the existing peer compatibility rules.
  Every report explicitly leaves hardware verification false.
- Verification: 232 focused catalog, draft, profile, configuration and CLI tests
  passed; the optional Textual smoke test was skipped in the base environment.
  Tests cover all five modulations, CLI acceptance/rejection parity, boundary and
  nonfinite inputs, fractional sample ratios, duplicate hops, oversize bursts,
  relative paths, peer mismatches, source isolation and provenance. Ruff and mypy
  passed. No SDR was used.

Example service usage (no interactive screens or file saving yet):

```python
from packet_radio.configuration.draft import RadioDraft

draft = RadioDraft.load("configs/v1_1/peer-a-simple.yaml")
draft.set("node.overrides.frequency.center_frequency_hz", 916_000_000)
report = draft.validate()
if report.valid:
    print(report.derived)
else:
    for issue in report.issues:
        print(issue.fields, issue.message)
```

Step 3 builds keyboard navigation and conditional forms on these services.

### Step 3 — Build the terminal shell and conditional forms

Implement project selection, keyboard navigation, help, units and the field
catalog renderer. Provide MHz/kHz, kbit/s, Msps and ms inputs that convert
unambiguously to canonical units. Add preamble/sync hex editors and an ordered
hop-table editor. Make validation errors visible beside fields and in a summary;
focus the relevant field when selected. Use explicit presets rather than silently
inventing settings when a required modulation-specific value is missing.

Display applicable minimum/maximum values and units beside numeric controls.
Allow temporary incomplete typing, but show actionable field errors and block
saving a runnable configuration or starting it until validation passes. Do not
silently clamp or round invalid input. Discrete choices such as Gaussian BT use
selectors; even-only values such as parity bytes enforce their increment too.
Test lower/upper boundaries, values just outside them, NaN/infinity, and changes
that make another field invalid. Advanced controls obey the same limits.

Test all modulation switches, including 2FSK → GMSK → QPSK → 2FSK, plus packet
format, hopping, CRC, FEC and operation changes. Test small terminals, scrolling,
resize and keyboard-only use; color must not be the only indication of errors.

**Complete when:** all supported applicable fields are editable without raw YAML,
and hidden stale values cannot invalidate or alter another modulation's config.
**Hardware:** not needed.

**Completed 2026-10-20:**

- [`tui/app.py`](../packet_radio/tui/app.py) supplies the optional Textual application,
  project opening/new drafts, section navigation, Basic/Advanced controls,
  field errors, validation summary and error focus. The catalog drives visible
  controls, including the five modulations and other capability selectors.
- [`configuration/forms.py`](../packet_radio/configuration/forms.py) converts
  canonical units and MHz/kHz, kbit/s, Msps and ms without rounding integer inputs.
  Explicit modulation/format changes apply preset waveform/protection values;
  incompatible packet/link/frequency settings are replaced when required.
  Newly irrelevant controls are reset to valid defaults. Advanced visibility
  does not discard still-applicable unfinished edits.
- [`tui/widgets.py`](../packet_radio/tui/widgets.py) provides field rows, hex
  inputs, ordered frequency rows with add/remove/up/down actions, UHD argument
  rows and peer/key-path rows. No raw YAML editing is required for supported
  controls. Bounds and validation messages appear as text, not color alone.
- Text fields retain incomplete input until Enter/F5; selectors apply immediately.
  Table edits survive form rebuilds. Invalid values remain visibly invalid;
  values are never silently clamped. Open failures retain the current project,
  and replacing a dirty project or quitting asks before discarding edits.
- `packet-radio-ui [node.yaml]` starts the editor. Without a file it creates an
  in-memory simple 2FSK draft. The optional-dependency error explains installation.
  Wheel installation was checked outside the checkout, including bundled presets.
  No hardware probing, transmission or file saving is performed.
- Verification: focused headless tests exercise keyboard modulation selection,
  2FSK → GMSK → QPSK → 2FSK, Advanced and operation filtering, unit/error recovery,
  ordered hop editing, retained incomplete edits, discard confirmation and
  resizing to 48×16. Catalog/draft/interface tests run in CI with the optional UI
  extra on Python 3.10 and 3.14. See the commands below to rerun them.
  The full offline regression run passed 1,721 tests; after final input/focus
  refinements, all 122 focused configuration/interface tests passed on both
  Python 3.10.21 and 3.14.4. Ruff, formatting, mypy and the wheel build passed.

Run from a checkout:

```bash
python -m pip install -e '.[ui,test]'
packet-radio-ui
# Or open an existing schema-2 node:
packet-radio-ui configs/v1_1/peer-a-simple.yaml
python -m pytest -q tests/configuration tests/tui
```

Use Tab/Shift+Tab between controls and arrow keys/Enter in selectors. The section
selector jumps to that section's first editable field. Enter applies a text
field; F5 validates all pending inputs and table edits. F2 toggles Advanced,
F8 focuses an error, Ctrl+O opens a node file, Ctrl+N starts a new draft and
Ctrl+Q quits. Frequency rows accept units such as `915 MHz`; their order is the
hop order. The profile path field selects another profile relative to the node.

**Current scope:** offline editing, validation, saving, reopening and peer export
(Step 4 below), plus explicit device discovery and hardware preflight (Step 5).
Owned start/stop and live RF/status monitoring are implemented in Steps 6 and 7
below. Existing headless radio commands remain supported.

### Step 4 — Save, reopen and export user projects

Keep schema-2 YAML as the interchange format. A user-selected project folder
holds node YAML and reusable profile YAML, with an optional peer node file.
Ship reference definitions as templates and save customizations as user copies or
supported node overrides. Use existing custom identity/fingerprint rules; an edit
does not inherit the source's qualification claim. Metadata describing test
qualification is provenance, not an editable "make qualified" selector.

Use atomic file replacement and stage multi-file saves so the old node remains
usable if saving its new profile fails. Check for external edits before overwrite.
Preserve unrelated supported settings and document comment/format normalization.
Resolve profile, key, recording and replay-state paths correctly after Save As or
export. Never silently overwrite another project or move/reset replay state.
Schema-1 files can remain read-only with an explicit supported Save As conversion;
unsupported conversion must explain the limitation and preserve the source.

**Complete when:** save/reopen resolves identically, generated files pass the
existing validator, and exported peer files preserve radio settings while using
correct identities, roles, device selection, ports and local paths.
**Hardware:** not needed. This is the first useful standalone editor milestone.

**Completed 2026-10-20:**

- Ctrl+S saves an owned project; new drafts, reference templates and imported
  schema-1 files prompt for Save As. F4 explicitly selects a destination, Ctrl+O
  reopens, F6 exports a complementary peer and F7 imports legacy YAML.
- Projects contain a node YAML and reusable immutable
  `profiles/<profile-fingerprint>.yaml` files. Profiles are written and synced
  first; atomic node publication is the commit point. Existing unrelated node
  files and shipped reference templates cannot be overwritten. Old profiles are
  retained; a failed save may leave an unused profile but keeps the old node usable.
- Exact source-byte digests detect external edits, including comment changes.
  A cooperative per-node lock serializes editor saves; checks run again immediately
  before publication. This is optimistic conflict detection, not a filesystem-wide
  lock against arbitrary external writers. A lock left by a terminated writer must
  be removed explicitly after checking that no writer is active.
- Saving normalizes YAML formatting/comments and materializes effective overrides
  into the saved profile. Edited profiles receive a custom identity and unqualified
  provenance. Save/reopen must preserve the full resolved configuration, including
  private paths, before any files are committed.
- Save As rebases profile/key/recording/replay-state references without moving or
  copying their data. Peer export swaps identities and timing roles, complements
  transmit/receive modes, and validates the pair. It requires explicit hardware
  identity, distinct ports and independent private-key/replay-state paths when
  authentication is enabled. A shared X310 requires different channels. Peer
  recording is disabled unless a separate directory is supplied. Check paths on
  the destination host before running; export does not provision keys or directories.
- F7 or `packet-radio-ui --import-legacy /path/to/legacy.yaml` converts validated
  fixed-frequency schema-1 generic-v5 settings to a new schema-2 draft. Unsupported
  conversion reports an error and preserves the original. The original remains
  read-only and conversion requires Save As.
- Persistence tests exercise every modulation, round trips, external edits,
  destination races, injected write failures, immutable-profile conflicts,
  path preservation, complementary peers and legacy conversion. Headless keyboard
  tests cover saving/reopening, rejected pending input, conflicts and peer export.

Validation: all 152 configuration/TUI tests passed on Python 3.10.21 and 3.14.4
with Textual 8.2.8. The full regression suite passed: **1,752 tests** in 340.27
seconds. Ruff and mypy passed. The built wheel was installed into a
fresh Python 3.10 environment; bundled templates, save/reopen, peer validation
and a headless editor launch passed outside the checkout.

No hardware was opened or RF qualification changed for this step.

### Step 5 — Add hardware selection and preflight

Add explicit Refresh Devices and Check Hardware actions, with bounded timeouts,
cancellation and actionable missing-UHD/device-busy/unreachable messages. Run
probe work outside the TUI event loop. Show device identity, channels, antenna
choices, supported ranges and actual settings. Keep software validity separate
from device compatibility and from measured performance. Validate paired X310
channel ownership and distinct endpoint/status ports before launch.

**Complete when:** mocked discovery/probe failures leave the editor responsive;
live X310 discovery and requested-versus-actual settings are verified once available.
**Hardware:** all UI/error-path work offline; X310 needed for live probe acceptance.
B205 probing is a separate optional compatibility check.

**Completed 2026-10-20:**

- F9 explicitly discovers UHD devices; F3 displays results and applies a selected
  B205/X310 to the draft. F10 validates and probes the current snapshot. Ctrl+P
  loads a second schema-2 node and probes both channels of one shared X310.
  The command palette moves to Ctrl+Shift+P to keep Ctrl+P available for the pair.
- Checks run in an owned subprocess outside the terminal event loop, with a
  30-second timeout. F11 cancellation and normal editor shutdown terminate/reap
  the child, escalating to kill after two seconds if needed. Repeated requests
  cannot open concurrent probe children. Errors explain missing UHD bindings,
  connection/address/power problems and potential device ownership conflicts.
- Preflight uses UHD capability validation before setters, checks every configured
  frequency, and reports motherboard/daughterboard identity, channels, antennas,
  range envelopes, requested settings, actual readbacks and adjustments. Sample
  rate or frequency coercion fails compatibility. Range envelopes alone do not
  imply all intermediate values are realizable. Shared-channel rates are read
  again after both nodes have been configured.
- Paired preflight requires matching explicit X310 device arguments, one
  motherboard, distinct channels, matching sample rates, compatible network
  settings, distinct payload input/output ports and distinct enabled HTTP ports.
  It detects configuration conflicts, not ownership by arbitrary external programs.
- Software validity, device compatibility at check time and measured RF performance
  remain separate. Pending edits, changed effective settings or external changes to
  the checked peer file make a result stale. Device details remain inspectable.
- Probing changes radio settings but creates no stream, resets no device clock,
  sends no packets and accesses no authentication key/state data. Stop any other
  application using the SDR before probing. The UI and worker use the same Python
  interpreter, which must provide UHD bindings for hardware checks.

**Live X310 acceptance:** discovered `192.168.10.2`, serial `3267DE0`, HG FPGA,
with two UBX-160 v2 RF channels. Both channels read back 2,000,000 samples/s,
915,000,000 Hz, 0 dB RX/TX gain, RX2 reception and TX/RX transmission antennas.
The requested 1 MHz bandwidth reads back the boards' fixed 160 MHz analog
bandwidth and appears as an explicit adjustment. Discovery, device selection,
single-channel checking and paired checking were exercised through the headless
terminal keyboard workflow. No RF traffic or new performance qualification was
performed; B205 live probing remains optional and was not exercised here.

**Validation:** 261 focused configuration, terminal editor and hardware tests
passed on both Python 3.10.21 and 3.14.4 with Textual 8.2.8. Tests cover timeout
and cancellation cleanup (including cancellation during subprocess creation),
missing UHD, worker failure, responsive editing during probes, stale results,
range rejection before settings are applied, rate/frequency coercion, fixed-filter
adjustments and paired channel/port conflicts. Ruff and mypy passed. An installed
wheel was checked outside the checkout for worker launch and missing-UHD recovery.
The live checks above used the UHD-enabled Python 3.14 environment.

### Step 6 — Implement owned radio start/stop

Launch a validated, stable saved configuration snapshot through the current CLI.
States are Stopped, Validating, Starting, Running, Stopping and Failed. Keep Start
unavailable for invalid drafts; offer save/revalidate before running unsaved edits.
Handle authentication exactly as the headless application does, including explicit
existing bench-only unauthenticated opt-in. Starting may emit link control traffic
as well as payloads; the Run view must reflect the selected operating mode.

Implement graceful stop, wait with a timeout, and escalation only for a child owned
by this UI. Handle repeated start clicks, partial startup, process failure, device
disconnection and Ctrl+C. Normal Quit stops owned radio children. A minimal parent-
liveness mechanism must ensure a radio child does not remain transmitting after
an abnormal UI exit; verify it rather than relying only on a UI cleanup callback.
Do not stop a separately launched radio just because its status port matches.

**Complete when:** mocked lifecycle tests prove no leaked processes or device
owners, and a stopped session can restart with a different saved configuration.
**Hardware:** controller work offline; live start/stop/reopen check needs X310.

**Completed 2026-10-20:**

- Ctrl+R / Start launches one saved node; Ctrl+Shift+R / Start pair loads a saved
  peer for shared X310 operation. Ctrl+T or Ctrl+C stops the owned radio. F12
  shows the launched settings, state, errors and a bounded in-memory log tail.
  Unsaved drafts offer Save first, then require choosing Start again. Invalid
  inputs block launch. The launch dialog describes operating modes and possible
  control traffic before starting transmit-capable configurations.
- The controller implements Stopped, Validating, Starting, Running, Stopping and
  Failed. It checks source digests, reloads and compares effective saved settings,
  validates paired device/channel/port ownership, and freezes rebased node/profile
  snapshots in a private temporary directory. Edits never mutate an active run.
  Temporary snapshots are removed after normal cleanup; no keys, recordings or
  replay-state contents are copied or reset. An abnormal parent termination can
  leave temporary snapshots for the system's normal temporary-file cleanup.
- The guarded child invokes the existing radio CLI. Authentication behavior is
  unchanged: generic-v5 without keys requires explicit isolated-bench opt-in on
  each launch; custom simple framing retains the CLI's unauthenticated behavior.
  Actual key loading and replay-state handling stay in the existing application.
- A private inherited readiness pipe reports startup only after hardware,
  payload endpoints and status services initialize. Paired readiness waits for
  both nodes. HTTP ports and logs are never used to infer process ownership or
  startup success. Running is not RF acquisition or successful payload delivery.
- Startup times out after 30 seconds. Stop sends SIGTERM to the owned child,
  waits up to five seconds, then escalates to SIGKILL and reaps it. Repeated Start
  cannot open a second radio. Normal quit awaits cleanup. Hardware probing and
  radio startup exclude each other; run failures preserve diagnostic logs.
- Before radio imports/startup, the child arms Linux `PR_SET_PDEATHSIG` with
  SIGKILL and checks the expected parent on both sides of arming. Abrupt editor
  death therefore stops the child and all its radio threads without relying on
  Python shutdown handlers. Owned launch fails on unsupported platforms. A real
  subprocess test forcibly killed an owner and verified its guarded child died.
  No externally launched process is signalled or adopted based on a port.

**Live X310 acceptance:** on `192.168.10.2`, the terminal keyboard workflow started
and stopped a receive-only simple 2FSK node at 915 MHz, saved a change to 916 MHz,
restarted, and stopped with Ctrl+C. A shared-channel pair then reached readiness
and normal Quit stopped it. All three child runs exited with code 0, demonstrating
reopening after cleanup. No payloads were injected; this was lifecycle acceptance,
not a new RF/performance qualification.

**Validation:** 73 controller, run-UI, CLI, application-lifecycle and shared-device
checks passed on both Python 3.10.21 and 3.14.4. The full regression run returned
1,783 passes and exposed one table-refresh race: a run-status timer read cells
while Textual rebuilt them. That race is fixed, with a new regression test; the
complete 18-test editor suite passed on both interpreters after the fix. The
controller tests exercise frozen snapshots, restart, timeout, disconnected/crashed
children, immediate stop, cancellation during spawn, forced-stop escalation,
readiness/stop races, source edits, authentication gating, unrelated-process
protection and abnormal parent death. Ruff and mypy passed. An installed wheel
outside the checkout successfully exercised the saved-launch path, guarded child,
missing-UHD failure and cleanup. No new RF performance results were recorded.

### Step 7 — Add live status and actionable errors

Use existing read-only snapshots for configured/actual frequency, modulation,
rate, throughput, acquisition, peer activity, synchronization, queue/ACK backlog,
invalid/FEC/CRC counts, retries and UHD errors. Distinguish "process running" from
"RF acquired" and "payload delivered". Expose both channels for a paired X310.

Poll at a bounded rate (initial target 2 Hz), cap log history and mark stale or
unavailable status. Tie status to the launched session with an explicit run
identifier and effective configuration identity, adding read-only runtime metadata
if needed, so an old server cannot falsely report a new launch as ready. Treat
external logs as literal text. Do not add a network-accessible control service.

**Complete when:** UI responsiveness, stale/mismatched status, startup failure and
counter changes are tested without hardware; live readings match the CLI snapshot.
**Hardware:** mocked implementation offline; final status comparison needs X310.

**Completed 2026-10-20:**

- F12 displays both nodes of a shared pair, including configured and active target
  frequencies, hardware frequency/rate readbacks, modulation, bit rate, throughput,
  acquisition, peer activity, payload delivery, synchronization, queues, ACKs,
  retries, CRC/FEC/invalid counts and UHD errors. Actual readbacks refresh after
  tuning. Missing or inapplicable fields display N/A.
- Each launch receives a new run ID. Read-only status includes that ID, process ID,
  node/channel and a SHA256 of the effective configuration. The UI verifies all of
  them before accepting a snapshot; process readiness still uses the private pipe.
  Configuration hashes expose no raw authentication paths or key contents.
- Independent asynchronous polls run at most twice per second per node, with
  400 ms request deadlines, bounded HTTP headers and a 1 MiB response cap. No new
  server or control API is introduced. HTTP-disabled nodes show a restart hint.
  Failed/mismatched/non-advancing snapshots become unavailable or stale; readings
  older than two seconds are stale. Stop cancels polling and marks retained values
  historical. Logs remain literal text in the existing bounded in-memory history.
- Ctrl+T now has the same priority as Ctrl+C, so both stop from the details dialog
  even when a status request hangs. Poll errors include settings/log inspection
  hints and never adopt another server or declare a launch ready.

**Live comparison:** the connected X310 at `192.168.10.2` ran a shared simple 2FSK
pair at 915 MHz, 250 kbit/s and 2 Msps. Channel 1 transmitted one 22-byte UDP
payload; channel 0 delivered the identical bytes. Both live nodes matched CLI
JSON for session/configuration identities, actual hardware settings, modulation,
configured bit rate, active frequency and selected delivery/packet/error counters.
RF acquisition and payload activity became true on the receiving node. Stream,
CRC and late-TX counters were zero; shutdown exited 0. This is status acceptance,
not a throughput benchmark or qualification of other waveforms.

**Validation:** 125 relevant runtime, terminal UI, CLI, application lifecycle,
statistics/status HTTP and shared-device tests passed on each of Python 3.10.21
and 3.14.4. Offline tests cover changing counters, identity mismatches, repeated
snapshots, disabled/unavailable status, malformed/oversized HTTP responses,
independent paired polling, timeout/cancellation, literal logs and keyboard Stop
from the details dialog while polling hangs. Existing startup-failure and child
ownership tests also pass. Ruff, formatting, mypy and whitespace checks passed.
The complete software-suite and installation results are recorded under Step 8.

### Step 8 — Verify the complete offline workflow and package it

Headless TUI tests cover create/edit/switch/validate/save/reopen/export and
start/stop with fake radio children. Test every modulation's visibility matrix,
format dependencies, lost edits, read-only destinations, failed atomic saves,
missing profile/key paths, conflicting ports, process crashes and UI termination.
Exercise installation outside the checkout with and without the UI extra and
without UHD, ensuring all existing commands still work. Run normal lint, type,
conformance, applicable packaging checks and the full software suite once stable.
Update the program/configuration guides with real implemented commands and screens.
Correct stale README hardware-reference wording to reflect the final X310/B205
comparison as part of that documentation pass.

**Complete when:** a user can perform the full supported configuration workflow
without editing YAML, and UI dependencies do not become mandatory for headless use.
**Hardware:** not needed.

**Completed 2026-10-20:**

- The headless editor checks cover every modulation's visibility, framing/FEC/CRC
  dependencies, pending and lost edits, validation, save/reopen/export, conflicting
  ports, startup failures, process crashes and owned cleanup. Added UI regressions
  for read-only/disk-write failures and successful retry, missing-profile recovery,
  and actual child startup with missing authentication files before hardware access.
- The compatibility run exposed a Python 3.10 cancellation race: `asyncio.wait_for`
  could discard Stop when a status request completed in the same loop turn. Added
  an explicit wait/cleanup helper for status polling and lifecycle deadlines, with
  deterministic completion/error/timeout/cancellation regressions. The corrected
  workflow no longer stalls while stopping its fake radio child.
- `tools/check_ui_install.py` verifies the installed editor outside the checkout:
  create/edit/switch/validate/save/reopen/export, discard protection, fake-child
  start, details, Stop and Quit. Without Textual, it verifies the actionable
  optional-dependency error. `check_install.py` invokes this check, and CI tests
  the core-only installed wheel before adding the UI extra and exercising the
  editor. No UHD installation, SDR access or RF transmission is required.
- README, the program guide and configuration reference now document the actual
  commands, screens, shortcuts, conditional fields, limits, persistence semantics,
  status meanings and operational boundaries. README and the program guide use
  the final X310 reference scope and preserve the B205 comparison limitations.

**Validation:** all **1,809 tests passed** on Python 3.14.4 with **89.55% combined
line/branch coverage**, exceeding the configured 75% gate. The complete
configuration/editor/runtime subset passed **194 tests** on Python 3.10.21.
Ruff, formatting, mypy and whitespace checks passed. The installed core wheel
passed console help, protocol-v5 conformance, fidelity, all five signed simulated
modulations, schema-2 validation, capture/replay and payload checks in an isolated
Python 3.10 environment without Textual or UHD. A separate UI-extra environment,
also without UHD, passed the full installed-editor workflow. Wheel and source
archive checks passed; the source archive includes the new regressions and tools.
Final release artifacts are `dist/packet_radio-1.1.0-py3-none-any.whl` and
`dist/packet_radio-1.1.0.tar.gz`.

This step adds no RF qualification or performance claims. Step 9 below records
the subsequent matching headless/TUI conducted X310 workloads and final per-mode results.

### Step 9 — Qualify the interface on the X310 and close out

Use the existing crossed, attenuated X310 setup. Run matching short headless and
TUI-controlled workloads sequentially, with identical saved profiles and radio
settings. Exercise all five generic modulations plus custom simple 2FSK framing
and hopping. Include start/stop/reopen, paired channels, one configuration change
followed by restart, and status while packets flow. Check that the UI's polling
and logging do not introduce stream errors, backlog or material latency regressions.
Use existing exact-delivery and echo gates; investigate a changed result rather
than increasing timeouts or substituting a faster workload. Repeat affected
measurements when a difference needs confirmation; no mandatory 30-minute soak.

Treat newly edited RF/framing combinations as custom/unqualified until measured;
passing the interface test does not qualify every valid parameter combination.
B205-specific qualification and fixes remain separate from X310 UI acceptance.
Retain final per-mode results only in `MODULATION_RESULTS.md`, remove temporary
benchmark reports, and update this plan's status without creating per-step files.

**Complete when:** users can configure and operate the tested radio entirely from
the terminal interface, existing headless behavior still works, and the final
measurements support the declared interface/hardware scope.
**Hardware:** X310 required. B205s only for separately scheduled compatibility tests.

**Completed 2026-10-20:**

- The normal terminal driver on a real pseudoterminal controlled the shared X310
  pair through reopen, launch, live F12 status, Stop and Quit. Matching CLI/UI
  settings passed all five generic modulations, custom simple 2FSK framing and
  phase-coordinated 2-hop/s operation. Every positive run delivered all 50 packets
  each way and all 50 echoes, met the existing one-second echo gate and ended
  without stream errors or backlog. Final per-mode measurements and scope are in
  [MODULATION_RESULTS.md](../MODULATION_RESULTS.md#terminal-interface-x310-qualification-2026-10-20).
- Custom framing was edited/saved from 915 to 916 MHz on both nodes, reopened and
  restarted with a new run identity. Hardware readbacks matched; five fresh packets
  each way and clean shutdown passed. No shipped qualification labels were expanded.
- Hopping investigation preserved the existing boundary-drop policy. Unaligned
  echo failed 40/50; the same load with coordinated start phase passed 50/50.
  A deliberate UI boundary case showed the expected one rejection/drop per node.
  Hop admission/retune/missed-slot diagnostics and an explanatory hint now appear
  in F12 only for hopping profiles. Arbitrary payload-arrival phases remain outside
  the lossless hopping acceptance scope.
- The UDP probe accepts hopping status while retaining full configuration identity
  checks. All 37 affected probe, status-monitor and run-UI tests pass on Python
  3.10.21 and 3.14.4. Ruff, formatting and mypy pass. Core wire format and radio
  scheduling behavior did not change. Temporary benchmark artifacts are removed
  after retaining final summaries; final release archives are refreshed.

## Work that stays outside this first interface iteration

New modulation families, configurable framing on additional modulations, new FEC
or whitening schemes, arbitrary CRC polynomials/non-byte fields, faster hopping,
remote control, spectrum/waterfall processing, live waveform switching and actual
commercial-radio interoperability require their own backend work. The TUI should
make current capabilities understandable and usable, while clearly identifying
which requested combinations need an implementation rather than merely a setting.

**Closeout:** All nine steps are complete for the documented X310 interface scope.
Offline editing and software verification remain hardware-free. Repeat affected
conducted checks when changing runtime behavior; B205 interface qualification and
the deferred capabilities above remain separate work.
