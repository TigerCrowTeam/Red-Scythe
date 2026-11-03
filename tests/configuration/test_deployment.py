"""Automatic topology selection preserves the wire profile and never guesses a peer."""

from dataclasses import replace
from pathlib import Path

import pytest

from packet_radio.config import load_config, validate_network
from packet_radio.configuration.deployment import (
    available_deployments,
    bind_device,
    deployment,
    new_deployment,
    select_deployment,
    shared_peer,
)
from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.storage import save_project
from packet_radio.simulation import SimulatedChannel, SimulatedNetwork

B205 = {"type": "b200", "product": "B205mini", "serial": "USB-A"}
X310 = {"type": "x300", "product": "X310", "serial": "NET", "addr": "192.168.10.2"}


@pytest.mark.parametrize("product", ["B205", "B205mini", "B205mini-i"])
def test_b205_requires_serial_and_selects_one_local_node(product):
    selected = deployment({**B205, "product": product})
    assert selected.mode == "remote-b205"
    assert selected.arguments == {"type": "b200", "serial": "USB-A"}
    with pytest.raises(ValueError, match="serial"):
        deployment({**B205, "serial": ""})


@pytest.mark.parametrize(
    "device",
    [
        {"type": "b200"},
        {**B205, "product": "B210"},
        {**X310, "product": "X300"},
        {"type": "n3xx", "product": "N310"},
    ],
)
def test_unidentified_or_other_boards_are_not_silently_classified(device):
    with pytest.raises(ValueError, match="identified"):
        deployment(device)


def test_discovery_ambiguity_filtering_and_duplicate_interfaces():
    with pytest.raises(ValueError, match="No supported"):
        select_deployment([])
    with pytest.raises(ValueError, match="Multiple"):
        select_deployment([B205, X310])
    assert select_deployment([B205, X310], serial="NET").mode == "shared-x310"
    assert len(available_deployments([X310, {**X310, "addr": "192.168.20.2"}])) == 1
    with pytest.raises(ValueError, match="No supported"):
        select_deployment([B205], serial="MISSING")


def test_roles_and_local_udp_do_not_depend_on_a_network_between_computers():
    with pytest.raises(ValueError, match="Choose computer"):
        new_deployment(deployment(B205))
    configs = {}
    for role, serial in (("a", "USB-A"), ("b", "USB-B")):
        cfg = new_deployment(deployment({**B205, "serial": serial}), role).validate().config
        assert cfg.operation == "bidirectional" and cfg.sdr.channel == 0
        assert len(cfg.frequency.frequencies_hz) == 1
        assert cfg.endpoint.bind_host == cfg.endpoint.destination_host == "127.0.0.1"
        assert cfg.endpoint.bind_port == 50000 and cfg.endpoint.destination_port == 50001
        assert not cfg.link.hardware_clock_sync and cfg.link.tdd_period_seconds == 0
        # The deterministic IQ simulator has no UHD timestamp/worker interface.
        configs[role] = replace(
            cfg,
            sdr=replace(cfg.sdr, decoder_worker_enabled=False),
            link=replace(cfg.link, hardware_timed_io=False),
            status=replace(cfg.status, output="off", http_enabled=False),
        )
    validate_network(configs)
    assert configs["a"].timing_role == "leader" and configs["b"].timing_role == "follower"
    network = SimulatedNetwork(
        configs, clock_offsets={"a": 120, "b": -90}, channel=SimulatedChannel(sample_rate=2_000_000)
    )
    try:
        network.step(4)
        for tx, rx in (("a", "b"), ("b", "a")):
            data = (tx.encode() + b"\x00\xff") * 20
            network.inject(tx, data)
            network.step(40)
            assert network.received(rx) == [data]
        for node in network.nodes.values():
            assert node.app.statistics.snapshot()["acknowledgements_sent"] == 0
    finally:
        network.close()


def test_shared_peer_uses_same_profile_other_channel_and_distinct_local_ports(tmp_path):
    source = new_deployment(deployment(X310))
    source.set("node.hardware.device.send_buff_size", "1048576")
    source = save_project(source, tmp_path / "node.yaml")
    original = (tmp_path / "node.yaml").read_bytes()
    peer = shared_peer(source, tmp_path / "ephemeral.yaml")
    a, b = source.validate().config, peer.validate().config
    assert a.sdr.device == b.sdr.device
    assert (a.sdr.channel, b.sdr.channel) == (0, 1)
    assert a.emulation == b.emulation
    assert a.node_id == b.peer_id and b.node_id == a.peer_id
    assert (
        len(
            {
                a.endpoint.bind_port,
                a.endpoint.destination_port,
                b.endpoint.bind_port,
                b.endpoint.destination_port,
            }
        )
        == 4
    )
    assert a.status.http_port != b.status.http_port
    assert not (tmp_path / "ephemeral.yaml").exists()
    assert (tmp_path / "node.yaml").read_bytes() == original


def test_binding_usb_does_not_inherit_x310_channel_or_address():
    source = RadioDraft.load("configs/v1_1/peer-b-x310-simple.yaml")
    before = source.node
    result = bind_device(source, deployment(B205))
    assert result.profile == source.profile and source.node == before
    cfg = result.validate().config
    assert cfg.sdr.channel == 0 and cfg.sdr.device == {"type": "b200", "serial": "USB-A"}
    assert cfg.node_id == 2 and cfg.timing_role == "follower"


def test_auto_usb_rejects_hopping_instead_of_silently_retuning_or_clamping():
    source = RadioDraft.load("configs/v1_1/peer-a-hopping.yaml")
    with pytest.raises(ValueError, match="fixed frequency"):
        bind_device(source, deployment(B205))


def test_shipped_two_host_examples_are_complementary():
    configs = {
        role: load_config(Path(f"configs/v1_1/peer-{role}-b205-two-host.yaml"))
        for role in ("a", "b")
    }
    validate_network(configs)
    assert all(c.operation == "bidirectional" for c in configs.values())
