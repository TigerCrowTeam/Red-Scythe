"""Decode independently generated simple-frame IQ, including malformed streams."""

import numpy as np
import pytest

from packet_radio.protocol.simple import FrameSettings, SimpleFrameCodec, make_frame_modem


def reference(frame, cfg=FrameSettings(), offset=0, noise=0):
    # No production envelope, bit serializer, FEC or waveform function is used.
    raw = cfg.training + cfg.sync + frame
    bits = [
        (byte >> bit) & 1
        for byte in raw
        for bit in (range(7, -1, -1) if cfg.bit_order == "big" else range(8))
    ]
    phase = 0.0
    result = []
    for bit in bits:
        for _ in range(8):
            phase += 2 * np.pi * (125000 if bit else -125000) / 2000000
            result.append(complex(np.cos(phase), np.sin(phase)))
    iq = np.asarray(result, dtype=np.complex64)
    iq *= np.exp(1j * (0.73 + 2 * np.pi * offset * np.arange(len(iq)) / 2000000))
    rng = np.random.default_rng(1101)
    return iq + (rng.normal(0, noise, len(iq)) + 1j * rng.normal(0, noise, len(iq))).astype(
        np.complex64
    )


def collect(rx, samples, chunk=257):
    frames = []
    for start in range(0, len(samples), chunk):
        frames.extend(rx.feed(samples[start : start + chunk]))
    return frames


@pytest.mark.parametrize("size,expected", [(1, 2624), (64, 6656), (256, 18944), (1024, 68096)])
def test_sample_counts_and_independent_phase_reference(size, expected):
    codec = SimpleFrameCodec()
    modem = make_frame_modem()
    frame = codec.encode(bytes(i % 256 for i in range(size)))
    samples = modem.modulate(frame)
    assert len(samples) == expected == modem.sample_count(len(frame))
    assert modem.frame_size(len(frame)) == 40 + size
    np.testing.assert_allclose(samples, reference(frame) * np.exp(-0.73j), atol=1e-5, rtol=0)


@pytest.mark.parametrize("size", [1, 64, 256, 1024])
@pytest.mark.parametrize("offset", [-750, 0, 750])
@pytest.mark.parametrize("phase", range(8))
def test_reference_iq_noise_offset_and_split_blocks(size, offset, phase):
    codec = SimpleFrameCodec()
    # Independently construct CRC with a scalar loop.
    payload = bytes(np.random.default_rng(1101).integers(0, 256, size, dtype=np.uint8))
    body = size.to_bytes(2, "big") + payload
    crc = 0xFFFF
    for byte in body:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    frame = body + crc.to_bytes(2, "big")
    samples = np.concatenate(
        (np.zeros(phase, np.complex64), reference(frame, offset=offset, noise=0.08))
    )
    frames = collect(make_frame_modem(clock=lambda: 0), samples)
    assert [codec.decode(f) for f in frames] == [payload]


@pytest.mark.parametrize(
    "cfg",
    [
        FrameSettings(bit_order="little", length_byte_order="little"),
        FrameSettings(length_bytes=1, max_payload_bytes=255, crc="none"),
        FrameSettings(length_bytes=4, crc="crc32-iso-hdlc", crc_byte_order="little"),
        FrameSettings(length_bytes=0, fixed_payload_bytes=64),
        FrameSettings(
            preamble=b"\xaa\x55", preamble_repetitions=16, sync=bytes.fromhex("d3914ac7")
        ),
        FrameSettings(fec="extended-hamming-8-4"),
        FrameSettings(fec="extended-hamming-8-4", rs_parity_bytes=16),
    ],
)
def test_configured_tx_rx_and_accounting_use_the_same_pipeline(cfg):
    codec = SimpleFrameCodec(cfg)
    tx = make_frame_modem(cfg)
    rx = make_frame_modem(cfg, clock=lambda: 0)
    payload = bytes(range(64))
    wire = codec.encode(payload)
    samples = tx.modulate(wire)
    assert len(samples) == tx.sample_count(len(wire))
    assert [codec.decode(f) for f in collect(rx, samples)] == [payload]
    if cfg.fec == "none":
        np.testing.assert_allclose(
            samples, reference(wire, cfg) * np.exp(-0.73j), atol=1e-5, rtol=0
        )


def test_bad_crc_illegal_length_and_truncated_stream_recovery():
    codec = SimpleFrameCodec()
    rx = make_frame_modem(clock=lambda: 0)
    valid = codec.encode(b"fresh")
    bad = bytearray(codec.encode(bytes(range(64))))
    bad[-1] ^= 1
    illegal = bytes.fromhex("ffff") + bytes(32)
    for badframe in (bytes(bad), illegal):
        samples = np.concatenate(
            (reference(badframe), np.zeros(4000, np.complex64), reference(valid))
        )
        assert collect(rx, samples) == [valid]
        rx.reset()
    clock = [0.0]
    rx = make_frame_modem(clock=lambda: clock[0])
    partial = reference(codec.encode(bytes(1024)))[:20000]
    assert not collect(rx, partial)
    clock[0] = 0.101
    assert rx.feed(np.zeros(0, np.complex64)) == []
    assert len(rx._samples) == 0
    assert collect(rx, reference(valid)) == [valid]


def test_repeated_payloads_embedded_sync_and_timestamp_gap():
    codec = SimpleFrameCodec()
    rx = make_frame_modem(clock=lambda: 0)
    payload = b"\x55" * 32 + bytes.fromhex("a7c35a9e") + b"\0\xffend"
    wire = codec.encode(payload)
    assert collect(rx, np.concatenate([reference(wire)] * 3)) == [wire] * 3
    rx.reset()
    iq = reference(codec.encode(bytes(256)))
    assert rx.feed_timed(iq[:1000], 0) == []
    assert rx.feed_timed(reference(wire), 100000000) == [(wire, 100000000)]


def test_noise_buffer_remains_bounded_and_invalid_rates_fail():
    rx = make_frame_modem(clock=lambda: 0)
    for _ in range(5):
        assert not rx.feed(np.zeros(100000, np.complex64))
        assert len(rx._samples) <= rx.sample_count(rx.max_frame)
    for kwargs in (
        {"sample_rate": float("nan")},
        {"bit_rate": 0},
        {"bit_rate": 333333},
        {"deviation": 500000},
    ):
        with pytest.raises(ValueError):
            make_frame_modem(**kwargs)


@pytest.mark.parametrize("modulation", ["bpsk", "qpsk", "msk", "gmsk", "2fsk"])
@pytest.mark.parametrize("size", [1, 64, 1024])
@pytest.mark.parametrize("variant", ["default", "fixed", "fec", "little_length"])
def test_simple_frame_registry_roundtrip(modulation, size, variant):
    from pathlib import Path

    from packet_radio.configuration.forms import preset
    from packet_radio.profiles import load_v2, read_yaml
    from packet_radio.protocol.registry import resolve_processing

    path = Path("configs/v1_1/peer-a-x310-simple.yaml")
    definition = preset(modulation)
    frame = definition["packet"]["frame"]
    if variant == "fixed":
        frame.update(fixed_payload_bytes=size, length_bytes=0)
        definition["packet"]["max_payload_bytes"] = size
    elif variant == "fec":
        definition["protection"].update(scheme="extended-hamming-8-4", rs_parity_bytes=16)
    elif variant == "little_length":
        frame.update(length_byte_order="little", crc="crc32-iso-hdlc", crc_byte_order="little")
    cfg = load_v2(read_yaml(path), path, definition=definition)
    processing = resolve_processing(cfg)
    processing.validate(cfg)
    tx, rx = processing.make_modem(cfg.sdr), processing.make_modem(cfg.sdr)
    payload = (bytes(range(251)) * 5)[:size]
    wire = processing.codec.encode(payload)
    samples = tx.modulate(wire)
    assert len(samples) == tx.sample_count(len(wire))
    decoded = rx.feed(
        np.concatenate(
            (np.zeros(256, dtype=np.complex64), samples, np.zeros(1024, dtype=np.complex64))
        )
    )
    assert [processing.codec.decode(frame) for frame in decoded] == [payload]


def test_maximum_frame_survives_low_power_stream_idle_history_and_block_rounding():
    codec = SimpleFrameCodec()
    payload = bytes(range(256)) * 4
    tx = make_frame_modem()
    rx = make_frame_modem(clock=lambda: 0)
    waveform = tx.modulate(codec.encode(payload)) * 0.01
    rng = np.random.default_rng(1101)
    length = 3 * 4096 + len(waveform) + 3 * 4096
    stream = (rng.normal(0, 1e-5, length) + 1j * rng.normal(0, 1e-5, length)).astype(np.complex64)
    stream[3 * 4096 : 3 * 4096 + len(waveform)] += waveform
    frames = collect(rx, stream, chunk=4096)
    assert [codec.decode(frame) for frame in frames] == [payload]
    assert len(rx._samples) <= 2 * rx.sample_count(rx.max_frame)


def test_maximum_live_burst_survives_a_slow_frontend_tail():
    codec = SimpleFrameCodec()
    payload = bytes(range(256)) * 4
    frame = codec.encode(payload)
    signal = reference(frame) * 0.03
    rng = np.random.default_rng(10310)
    # A long low-level post-burst tail remains above the learned idle floor.
    # It must not evict the maximum frame's preamble before the first search.
    tail = (0.001 * np.exp(-np.arange(60000) / 12000)).astype(np.complex64)
    samples = np.concatenate(
        (np.zeros(16384, np.complex64), signal, tail, np.zeros(16384, np.complex64))
    )
    samples += (rng.normal(0, 1e-5, len(samples)) + 1j * rng.normal(0, 1e-5, len(samples))).astype(
        np.complex64
    )
    modem = make_frame_modem()
    assert collect(modem, samples, chunk=4096) == [frame]


@pytest.mark.parametrize("rate", [100000, 250000, 500000])
@pytest.mark.parametrize("size", [256, 1024])
def test_live_frame_delivered_before_slow_tail_with_bounded_searches(rate, size):
    from unittest.mock import Mock

    from packet_radio.diagnostics import TimingDiagnostics

    codec = SimpleFrameCodec()
    wire = codec.encode(bytes(i % 256 for i in range(size)))
    # Independent rectangular phase reference at each supported simple rate.
    bits = np.unpackbits(
        np.frombuffer(b"\x55" * 32 + bytes.fromhex("a7c35a9e") + wire, dtype=np.uint8)
    ).astype(float)
    sps = 2000000 // rate
    signal = (
        0.03
        * np.exp(1j * np.cumsum(np.repeat(bits * 2 - 1, sps)) * 2 * np.pi * (rate / 2) / 2000000)
    ).astype(np.complex64)
    prefix = 16384
    end = prefix + len(signal)
    samples = np.concatenate(
        (np.zeros(prefix, np.complex64), signal, np.full(60000, 0.001, np.complex64))
    )
    rx = make_frame_modem(bit_rate=rate, deviation=rate / 2, clock=lambda: 0)
    rx.timing = TimingDiagnostics(True)
    rx._find_frame = Mock(wraps=rx._find_frame)
    decoded = []
    recognized_at = None
    for start in range(0, len(samples), 4096):
        frames = rx.feed_timed(samples[start : start + 4096], 1000000000 + start * 500)
        if frames and recognized_at is None:
            recognized_at = start + 4096
        decoded.extend(frames)
    assert [frame for frame, _ in decoded] == [wire]
    assert abs(decoded[0][1] - (1000000000 + prefix * 500)) <= sps * 500
    assert recognized_at <= end + 8192  # No waiting for the long tail to become idle.
    assert rx._find_frame.call_count < 25  # Includes the subsequent 30 ms tail.
    assert len(rx._samples) <= 2 * rx.sample_count(rx.max_frame)
    assert rx.timing.snapshot()["rx_frame_buffer_tail_ms"]["max"] <= 4.096


def test_live_length_hint_is_cleared_on_reset_and_timestamp_discontinuity():
    codec = SimpleFrameCodec()
    rx = make_frame_modem(clock=lambda: 0)
    rx.feed_timed(np.zeros(16384, np.complex64), 0)
    long = reference(codec.encode(b"x" * 1024)) * 0.03
    rx.feed_timed(long[:8192], 16384 * 500)
    assert rx._next_live_search_samples > len(rx._samples)
    # A timed gap must not let the previous long length delay a new short frame.
    short = codec.encode(b"fresh")
    assert rx.feed_timed(reference(short) * 0.03, 1000000000)[0][0] == short
    rx.reset()
    assert rx._next_live_search_samples == 0
    assert rx._pending_frame_samples is None


def test_live_search_rejects_noise_and_corrupt_frame_then_recovers():
    codec = SimpleFrameCodec()
    rx = make_frame_modem(clock=lambda: 0)
    bad = bytearray(codec.encode(bytes(range(256))))
    bad[-1] ^= 1
    good = codec.encode(b"valid after noise and corruption")
    rng = np.random.default_rng(413)
    noise = (rng.normal(0, 0.0005, 150000) + 1j * rng.normal(0, 0.0005, 150000)).astype(
        np.complex64
    )
    stream = np.concatenate(
        (
            np.zeros(16384, np.complex64),
            noise,
            reference(bytes(bad)) * 0.03,
            np.full(4096, 0.001, np.complex64),
            reference(good) * 0.03,
            np.zeros(16384, np.complex64),
        )
    )
    frames = []
    for start in range(0, len(stream), 4096):
        frames.extend(rx.feed(stream[start : start + 4096]))
        assert len(rx._samples) <= 2 * rx.sample_count(rx.max_frame)
    assert frames == [good]


@pytest.mark.parametrize("modulation", ["bpsk", "qpsk", "msk", "gmsk", "2fsk"])
def test_simple_registry_stream_reset_and_crc_rejection(modulation):
    from pathlib import Path

    from packet_radio.configuration.forms import preset
    from packet_radio.profiles import load_v2, read_yaml
    from packet_radio.protocol.registry import resolve_processing

    path = Path("configs/v1_1/peer-a-x310-simple.yaml")
    cfg = load_v2(read_yaml(path), path, definition=preset(modulation))
    processing = resolve_processing(cfg)
    tx, rx = processing.make_modem(cfg.sdr), processing.make_modem(cfg.sdr)
    payload = bytes(range(64))
    wire = processing.codec.encode(payload)
    samples = tx.modulate(wire)
    # A hop reset must discard the old partial frame.
    assert not list(rx.feed(samples[: len(samples) // 2]))
    rx.reset()
    decoded = []
    stream = np.concatenate(
        (np.zeros(256, dtype=np.complex64), samples, np.zeros(2048, dtype=np.complex64))
    )
    for start in range(0, len(stream), 1024):
        decoded.extend(rx.feed(stream[start : start + 1024]))
    assert [processing.codec.decode(frame) for frame in decoded] == [payload]
    # Corrupt the on-air body, bypassing the TX codec's deliberate CRC check.
    bad = bytearray(wire)
    bad[-1] ^= 1
    protection = tx.processing.protection
    original = protection.encode
    protection.encode = lambda frame: bytes(bad)
    try:
        broken = tx.modulate(wire)
    finally:
        protection.encode = original
    rx.reset()
    candidates = rx.feed(np.concatenate((broken, np.zeros(2048, dtype=np.complex64))))
    assert not any(processing.codec.candidate_valid(frame) for frame in candidates)


@pytest.mark.parametrize("modulation", ["bpsk", "qpsk"])
def test_simple_psk_rrc_uses_exact_airtime(modulation):
    from pathlib import Path

    from packet_radio.configuration.forms import preset
    from packet_radio.profiles import load_v2, read_yaml
    from packet_radio.protocol.registry import resolve_processing

    definition = preset(modulation)
    definition["waveform"].update(rrc_rolloff=0.35, rrc_span_symbols=8)
    path = Path("configs/v1_1/peer-a-x310-simple.yaml")
    cfg = load_v2(read_yaml(path), path, definition=definition)
    processing = resolve_processing(cfg)
    tx, rx = processing.make_modem(cfg.sdr), processing.make_modem(cfg.sdr)
    payload = bytes(range(64))
    wire = processing.codec.encode(payload)
    samples = tx.modulate(wire)
    assert len(samples) == tx.sample_count(len(wire))
    decoded = rx.feed(
        np.concatenate(
            (np.zeros(256, dtype=np.complex64), samples, np.zeros(2048, dtype=np.complex64))
        )
    )
    assert [processing.codec.decode(frame) for frame in decoded] == [payload]


@pytest.mark.parametrize("modulation", ["bpsk", "qpsk", "msk", "gmsk", "2fsk"])
@pytest.mark.parametrize("size", [1, 64, 1024])
@pytest.mark.parametrize("offset", [0, 123, 2048, 4095])
def test_simple_registry_low_power_block_alignment(modulation, size, offset):
    from pathlib import Path

    from packet_radio.configuration.forms import preset
    from packet_radio.profiles import load_v2, read_yaml
    from packet_radio.protocol.registry import resolve_processing

    path = Path("configs/v1_1/peer-a-x310-simple.yaml")
    cfg = load_v2(read_yaml(path), path, definition=preset(modulation))
    processing = resolve_processing(cfg)
    tx, rx = processing.make_modem(cfg.sdr), processing.make_modem(cfg.sdr)
    payload = (bytes(range(251)) * 5)[:size]
    samples = tx.modulate(processing.codec.encode(payload)) * 0.01
    rng = np.random.default_rng(1101)
    start = 3 * 4096 + offset
    length = start + len(samples) + 4 * 4096
    stream = (rng.normal(0, 1e-5, length) + 1j * rng.normal(0, 1e-5, length)).astype(np.complex64)
    stream[start : start + len(samples)] += samples
    frames = collect(rx, stream, chunk=4096)
    assert [processing.codec.decode(frame) for frame in frames] == [payload]
