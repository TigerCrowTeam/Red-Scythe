from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

from packet_radio.config import load_config, validate_config
from packet_radio.modems.registry import make_modem
from packet_radio.protocol.framing import RadioPacket


def profile(tmp_path, mutate):
    raw = yaml.safe_load(Path("configs/examples/peer-a-bpsk.yaml").read_text())
    mutate(raw)
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(raw))
    return load_config(path)


@pytest.mark.parametrize("modulation,bits", [("bpsk", 1), ("qpsk", 2)])
@pytest.mark.parametrize("bit_rate", [125000, 250000, 500000])
def test_rates_derive_symbol_timing_and_round_trip(tmp_path, modulation, bits, bit_rate):
    config = profile(
        tmp_path, lambda r: r["modem"].update(modulation=modulation, bit_rate_bps=bit_rate)
    )
    assert config.sdr.samples_per_symbol == 2_000_000 * bits // bit_rate
    assert config.sdr.bit_rate_bps == bit_rate
    assert config.center_frequency_hz == 915e6
    tx = make_modem(config.sdr)
    rx = make_modem(config.sdr)
    data = bytes(range(64))
    samples = tx.modulate(data)
    assert len(samples) == tx.sample_count(len(data))
    decoded = []
    for block in np.array_split(samples, 13):
        decoded.extend(rx.feed(block))
    assert decoded == [data]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("schema_version"),
        lambda r: r.update(schema_version=True),
        lambda r: r.update(schema_version=2),
        lambda r: r["radio"].pop("center_frequency_hz"),
        lambda r: r["radio"].update(center_frequency_hz=-1),
        lambda r: r["modem"].update(bit_rate_bps=300001),
        lambda r: r["modem"].update(bit_rate_bps=0),
        lambda r: r["modem"].update(bit_rate_bps=True),
        lambda r: r["modem"].update(modulation="2fsk"),
        lambda r: r["modem"].update(samples_per_symbol=8),
        lambda r: (
            r["modem"].update(modulation="qpsk") or r["error_correction"].update(bit_repetitions=2)
        ),
        lambda r: r["packet"].update(format="arbitrary"),
        lambda r: r["packet"].update(max_payload_bytes=1025),
        lambda r: r["error_correction"].update(scheme="none"),
        lambda r: r["error_correction"].update(rs_parity_bytes=256),
        lambda r: r["endpoint"].update(max_payload_bytes=64),
    ],
)
def test_bad_profile_rejected_before_hardware(tmp_path, mutate):
    with pytest.raises(ValueError):
        profile(tmp_path, mutate)


def test_maximum_signed_rs_packet_must_fit_tdd_window(tmp_path):
    config = profile(tmp_path, lambda r: r["error_correction"].update(rs_parity_bytes=32))
    modem = make_modem(config.sdr)
    wire_size = RadioPacket(1, 2, 0, bytes(1024)).encoded_size(authenticated=True)
    airtime = modem.sample_count(wire_size) / config.sdr.sample_rate
    # The actual signed burst, including parity on every RS block, cannot fit.
    invalid = replace(config, link=replace(config.link, tdd_period_seconds=airtime * 1.9))
    with pytest.raises(ValueError, match="cannot fit"):
        validate_config(invalid)
    validate_config(
        replace(config, link=replace(config.link, tdd_period_seconds=(airtime + 0.01) * 2))
    )


def test_queue_airtime_budget_cannot_reject_every_maximum_packet(tmp_path):
    with pytest.raises(ValueError, match="queue airtime"):
        profile(tmp_path, lambda r: r["link"].update(queue_airtime_seconds=0.001))
