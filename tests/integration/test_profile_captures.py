"""Reconstruct resolved profiles from captures without consulting external files."""

import json
from dataclasses import replace

import pytest

from packet_radio.app import RadioApp
from packet_radio.config import (
    AuthenticationConfig,
    configuration_snapshot,
    load_config,
)
from packet_radio.endpoint import MemoryEndpoint
from packet_radio.profiles import profile_fingerprint
from packet_radio.protocol.framing import RadioPacket
from packet_radio.protocol.registry import resolve_processing
from packet_radio.recording import (
    CaptureRecorder,
    describe_frames,
    load_manifest,
    manifest_processing,
    replay_frames,
    replay_iq,
)
from packet_radio.simulation import SimulatedChannel, SimulatedClock
from tests.integration.test_profiles_v2 import ROOT, files, save


def recorder(path, cfg):
    s = cfg.sdr
    return CaptureRecorder(
        path,
        resolved_configuration=configuration_snapshot(cfg),
        sample_rate=s.sample_rate,
        center_frequency_hz=cfg.center_frequency_hz,
        samples_per_symbol=s.samples_per_symbol,
        modulation=s.modulation,
        frequency_deviation_hz=s.frequency_deviation_hz,
        gaussian_bt=s.gaussian_bt,
        gaussian_span_symbols=s.gaussian_span_symbols,
        rrc_rolloff=s.rrc_rolloff,
        rrc_span_symbols=s.rrc_span_symbols,
        interleaver_depth=s.fec_interleaver_depth,
        bit_repetitions=s.bit_repetitions,
        rs_parity_bytes=s.rs_parity_bytes,
        authenticated=cfg.authentication.private_key_file is not None,
        max_file_bytes=16384,
    )


@pytest.mark.parametrize("variant", ["default", "crc32", "fixed", "little", "fec"])
def test_simple_rotated_capture_replays_exact_profile_without_source(tmp_path, variant):
    _, node, path = files(tmp_path)
    overrides = {
        "default": {},
        "crc32": {"packet": {"frame": {"crc": "crc32-iso-hdlc"}}},
        "fixed": {
            "packet": {
                "max_payload_bytes": 64,
                "frame": {"length_bytes": 0, "fixed_payload_bytes": 64},
            }
        },
        "little": {
            "packet": {
                "frame": {
                    "bit_order": "little",
                    "length_byte_order": "little",
                    "crc_byte_order": "little",
                }
            }
        },
        "fec": {"protection": {"scheme": "extended-hamming-8-4", "rs_parity_bytes": 16}},
    }
    node["overrides"] = overrides[variant]
    save(path, node)
    cfg = load_config(path)
    processing = resolve_processing(cfg)
    modem = processing.make_modem(cfg.sdr)
    payload = bytes(range(64))
    wire = processing.codec.encode(payload)
    capture = tmp_path / "capture"
    r = recorder(capture, cfg)
    r.record_iq(modem.modulate(wire))
    r.record_frame(wire)
    r.close()
    path.unlink()
    (tmp_path / "radio.yaml").unlink()
    m = load_manifest(capture)
    assert m["capture_format_version"] == 3 and m["packet_format"] == "simple-frame-v1"
    assert m["processing"] == processing.describe()
    assert m["tx_idle_samples"] == 64
    assert list(replay_iq(capture, 257)) == [wire]
    assert list(replay_frames(capture)) == [wire]
    assert list(describe_frames([wire], processing=manifest_processing(m)))[0]["valid"]


@pytest.mark.parametrize("mode", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_generic_resolved_capture_preserves_all_existing_waveforms(tmp_path, mode):
    cfg = load_config(ROOT / f"configs/examples/peer-a-{mode}.yaml")
    cfg = replace(cfg, authentication=AuthenticationConfig())
    processing = resolve_processing(cfg)
    wire = processing.codec.encode(RadioPacket(1, 2, 17, b"exact legacy bytes"))
    r = recorder(tmp_path, cfg)
    r.record_iq(processing.make_modem(cfg.sdr).modulate(wire))
    r.record_frame(wire)
    r.close()
    assert list(replay_iq(tmp_path, 257)) == [wire]
    assert (
        list(
            describe_frames(
                replay_frames(tmp_path), processing=manifest_processing(load_manifest(tmp_path))
            )
        )[0]["sequence"]
        == 17
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("packet_format", "generic-v5"),
        ("authenticated", True),
        ("tx_idle_samples", 0),
        ("sample_rate", 1e6),
        ("bit_rate_bps", 500000),
        ("fec_scheme", "extended-hamming-8-4"),
        ("configuration_schema_version", 1),
        ("resolved_configuration", {}),
        ("processing", {}),
        ("configuration_sha256", "wrong"),
    ],
)
def test_conflicting_capture_metadata_rejected(tmp_path, field, value):
    _, _, path = files(tmp_path)
    cfg = load_config(path)
    r = recorder(tmp_path / "capture", cfg)
    r.close()
    target = tmp_path / "capture/manifest.json"
    m = json.loads(target.read_text())
    m[field] = value
    target.write_text(json.dumps(m))
    with pytest.raises(ValueError):
        load_manifest(target.parent)


def test_tampered_resolved_frame_cannot_reuse_original_descriptor(tmp_path):
    _, _, path = files(tmp_path)
    cfg = load_config(path)
    r = recorder(tmp_path / "capture", cfg)
    r.close()
    target = tmp_path / "capture/manifest.json"
    m = json.loads(target.read_text())
    m["resolved_configuration"]["emulation"]["packet"]["frame"]["crc"] = "crc32-iso-hdlc"
    m["configuration_sha256"] = profile_fingerprint(m["resolved_configuration"])
    target.write_text(json.dumps(m))
    with pytest.raises(ValueError, match="processing"):
        load_manifest(target.parent)


def test_live_simple_records_iq_and_payload_frames(tmp_path):
    _, node, path = files(tmp_path)
    tx_cfg = load_config(path)
    node["operation"] = "receive-only"
    node["recording"] = {"directory": "capture", "min_free_bytes": 0}
    save(path, node)
    rx_cfg = load_config(path)
    channel = SimulatedChannel(sample_rate=2e6)
    clock = SimulatedClock()
    apps = []
    try:
        for cfg in (tx_cfg, rx_cfg):
            app = RadioApp(
                cfg,
                sdr=channel.radio(),
                endpoint=MemoryEndpoint(),
                wall_clock=lambda: clock.wall,
                monotonic_clock=lambda: clock.monotonic,
            )
            app._retune()
            apps.append(app)
        payload = bytes(range(256))
        apps[0].endpoint.inject(payload)
        for _ in range(20):
            for app in apps:
                app.step()
            clock.advance(0.001)
        assert list(apps[1].endpoint.inbound) == [payload]
        directory = apps[1].recorder.directory
    finally:
        for app in apps:
            app.close()
    m = load_manifest(directory)
    p = manifest_processing(m)
    assert [p.codec.decode(w) for w in replay_iq(directory)] == [payload]
    assert [p.codec.decode(w) for w in replay_frames(directory)] == [payload]


def test_replay_cli_uses_simple_codec(tmp_path, monkeypatch, capsys):
    _, _, path = files(tmp_path)
    cfg = load_config(path)
    processing = resolve_processing(cfg)
    r = recorder(tmp_path / "capture", cfg)
    r.record_frame(processing.codec.encode(b"hello"))
    r.close()
    from packet_radio.cli.replay import main

    monkeypatch.setattr("sys.argv", ["replay", str(tmp_path / "capture"), "--source", "frames"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] and result["payload_base64"] == "aGVsbG8="
    assert "source" not in result and result["packet_format"] == "simple-frame-v1"


@pytest.mark.parametrize("rate", [100, 500])
def test_reference_rate_capture_preserves_timing_and_waveform(tmp_path, rate):
    cfg = load_config(ROOT / f"configs/v1_1/peer-a-simple-{rate}k.yaml")
    processing = resolve_processing(cfg)
    wire = processing.codec.encode(bytes(range(256)) * 4)
    modem = processing.make_modem(cfg.sdr)
    samples = modem.modulate(wire)
    assert len(samples) / cfg.sdr.sample_rate == pytest.approx(1064 * 8 / (rate * 1000))
    capture = recorder(tmp_path, cfg)
    capture.record_iq(samples)
    capture.record_frame(wire)
    capture.close()
    manifest = load_manifest(tmp_path)
    assert manifest["resolved_configuration"]["sdr"]["samples_per_symbol"] == 2000 // rate
    assert list(replay_iq(tmp_path, 4093)) == [wire]
    assert list(replay_frames(tmp_path)) == [wire]
