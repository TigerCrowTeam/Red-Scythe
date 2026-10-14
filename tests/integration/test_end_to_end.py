from dataclasses import replace

import pytest

from packet_radio.config import FrequencyConfig, LinkConfig, RadioConfig, SDRConfig
from packet_radio.simulation import SimulatedNetwork


def node(timing_role, node_id):
    return RadioConfig(
        node_id=node_id,
        peer_id=2 if node_id == 1 else 1,
        timing_role=timing_role,
        sdr=SDRConfig(samples_per_symbol=4),
        frequency=FrequencyConfig(
            (905e6, 915e6, 925e6),
            1_799_999_970,
            guard_seconds=0,
            reacquire_dwell_seconds=0.01,
            sync_loss_seconds=5,
        ),
        link=LinkConfig(
            ack_timeout_seconds=0.1, max_retries=4, backoff_min_seconds=0, backoff_max_seconds=0
        ),
    )


def network():
    result = SimulatedNetwork({"a": node("leader", 1), "b": node("follower", 2)})
    result.step(4)
    return result


@pytest.mark.parametrize("modulation", ["bpsk", "qpsk"])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
def test_binary_packets_round_trip_in_both_directions(modulation, size):
    configs = {"a": node("leader", 1), "b": node("follower", 2)}
    configs = {
        name: replace(c, sdr=replace(c.sdr, modulation=modulation)) for name, c in configs.items()
    }
    net = SimulatedNetwork(configs)
    try:
        net.step(4)
        first = bytes(i % 256 for i in range(size))
        second = bytes(255 - i % 256 for i in range(size))
        net.inject("a", first)
        net.inject("b", second)
        net.step(250)
        assert net.received("b") == [first]
        assert net.received("a") == [second]
    finally:
        net.close()


def test_empty_and_oversized_packets_do_not_stop_polling_or_get_truncated():
    net = network()
    try:
        for payload in (b"", b"x" * 1025, b"valid\x00"):
            net.inject("a", payload)
        net.step(100)
        assert net.received("b") == [b"valid\x00"]
        assert net.nodes["a"].app.statistics.snapshot()["invalid_payloads"] == 2
    finally:
        net.close()


def test_same_contents_are_independent_packets_in_fifo_order():
    net = network()
    try:
        for data in (b"same", b"same", b"last"):
            net.inject("a", data)
        net.step(150)
        assert net.received("b") == [b"same", b"same", b"last"]
    finally:
        net.close()


def test_duplicate_node_ids_are_rejected():
    with pytest.raises(ValueError):
        SimulatedNetwork({"a": node("leader", 1), "b": node("follower", 1)})


def test_clean_channel_delivers_thousand_packets_each_direction():
    net = network()
    try:
        expected_a, expected_b = [], []
        for index in range(1000):
            forward = index.to_bytes(4, "big") + bytes(range(60))
            reverse = index.to_bytes(4, "big") + bytes(range(60, 120))
            expected_b.append(forward)
            expected_a.append(reverse)
            net.inject("a", forward)
            net.inject("b", reverse)
            net.step(25)
        net.step(100)
        assert net.received("a") == expected_a
        assert net.received("b") == expected_b
        for peer in net.nodes.values():
            assert peer.app.statistics.snapshot()["queue_drops"] == 0
            assert peer.app.statistics.snapshot()["retry_exhausted"] == 0
    finally:
        net.close()
