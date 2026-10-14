"""Shared supported-waveform application gates and the frozen Step 5 BPSK impairment matrix."""

from dataclasses import replace

import numpy as np
import pytest

from packet_radio.config import AuthenticationConfig, load_config
from packet_radio.modems.registry import make_modem, samples_per_symbol
from packet_radio.protocol.framing import PacketKind
from packet_radio.simulation import SimulatedNetwork
from tests.protocol.test_authentication import identities


def signed_network(tmp_path, *, queue_size=256, modulation="bpsk"):
    private, public, _ = identities(tmp_path, (1, 2))
    configs = {}
    for node, name in enumerate(("a", "b"), 1):
        c = load_config(f"configs/examples/peer-{name}-{modulation}.yaml")
        configs[name] = replace(
            c,
            authentication=AuthenticationConfig(
                private_key_file=private[node],
                public_key_files=public,
            ),
            link=replace(
                c.link, queue_size=queue_size, backoff_min_seconds=0, backoff_max_seconds=0
            ),
        )
    net = SimulatedNetwork(configs, clock_offsets={"b": -30})
    net.step(30)
    return net


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_signed_fixed_profile_thousand_packets_each_direction(tmp_path, modulation):
    net = signed_network(tmp_path, modulation=modulation)
    rng = np.random.default_rng(5001)
    expected = {"a": [], "b": []}
    try:
        for seq in range(1000):
            for source, dest in (("a", "b"), ("b", "a")):
                payload = seq.to_bytes(4, "big") + rng.bytes(252)
                expected[dest].append(payload)
                net.inject(source, payload)
            net.step(50)  # paced below modeled link capacity
        net.step(500)
        for name, peer in net.nodes.items():
            assert net.received(name) == expected[name]
            stats = peer.app.statistics.snapshot()
            assert stats["queue_drops"] == stats["retry_exhausted"] == 0
            assert stats["authentication_failures"] == 0
    finally:
        net.close()


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_first_ack_lost_for_each_of_100_signed_packets(tmp_path, monkeypatch, modulation):
    net = signed_network(tmp_path, modulation=modulation)
    dropped = set()
    leader = net.nodes["a"]
    transmit = leader.radio.transmit
    # Record the kind at selection, then drop only the first ACK for each DATA ID.
    select = leader.app.link.next_packet
    selected = []

    def next_packet(*args, **kwargs):
        packet = select(*args, **kwargs)
        selected[:] = [packet] if packet else []
        return packet

    def send(samples):
        packet = selected[0].packet
        if packet.kind == PacketKind.ACK and packet.payload not in dropped:
            dropped.add(packet.payload)
            return
        transmit(samples)

    monkeypatch.setattr(leader.app.link, "next_packet", next_packet)
    monkeypatch.setattr(leader.radio, "transmit", send)
    expected = [i.to_bytes(4, "big") + b"\x00\xff" * 30 for i in range(100)]
    try:
        for payload in expected:
            net.inject("b", payload)
            net.step(150)
        assert len(dropped) == 100
        assert net.received("a") == expected
        stats = net.nodes["b"].app.statistics.snapshot()
        assert stats["retries"] == 100
        assert stats["retry_exhausted"] == 0
        assert not net.nodes["b"].app.link.awaiting_ack
    finally:
        net.close()


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_overload_rejects_newest_and_recovers_after_channel_restored(tmp_path, modulation):
    net = signed_network(tmp_path, queue_size=8, modulation=modulation)
    try:
        net.disconnect("b")
        packets = [i.to_bytes(2, "big") for i in range(32)]
        for payload in packets:
            net.inject("a", payload)
        net.step(2)
        app = net.nodes["a"].app
        assert len(app.link.queue) + len(app.link.awaiting_ack) == 8
        assert app.statistics.snapshot()["queue_drops"] == 24
        net.reconnect("b")
        net.step(400)
        assert net.received("b") == packets[:8]
        net.inject("a", b"service resumed")
        net.step(100)
        assert net.received("b")[-1] == b"service resumed"
    finally:
        net.close()


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_fixed_channel_recovers_within_five_simulated_seconds(tmp_path, modulation):
    net = signed_network(tmp_path, modulation=modulation)
    try:
        net.disconnect("b")
        net.step(1700)  # exceed 15-second synchronization loss threshold
        assert net.nodes["b"].app.recovering
        net.reconnect("b")
        net.inject("b", b"after interruption\x00\xff")
        for _ in range(500):
            net.step()
            if net.received("a"):
                break
        assert net.received("a") == [b"after interruption\x00\xff"]
        assert not net.nodes["b"].app.recovering
    finally:
        net.close()


@pytest.mark.parametrize("modulation,parity", [("bpsk", 0), ("qpsk", 0), ("qpsk", 16)])
@pytest.mark.parametrize("rate", [125000, 250000, 500000])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
@pytest.mark.parametrize("offset", [-750, 0, 750])
def test_rate_payload_noise_offset_and_split_stream_matrix(rate, size, offset, modulation, parity):
    # Frozen Step 5 characterization: unit-amplitude rectangular TX, independent
    # Gaussian noise stddev .08 per I/Q sample; not calibrated RF SNR.
    # Preserve BPSK seed 5005; freeze QPSK seed 9009 before hardware qualification.
    config = load_config(f"configs/examples/peer-a-{modulation}.yaml")
    config = replace(
        config.sdr,
        samples_per_symbol=samples_per_symbol(modulation, 2_000_000, rate),
        rs_parity_bytes=parity,
    )
    tx, rx = make_modem(config), make_modem(config)
    rng = np.random.default_rng(9009 if modulation == "qpsk" else 5005)
    payload = rng.bytes(size)
    samples = tx.modulate(payload)
    samples *= np.exp(2j * np.pi * offset * np.arange(len(samples)) / config.sample_rate)
    samples += rng.normal(0, 0.08, len(samples)) + 1j * rng.normal(0, 0.08, len(samples))
    stream = np.concatenate(
        (np.zeros(config.samples_per_symbol - 1), samples, np.zeros(4096))
    ).astype(np.complex64)
    decoded = []
    cursor = 0
    while cursor < len(stream):
        width = int(rng.integers(1, 8193))
        decoded.extend(rx.feed(stream[cursor : cursor + width]))
        cursor += width
    assert decoded == [payload]


@pytest.mark.parametrize("modulation", ["msk", "gmsk", "qpsk"])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
def test_signed_binary_boundaries_and_invalid_local_datagrams(tmp_path, size, modulation):
    net = signed_network(tmp_path, modulation=modulation)
    try:
        forward = bytes(i % 256 for i in range(size))
        reverse = bytes(255 - i % 256 for i in range(size))
        for source, payload in (("a", forward), ("b", reverse)):
            net.inject(source, b"")
            net.inject(source, b"x" * 1025)
            net.inject(source, payload)
        net.step(300)
        assert net.received("b") == [forward]
        assert net.received("a") == [reverse]
        for peer in net.nodes.values():
            assert peer.app.statistics.snapshot()["invalid_payloads"] == 2
    finally:
        net.close()
