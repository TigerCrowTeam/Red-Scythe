"""Validate reusable profile composition, mode-aware compatibility and CLI selection."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from packet_radio.app import RadioApp
from packet_radio.config import (
    config_from_snapshot,
    configuration_snapshot,
    load_config,
    validate_config,
    validate_network,
)
from packet_radio.profiles import normalize_definition, profile_fingerprint
from packet_radio.protocol.registry import resolve_processing

ROOT = Path(__file__).resolve().parents[2]


def files(tmp_path, kind="simple"):
    name = "simple-2fsk-v1" if kind == "simple" else "generic-2fsk-v5"
    profile = yaml.safe_load((ROOT / f"configs/profiles/{name}.yaml").read_text())
    node = yaml.safe_load((ROOT / f"configs/v1_1/peer-a-{kind}.yaml").read_text())
    node["profile"] = "radio.yaml"
    node["runtime"]["decoder_worker_enabled"] = False
    node["runtime"]["hardware_timed_io"] = False
    (tmp_path / "radio.yaml").write_text(yaml.safe_dump(profile))
    (tmp_path / "node.yaml").write_text(yaml.safe_dump(node))
    return profile, node, tmp_path / "node.yaml"


def save(path, value):
    path.write_text(yaml.safe_dump(value))


@pytest.mark.parametrize("kind", ["simple", "generic"])
def test_example_pairs_and_complete_snapshot_roundtrip(kind):
    configs = {p: load_config(ROOT / f"configs/v1_1/peer-{p}-{kind}.yaml") for p in ("a", "b")}
    validate_network(configs)
    for cfg in configs.values():
        restored = config_from_snapshot(configuration_snapshot(cfg))
        assert resolve_processing(restored).describe() == resolve_processing(cfg).describe()
        assert restored.emulation == cfg.emulation
        assert profile_fingerprint(restored.emulation) == profile_fingerprint(cfg.emulation)


def test_relative_paths_are_node_relative_without_changing_legacy_semantics(tmp_path, monkeypatch):
    _, node, path = files(tmp_path, "generic")
    node["recording"] = {"directory": "captures"}
    save(path, node)
    monkeypatch.chdir("/")
    cfg = load_config(path)
    assert cfg.authentication.private_key_file == str(tmp_path / "keys/peer-a-private.pem")
    assert cfg.authentication.public_key_files[1] == (str(tmp_path / "keys/peer-a-public.pem"),)
    assert cfg.recording.directory == str(tmp_path / "captures")
    old = load_config(ROOT / "configs/examples/peer-a-2fsk.yaml")
    assert old.authentication.private_key_file == "keys/peer-a-private.pem"


def test_overrides_are_explicit_normalized_variants_and_do_not_mutate_base(tmp_path):
    base, node, path = files(tmp_path)
    node["overrides"] = {
        "packet": {"frame": {"crc": "crc32-iso-hdlc", "bit_order": "little"}},
        "frequency": {"center_frequency_hz": 916e6},
    }
    save(path, node)
    cfg = load_config(path)
    assert cfg.center_frequency_hz == 916e6
    assert cfg.emulation["id"] == "simple-2fsk-v1-custom"
    assert cfg.emulation["qualification"] == "unqualified"
    assert resolve_processing(cfg).frame.crc == "crc32-iso-hdlc"
    assert yaml.safe_load((tmp_path / "radio.yaml").read_text()) == base
    assert cfg.emulation == normalize_definition(cfg.emulation)
    assert profile_fingerprint(cfg.emulation) == profile_fingerprint(load_config(path).emulation)


@pytest.mark.parametrize(
    "overrides",
    [
        {"node_id": 3},
        {"waveform": {"device": {}}},
        {"packet": {"format": "generic-v5"}},
        {"link": {"mode": "reliable"}},
        {"packet": None},
        {"waveform": {"bit_rate_bps": {}}},
        {"packet": {"frame": {"unknown": 1}}},
        {"id": "pretend-qualified"},
    ],
)
def test_bad_overrides_rejected(tmp_path, overrides):
    _, node, path = files(tmp_path)
    node["overrides"] = overrides
    save(path, node)
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("waveform", "sample_rate_hz", "2e6"),
        ("waveform", "sample_rate_hz", float("nan")),
        ("waveform", "bandwidth_hz", True),
        ("waveform", "bit_rate_bps", 0),
        ("waveform", "decoder_worker_enabled", True),
        ("waveform", "frequency_deviation_hz", 0),
        ("frequency", "mode", "hopping"),
        ("frequency", "center_frequency_hz", -1),
        ("frequency", "center_frequency_hz", None),
        ("packet", "max_payload_bytes", True),
        ("packet", "format", "unknown"),
        ("protection", "scheme", "unknown"),
        ("protection", "bit_repetitions", True),
        ("protection", "rs_parity_bytes", 3),
        ("protection", "interleaver_depth", 0),
        ("link", "mode", "reliable"),
        ("link", "hardware_timed_tx_lead_seconds", 0.01),
        ("link", "max_retries", 1),
    ],
)
def test_invalid_reusable_fields_fail_before_resources(tmp_path, monkeypatch, section, key, value):
    profile, _, path = files(tmp_path)
    profile[section][key] = value
    save(tmp_path / "radio.yaml", profile)
    hardware = Mock()
    monkeypatch.setattr("packet_radio.app.UHDRadio", hardware)
    with pytest.raises(ValueError):
        RadioApp(load_config(path))
    hardware.assert_not_called()


@pytest.mark.parametrize(
    "key,value",
    [
        ("profile_version", True),
        ("revision", 0),
        ("revision", True),
        ("id", "bad id"),
        ("reference", ""),
        ("qualification", "magic"),
        ("limitations", []),
        ("limitations", [3]),
    ],
)
def test_invalid_profile_identity(tmp_path, key, value):
    profile, _, path = files(tmp_path)
    profile[key] = value
    save(tmp_path / "radio.yaml", profile)
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize("location", ["node", "profile"])
def test_duplicate_yaml_keys_and_merges_fail(tmp_path, location):
    _, _, path = files(tmp_path)
    target = path if location == "node" else tmp_path / "radio.yaml"
    original = target.read_text()
    target.write_text(
        original + ("\nschema_version: 2\n" if location == "node" else "\nid: replacement\n")
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_config(path)
    target.write_text(original + "\n<<: {unexpected: true}\n")
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize("change", ["different-frame", "different-format", "two-tx", "two-rx"])
def test_pair_compatibility_rejects_incompatible_settings(tmp_path, change):
    _, node, path = files(tmp_path)
    a = load_config(path)
    node["operation"] = "receive-only"
    if change == "different-frame":
        node["overrides"] = {"packet": {"frame": {"bit_order": "little"}}}
    if change == "different-format":
        node["profile"] = str(ROOT / "configs/profiles/generic-2fsk-v5.yaml")
        node["operation"] = "bidirectional"
    if change == "two-tx":
        node["operation"] = "transmit-only"
    if change == "two-rx":
        a = replace(a, operation="receive-only")
    save(path, node)
    b = load_config(path)
    with pytest.raises(ValueError):
        validate_network({"a": a, "b": b})


def test_simple_pair_does_not_require_leader_follower_or_equal_host_margins(tmp_path):
    _, node, path = files(tmp_path)
    a = load_config(path)
    node["operation"] = "receive-only"
    node["runtime"]["hardware_timed_tx_lead_seconds"] = 0.02
    save(path, node)
    b = load_config(path)
    validate_network({"a": a, "b": b})


@pytest.mark.parametrize("kind", ["simple", "generic"])
def test_schema2_cli_selects_profile_without_python_injection(tmp_path, monkeypatch, kind):
    _, _, path = files(tmp_path, kind)
    import packet_radio.__main__ as cli

    runner = Mock()
    monkeypatch.setattr(cli, "run", runner)
    monkeypatch.setattr("sys.argv", ["packet-radio", "--config", str(path), "--no-status-http"])
    cli.main()
    cfg = runner.call_args.args[0]
    assert resolve_processing(cfg).name == ("simple-frame-v1" if kind == "simple" else "generic-v5")


def test_generic_still_requires_keys_or_explicit_opt_out(tmp_path, monkeypatch):
    _, node, path = files(tmp_path, "generic")
    node.pop("authentication")
    save(path, node)
    import packet_radio.__main__ as cli

    runner = Mock()
    monkeypatch.setattr(cli, "run", runner)
    monkeypatch.setattr("sys.argv", ["packet-radio", "--config", str(path)])
    with pytest.raises(SystemExit):
        cli.main()
    runner.assert_not_called()


def test_resolved_preview_is_json_redacts_key_paths_and_reports_fingerprint(
    tmp_path, monkeypatch, capsys
):
    _, _, path = files(tmp_path, "generic")
    from packet_radio.cli.validate import main

    monkeypatch.setattr("sys.argv", ["validate", str(path), "--resolved"])
    main()
    result = json.loads(capsys.readouterr().out)[str(path)]
    assert len(result["profile_sha256"]) == 64
    assert result["configuration"]["authentication"]["private_key_file"] == "<configured>"
    assert "peer-a-private" not in json.dumps(result)


def test_resolved_settings_cannot_silently_disagree_with_runtime(tmp_path):
    _, _, path = files(tmp_path)
    cfg = load_config(path)
    with pytest.raises(ValueError, match="disagrees"):
        validate_config(replace(cfg, sdr=replace(cfg.sdr, frequency_deviation_hz=100000)))
    with pytest.raises(ValueError):
        validate_config(replace(cfg, schema_version=1))
    raw = configuration_snapshot(cfg)
    raw.pop("operation")
    with pytest.raises(ValueError):
        config_from_snapshot(raw)


@pytest.mark.parametrize("mode", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_schema2_generic_signed_delivery_on_all_waveforms(tmp_path, mode):
    from packet_radio.cli.key_cli import generate_key_pair
    from packet_radio.simulation import SimulatedNetwork

    _, node, path = files(tmp_path, "generic")
    keys = {i: generate_key_pair(i, tmp_path, "v2") for i in (1, 2)}
    node["overrides"] = {
        "waveform": {
            "modulation": mode,
            "bit_rate_bps": 500000 if mode == "qpsk" else 250000,
            "frequency_deviation_hz": 125000 if mode == "2fsk" else None,
            "gaussian_bt": 0.3 if mode == "gmsk" else None,
            "gaussian_span_symbols": 4 if mode == "gmsk" else None,
        },
        "protection": {"rs_parity_bytes": 16 if mode == "qpsk" else 0},
    }
    configs = {}
    for i, label in enumerate(("a", "b"), 1):
        node.update(node_id=i, peer_id=3 - i, timing_role="leader" if i == 1 else "follower")
        node["authentication"] = {
            "private_key_file": str(keys[i][0]),
            "public_key_files": {j: str(keys[j][1]) for j in (1, 2)},
        }
        save(path, node)
        configs[label] = load_config(path)
    network = SimulatedNetwork(configs)
    try:
        network.step(4)
        network.inject("a", b"forward signed v2")
        network.inject("b", b"reverse signed v2")
        network.step(500)
        assert network.received("b") == [b"forward signed v2"]
        assert network.received("a") == [b"reverse signed v2"]
    finally:
        network.close()


@pytest.mark.parametrize(
    "change",
    [
        {"runtime": {"max_retries": 0}},
        {"hardware": {"sample_rate": 2e6}},
        {"endpoint": {"max_payload_bytes": 5}},
        {"profile": None},
        {"extends": "other.yaml"},
        {"schema_version": True},
        {"operation": "automatic"},
        {"input_kind": "iq"},
    ],
)
def test_invalid_host_settings(tmp_path, change):
    _, node, path = files(tmp_path)
    node.update(change)
    save(path, node)
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_unslotted_profiles_preserve_wire_definition_and_explicit_policy(modulation):
    from dataclasses import replace
    from pathlib import Path

    from packet_radio.config import load_config, validate_config
    from packet_radio.profiles import normalize_definition, read_yaml

    path = Path(f"configs/profiles/generic-{modulation}-unslotted-v5.yaml")
    definition = normalize_definition(read_yaml(path))
    assert definition["packet"]["format"] == "generic-v5"
    assert definition["link"]["mode"] == "reliable"
    assert definition["link"]["tdd_period_seconds"] == 0
    assert definition["link"]["ack_timeout_seconds"] == 0.6
    assert definition["waveform"]["modulation"] == modulation
    c = load_config("configs/v1_1/peer-a-x310-generic.yaml")
    validate_config(c)
    assert c.link.queue_max_age_seconds == 5
    assert c.link.queue_size == 256
    # Existing profiles remain usable with unlimited host queue age.
    legacy = load_config("configs/bench/peer-a-x310-bpsk.yaml")
    assert legacy.link.tdd_period_seconds == 0.18
    assert legacy.link.queue_max_age_seconds is None
    validate_config(replace(c, link=replace(c.link, queue_max_age_seconds=None)))
