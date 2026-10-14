"""Real loopback HTTP tests; no SDR or fixed listening ports required."""

import json
from http.client import HTTPConnection

import pytest

from packet_radio.cli import status_cli
from packet_radio.config import StatusConfig
from packet_radio.status import StatusHTTPServer, StatusService
from tests.integration.test_cli import invoke
from tests.integration.test_status import populated_statistics


@pytest.fixture
def service():
    service = StatusService(populated_statistics(), StatusConfig(http_port=0), "leader", 1)
    service.start()
    try:
        yield service
    finally:
        service.close()
    assert not service.http.thread.is_alive()
    assert service.http.server.fileno() == -1


def request(service, path):
    connection = HTTPConnection("127.0.0.1", service.http.port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        assert int(response.getheader("Content-Length")) == len(body)
        return response.status, response.getheader("Content-Type"), body.decode()
    finally:
        connection.close()


def test_live_status_server_exposes_current_statistics(service):
    code, kind, body = request(service, "/status")
    assert code == 200 and kind == "application/json"
    assert json.loads(body)["packets_transmitted"] == 4
    service.statistics.increment("packets_transmitted")
    assert json.loads(request(service, "/status")[2])["packets_transmitted"] == 5
    code, kind, body = request(service, "/metrics")
    assert code == 200 and kind.startswith("text/plain")
    assert "packet_radio_packets_transmitted 5" in body
    assert request(service, "/healthz")[0] == 200
    service.statistics.set_gauge("synchronized", False)
    code, _, body = request(service, "/healthz")
    assert code == 503 and json.loads(body) == {"ok": False}
    assert request(service, "/missing")[0] == 404


@pytest.mark.parametrize("output", ["text", "json", "prometheus"])
def test_status_cli_queries_live_server(service, monkeypatch, capsys, output):
    invoke(
        monkeypatch,
        status_cli,
        "--url",
        f"http://127.0.0.1:{service.http.port}/",
        "--format",
        output,
    )
    body = capsys.readouterr().out
    if output == "json":
        assert json.loads(body)["retries"] == 1
    elif output == "text":
        assert "tx=4" in body
    else:
        assert "packet_radio_packets_transmitted 4" in body


def test_status_cli_reports_unreachable_server(monkeypatch, capsys):
    server = StatusHTTPServer(populated_statistics(), "leader", 1, "127.0.0.1", 0)
    port = server.port
    server.start()
    server.close()
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, status_cli, "--url", f"http://127.0.0.1:{port}")
    assert error.value.code == 1
    assert "packet-radio-status:" in capsys.readouterr().err


def test_close_before_start_does_not_hang_or_leave_port_open():
    import threading

    server = StatusHTTPServer(populated_statistics(), "leader", 1, "127.0.0.1", 0)
    closer = threading.Thread(target=server.close, daemon=True)
    closer.start()
    closer.join(1)
    hung = closer.is_alive()
    if hung:
        # Release the old shutdown-before-serve deadlock so a regression cannot
        # strand the test process or leave a listening socket behind.
        server.start()
        closer.join(2)
    assert not hung, "closing a server that never started blocked"
    assert server.server.fileno() == -1
    server.close()
