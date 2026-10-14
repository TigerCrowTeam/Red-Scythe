import json
import logging

from packet_radio.cli.status_cli import render_status_response
from packet_radio.config import StatusConfig
from packet_radio.statistics import LinkStatistics
from packet_radio.status import (
    StatusService,
    format_json,
    format_prometheus,
    format_text,
    render_endpoint,
)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def populated_statistics():
    statistics = LinkStatistics()
    statistics.increment("packets_transmitted", 4)
    statistics.increment("retries", 1)
    statistics.set_gauge("active_frequency_hz", 915e6)
    statistics.set_gauge("synchronized", True)
    statistics.set_gauge("radio_ready", True)
    statistics.mark_activity("peer")
    return statistics


def test_human_and_json_status_formatters():
    snapshot = populated_statistics().snapshot()
    text = format_text(snapshot)
    assert "frequency=915.000 MHz" in text
    assert "tx=4" in text
    assert json.loads(format_json(snapshot))["retries"] == 1


def test_prometheus_formatter_includes_node_and_link_metrics():
    output = format_prometheus(populated_statistics().snapshot(), "leader", 1)
    assert 'packet_radio_info{role="leader",node_id="1"} 1' in output
    assert "# TYPE packet_radio_packets_transmitted counter" in output
    assert "# TYPE packet_radio_synchronized gauge" in output
    assert "packet_radio_packets_transmitted 4" in output
    assert "packet_radio_synchronized 1" in output


def test_http_status_metrics_and_health_endpoint_rendering():
    statistics = populated_statistics()
    snapshot = statistics.snapshot()
    code, content_type, body = render_endpoint("/status", snapshot, "follower", 2)
    assert code == 200 and content_type == "application/json"
    assert json.loads(body)["active_frequency_hz"] == 915e6
    assert "packet_radio_retries 1" in render_endpoint("/metrics", snapshot, "follower", 2)[2]
    assert render_endpoint("/healthz", snapshot, "follower", 2)[0] == 200
    statistics.set_gauge("synchronized", False)
    assert render_endpoint("/healthz", statistics.snapshot(), "follower", 2)[0] == 503
    assert render_endpoint("/missing", snapshot, "follower", 2)[0] == 404


def test_periodic_status_service_uses_injected_clock(caplog):
    clock = Clock()
    service = StatusService(
        populated_statistics(),
        StatusConfig(output="json", interval_seconds=5, http_enabled=False),
        "follower",
        2,
        clock,
    )
    with caplog.at_level(logging.INFO):
        service.maybe_report()
        assert not caplog.records
        clock.now = 5
        service.maybe_report()
        assert '"packets_transmitted":4' in caplog.records[-1].message


def test_status_cli_renders_http_json_as_text_or_pretty_json():
    body = format_json(populated_statistics().snapshot())
    assert "frequency=915.000 MHz" in render_status_response(body, "text")
    assert json.loads(render_status_response(body, "json"))["packets_transmitted"] == 4


def test_synchronized_leader_without_peer_is_not_link_healthy():
    stats = LinkStatistics()
    stats.set_gauge("radio_ready", True)
    assert stats.snapshot()["synchronized"]
    assert render_endpoint("/healthz", stats.snapshot(), "leader", 1)[0] == 503
    stats.mark_activity("peer")
    assert render_endpoint("/healthz", stats.snapshot(), "leader", 1)[0] == 200
    stats.set_gauge("radio_ready", False)
    assert render_endpoint("/healthz", stats.snapshot(), "leader", 1)[0] == 503


def test_waveform_and_activity_are_visible_in_text_and_metrics():
    stats = populated_statistics()
    stats.set_gauge("modulation", "qpsk")
    stats.set_gauge("configured_bit_rate_bps", 500000)
    stats.increment("rx_overflows")
    text = format_text(stats.snapshot())
    assert "modulation=qpsk bit_rate=500000 bps" in text
    assert "peer_active=True delivering=False" in text
    assert "overflows=1" in text
    assert 'packet_radio_waveform_info{modulation="qpsk"} 1' in format_prometheus(
        stats.snapshot(), "leader", 1
    )
