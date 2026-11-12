"""Portable project exports preserve wire settings and isolate host configuration."""

import shutil

import pytest

from packet_radio.config import load_config, validate_network
from packet_radio.configuration.bundle import export_two_host
from packet_radio.configuration.deployment import configure_setup
from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.storage import SaveConflict, save_project


def source(role="a", preset="b205-bench"):
    return configure_setup(
        RadioDraft.load(f"configs/v1_1/peer-{role}-b205-two-host.yaml"),
        mode="remote-b205",
        role=role,
        identifier="LOCAL",
        preset=preset,
    )


@pytest.mark.parametrize("role", ["a", "b"])
def test_bundle_runs_after_copy_without_original_source_paths(tmp_path, role):
    draft = source(role)
    draft.set("profile.waveform.bit_rate_bps", 125000)
    draft.set("node.recording.directory", "/host-only/captures")
    before = draft.node
    path = export_two_host(draft, tmp_path / "export", "REMOTE")
    copied = tmp_path / "other-host"
    shutil.copytree(path, copied)
    shutil.rmtree(path)
    configs = {r: load_config(copied / f"computer-{r}.yaml") for r in "ab"}
    validate_network(configs)
    assert configs[role].sdr.device["serial"] == "LOCAL"
    assert configs["b" if role == "a" else "a"].sdr.device["serial"] == "REMOTE"
    assert configs["a"].emulation == configs["b"].emulation
    assert len(list((copied / "profiles").glob("*.yaml"))) == 1
    for cfg in configs.values():
        assert cfg.sdr.bit_rate_bps == 125000
        assert (cfg.sdr.tx_gain, cfg.sdr.rx_gain) == (20, 40)
        assert cfg.endpoint.bind_port == 50000
        assert cfg.endpoint.destination_port == 50001
        assert cfg.endpoint.bind_host == cfg.endpoint.destination_host == "127.0.0.1"
        assert cfg.status.http_enabled and cfg.status.http_port == 8765
        assert cfg.recording.directory is None and cfg.authentication.private_key_file is None
    assert draft.node == before
    # Local-only settings can differ without modifying the shared waveform or other node.
    peer = RadioDraft.load(copied / "computer-b.yaml")
    peer.set("node.hardware.rx_gain", 30)
    save_project(peer, peer.node_path)
    assert load_config(copied / "computer-a.yaml").sdr.rx_gain == 40
    assert load_config(copied / "computer-b.yaml").sdr.rx_gain == 30


def test_bundle_rejects_ambiguous_identity_and_existing_folder(tmp_path):
    with pytest.raises(ValueError, match="different SDR"):
        export_two_host(source(), tmp_path / "pair", "LOCAL")
    assert not (tmp_path / "pair").exists()
    with pytest.raises(ValueError, match="serial"):
        export_two_host(source(), tmp_path / "pair", "")
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    with pytest.raises(SaveConflict, match="new folder"):
        export_two_host(source(), target, "REMOTE")
    assert (target / "keep.txt").read_text() == "keep"


def test_failed_second_node_export_rolls_back_new_folder(tmp_path, monkeypatch):
    from packet_radio.configuration import bundle

    real_save = bundle.save_project
    count = 0

    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("disk unavailable")
        return real_save(*args)

    monkeypatch.setattr(bundle, "save_project", fail_second)
    with pytest.raises(OSError, match="disk unavailable"):
        export_two_host(source(), tmp_path / "pair", "REMOTE")
    assert not (tmp_path / "pair").exists()


def test_setup_preserves_waveform_and_existing_x310_channel(tmp_path):
    original = RadioDraft.load("configs/v1_1/peer-b-x310-simple.yaml")
    old = original.node
    configured = configure_setup(
        original, mode="shared-x310", role="b", identifier="192.168.10.2", preset="keep"
    )
    assert configured.profile == original.profile
    assert configured.validate().config.sdr.channel == original.validate().config.sdr.channel
    assert original.node == old
    with pytest.raises(ValueError, match="cannot be applied"):
        configure_setup(
            original, mode="shared-x310", role="b", identifier="192.168.10.2", preset="b205-bench"
        )
    with pytest.raises(ValueError, match="serial"):
        configure_setup(original, mode="remote-b205", role="a", identifier="")
    saved = save_project(source(), tmp_path / "node.yaml")
    changed = configure_setup(
        saved, mode="remote-b205", role="b", identifier="OTHER", preset="conservative"
    )
    assert changed.dirty and changed.source_versions == saved.source_versions
    assert changed.validate().config.sdr.tx_gain == 0
    assert load_config(saved.node_path).node_id == 1
    reopened = save_project(changed, saved.node_path)
    assert reopened.validate().config.node_id == 2


def test_export_rejects_unqualified_hopping(tmp_path):
    draft = RadioDraft.load("configs/v1_1/peer-a-hopping.yaml")
    draft.set("node.hardware.device", {"type": "b200", "serial": "LOCAL"})
    with pytest.raises(ValueError, match="fixed-frequency"):
        export_two_host(draft, tmp_path / "pair", "REMOTE")


def test_setup_does_not_reassign_an_authenticated_identity():
    draft = RadioDraft.load("configs/v1_1/peer-a-generic.yaml")
    with pytest.raises(ValueError, match="Authenticated identities"):
        configure_setup(draft, mode="remote-b205", role="b", identifier="OTHER")
    configured = configure_setup(draft, mode="remote-b205", role="a", identifier="LOCAL")
    assert configured.validate().config.authentication == draft.validate().config.authentication
