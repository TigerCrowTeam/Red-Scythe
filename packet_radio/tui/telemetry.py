"""Render read-only node snapshots as literal text with explicit freshness labels."""

import time


def value(snapshot, key):
    item = snapshot.get(key)
    if item is None:
        return "N/A"
    if isinstance(item, bool):
        return "yes" if item else "no"
    if isinstance(item, float):
        return f"{item:,.3f}"
    return str(item)


def render_node(node):
    s = node.snapshot
    state = node.state
    age = "never" if node.received_at is None else f"{time.monotonic() - node.received_at:.1f}s ago"
    lines = [f"Node {node.node_id} / channel {node.channel}: {state} · last update {age}"]
    if node.error:
        lines.append(node.error)
    if not s:
        lines.append(
            "RF acquired / peer active / payload delivered: unknown (no matching snapshot)"
        )
        return "\n".join(lines)
    if state != "Live":
        lines.append("Historical readings below; current RF acquisition and delivery are unknown.")
    identity = s["runtime"]
    lines.append(f"Effective config SHA256: {identity['config_sha256']}")
    groups = [
        (
            "Activity",
            [
                ("radio_ready", "radio ready"),
                ("receiver_acquired", "RF acquired"),
                ("peer_active", "peer active"),
                ("application_delivering", "payload delivering"),
                ("synchronized", "synchronized"),
            ],
        ),
        (
            "Waveform",
            [
                ("modulation", "modulation"),
                ("configured_bit_rate_bps", "configured bit/s"),
                ("configured_center_frequency_hz", "configured Hz"),
                ("active_frequency_hz", "active target Hz"),
                ("sample_rate_hz", "sample/s"),
            ],
        ),
        (
            "Payload",
            [
                ("delivered_payload_bps", "delivered bit/s"),
                ("payload_packets_delivered", "packets delivered"),
                ("payload_bytes_delivered", "bytes delivered"),
                ("packets_transmitted", "RF packets TX"),
                ("packets_received", "RF packets RX"),
            ],
        ),
        (
            "Backlog",
            [
                ("queue_depth", "queued"),
                ("queue_capacity", "capacity"),
                ("control_queue_depth", "control queued"),
                ("awaiting_acknowledgements", "awaiting ACK"),
                ("retries", "retries"),
                ("queue_drops", "queue drops"),
            ],
        ),
        (
            "Decode",
            [
                ("invalid_packets", "invalid"),
                ("crc_failures", "CRC failures"),
                ("fec_corrected_bits", "FEC corrected bits"),
                ("fec_uncorrectable_frames", "FEC failed frames"),
            ],
        ),
        (
            "UHD",
            [
                ("tx_underflows", "TX underflows"),
                ("rx_overflows", "RX overflows"),
                ("tx_timeouts", "TX timeouts"),
                ("rx_timeouts", "RX timeouts"),
                ("tx_stream_errors", "TX errors"),
                ("rx_stream_errors", "RX errors"),
                ("uhd_tx_late_events", "late TX"),
            ],
        ),
    ]
    frequency = s.get("configuration", {}).get("frequency", {})
    if len(frequency.get("frequencies_hz", [])) > 1:
        groups.append(
            (
                "Hopping",
                [
                    ("frequency_slot", "slot"),
                    ("frequency_hops", "hops"),
                    ("hop_retune_ms", "retune ms"),
                    ("hop_burst_rejections", "burst admission rejections"),
                    ("transmit_drops", "TX drops"),
                    ("hop_slots_missed", "missed slots"),
                    ("hop_tune_deadlines_missed", "missed tune deadlines"),
                ],
            )
        )
        if s.get("hop_burst_rejections", 0):
            lines.append(
                "Recorded hop admission rejections: selected bursts did not fit the dwell. "
                "The current policy drops these bursts; schedule payload input within the hop windows."
            )
    for label, fields in groups:
        lines.append(label + ": " + " · ".join(f"{name}={value(s, key)}" for key, name in fields))
    actual = s.get("hardware", {}).get("actual_settings", {})
    lines.append(
        "Hardware readback: "
        + " · ".join(
            f"{name}={value(actual, key)}"
            for key, name in [
                ("tx_frequency", "TX Hz"),
                ("rx_frequency", "RX Hz"),
                ("tx_rate", "TX sample/s"),
                ("rx_rate", "RX sample/s"),
            ]
        )
    )
    return "\n".join(lines)


def render_monitor(controller):
    if controller.monitor is None:
        return "Live status unavailable until the owned radio completes startup."
    return "\n\n".join(render_node(node) for node in controller.monitor.nodes)
