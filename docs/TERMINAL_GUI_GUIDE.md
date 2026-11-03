# Terminal GUI: Editable Fields

This guide explains the boxes, lists and checkboxes you can change in the GUI.
Fields are grouped by the section names shown on screen. Each explanation tells
you the allowed inputs and how changing the value affects the radio.

- [Entering values](#terminal-configuration-and-operation)
- [Project](#project) · [Radio](#radio) · [Frequency](#frequency)
- [Framing](#framing) · [Protection](#protection) · [Link](#link)
- [Hardware](#hardware) · [Advanced](#advanced)
- [Local I/O](#local-io) · [Authentication](#authentication)
- [Status](#status) · [Recording](#recording)
- [Packet Builder](#build-modify-and-inspect-packets)
- [Peer setup](#peer-setup-fields) · [Other dialog inputs](#other-dialog-inputs)

## Detected setup inputs

New GUI sessions discover local SDRs; `--offline` skips this. Ctrl+D repeats setup
detection for a new draft. Saved projects retain their settings when reopened.

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Detected SDR** | An identified B205/B205mini or X310 from the discovered-device list. | B205 selects one local radio node; X310 selects the local two-channel simple-frame setup. A single supported radio is selected automatically once any required role is known. Multiple radios require a selection. |
| **Computer role** | `Computer A` or `Computer B`. Required for B205 setup. | Sets complementary node IDs and leader/follower roles. Choose A on one computer and B on the other. This does not discover the remote computer. `--node-role a` or `--node-role b` can supply the initial choice. |
| **Use detected setup** | Select to create the draft; **Keep editing** cancels. | Creates a fixed-frequency, bidirectional draft for the selected hardware. Save and Start remain separate actions. Draft edits made during discovery are preserved. |

For B205 projects, Start launches only this computer's node. For simple-frame
X310 projects, Start generates the other local channel automatically. Start pair
still accepts two saved X310 nodes for explicit peer settings. Both B205 hosts
must use matching radio/packet settings; their UDP endpoints remain local.

## Terminal configuration and operation

Use the **section selector** to jump to a group. Check **Advanced** (or press F2)
to show additional settings. A field appears only when it applies to your selected
modulation, packet format and transmit/receive mode.

Parameters you cannot currently change appear as a compact name/value list at the
end of their section, without descriptions. This includes selectors with only one
available choice. When your selections make a parameter editable again, its input
control and description return automatically.

Press Enter to apply a typed value, or F5 to check all pending edits. Save and
restart the radio to use changed radio settings. Changes to the waveform, packet
format or hopping schedule usually need matching changes on the other radio.

**Include units when typing frequencies, rates and durations.** A bare number uses
the underlying unit even when the current value is displayed in a larger unit:

- Frequency: enter `915 MHz` or `125 kHz`. Bare `915` means 915 Hz.
- Sample rate: enter `2 Msps` for two million samples per second.
- Bit rate: enter `250 kbit/s` for 250,000 bits per second.
- Time: enter `100 ms` or `0.1 s`. Bare `100` means 100 seconds.
- Gain: enter `0 dB`, or another value supported by your radio.
- Hop rate: enter `2` for two hops per second.
- Byte and packet counts: enter whole numbers, such as `256`.
- Hex bytes: enter pairs of hexadecimal digits, such as `a7 c3 5a 9e`.

The values already filled in come from your selected setup. Limits can depend on
other fields and the connected hardware, so correct any validation message before
saving or starting.

## Project

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Profile** | Path to an existing radio-profile file. | Choose the saved radio profile you want to use. A profile contains a set of radio settings. Loading another one replaces the current profile settings, so save your work first if you want to keep it. |
| **Node id** | Whole number 1–255. | This radio's logical identity, 1–255. It must differ from Peer id. For a pair, A's node ID equals B's peer ID; these are not SDR channel numbers or IP addresses. |
| **Peer id** | Whole number 1–255. | Logical identity of the other radio, 1–255. Set it to the other node's Node id. generic-v5 uses these identities for addressing and key lookup. |
| **Timing role** | `leader`, `follower`. | `leader` supplies the reference schedule; `follower` follows it. Use complementary roles. Both can carry application data; this does not mean transmit-only versus receive-only. |
| **Operation** | `bidirectional`, `transmit-only`, `receive-only`. | `bidirectional`, `transmit-only`, or `receive-only`. Simple frames support all three; generic-v5 remains bidirectional because it exchanges control traffic and ACKs. Irrelevant TX/RX controls disappear. |
| **Input kind** | `payload`, `frame`. | `payload` accepts application bytes and lets the radio add framing. `frame` accepts a validated simple length/payload/CRC body, without preamble, sync or FEC. The Packet Builder adapts its output automatically; external UDP senders must supply the selected kind. |
| **Log level** | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. | Amount of diagnostic logging: DEBUG, INFO, WARNING, ERROR or CRITICAL. DEBUG gives more detail and can add overhead. It does not change the radio packet format. |

## Radio

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Modulation** | `bpsk`, `2fsk`, `msk`, `gmsk`, `qpsk`. | Waveform family: BPSK and QPSK use phase changes; 2FSK uses two tones; MSK uses continuous-phase minimum-shift keying; GMSK adds Gaussian shaping. Changing this selector installs preset waveform defaults, so review dependent fields afterward. |
| **Bit rate bps** | Positive rate in bps, kbit/s or Mbit/s; must fit the selected sample rate and bandwidth. | Modulation bit rate before framing and protection overhead, not delivered application throughput. Positive; must produce an integral supported samples-per-symbol ratio. Example: `250 kbit/s`. Both peers must agree. |
| **Frequency deviation hz** | Positive Hz/kHz/MHz value; less than sample rate ÷ 4 and within the bandwidth limit. | For 2FSK, the offset of each tone from center: tones are center ± deviation. The tone separation is twice this value. Positive and less than sample rate / 4; bandwidth must also cover it. Example: `125 kHz`. |
| **Gaussian bt** | `0.3`, `0.5`. | GMSK Gaussian filter bandwidth–symbol-time product: 0.3 or 0.5. Smaller BT narrows shaping and increases symbol interaction. Keep matched peers on the same supported preset unless evaluating the change. |
| **Gaussian span symbols** | `4`, `6`. | GMSK Gaussian filter length: 4 or 6 symbols. A longer span retains more of the filter response and affects waveform length/delay. It is not the number of payload symbols. |
| **Rrc rolloff** | Number 0–1. | Root-raised-cosine excess-bandwidth factor. Range 0–1; this implementation uses zero for rectangular pulses, so select a positive value for RRC. Larger values widen the nominal shaped spectrum. |
| **Rrc span symbols** | Whole number 2 or greater; multiples of 2. | RRC filter length in symbols: even integer at least 2. Longer filters affect transient length, delay and processing. Both peers should use the matching waveform definition. |
| **Sample rate hz** | Positive sps/ksps/Msps value supported by the SDR; must meet bandwidth and samples-per-symbol requirements. | Complex I/Q samples per second exchanged with the SDR. Example: `2 Msps`. This is not packet rate. Sample rate divided by symbol rate must be a whole number: at least 2 for BPSK/QPSK and 4 for 2FSK/MSK/GMSK; UHD may support only particular rates, so check its readback. |
| **Bandwidth hz** | Positive Hz/kHz/MHz value; no greater than sample rate and wide enough for the waveform. | Requested RF bandwidth. Positive and no greater than the configured sample rate; also must cover the selected waveform. 2FSK requires at least 2×deviation + 2×bit rate; MSK/GMSK require at least 2.5×bit rate. UHD may coerce this request; it is not a measured occupied-bandwidth result. |
| **Pulse shaping** | `rectangular`, `rrc`. | Choose rectangular pulses or root-raised-cosine (RRC) shaping for BPSK/QPSK. RRC exposes rolloff and filter-span controls. Use matching shaping on both radios. |

## Frequency

For this X310's UBX-160 boards, hop centers must be between **10 MHz and 6 GHz**.
The hop table must also satisfy **highest frequency − lowest frequency + configured
signal bandwidth ≤ 160 MHz**. A 1 MHz signal therefore permits at most a 159 MHz
hop span. Invalid tables block saving and starting; reducing the hop rate does not
relax this bandwidth limit. This rule applies with device type `x300`.

On the X310/UBX-160, hopping automatically holds each RX/TX analog LO at the
midpoint of the table and changes only the digital frequency offset between
hops. There is no extra field to configure. Apply hardware checks to the complete
table before a run. The current maximum is 29 hops/s. Reception continues through DSP hops; hardware
timestamps exclude samples from the old frequency.


| Field | Allowed inputs | What it changes |
|---|---|---|
| **Hop rate** | Positive hops/s. Manual sizing: up to 2 for x300, otherwise 1. Automatic sizing: up to 29 for x300, or the displayed timing-based limit if lower (other devices still at most 1). | Frequency changes per second; 7 gives about 143 ms per frequency. Automatic mode reduces payload capacity as you increase the rate. Its upper bound is a timing estimate, not a guarantee of hardware performance. Both peers need matching settings. |
| **Mode** | `fixed`; simple-frame mode also allows `hopping`. | `fixed` holds one frequency. `hopping` follows a timed table and is currently restricted to simple-frame mode. Changing mode installs the corresponding supported frequency defaults; review the resulting table/schedule. |
| **Center frequency hz** | Positive Hz/kHz/MHz value within the SDR’s tuning range; e.g. `915 MHz`. | Requested RF center frequency in fixed mode, such as `915 MHz`. Positive, within the selected daughterboard's range. This is independent of the USRP's Ethernet address and the local UDP ports. |
| **Frequencies hz** | 2–256 unique positive frequencies, in the desired order; each within the SDR’s tuning range. | Ordered hop table, 2–256 unique positive frequencies. Add/remove/reorder entries, then apply rows or F5. Both peers need the same table/order; each entry must pass device and airtime checks. |
| **Guard seconds** | Manual sizing: at least `100 ms`. Automatic sizing with x300: at least `1 ms`. Always less than half the dwell. | Settling and end-of-hop quiet time. Larger guards leave less room for payload. The 1 ms guard applies to the continuous DSP path on the shared X310/UBX setup; other boards and hosts need their own checks. |
| **Epoch** | Nonnegative numeric UTC timestamp in seconds; e.g. `0`. No formatted date strings. | Shared schedule origin as a nonnegative numeric POSIX/UTC timestamp (seconds since 1970-01-01 UTC), not a local clock string. Both peers must use the same value and synchronized UTC clocks. |
| **Sequence mode** | `cycle`, `ping_pong`. | `cycle` repeats the table from the beginning; `ping_pong` traverses it forward and backward without repeating the endpoints at each turn. For A/B/C: cycle A-B-C-A; ping-pong A-B-C-B-A. |

With the supplied automatic X310 example (250 kbit/s 2FSK, 2 Msps, 1 ms guards,
8 ms TX lead, 16-packet TX batches), the effective limits are **1,024 bytes at
8 hops/s**, **160 bytes at 26**, **75 bytes at 28**, and **36 bytes at 29**.
Use matching settings on both peers. The calculated rate/payload tables are
in `MODULATION_RESULTS.md`. A larger guard or TX lead reduces capacity.

For QPSK on the tested shared X310, a 60-second check passed at **26 hops/s**
with **Max payload bytes = 1024** (effective limit **160 bytes**), automatic payload sizing, 250 kbit/s,
2 Msps, a 1 ms guard and 8 ms TX lead. Set both peers identically. This trades
packet capacity for faster hopping; 27 hops/s still failed its longer check.
The tables in `MODULATION_RESULTS.md` give calculated capacities at each hop rate;
they do not mean every listed rate/payload combination has passed hardware testing.

## Framing

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Format** | `generic-v5`, `simple-frame-v1` for all five modulations. | `generic-v5` provides the fixed reliable protocol, addressing and authentication support. `simple-frame-v1` provides configurable ACK-free framing and enables hopping. Changing format resets dependent profile sections; it is more than changing a header byte. |
| **Max payload bytes** | Whole number 1–1024 bytes. | In manual mode, the accepted application packet limit. In automatic mode, an upper ceiling: the actual limit can be smaller to fit a hop. Application headers/trailers count toward this limit; outer framing/protection is additional. |
| **Payload sizing** | `manual` or `automatic`; available for variable-length hopping packets. | Automatic recalculates the accepted payload size when hop timing, bit rate, framing, protection or the ceiling changes. Smaller packets remain valid. Oversized inputs are rejected, never truncated or automatically fragmented. The effective byte limit appears in the form and status summary. |
| **Preamble** | 1–64 hex bytes, e.g. `55`; repeated total must not exceed 256 bytes. | Hex bytes repeated before sync to aid signal acquisition. Enter complete bytes, e.g. `55`. Pattern length is 1–64 bytes and the expanded repeated preamble must be 1–256 bytes. |
| **Preamble repetitions** | Whole number 1–256. | Number of repeats of the entire preamble pattern, 1–256. `55` repeated 32 times produces 32 bytes; `55 aa` repeated 32 times produces 64. Longer preambles add airtime. |
| **Sync** | 2–16 hex bytes, e.g. `a7 c3 5a 9e`; acquisition pattern must contain both bit values. | Hex synchronization marker following the preamble, 2–16 bytes. Both peers must agree. The combined preamble/sync must contain both zero and one bits for acquisition. |
| **Bit order** | `big`, `little`. | Order in which bits in a byte are mapped: `big` or `little`. This differs from multi-byte integer byte order. Changing it changes the waveform interpretation and requires a matching receiver. |
| **Length bytes** | Whole number `1`, `2` or `4` for variable length; select fixed length to use 0. | Variable payload-length field width: 1, 2 or 4 bytes. The backend also uses 0 for fixed mode; the main editor switches that through Length mode. The configured payload limit must fit the field (one byte supports at most 255). |
| **Length byte order** | `big`, `little`. | `big` sends the most significant length byte first; `little` sends the least significant first. For length 256 in two bytes: big `01 00`, little `00 01`. |
| **Fixed payload bytes** | Whole number 1–1024, matching the payload limit and exact packet size. | Exact required application packet size in fixed mode, within 1–1024 and the payload limit. There is no radio length field. The radio requires a consistent maximum size; the builder's Apply action aligns that limit automatically. |
| **Crc** | `crc16-ccitt-false`, `crc32-iso-hdlc`, `none`. | Outer simple-frame integrity check: `crc16-ccitt-false` (2 bytes), `crc32-iso-hdlc` (4 bytes), or `none`. It covers transmitted length bytes plus application bytes. A CRC detects corruption; it does not authenticate the sender. |
| **Crc byte order** | `big`, `little`. | Byte order of the outer simple-frame CRC value. Match the receiver. Only relevant when CRC is enabled; it does not change the CRC polynomial or calculation. |
| **Length mode** | `variable`, `fixed`. | Choose variable to allow different packet sizes up to the payload limit, or fixed to require every packet to have one exact size. Fixed mode removes the radio length field. Review the size settings after switching. |

## Protection

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Fec** | `extended-hamming-8-4`; simple-frame mode also allows `none`. | Forward-error-correction (FEC) selection. generic-v5 requires extended-hamming-8-4; simple frames support that or none. FEC adds redundancy and airtime to recover some errors; both peers must use the same scheme. |
| **Interleaver depth** | Whole number 1–256. | Reorders protected bits to spread burst errors before decoding. Integer 1–256. It changes wire layout and buffering; it must match the receiver. It is not a queue capacity. |
| **Rs parity bytes** | Whole number 0–254; multiples of 2. | Additional Reed–Solomon parity bytes: even integer 0–254, with zero disabling this outer protection. More parity costs airtime and changes coding/block-size constraints; the full configuration validator checks the combination. |
| **Bit repetitions** | Whole number 1 or greater. | Generic BPSK repetition count, integer at least 1. One means no extra repetition. Higher counts add redundancy and airtime; this control is not supported for the other modulation/format combinations. |

## Link

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Ack timeout seconds** | Positive duration, e.g. `600 ms`. | Positive time the generic link waits for an acknowledgement before retrying. Too short can cause unnecessary retries; longer values delay recovery. Include peer turnaround and any scheduled receive opportunity. |
| **Max retries** | Whole number 0 or greater. | Number of generic-link retry attempts allowed after the initial transmission, integer at least 0. Zero disables retransmission. More retries can improve delivery but increase latency and airtime. |
| **Duplicate window seconds** | Positive duration, e.g. `30 s`. | Positive retention period for recently delivered generic packet identities. It suppresses duplicate application delivery when a retransmission follows a lost ACK. Distinct from authentication replay protection. |
| **Channel busy threshold** | Number 0 or greater, in normalized signal-power units. | Nonnegative channel-power threshold used for carrier-sense decisions, based on mean squared normalized I/Q magnitude, not dBm. Lower values make the link more likely to defer transmission; this is not RX gain. |
| **Backoff min seconds** | Duration 0 or greater, no greater than Backoff max. | Minimum randomized wait when backing off a busy channel. Nonnegative and no greater than the maximum. Zero removes the minimum delay but does not guarantee an immediate transmission. |
| **Backoff max seconds** | Duration at least as large as Backoff min. | Maximum randomized backoff, at least the minimum. Larger values can spread competing transmissions but increase waiting time. |
| **Tdd period seconds** | `0` to disable, or a positive duration with enough room for the bursts and guards. | Time-division-duplex cycle length. Zero disables TDD; a positive period allocates alternating leader/follower half-cycles. It is independent of hopping dwell. The maximum encoded packet must fit its usable half-cycle. |
| **Tdd guard seconds** | Duration 0 or greater; must leave a usable transmit window. Use 0 when TDD is disabled. | Margin around TDD transmit windows, nonnegative. Ordinary windows remove guards from both ends of each half; staggered windows remove the trailing guard. Guards must leave room for the whole packet and timing lead. |
| **Tdd inter burst seconds** | Blank to use the TDD guard, or a duration 0 or greater. | Gap between bursts within a TDD opportunity. Blank uses the TDD guard; explicit values must be nonnegative. This does not change the number of allowed bursts or the slot boundary. |
| **Tdd leader bursts per slot** | Whole number 1 or greater. | Positive integer limit on leader bursts in one TDD slot. Increasing it helps only if every complete burst and gap fits; it does not lengthen the slot. |
| **Tdd follower bursts per slot** | Whole number 1 or greater. | Positive integer limit on follower bursts in one TDD slot. May differ from the leader to reflect asymmetric traffic; keep peer scheduling expectations compatible. |
| **Tdd staggered windows** | Checked (on) or unchecked (off). | Allows a side's TX window to start at its half-cycle boundary instead of after a leading guard; the trailing guard remains. This is a timing-policy change requiring matched peer settings, not a throughput guarantee. |
| **Hardware clock sync** | Checked (on) or unchecked (off). | Uses the implemented generic-link hardware timestamp synchronization. Requires timed I/O. It is separate from the external-UTC contract used by simple hopping and does not configure an external GPS/PPS source. |
| **Hardware slot scheduling** | Checked (on) or unchecked (off). | Schedules generic TDD bursts at hardware times. Requires timed I/O, hardware clock sync and a positive TDD period. Slot lookahead and submission margin then become relevant. |
| **Minimum gap seconds** | Duration at least `2 ms`. | Simple-frame minimum idle gap after transmission completion, at least 2 ms. A larger value reduces offered burst rate. This is not start-to-start spacing: packet airtime comes before the gap. |
| **Minimum interval seconds** | Duration 0 or greater. | Additional simple-frame post-completion spacing, nonnegative; zero adds no interval beyond the other rules. Despite its name, the implementation waits after completion, so it is not an exact packet repetition period. |
| **Turnaround seconds** | Duration 0 or greater. | Minimum simple-link host delay between validated receive delivery and the next transmission, nonnegative. It does not measure or guarantee physical RF switching time. |

## Hardware

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Device** | Name/value text rows containing supported UHD options; do not repeat type, serial or addr. | Additional UHD device options, entered as name/value rows. Keep the existing values unless a specific device setup needs an option. Set type, serial number and address in their dedicated fields instead. |
| **Tx gain** | Number in dB within the selected channel’s TX gain range. | Requested transmit-chain gain in dB. Device-specific range; larger values increase gain but are not an absolute calibrated transmit-power setting. Use hardware checking/readback for the selected channel. |
| **Rx gain** | Number in dB within the selected channel’s RX gain range. | Requested receive-chain gain in dB. Device-specific range; more gain may help weak signals but can overload a strong conducted signal. It is not the channel-busy threshold. |
| **Tx antenna** | Supported TX connector name, such as `TX/RX`, or blank for the device default. | UHD TX connector/antenna name, e.g. `TX/RX` when supported. Must be one of the selected channel's available names. Blank leaves selection to the device default. |
| **Rx antenna** | Supported RX connector name, such as `RX2`, or blank for the device default. | UHD RX connector/antenna name, e.g. `RX2` for the documented crossed-cable bench. Match the physical connector and capabilities; selecting a name does not move the cable. |
| **Channel** | Whole number 0 or greater that exists on the SDR; usually 0 for B205, 0 or 1 for X310. | Zero-based RF channel on the selected device. Shared X310 peers use distinct channels, typically 0 and 1. A single-channel B205 normally uses 0. Logical node IDs remain separate. |
| **Open retry attempts** | Whole number 1–60. | How many attempts to open the USRP before reporting failure, 1–60. Useful for transient USB/device readiness failures; it cannot fix a wrong device identifier. |
| **Open retry seconds** | Number 0–30 s. | Delay between open attempts, 0–30 seconds. More delay increases startup time; the UI also has its own 30-second startup deadline. |
| **Type** | `auto`, `b200`, `x300`. | `b200` for the B205 family, `x300` for X310, or `auto` for UHD selection. Use x300 explicitly when using supported hopping above 1 hop/s. Changing type changes which identifier fields are shown. |
| **Serial** | The device’s serial-number text; blank omits the serial selector. | B205 USB serial number identifying the intended unit. It is not a logical node ID. Use discovery to distinguish two attached radios. |
| **Addr** | The X310’s network address, e.g. `192.168.10.2`; blank omits the address selector. | X310 network IP address, such as `192.168.10.2`. Both channels of a shared X310 use the same address. This is separate from local application UDP addresses. |

## Advanced

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Queue size** | Whole number 1 or greater. | Positive integer capacity for pending application traffic. A larger queue absorbs bursts but can increase latency; reaching capacity can drop new input. It does not increase RF throughput. |
| **Control queue size** | Whole number 1 or greater. | Positive generic-link control-traffic queue capacity. Separate from application payload capacity; control traffic includes scheduling/acknowledgement work. |
| **Queue airtime seconds** | Blank to disable this bound, or a positive duration large enough for the maximum packet. | Optional positive aggregate payload-queue airtime budget. Blank disables this additional bound. It must admit the maximum configured packet; a packet-count limit still applies. |
| **Queue max age seconds** | Blank to disable expiration, or a positive duration. | Optional positive age limit for queued payloads. Blank disables age expiration. Older waiting data can be discarded instead of being delivered after it has become stale. |
| **Tdd receive delay compensation seconds** | Duration from `0 ms` through `100 ms`. | Follower TDD time adjustment for receive-path delay, 0–100 ms. Keep the qualified preset unless measuring a timing correction; changing it shifts transmit-window decisions. |
| **Hardware timed io** | Checked (on) or unchecked (off). | Use device-timestamped I/O scheduling rather than only host timing where supported by the radio path. Other hardware timing controls depend on it. It does not by itself synchronize independent clocks. |
| **Hardware timed tx lead seconds** | Duration from `2 ms` through `100 ms`. | How far ahead ordinary timed transmissions are submitted, 2–100 ms. More lead gives the host/device more time but consumes usable scheduling margin; too little can produce late events. |
| **Hardware sync tx lead seconds** | Duration from `2 ms` through `100 ms`. | Lead for hardware-timed synchronization transmissions, 2–100 ms. Visible for generic hardware clock sync. Keep consistent with the synchronization budget and minimum lead. |
| **Hardware min tx lead seconds** | Duration 0 or greater, strictly below both timed-TX lead settings. | Minimum acceptable lead before a scheduled transmission. Nonnegative and strictly below both ordinary and synchronization lead values. Too little remaining lead causes admission/scheduling failure rather than time travel. |
| **Hardware tx batch packets** | Whole number 1–256. | Maximum packet count grouped for a hardware TX batch, 1–256. This is a host/streaming efficiency control, not the Packet Builder repeat count or a change to on-air packet format. |
| **Hardware epoch lead seconds** | Duration from `0.5 s` through `10 s`. | Future offset used when establishing the generic hardware timing epoch, 0.5–10 seconds. Allows startup/synchronization preparation. It is not the hopping Epoch timestamp. |
| **Hardware slot lookahead seconds** | Duration from `3 ms` through `50 ms`. | How far ahead the hardware slot scheduler considers a TX opportunity, 3–50 ms. Too small can leave insufficient host preparation time; it does not extend the slot. |
| **Hardware slot submission margin seconds** | Duration from `0 ms` through `100 ms`. | Extra host submission margin for timed slots, 0–100 ms. Use measured settings; larger margin changes when an opportunity must be ready, not the packet's RF duration. |
| **Decoder worker enabled** | Checked (on) or unchecked (off). | Moves receive decoding into the supported background worker so SDR reception need not wait for each decode operation. It can reduce receive stalls but adds queued work and processing overhead. |
| **Decoder output batches** | Whole number 16–65536. | Bound on decoded output batches waiting from the worker, integer 16–65536. Too small can overflow; larger values consume memory and can retain more delayed work. Shown only with the worker enabled. |

## Local I/O

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Bind host** | Local IPv4 address or host name; commonly `127.0.0.1` or `0.0.0.0`. | Local network interface where the radio accepts application UDP input. `127.0.0.1` limits input to this computer; `0.0.0.0` listens on all local IPv4 interfaces. This is not the peer radio's RF/network address. |
| **Bind port** | Whole number 1–65535. | UDP port where applications or the Packet Builder submit payloads, 1–65535. Each datagram represents one packet. Two radio processes on the same interface need separate input ports. |
| **Destination host** | Destination IPv4 address or host name; commonly `127.0.0.1`. | Address of the application receiving payloads decoded by this radio. Often `127.0.0.1`; a remote address forwards delivery to that host. The builder can listen only to a local destination. |
| **Destination port** | Whole number 1–65535. | UDP port receiving decoded application packets, 1–65535. An application or the builder binds this port to listen. Keep the input and output endpoints distinct to avoid feeding received bytes back into TX. |
| **Drain max packets** | Whole number 1–256. | Maximum UDP input datagrams drained per radio service pass, 1–256. Larger values process input bursts faster but consume more loop time. It is not an RF batch size or an application byte limit. |

## Authentication

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Private key file** | Path to this node’s private key; blank for an unsigned setup, with matching authentication settings. | Path to this node's Ed25519 signing key for generic-v5. A matching public-key map is required. The file is checked/opened at runtime, not by offline form validation; export does not copy private keys. |
| **Public key files** | Rows of node ID (1–255) and public-key file path; authenticated setups need local and peer IDs. | Enter the public-key file paths for your local node ID and the other radio’s node ID. These let each radio verify who sent a packet. Multiple key paths can be listed for one node. This verifies identity; it does not hide the packet contents. |
| **Replay window** | Whole number 1 or greater. | Positive integer sequence acceptance/history window used for replay protection. This is a packet-sequence window, not seconds. It is separate from duplicate delivery suppression and should accommodate expected reordering. |
| **Sync max age seconds** | Positive duration. | Positive maximum permitted age of authenticated synchronization messages. A smaller value rejects older synchronization traffic more aggressively; it can also reject delayed valid messages. |
| **Sync max future seconds** | Positive duration. | Positive allowed future-time tolerance for authenticated synchronization messages. A too-small tolerance can reject messages when clocks differ; it does not correct the clocks. |
| **Replay state file** | Path to a replay-state file, or blank if not configured. | Persistent anti-replay state path for this node. Use a separate path per node; preserving it retains protection across restarts. The editor references it rather than copying its contents. |

## Status

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Output** | `off`, `text`, `json`. | Choose off, text or json for periodic status messages from the running program. Choose text for readable messages. This is separate from the HTTP setting used by the live-status screen. |
| **Interval seconds** | Positive duration. | Positive interval between periodic text/JSON status outputs. It is not the F12 polling period; the UI polls enabled HTTP status at up to 2 Hz. |
| **Http enabled** | Checked (on) or unchecked (off). | Enable the read-only status HTTP service. F12 live readings depend on it even if periodic Output is off. This endpoint does not remotely edit or control the radio. |
| **Http host** | Local IPv4 address or host name, e.g. `127.0.0.1`. | Local interface on which the read-only status service listens. `127.0.0.1` is local-only. It must be a bindable host/interface and nonempty. |
| **Http port** | Whole number 1–65535; choose an available port. | Status HTTP port, 1–65535. Give same-host nodes distinct HTTP ports. This port carries status requests, not application UDP payloads. |

## Recording

| Field | Allowed inputs | What it changes |
|---|---|---|
| **Directory** | Folder path, or blank to disable recording. | Root folder for bounded captures. Blank disables recording and hides its subordinate controls. Enabling recording creates files and can add disk/CPU load; it is optional for normal operation. |
| **Iq** | Checked (on) or unchecked (off). | Record complex sample data for later analysis/replay. Raw I/Q can grow quickly; use size and free-space limits. At least I/Q or frame recording must be enabled when a directory is selected. |
| **Frames** | Checked (on) or unchecked (off). | Record decoded frame events alongside metadata, including available failure information. This is different from saving a Packet Builder template or a final Markdown measurement summary. |
| **Max file bytes** | Whole number at least 16384, entered in bytes. | Per-capture-file size/rotation limit, integer at least 16384 bytes. It bounds individual files, not the entire recording directory or all sessions combined. |
| **Max sessions** | Whole number 1 or greater. | Number of capture sessions retained, positive integer. The recorder prunes old managed sessions as new sessions are created. Choose this according to storage needs. |
| **Min free bytes** | Whole number 0 or greater, entered in bytes. | Nonnegative free-disk-space reserve in bytes. Recording checks available storage and stops/limits capture when space is insufficient. This is not reserved memory or an allocated file size. |

## Build, modify and inspect packets

Open **Packets** or press **Ctrl+B**. These are the fields you can change in the
Packet Builder. Save a template to keep your packet layout and contents for later.
If you change radio framing, apply it to the main draft, save and restart both
radios with matching settings.

| Field | What to enter and what it changes |
|---|---|
| Template name | A name for your saved packet, 1–100 characters. This name is not transmitted. |
| Field name | A unique label for one part of your packet, such as message type or temperature. Maximum 64 characters. The label is not transmitted. |
| Field kind | Choose how the value is represented: number, text, hex bytes, payload, automatic length or CRC. Choices are explained below. |
| Field width | Number of bytes for that field. Use a positive width for fixed-size fields. The payload field uses 0, meaning its size is calculated from the payload content. |
| Field byte order | Choose big endian for most-significant byte first, or little endian for least-significant byte first. Applies to multi-byte numbers, lengths and CRCs; match the receiver. |
| Field value | The number, text or bytes you want to send. Numeric fields accept decimal or a hexadecimal number beginning with `0x`. Hex byte fields use pairs such as `01 ff`, without `0x`. Automatic fields calculate their own values. |
| Payload encoding | Choose UTF-8 text for readable text, or hexadecimal for exact bytes. Changing the selection changes how the content is interpreted; it does not convert existing text automatically. |
| Payload content | The data to place in the payload field. At most 4096 typed characters, and the resulting packet must fit the radio's byte limit. Some text characters use more than one byte. |
| Preamble | Hex pattern sent before the packet's sync marker. See Framing above for limits. Available for simple-frame mode. |
| Preamble repetitions | How many times to repeat that pattern. More repeats add airtime. |
| Sync | Shared hex marker used to locate the packet. Both radios must match. |
| Bit order | Order of bits within each transmitted byte. Different from byte order in a multi-byte number. |
| Length bytes | Choose 1, 2 or 4 bytes for a variable-size packet's radio length field, or 0 for fixed size. |
| Length byte order | Big or little endian for the radio length value. |
| Fixed payload bytes | Leave blank for variable size. For fixed size, enter the exact total application packet size and set Length bytes to 0. Applying this also sets the radio payload limit to the same size. |
| CRC | Choose the radio's outer checksum: CRC16-CCITT-FALSE, CRC32-ISO-HDLC or none. This is separate from any CRC field you add inside your packet. |
| CRC byte order | Big or little endian for that outer CRC value. Match the other radio. |
| Local node / Peer node | Choose which running radio sends or supplies received packets. Peer node is available for a running shared pair. |
| Repeat count | Number of submissions for Send repeat, 1–10000. Send once always submits one. |
| Interval | Time in seconds between repeated submissions, 0.01–3600. It must also allow enough time for the packet and configured gap. Hopping, acknowledgements and retries may require longer spacing. |

For **Field kind**, choose:

- **uint:** a whole number that cannot be negative. Use 1, 2, 4 or 8 bytes.
  One byte allows 0–255.
- **int:** a whole number that can be negative. Use 1, 2, 4 or 8 bytes.
  One byte allows −128–127.
- **hex:** exact bytes. Enter enough bytes to fill the declared width.
- **text:** text encoded as UTF-8. Its encoded bytes must exactly fill the width;
  spaces are not added automatically.
- **payload:** inserts the Payload content. Keep exactly one payload field,
  with width 0.
- **length:** automatically writes the total application packet length, including
  all its fields and application CRCs. Use 1, 2, 4 or 8 bytes.
- **crc16:** a two-byte CRC16-CCITT-FALSE checksum of the application bytes before
  this field. Move it after the payload if it should check the payload too.
- **crc32:** a four-byte CRC32-ISO-HDLC checksum of the application bytes before
  this field.

You can arrange up to 64 fields with the arrow controls. All their bytes together,
including the payload, must fit the selected radio's limit of at most 1024 bytes.
Application fields work with every supported modulation. Editable radio framing
is available only with simple-frame mode.

## Peer setup fields

Open **Export peer** with F6 to create the settings for the other radio.

| Field | What to enter |
|---|---|
| New node file | New destination YAML for the peer configuration, distinct from the local node. |
| Peer SDR type | B205 (`b200`) or X310 (`x300`). |
| Peer B205 serial / X310 address | Required physical device identifier appropriate to the selected type. Shared X310 peers use the same address. |
| Channel | Peer RF channel; distinct from the local channel for a shared X310, normally the other of 0/1. |
| Payload input UDP port | Port where applications submit data to the peer radio. |
| Payload output UDP port | Port where applications listen for data received by the peer radio. |
| Status HTTP port | Peer read-only status service. Same-host peer ports must not conflict with local endpoints/services. |
| Payload input bind host | Local interface on the peer host accepting application input. |
| Payload output destination host | Application host receiving the peer's decoded payloads. |
| Peer private key path | Peer signing-key path; required for authenticated export. It must belong to the peer identity. |
| Separate peer replay-state path | Peer-specific persistent replay protection; required for authenticated export and distinct from the local node's state. |
| Peer recording directory | Optional independent capture location; blank disables peer recording. |

## Other dialog inputs

| Field or selection | What to enter or select |
|---|---|
| Open project path | The saved node configuration file you want to edit. |
| Save / Save As path | Where to save your node configuration. Use a new filename to keep the old setup. |
| Import path | An older configuration file to convert into a new editable draft. |
| Peer configuration path | The other saved node file when checking or starting a shared X310 pair. |
| Discovered device selection | Choose the intended radio from the discovery list, then use that selection to fill in its identity. |
| Allow unauthenticated operation on an isolated bench | Checkbox shown when starting generic-v5 without signing keys. Checking it allows that run without verifying sender identities. |
| Open packet-template path | The saved packet template you want to load into the Packet Builder. |
| Save packet-template path | Where to save the packet layout and content. This is a separate file from the radio configuration. |

For hopping on BPSK, MSK, GMSK or QPSK, select `simple-frame-v1`, then `hopping`.
Changing modulation retains the hop schedule and automatic sizing; it selects
an appropriate preamble and resets waveform/protection presets. Check both peers
and the effective payload limit after changing settings. Non-2FSK modes use
`big` bit order; length and CRC byte order can still be selected independently.
See `MODULATION_RESULTS.md` for measured rates rather than treating the GUI's
upper bound as a guarantee for every modulation.
