"""Experimental B205 sweeps remain bounded and cannot weaken normal profile validation."""

from dataclasses import replace

import pytest

from packet_radio.config import load_config, validate_config
from packet_radio.runtime.bench_experiment import experiment_config


def baseline():
    return load_config("configs/v1_1/peer-a-b205-rf-hopping.yaml")


def settings(**overrides):
    return dict(hop_rate=4, guard=0.025, sync_lead=0.01, payload=128, **overrides)


def test_experiment_is_explicit_isolated_and_normal_validation_stays_strict():
    original = baseline()
    experiment = settings()
    actual = experiment_config(original, experiment)
    assert actual.frequency.interval_seconds == 0.25
    assert actual.endpoint.max_payload_bytes == 128
    assert actual.emulation["frequency"]["dwell_seconds"] == 0.25
    assert original.frequency.interval_seconds == 1
    assert original.emulation["frequency"]["dwell_seconds"] == 1
    assert original.endpoint.max_payload_bytes == 1024
    with pytest.raises(ValueError):
        validate_config(actual)
    assert experiment_config(original, None) is original


@pytest.mark.parametrize(
    "key,value",
    [
        ("hop_rate", float("nan")),
        ("hop_rate", 0),
        ("hop_rate", 31),
        ("guard", 0),
        ("guard", 0.2),
        ("sync_lead", 0.001),
        ("payload", 12),
        ("payload", 1025),
        ("payload", 128.0),
    ],
)
def test_invalid_experiment_is_rejected_before_hardware(key, value):
    experiment = settings()
    experiment[key] = value
    with pytest.raises(ValueError):
        experiment_config(baseline(), experiment)


def test_experiment_rejects_a_dwell_that_cannot_fit_control_and_data():
    experiment = settings()
    experiment.update(hop_rate=30, payload=1024)
    with pytest.raises(ValueError, match="cannot fit"):
        experiment_config(baseline(), experiment)


def test_experiment_requires_rf_b205_and_disabled_recording():
    with pytest.raises(ValueError, match="RF-synchronized B205"):
        experiment_config(load_config("configs/v1_1/peer-a-b205-two-host.yaml"), settings())
    cfg = baseline()
    with pytest.raises(ValueError, match="recording"):
        experiment_config(
            replace(cfg, recording=replace(cfg.recording, directory="capture")), settings()
        )
