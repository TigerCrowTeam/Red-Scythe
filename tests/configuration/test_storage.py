"""Persistence failure safety, path preservation, peer export and legacy migration."""

from dataclasses import replace
from pathlib import Path

import pytest

from packet_radio.config import load_config, validate_network
from packet_radio.configuration import storage
from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.storage import (
    PeerOptions,
    SaveConflict,
    export_peer,
    import_legacy,
    save_project,
)

ROOT = Path(__file__).resolve().parents[2]


def source(kind="simple", peer="a"):
    return RadioDraft.load(ROOT / f"configs/v1_1/peer-{peer}-{kind}.yaml")


def options(**changes):
    return replace(PeerOptions("b200", "PEER-SERIAL", 0, 50002, 50003, 8766), **changes)


@pytest.mark.parametrize("kind", ["simple", "generic"])
def test_save_as_roundtrip_preserves_full_config_including_paths(tmp_path, kind):
    original = source(kind)
    original.set("node.recording.directory", "captures")
    expected = original.validate().config
    saved = save_project(original, tmp_path / "new-project/node.yaml")
    assert saved.validate().config == expected
    assert load_config(saved.node_path) == expected
    assert saved.node["profile"].startswith("profiles/")
    assert not saved.dirty
    assert saved.source_versions
    assert not (tmp_path / "new-project/keys").exists()
    assert not (tmp_path / "new-project/captures").exists()


@pytest.mark.parametrize("mode", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_save_each_modulation_and_custom_identity(tmp_path, mode):
    form = FormSession.new()
    form.change("profile.waveform.modulation", mode)
    form.change("profile.frequency.center_frequency_hz", 916e6)
    expected = form.report
    saved = save_project(form.draft, tmp_path / f"{mode}.yaml")
    actual = saved.validate()
    assert actual.config == expected.config
    assert actual.derived.profile_sha256 == expected.derived.profile_sha256
    assert actual.config.emulation["qualification"] == "unqualified"
    assert actual.config.emulation["id"].endswith("-custom")


def test_save_existing_uses_new_profile_without_mutating_old_shared_profile(tmp_path):
    first = save_project(source(), tmp_path / "node.yaml")
    old_profile = first.node_path.parent / first.node["profile"]
    before = old_profile.read_bytes()
    first.set("profile.frequency.center_frequency_hz", 916e6)
    updated = save_project(first, first.node_path)
    assert old_profile.read_bytes() == before
    assert updated.node["profile"] != first.node["profile"]
    assert updated.validate().config.center_frequency_hz == 916e6
    assert not list(tmp_path.glob("*.lock"))


@pytest.mark.parametrize("which", ["node", "profile", "deleted"])
def test_conflicting_external_edits_never_overwritten(tmp_path, which):
    current = save_project(source(), tmp_path / "node.yaml")
    edited = (
        current.node_path
        if which != "profile"
        else current.node_path.parent / current.node["profile"]
    )
    if which == "deleted":
        edited.unlink()
    else:
        edited.write_bytes(edited.read_bytes() + b"\n# outside edit\n")
    current.set("node.log_level", "DEBUG")
    with pytest.raises(SaveConflict, match="Source changed"):
        save_project(current, current.node_path)
    if which != "deleted":
        assert b"outside edit" in edited.read_bytes()


def test_unrelated_existing_destination_and_reference_templates_are_protected(tmp_path):
    path = tmp_path / "node.yaml"
    path.write_text("unrelated data")
    with pytest.raises(SaveConflict, match="already exists"):
        save_project(source(), path)
    assert path.read_text() == "unrelated data"
    original = source()
    before = original.node_path.read_bytes()
    with pytest.raises(SaveConflict, match="Reference templates"):
        save_project(original, original.node_path)
    assert original.node_path.read_bytes() == before


def test_invalid_draft_writes_nothing(tmp_path):
    original = source()
    original.set("profile.waveform.bit_rate_bps", -1)
    with pytest.raises(ValueError):
        save_project(original, tmp_path / "project/node.yaml")
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize("stage", ["profile", "node"])
def test_failed_commit_leaves_previous_node_usable(tmp_path, monkeypatch, stage):
    current = save_project(source(), tmp_path / "node.yaml")
    before = current.node_path.read_bytes()
    current.set("profile.frequency.center_frequency_hz", 916e6)
    publish = storage._publish

    def fail(path, data, **kwargs):
        if (path.parent.name == "profiles") == (stage == "profile"):
            raise OSError("injected disk failure")
        return publish(path, data, **kwargs)

    monkeypatch.setattr(storage, "_publish", fail)
    with pytest.raises(OSError, match="disk failure"):
        save_project(current, current.node_path)
    assert current.node_path.read_bytes() == before
    assert load_config(current.node_path).center_frequency_hz == 915e6
    assert not (tmp_path / ".node.yaml.lock").exists()


def test_external_edit_during_staging_is_detected_before_replace(tmp_path, monkeypatch):
    current = save_project(source(), tmp_path / "node.yaml")
    current.set("node.log_level", "DEBUG")
    publish = storage._publish

    def edit_just_before_node(path, data, **kwargs):
        if path == current.node_path:
            path.write_bytes(path.read_bytes() + b"\n# concurrent editor\n")
        return publish(path, data, **kwargs)

    monkeypatch.setattr(storage, "_publish", edit_just_before_node)
    with pytest.raises(SaveConflict):
        save_project(current, current.node_path)
    assert b"concurrent editor" in current.node_path.read_bytes()


def test_new_destination_race_does_not_replace_winner(tmp_path, monkeypatch):
    path = tmp_path / "node.yaml"
    publish = storage._publish

    def race(target, data, **kwargs):
        if target == path:
            target.write_text("other writer")
        return publish(target, data, **kwargs)

    monkeypatch.setattr(storage, "_publish", race)
    with pytest.raises(FileExistsError):
        save_project(source(), path)
    assert path.read_text() == "other writer"


def test_lock_and_corrupt_immutable_profile_are_rejected(tmp_path):
    path = tmp_path / "node.yaml"
    lock = tmp_path / ".node.yaml.lock"
    lock.write_text("owned")
    with pytest.raises(SaveConflict, match="Another save"):
        save_project(source(), path)
    lock.unlink()
    current = save_project(source(), path)
    profile = path.parent / current.node["profile"]
    profile.write_text("tampered")
    with pytest.raises(SaveConflict, match="different data"):
        save_project(source(), tmp_path / "other.yaml")
    assert profile.read_text() == "tampered"


def test_peer_export_is_complementary_and_keeps_local_source(tmp_path):
    local = source()
    before = local.node_path.read_bytes()
    peer = export_peer(local, tmp_path / "peer.yaml", options())
    a, b = local.validate().config, peer.validate().config
    validate_network({"a": a, "b": b})
    assert (b.node_id, b.peer_id) == (a.peer_id, a.node_id)
    assert b.operation == "receive-only" and b.timing_role == "follower"
    assert b.sdr.device == {"type": "b200", "serial": "PEER-SERIAL"}
    assert b.sdr.channel == 0
    assert b.endpoint.bind_port == 50002 and b.status.http_port == 8766
    assert b.recording.directory is None
    assert local.node_path.read_bytes() == before


def test_authenticated_peer_requires_independent_key_and_state_paths(tmp_path):
    local = source("generic")
    with pytest.raises(ValueError, match="explicit peer private-key"):
        export_peer(local, tmp_path / "peer.yaml", options())
    config = local.validate().config
    with pytest.raises(ValueError, match="must not reuse"):
        export_peer(
            local,
            tmp_path / "peer.yaml",
            options(
                private_key_file=config.authentication.private_key_file,
                replay_state_file="peer-state.json",
            ),
        )
    peer = export_peer(
        local,
        tmp_path / "peer.yaml",
        options(
            private_key_file="keys/peer-private.pem", replay_state_file="state/peer-state.json"
        ),
    )
    result = peer.validate().config
    validate_network({"a": config, "b": result})
    assert result.authentication.private_key_file == str(tmp_path / "keys/peer-private.pem")
    assert result.authentication.replay_state_file == str(tmp_path / "state/peer-state.json")
    assert result.authentication.public_key_files == config.authentication.public_key_files
    assert not (tmp_path / "keys").exists() and not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "opts",
    [
        options(identifier=""),
        options(bind_port=50000),
        options(destination_port=50002),
        options(http_port=0),
    ],
)
def test_bad_peer_choices_do_not_write(tmp_path, opts):
    with pytest.raises(ValueError):
        export_peer(source(), tmp_path / "peer.yaml", opts)
    assert not list(tmp_path.iterdir())


def test_shared_x310_peer_requires_distinct_channel(tmp_path):
    local = source()
    local.set("node.hardware.device", {"type": "x300", "addr": "192.168.10.2"})
    with pytest.raises(ValueError, match="distinct channel"):
        export_peer(
            local,
            tmp_path / "peer.yaml",
            options(device_type="x300", identifier="192.168.10.2", channel=0),
        )
    peer = export_peer(
        local,
        tmp_path / "peer.yaml",
        options(device_type="x300", identifier="192.168.10.2", channel=1),
    )
    assert peer.validate().config.sdr.channel == 1


def test_legacy_conversion_preserves_source_and_operational_settings(tmp_path, monkeypatch):
    path = ROOT / "configs/examples/peer-a-bpsk.yaml"
    before = path.read_bytes()
    monkeypatch.chdir(tmp_path)
    original = load_config(path)
    converted = import_legacy(path)
    assert converted.read_only_source
    saved = save_project(converted, tmp_path / "converted/node.yaml")
    result = saved.validate().config
    assert result.sdr == original.sdr and result.link == original.link
    assert result.frequency == original.frequency and result.endpoint == original.endpoint
    assert result.authentication.private_key_file == str(
        tmp_path / original.authentication.private_key_file
    )
    assert path.read_bytes() == before
    assert result.emulation["qualification"] == "unqualified"
    with pytest.raises(ValueError):
        import_legacy(ROOT / "configs/v1_1/peer-a-simple.yaml")


def test_form_session_preserves_stamps_and_auto_device_settings(tmp_path):
    original = source()
    original.set("node.hardware.device", {})
    saved = save_project(original, tmp_path / "node.yaml")
    form = FormSession(saved)
    assert not form.unsaved
    assert form.selection.device_type == "auto"
    assert form.draft.validate().config == saved.validate().config
    assert form.draft.source_versions == saved.source_versions
    form.change("node.log_level", "DEBUG")
    assert form.unsaved
