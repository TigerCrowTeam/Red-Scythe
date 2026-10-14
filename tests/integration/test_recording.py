import json
from types import SimpleNamespace

from packet_radio.modems import BPSKModem, QPSKModem
from packet_radio.protocol.framing import RadioPacket
from packet_radio.recording import (
    CaptureRecorder,
    describe_frames,
    replay_frames,
    replay_iq,
)


def test_iq_capture_replays_through_demodulator(tmp_path):
    wire = RadioPacket(2, 1, 42, b"captured telemetry").encode()
    samples = BPSKModem(4).modulate(wire)
    recorder = CaptureRecorder(tmp_path, sample_rate=1_000_000, samples_per_symbol=4)
    recorder.record_iq(samples[:37])
    recorder.record_iq(samples[37:])
    recorder.close()

    assert list(replay_iq(tmp_path, chunk_samples=31)) == [wire]
    assert json.loads((tmp_path / "manifest.json").read_text())["sample_rate"] == 1_000_000


def test_qpsk_iq_capture_replays_with_fec_waveform_settings(tmp_path):
    wire = RadioPacket(2, 1, 43, b"captured qpsk telemetry").encode()
    samples = QPSKModem(8, interleaver_depth=32, rs_parity_bytes=16).modulate(wire)
    recorder = CaptureRecorder(
        tmp_path,
        sample_rate=1_000_000,
        samples_per_symbol=8,
        modulation="qpsk",
        interleaver_depth=32,
        rs_parity_bytes=16,
    )
    recorder.record_iq(samples)
    recorder.close()

    assert list(replay_iq(tmp_path, chunk_samples=257)) == [wire]


def test_decoded_frame_capture_preserves_valid_and_invalid_wire_bytes(tmp_path):
    valid = RadioPacket(2, 1, 7, b"valid").encode()
    invalid = b"bad frame"
    recorder = CaptureRecorder(tmp_path, sample_rate=1_000_000, samples_per_symbol=4)
    recorder.record_frame(valid)
    recorder.record_frame(invalid, "truncated packet")
    recorder.close()

    assert list(replay_frames(tmp_path)) == [valid, invalid]
    entries = [
        json.loads(line)
        for path in sorted(tmp_path.glob("frames-*.jsonl"))
        for line in path.read_text().splitlines()
    ]
    assert entries[0]["valid"] is True
    assert entries[1]["valid"] is False
    assert entries[1]["error"] == "truncated packet"


def test_replay_description_reports_packet_metadata_and_decode_failures(tmp_path):
    wire = RadioPacket(2, 1, 7, b"payload").encode()
    results = list(describe_frames([wire, b"bad"]))
    assert results[0] == {
        "index": 0,
        "valid": True,
        "kind": "DATA",
        "source": 2,
        "destination": 1,
        "sequence": 7,
        "session_id": 0,
        "payload_base64": "cGF5bG9hZA==",
    }
    assert results[1]["valid"] is False
    assert "truncated" in results[1]["error"]


def test_existing_capture_rejects_incompatible_settings(tmp_path):
    CaptureRecorder(tmp_path, sample_rate=1_000_000, samples_per_symbol=4).close()
    try:
        CaptureRecorder(tmp_path, sample_rate=2_000_000, samples_per_symbol=4)
    except ValueError as exc:
        assert "incompatible manifest" in str(exc)
    else:
        raise AssertionError("mixed capture settings should be rejected")


def test_capture_rotates_bounded_iq_and_frame_segments(tmp_path):
    recorder = CaptureRecorder(
        tmp_path,
        sample_rate=1_000_000,
        samples_per_symbol=4,
        max_file_bytes=16 * 1024,
        min_free_bytes=0,
    )
    samples = BPSKModem(4).modulate(b"x" * 200)
    recorder.record_iq(samples)
    wire = RadioPacket(2, 1, 7, b"x" * 4096).encode()
    for _ in range(4):
        recorder.record_frame(wire)
    recorder.close()
    segments = list(tmp_path.glob("iq-*.c64")) + list(tmp_path.glob("frames-*.jsonl"))
    assert len(list(tmp_path.glob("iq-*.c64"))) > 1
    assert len(list(tmp_path.glob("frames-*.jsonl"))) > 1
    assert all(path.stat().st_size <= 16 * 1024 for path in segments)


def test_capture_session_retention_removes_only_old_capture_sessions(tmp_path):
    unrelated = tmp_path / "do-not-remove"
    unrelated.mkdir()
    for _ in range(3):
        CaptureRecorder(
            tmp_path,
            sample_rate=1_000_000,
            samples_per_symbol=4,
            create_session=True,
            max_sessions=2,
            min_free_bytes=0,
        ).close()
    assert len(list(tmp_path.glob("capture-*"))) == 2
    assert unrelated.is_dir()


def test_low_space_disables_recording_without_raising(tmp_path, monkeypatch):
    failures = []
    recorder = CaptureRecorder(
        tmp_path,
        sample_rate=1_000_000,
        samples_per_symbol=4,
        min_free_bytes=100,
        on_disable=failures.append,
    )
    monkeypatch.setattr(
        "packet_radio.recording.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=50),
    )
    recorder.record_iq(BPSKModem(4).modulate(b"still keep radio running"))
    recorder.record_frame(b"ignored after disable")
    assert recorder.disabled_reason == "minimum free-space threshold reached"
    assert failures == ["minimum free-space threshold reached"]


def test_capture_manifest_identifies_rate_and_burst_and_rejects_unknown_modem(tmp_path):
    import pytest

    from packet_radio.recording import load_manifest

    CaptureRecorder(
        tmp_path, sample_rate=2e6, samples_per_symbol=8, center_frequency_hz=915e6
    ).close()
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    assert manifest["capture_format_version"] == 2
    assert manifest["bit_rate_bps"] == 250000
    assert manifest["burst_format"] == "bpsk-burst-v1"
    manifest["modulation"] = "unknown-waveform"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsupported modulation"):
        load_manifest(tmp_path)


def test_replay_accepts_explicit_legacy_capture_but_rejects_conflicting_v2_rates(tmp_path):
    import pytest

    from packet_radio.recording import load_manifest

    CaptureRecorder(tmp_path, sample_rate=2e6, samples_per_symbol=8).close()
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["bit_rate_bps"] = 500000
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="rate fields disagree"):
        load_manifest(tmp_path)
    manifest["capture_format_version"] = 1
    for key in (
        "bit_rate_bps",
        "symbol_rate_baud",
        "burst_format",
        "packet_format",
        "configuration_schema_version",
        "center_frequency_hz",
    ):
        manifest.pop(key, None)
    path.write_text(json.dumps(manifest))
    assert load_manifest(tmp_path)["capture_format_version"] == 1


def test_replay_rejects_unsupported_capture_metadata(tmp_path):
    import pytest

    from packet_radio.recording import load_manifest

    CaptureRecorder(tmp_path, sample_rate=2e6, samples_per_symbol=8).close()
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    for field, value, message in (
        ("iq_dtype", ">c8", "IQ dtype"),
        ("configuration_schema_version", True, "configuration schema"),
        ("configuration_schema_version", 2, "configuration schema"),
        ("center_frequency_hz", -1, "center frequency"),
    ):
        path.write_text(json.dumps({**manifest, field: value}))
        with pytest.raises(ValueError, match=message):
            load_manifest(tmp_path)


def test_every_maintained_waveform_replays_rotated_iq_and_frames(tmp_path):
    import numpy as np

    from packet_radio.config import load_config
    from packet_radio.modems.registry import make_modem

    for name in ("bpsk", "qpsk", "2fsk", "msk", "gmsk"):
        config = load_config(f"configs/examples/peer-a-{name}.yaml").sdr
        modem = make_modem(config)
        root = tmp_path / name
        recorder = CaptureRecorder(
            root,
            sample_rate=config.sample_rate,
            samples_per_symbol=config.samples_per_symbol,
            modulation=name,
            frequency_deviation_hz=config.frequency_deviation_hz,
            gaussian_bt=config.gaussian_bt,
            gaussian_span_symbols=config.gaussian_span_symbols,
            interleaver_depth=config.fec_interleaver_depth,
            bit_repetitions=config.bit_repetitions,
            rs_parity_bytes=config.rs_parity_bytes,
            max_file_bytes=16384,
        )
        wires = [RadioPacket(1, 2, seq, bytes(range(256))).encode() for seq in (1, 2)]
        for wire in wires:
            recorder.record_iq(np.concatenate((modem.modulate(wire), np.zeros(128, np.complex64))))
            recorder.record_frame(wire)
        recorder.close()
        assert len(list(root.glob("iq-*.c64"))) > 1
        assert list(replay_iq(root, chunk_samples=257)) == wires
        assert list(replay_frames(root)) == wires
