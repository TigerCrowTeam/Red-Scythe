"""Check the frozen frame vectors and explicit configurable framing contracts."""

from dataclasses import replace

import pytest

from packet_radio.protocol.simple import (
    CRC_DEFINITIONS,
    FrameProtection,
    FrameSettings,
    SimpleEnvelope,
    SimpleFrameCodec,
)


@pytest.mark.parametrize(
    "payload,expected",
    [
        (b"\x00", "000100ffad"),
        (bytes.fromhex("000102ff"), "0004000102ffc8b4"),
        (b"123456789", "0009313233343536373839c0c2"),
        (bytes(range(256)), "0100" + bytes(range(256)).hex() + "9332"),
    ],
)
def test_frozen_contract_vectors_and_no_implicit_protection(payload, expected):
    cfg = FrameSettings()
    codec = SimpleFrameCodec(cfg)
    frame = bytes.fromhex(expected)
    assert codec.encode(payload) == frame
    assert codec.decode(frame) == payload
    protection = FrameProtection(cfg)
    assert protection.encode(frame) == frame
    assert protection.decode(frame).payload == frame
    envelope = SimpleEnvelope(cfg, protection)
    burst = b"\x55" * 32 + bytes.fromhex("a7c35a9e") + frame
    assert envelope.encode(frame) == burst
    assert envelope.decode(burst) == frame
    assert envelope.encoded_size(protection.encoded_size(codec.payload_size(len(payload)))) == len(
        burst
    )
    for damaged in (burst[:-1], burst + b"\x00", b"\x00" + burst):
        with pytest.raises(ValueError):
            envelope.decode(damaged)


@pytest.mark.parametrize(
    "crc,expected",
    [
        ("crc16-ccitt-false", "29b1"),
        ("crc32-iso-hdlc", "cbf43926"),
        ("none", ""),
    ],
)
def test_crc_check_values_with_fixed_length_and_explicit_output_order(crc, expected):
    cfg = FrameSettings(length_bytes=0, fixed_payload_bytes=9, crc=crc)
    codec = SimpleFrameCodec(cfg)
    assert codec.encode(b"123456789") == b"123456789" + bytes.fromhex(expected)
    little = SimpleFrameCodec(replace(cfg, crc_byte_order="little"))
    assert little.encode(b"123456789") == b"123456789" + bytes.fromhex(expected)[::-1]
    assert CRC_DEFINITIONS[crc].width == len(bytes.fromhex(expected)) * 8


@pytest.mark.parametrize("size", [1, 64, 256, 1024])
@pytest.mark.parametrize("width,order", [(2, "big"), (2, "little"), (4, "big"), (4, "little")])
def test_lengths_are_payload_only_and_crc_covers_the_transmitted_length(size, width, order):
    cfg = FrameSettings(length_bytes=width, length_byte_order=order)
    codec = SimpleFrameCodec(cfg)
    payload = bytes(i % 256 for i in range(size))
    wire = codec.encode(payload)
    assert wire[:width] == size.to_bytes(width, order)
    assert codec.decode(wire) == payload
    # Independent scalar CRC, not the production binascii implementation.
    crc = 0xFFFF
    for b in wire[:-2]:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    assert wire[-2:] == crc.to_bytes(2, "big")


def test_every_single_bit_error_in_short_contract_vectors_is_rejected():
    codec = SimpleFrameCodec()
    for vector in ("000100ffad", "0004000102ffc8b4", "0009313233343536373839c0c2"):
        frame = bytes.fromhex(vector)
        for bit in range(len(frame) * 8):
            damaged = bytearray(frame)
            damaged[bit // 8] ^= 1 << (bit % 8)
            assert not codec.candidate_valid(bytes(damaged))


@pytest.mark.parametrize(
    "changes",
    [
        {"preamble": b""},
        {"preamble": "55"},
        {"preamble_repetitions": 0},
        {"preamble_repetitions": True},
        {"preamble": bytes(64), "preamble_repetitions": 5},
        {"sync": b"x"},
        {"sync": bytes(17)},
        {"sync": "aabb"},
        {"preamble": b"\x00", "sync": b"\x00\x00"},
        {"preamble": b"\xff", "sync": b"\xff\xff"},
        {"crc": []},
        {"bit_order": "middle"},
        {"length_byte_order": "middle"},
        {"crc_byte_order": "middle"},
        {"length_bytes": 3},
        {"length_bytes": 1.5},
        {"length_bytes": 0},
        {"length_bytes": 1},
        {"fixed_payload_bytes": 10},
        {"length_bytes": 0, "fixed_payload_bytes": 1025},
        {"length_bytes": 0, "fixed_payload_bytes": True},
        {"max_payload_bytes": 0},
        {"max_payload_bytes": 1025},
        {"crc": "unknown"},
        {"fec": "unknown"},
        {"rs_parity_bytes": 16},
        {"rs_parity_bytes": 1},
        {"interleaver_depth": 0},
        {"interleaver_depth": 17},
        {"whitening": "pn9"},
        {"differential": True},
    ],
)
def test_unsupported_or_ambiguous_settings_fail(changes):
    with pytest.raises(ValueError):
        FrameSettings(**changes)


def test_one_byte_length_boundary_and_fixed_length_rejection():
    codec = SimpleFrameCodec(FrameSettings(length_bytes=1, max_payload_bytes=255))
    assert codec.encode(bytes(255))[0] == 255
    for payload in (b"", bytes(256)):
        with pytest.raises(ValueError):
            codec.encode(payload)
    fixed = SimpleFrameCodec(FrameSettings(length_bytes=0, fixed_payload_bytes=3))
    with pytest.raises(ValueError):
        fixed.encode(b"xx")
    for wire in (
        b"",
        b"\x00",
        bytes.fromhex("0000ffff"),
        bytes.fromhex("ffff"),
        codec.encode(b"a") + b"x",
    ):
        assert not codec.candidate_valid(wire)


@pytest.mark.parametrize("parity", [0, 16, 32])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
def test_named_hamming_rs_chain_counts_and_round_trips(parity, size):
    cfg = FrameSettings(fec="extended-hamming-8-4", rs_parity_bytes=parity)
    codec = SimpleFrameCodec(cfg)
    protection = FrameProtection(cfg)
    env = SimpleEnvelope(cfg, protection)
    wire = codec.encode(bytes(i % 256 for i in range(size)))
    protected = protection.encode(wire)
    assert protected[:2] == size.to_bytes(2, "big")
    assert len(protected) == protection.encoded_size(len(wire))
    assert env.body_size(protected[:2]) == len(protected) - 2
    assert protection.decode(env.decode(env.encode(protected))).payload == wire
    for bad in (protected[:-1], protected + b"\0"):
        with pytest.raises(ValueError):
            protection.decode(bad)


def test_signatures_are_explicitly_rejected():
    codec = SimpleFrameCodec()
    with pytest.raises(ValueError):
        codec.encode(b"x", object())
    with pytest.raises(ValueError):
        codec.decode(codec.encode(b"x"), {})
    with pytest.raises(ValueError):
        codec.payload_size(1, authenticated=True)


def test_size_estimators_and_envelope_reject_malformed_inputs():
    cfg = FrameSettings()
    codec = SimpleFrameCodec(cfg)
    protection = FrameProtection(cfg)
    envelope = SimpleEnvelope(cfg, protection)
    for size in (-1, 0, 1, 4, 1029):
        with pytest.raises(ValueError):
            protection.encoded_size(size)
    for frame in (b"", b"\x00", bytes.fromhex("ffff"), codec.encode(b"x") + b"x"):
        with pytest.raises(ValueError):
            protection.encode(frame)
        with pytest.raises(ValueError):
            envelope.encode(frame)
    assert codec.error_counter(ValueError("CRC mismatch")) == "crc_failures"
    assert codec.error_counter(ValueError("length mismatch")) is None
    assert not codec.candidate_valid(codec.encode(b"x")[:-1])
