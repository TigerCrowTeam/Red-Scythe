"""Cancellable, bounded UDP submission and reception for an owned running radio.

UDP submission is not proof of RF delivery. Receive sockets never reuse a port,
so an existing application listener remains the owner of its endpoint.
"""

from __future__ import annotations

import asyncio
import math
import socket

from packet_radio.protocol.registry import resolve_processing


def local_address(host):
    address = socket.gethostbyname(host)
    local = {"127.0.0.1", "0.0.0.0"}
    # Binding verifies the OS considers this a local address, without sending traffic.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind((address, 0))
    return "127.0.0.1" if address in local else address


def running_config(controller, index, run_id):
    if not controller.active or controller.state != "Running" or controller.run_id != run_id:
        raise ValueError("Radio stopped or changed; start this packet action again.")
    if not 0 <= index < len(controller.running_configs):
        raise ValueError("Select an owned running node.")
    return controller.running_configs[index]


async def submit(controller, index, template, count, interval, progress):
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("Count must be 1..10000.")
    if not math.isfinite(interval) or not 0.01 <= interval <= 3600:
        raise ValueError("Interval must be 0.01..3600 seconds.")
    run_id = controller.run_id
    config = running_config(controller, index, run_id)
    if config.operation == "receive-only":
        raise ValueError("Selected node is receive-only.")
    payload, _ = template.validate_radio(config)
    processing = resolve_processing(config)
    wire_size = processing.codec.payload_size(
        len(payload), authenticated=config.authentication.private_key_file is not None
    )
    airtime = processing.make_modem(config.sdr).sample_count(wire_size) / config.sdr.sample_rate
    link = (config.emulation or {}).get("link", {})
    minimum = max(
        0.01,
        airtime
        + max(link.get("minimum_gap_seconds", 0.002), link.get("minimum_interval_seconds", 0)),
    )
    if count > 1 and interval < minimum:
        raise ValueError(
            f"Interval must be at least {minimum:.6f} seconds for this packet's airtime and gap; retries and hopping can require longer."
        )
    if config.input_kind == "frame":
        payload = resolve_processing(config).codec.encode(payload)
    host = await asyncio.to_thread(local_address, config.endpoint.bind_host)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setblocking(False)
        for index_sent in range(count):
            running_config(controller, index, run_id)
            udp.sendto(payload, (host, config.endpoint.bind_port))
            progress(index_sent + 1)
            if index_sent + 1 < count:
                deadline = asyncio.get_running_loop().time() + interval
                while asyncio.get_running_loop().time() < deadline:
                    running_config(controller, index, run_id)
                    await asyncio.sleep(
                        min(0.1, max(0, deadline - asyncio.get_running_loop().time()))
                    )


async def receive(controller, index, template, report, raw=None):
    run_id = controller.run_id
    config = running_config(controller, index, run_id)
    if config.operation == "transmit-only":
        raise ValueError("Selected node is transmit-only.")
    template.validate_radio(config)
    host = await asyncio.to_thread(local_address, config.endpoint.destination_host)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind((host, config.endpoint.destination_port))
        udp.setblocking(False)
        while True:
            running_config(controller, index, run_id)
            try:
                data, source = udp.recvfrom(65535)
            except BlockingIOError:
                await asyncio.sleep(0.02)
                continue
            if raw is not None:
                raw(data)
            try:
                segments = template.inspect(data)
                text = "\n".join(f"{s.name} ({s.kind}): {s.value}" for s in segments)
            except ValueError as exc:
                text = f"Layout mismatch: {exc}"
            report(
                f"UDP {source[0]}:{source[1]} · {len(data)} bytes\n{text}\nHex: {data[:1024].hex(' ')}"
            )
            await asyncio.sleep(0.02)
