"""Exercise draft editing and acceptance parity with the actual file-based CLI loader."""

from pathlib import Path

import pytest
import yaml

from packet_radio.config import configuration_snapshot, load_config
from packet_radio.configuration.draft import RadioDraft, validate_pair
from packet_radio.protocol.registry import resolve_processing

ROOT = Path(__file__).resolve().parents[2]


def draft(kind="simple", peer="a"):
    return RadioDraft.load(ROOT / f"configs/v1_1/peer-{peer}-{kind}.yaml")


def persisted(tmp_path, subject):
    node = subject.node
    node["profile"] = "profile.yaml"
    # Use the same base directory for relative paths in both resolution routes.
    path = tmp_path / "node.yaml"
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(subject.profile))
    path.write_text(yaml.safe_dump(node))
    return RadioDraft(node, subject.profile, path), path


@pytest.mark.parametrize("kind", ["simple", "generic"])
def test_load_matches_cli_and_keeps_source_defaults_separate(kind):
    subject = draft(kind)
    report = subject.validate()
    assert report.valid, report.issues
    assert report.effective == configuration_snapshot(load_config(subject.node_path))
    assert not report.hardware_verified
    assert not subject.dirty
    assert "open_retry_attempts" not in subject.node["hardware"]
    assert report.config.sdr.open_retry_attempts >= 1


@pytest.mark.parametrize("mode", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_derived_values_match_processing_pipeline(mode):
    subject = draft("generic")
    subject.select_profile(f"../profiles/generic-{mode}-unslotted-v5.yaml")
    report = subject.validate()
    assert report.valid, report.issues
    config, derived = report.config, report.derived
    processing = resolve_processing(config)
    size = processing.codec.payload_size(config.endpoint.max_payload_bytes, authenticated=True)
    assert (
        derived.maximum_airtime_seconds
        == processing.make_modem(config.sdr).sample_count(size) / config.sdr.sample_rate
    )
    assert derived.samples_per_symbol == config.sdr.samples_per_symbol
    assert derived.symbol_rate_baud == derived.bit_rate_bps / (2 if mode == "qpsk" else 1)
    if mode in ("msk", "gmsk"):
        assert derived.frequency_deviation_hz == derived.bit_rate_bps / 4
    assert len(derived.profile_sha256) == 64


def test_edits_are_isolated_dirty_and_resettable():
    subject = draft()
    original = subject.node
    subject.node["node_id"] = 99
    assert subject.node == original
    value = {"frequency": {"center_frequency_hz": 916e6}}
    subject.set("node.overrides", value)
    value["frequency"]["center_frequency_hz"] = 917e6
    assert subject.validate().config.center_frequency_hz == 916e6
    assert subject.dirty
    subject.delete("node.overrides.frequency.center_frequency_hz")
    assert subject.validate().config.center_frequency_hz == 915e6
    subject.reset()
    assert not subject.dirty
    assert subject.node == original


def test_invalid_edit_can_be_corrected_without_stale_valid_report():
    subject = draft()
    good = subject.validate()
    subject.set("profile.waveform.bit_rate_bps", "")
    bad = subject.validate()
    assert not bad.valid and bad.config is None and bad.derived is None
    assert bad.issues[0].fields == ("profile.waveform.bit_rate_bps",)
    subject.set("profile.waveform.bit_rate_bps", 250000)
    assert subject.validate().valid
    assert good.config.sdr.bit_rate_bps == 250000


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), -1, 0, True, "bad", 250001]
)
def test_bad_rates_match_cli_rejection(tmp_path, value):
    subject = draft()
    subject.set("profile.waveform.bit_rate_bps", value)
    subject, path = persisted(tmp_path, subject)
    with pytest.raises(ValueError) as caught:
        load_config(path)
    report = subject.validate()
    assert not report.valid
    assert report.issues[0].message == str(caught.value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("node.node_id", 0),
        ("node.node_id", 256),
        ("node.endpoint.bind_port", 0),
        ("node.endpoint.bind_port", 65536),
        ("profile.packet.max_payload_bytes", 0),
        ("profile.packet.max_payload_bytes", 1025),
        ("profile.protection.rs_parity_bytes", 3),
        ("profile.waveform.bandwidth_hz", 2000001),
        ("profile.waveform.frequency_deviation_hz", 500000),
        ("profile.waveform.modulation", "qpsk"),
        ("node.hardware.channel", 1.5),
        ("node.unrecognized", 1),
    ],
)
def test_invalid_settings_match_cli(tmp_path, key, value):
    subject = draft()
    subject.set(key, value)
    subject, path = persisted(tmp_path, subject)
    with pytest.raises(ValueError) as caught:
        load_config(path)
    report = subject.validate()
    assert not report.valid
    assert report.issues[0].message == str(caught.value)


@pytest.mark.parametrize(
    "key,values",
    [
        ("node.endpoint.bind_port", (1, 65535)),
        ("profile.packet.max_payload_bytes", (1, 1024)),
        ("node.hardware.open_retry_attempts", (1, 60)),
    ],
)
def test_valid_inclusive_boundaries(key, values):
    subject = draft()
    for value in values:
        subject.set(key, value)
        assert subject.validate().valid


def test_paths_resolve_relative_to_node_without_reading_keys(tmp_path, monkeypatch):
    subject, path = persisted(tmp_path, draft("generic"))
    subject.set("node.recording.directory", "captures")
    monkeypatch.chdir("/")
    report = subject.validate()
    assert report.valid
    assert report.config.authentication.private_key_file == str(
        tmp_path / "keys/peer-a-private.pem"
    )
    assert report.config.recording.directory == str(tmp_path / "captures")
    assert not (tmp_path / "keys").exists()
    assert not (tmp_path / "captures").exists()


def test_validation_does_not_reread_or_write_sources(tmp_path, monkeypatch):
    subject, path = persisted(tmp_path, draft())
    before = path.read_bytes()

    def denied(*args, **kwargs):
        raise AssertionError("validation must not read a source file")

    monkeypatch.setattr(Path, "read_text", denied)
    subject.set("profile.frequency.center_frequency_hz", 916e6)
    report = subject.validate()
    assert report.valid
    assert report.config.emulation["qualification"] == "unqualified"
    assert path.read_bytes() == before


def test_profile_selection_is_atomic_and_discards_stale_overrides():
    subject = draft("generic")
    subject.set("node.overrides.waveform.frequency_deviation_hz", 100000)
    before = subject.node
    with pytest.raises(OSError):
        subject.select_profile("does-not-exist.yaml")
    assert subject.node == before
    subject.select_profile("../profiles/generic-gmsk-unslotted-v5.yaml")
    assert "overrides" not in subject.node
    assert subject.validate().valid
    with pytest.raises(ValueError, match="select_profile"):
        subject.set("node.profile", "another.yaml")
    subject.reset()
    assert not subject.dirty


def test_changed_profile_cannot_claim_qualification():
    subject = draft()
    original = subject.validate()
    subject.set("profile.frequency.center_frequency_hz", 916e6)
    report = subject.validate()
    assert report.config.emulation["qualification"] == "unqualified"
    assert report.derived.profile_sha256 != original.derived.profile_sha256
    subject.reset()
    assert subject.validate().derived == original.derived


def test_duplicate_hops_and_oversize_burst_are_rejected():
    subject = draft()
    subject.select_profile("../profiles/simple-2fsk-x310-hopping-v1.yaml")
    subject.set("node.hardware.device.type", "x300")
    assert subject.validate().derived.hop_rate_hz == 2
    subject.set("profile.frequency.frequencies_hz", [915e6, 915e6])
    issue = subject.validate().issues[0]
    assert "duplicates" in issue.message
    assert issue.fields == ("profile.frequency.frequencies_hz",)
    subject.set("profile.frequency.frequencies_hz", [915e6, 916e6])
    subject.set("profile.waveform.bit_rate_bps", 10000)
    issue = subject.validate().issues[0]
    assert "hop dwell" in issue.message
    assert "profile.packet.max_payload_bytes" in issue.fields


@pytest.mark.parametrize("kind", ["simple", "generic"])
def test_peer_validation_uses_network_contract(kind):
    first, second = draft(kind), draft(kind, "b")
    assert validate_pair(first, second).valid
    second.set("profile.frequency.center_frequency_hz", 916e6)
    report = validate_pair(first, second)
    assert not report.valid
    assert report.issues[0].scope == "network"
    assert "frequency schedule" in report.issues[0].message
    second.set("node.node_id", 0)
    assert validate_pair(first, second).issues[0].scope == "second"


def test_limits_include_static_and_dependent_constraints():
    subject = draft()
    assert subject.limits("node.node_id").maximum == 255
    assert subject.limits("profile.waveform.bit_rate_bps").maximum == 375000
    bounds = subject.limits("profile.waveform.frequency_deviation_hz")
    assert bounds.maximum == 250000 and bounds.maximum_inclusive
    assert not bounds.minimum_inclusive
    subject.set("profile.waveform.bandwidth_hz", 1500000)
    bounds = subject.limits("profile.waveform.frequency_deviation_hz")
    assert bounds.maximum == 500000 and not bounds.maximum_inclusive
    assert subject.limits("derived.hop_rate").maximum == 1
    subject.set("node.hardware.device.type", "x300")
    assert subject.limits("derived.hop_rate").maximum == 2
    assert subject.limits("node.hardware.tx_gain").maximum is None


def test_schema_one_rejected_explicitly():
    with pytest.raises(ValueError, match="schema_version: 2"):
        RadioDraft.load(ROOT / "configs/examples/peer-a-bpsk.yaml")


def test_invalid_intermediate_edit_is_atomic():
    subject = draft()
    before = subject.node
    with pytest.raises(ValueError, match="non-mapping"):
        subject.set("node.node_id.invalid", 1)
    assert subject.node == before and not subject.dirty


def test_modem_sample_ratio_limits_and_hop_airtime_limits():
    subject = draft()
    subject.set("profile.waveform.bandwidth_hz", 2000000)
    assert subject.limits("profile.waveform.bit_rate_bps").maximum == 500000
    assert subject.limits("profile.waveform.sample_rate_hz").minimum == 2000000
    subject.select_profile("../profiles/simple-2fsk-x310-hopping-v1.yaml")
    subject.set("node.hardware.device.type", "x300")
    subject.set("profile.frequency.dwell_seconds", 2)
    subject.set("profile.frequency.guard_seconds", 0.6)
    assert subject.validate().valid
    assert subject.limits("derived.hop_rate").maximum < 1


def test_override_error_does_not_modify_profile_or_source():
    subject = draft()
    before = subject.profile
    subject.set("node.overrides.waveform.bit_rate_bps", float("inf"))
    assert not subject.validate().valid
    assert subject.profile == before


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    path = tmp_path / "node.yaml"
    path.write_text("schema_version: 2\nschema_version: 2\n")
    with pytest.raises(ValueError, match="duplicate YAML"):
        RadioDraft.load(path)


def test_rendered_limits_reuse_one_validation_snapshot(monkeypatch):
    subject = draft()
    report = subject.validate()
    keys = (
        "profile.waveform.bit_rate_bps",
        "profile.waveform.sample_rate_hz",
        "profile.waveform.bandwidth_hz",
        "profile.waveform.frequency_deviation_hz",
        "derived.hop_rate",
    )
    expected = {key: subject.limits(key) for key in keys}

    def unexpected_validation():
        raise AssertionError("rendering an existing report must not resolve it again")

    with monkeypatch.context() as patch:
        patch.setattr(subject, "validate", unexpected_validation)
        assert {key: subject.limits(key, report=report) for key in keys} == expected
    # A later edit must still be validated when no snapshot is supplied.
    subject.set("profile.waveform.bandwidth_hz", 1500000)
    assert subject.limits("profile.waveform.frequency_deviation_hz").maximum == 500000
