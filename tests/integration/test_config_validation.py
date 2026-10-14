from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from packet_radio.config import (
    AuthenticationConfig,
    load_config,
    validate_config,
    validate_network,
)


def examples():
    return {name: load_config(f"configs/examples/peer-{name}-bpsk.yaml") for name in ("a", "b")}


@pytest.mark.parametrize("modulation", ["bpsk", "qpsk"])
def test_example_configs_form_a_consistent_network(modulation):
    validate_network(
        {
            name: load_config(f"configs/examples/peer-{name}-{modulation}.yaml")
            for name in ("a", "b")
        }
    )


def test_network_rejects_duplicate_ids_and_wrong_peers():
    configs = examples()
    configs["b"] = configs["a"]
    with pytest.raises(ValueError, match="duplicate"):
        validate_network(configs)
    configs = examples()
    configs["b"] = replace(configs["b"], peer_id=3, authentication=AuthenticationConfig())
    with pytest.raises(ValueError, match="peer IDs"):
        validate_network(configs)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda c: replace(c, frequency=replace(c.frequency, epoch=123)), "frequency schedule"),
        (lambda c: replace(c, sdr=replace(c.sdr, samples_per_symbol=16)), "waveform"),
        (lambda c: replace(c, sdr=replace(c.sdr, modulation="qpsk")), "waveform"),
        (lambda c: replace(c, timing_role="leader"), "timing leader"),
        (lambda c: replace(c, link=replace(c.link, tdd_period_seconds=1)), "timing"),
        (lambda c: replace(c, authentication=AuthenticationConfig()), "authentication enablement"),
    ],
)
def test_network_rejects_inconsistent_settings(mutate, message):
    configs = examples()
    configs["b"] = mutate(configs["b"])
    with pytest.raises(ValueError, match=message):
        validate_network(configs)


@pytest.mark.parametrize(
    "extra",
    [
        "role: aircraft",
        "unknown: 1",
        "endpoint: []",
        "link: {queue_size: 2.5}",
        "link: {hardware_timed_io: 'false'}",
        "frequency: {frequencies_hz: wrong, epoch: 0}",
        "authentication: {public_key_files: []}",
    ],
)
def test_bad_schema_fails_with_value_error(tmp_path, extra):
    path = tmp_path / "invalid.yaml"
    raw = yaml.safe_load(Path("configs/examples/peer-a-bpsk.yaml").read_text())
    raw.update(yaml.safe_load(extra))
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: replace(c, node_id=0), "reserved for broadcast"),
        (
            lambda c: replace(c, sdr=replace(c.sdr, bandwidth=c.sdr.sample_rate * 2)),
            "bandwidth cannot exceed",
        ),
        (
            lambda c: replace(c, frequency=replace(c.frequency, frequencies_hz=(915e6, 915e6))),
            "duplicates",
        ),
        (
            lambda c: replace(c, frequency=replace(c.frequency, guard_seconds=200)),
            "guard interval",
        ),
        (
            lambda c: replace(
                c, link=replace(c.link, backoff_min_seconds=2, backoff_max_seconds=1)
            ),
            "backoff",
        ),
        (
            lambda c: replace(c, sdr=replace(c.sdr, tx_gain=float("nan"))),
            "finite",
        ),
        (
            lambda c: replace(c, sdr=replace(c.sdr, modulation="8psk")),
            "modulation",
        ),
        (
            lambda c: replace(c, sdr=replace(c.sdr, modulation="qpsk", bit_repetitions=3)),
            "bit_repetitions",
        ),
        (
            lambda c: replace(c, link=replace(c.link, tdd_leader_bursts_per_slot=0)),
            "bursts_per_slot",
        ),
        (
            lambda c: replace(c, link=replace(c.link, tdd_inter_burst_seconds=-0.001)),
            "inter_burst",
        ),
    ],
)
def test_single_config_rejects_invalid_values(mutate, message):
    with pytest.raises(ValueError, match=message):
        validate_config(mutate(examples()["b"]))
