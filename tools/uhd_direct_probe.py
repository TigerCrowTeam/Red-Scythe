"""Conducted two-USRP tone probe; imports no packet-radio runtime code.

Run with the system Python matching UHD. Requires two cross-connected radios
with suitable attenuation; this script actively transmits when invoked.
"""

import argparse
import json
import threading
import time
from pathlib import Path

import numpy as np
import uhd


def probe(tx, rx, rx_port, rate, tx_gain, rx_gain):
    rx.set_rx_antenna(rx_port, 0)
    rx.set_rx_gain(rx_gain, 0)
    tx.set_tx_gain(tx_gain, 0)
    args = uhd.usrp.StreamArgs("fc32", "sc16")
    args.channels = [0]
    receive = rx.get_rx_stream(args)
    transmit = tx.get_tx_stream(args)
    buffer = np.empty((1, 4096), np.complex64)
    metadata = uhd.types.RXMetadata()
    # Initialize binding/NumPy conversion before concurrent send and recv.
    receive.recv(buffer, metadata, 0.0)
    start = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    start.stream_now = True
    receive.issue_stream_cmd(start)
    state = {"sent": 0, "errors": [], "async_events": []}
    rows = []
    thread = None

    def send():
        # Integer cycles per block maintain a continuous 100 kHz tone.
        tone = (
            (0.25 * np.exp(2j * np.pi * 100000 * np.arange(10000) / rate))
            .astype(np.complex64)
            .reshape(1, -1)
        )
        md = uhd.types.TXMetadata()
        md.start_of_burst = True
        try:
            while state["sent"] < int(rate * 0.3):
                n = min(tone.size, int(rate * 0.3) - state["sent"])
                sent = transmit.send(tone[:, :n], md, 0.5)
                if not sent:
                    raise RuntimeError("TX timeout")
                state["sent"] += int(sent)
                md.start_of_burst = False
            md.end_of_burst = True
            transmit.send(np.zeros((1, 1), np.complex64), md, 0.5)
            amd = uhd.types.TXAsyncMetadata()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if transmit.recv_async_msg(amd, 0.1):
                    state["async_events"].append(str(amd.event_code))
                    if amd.event_code == uhd.types.TXMetadataEventCode.burst_ack:
                        break
        except Exception as exc:
            state["errors"].append(repr(exc))

    begun = time.monotonic()
    try:
        while time.monotonic() - begun < 1.0:
            elapsed = time.monotonic() - begun
            if elapsed > 0.2 and thread is None:
                thread = threading.Thread(target=send, daemon=True)
                thread.start()
            n = receive.recv(buffer, metadata, 0.1)
            if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
                state["errors"].append(metadata.strerror())
                continue
            if n:
                samples = buffer[0, :n]
                spectrum = abs(np.fft.fft(samples * np.hanning(n))) ** 2
                frequencies = np.fft.fftfreq(n, 1 / rate)
                selection = (frequencies > 75000) & (frequencies < 125000)
                peak = np.flatnonzero(selection)[np.argmax(spectrum[selection])]
                rows.append(
                    {
                        "seconds": elapsed,
                        "power": float(np.mean(abs(samples) ** 2)),
                        "peak_hz": float(frequencies[peak]),
                        "tone_snr_db": float(
                            10
                            * np.log10(
                                max(spectrum[peak], 1e-30) / max(float(np.median(spectrum)), 1e-30)
                            )
                        ),
                    }
                )
    finally:
        if thread:
            thread.join(2)
        stop = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        receive.issue_stream_cmd(stop)
    before = [r for r in rows if r["seconds"] < 0.15]
    during = [r for r in rows if 0.25 < r["seconds"] < 0.45]
    return {
        "rx_port": rx_port,
        "rx_gain_db": rx.get_rx_gain(0),
        "tx_gain_db": tx.get_tx_gain(0),
        "tx": state,
        "thread_stopped": thread is None or not thread.is_alive(),
        "idle_power": float(np.median([r["power"] for r in before])) if before else None,
        "transmit_power_at_receiver": float(np.median([r["power"] for r in during]))
        if during
        else None,
        "strongest_tone": max(rows, key=lambda r: r["tone_snr_db"]) if rows else None,
        "blocks": len(rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serials", nargs=2, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tx-gain", type=float, default=0)
    parser.add_argument("--rx-gain", type=float, default=40)
    parser.add_argument("--rx-port", default="RX2", choices=["RX2", "TX/RX"])
    args = parser.parse_args()
    devices = []
    report = {
        "serials": args.serials,
        "sample_rate": 1000000,
        "center_frequency_hz": 915000000,
        "tone_offset_hz": 100000,
        "amplitude": 0.25,
        "results": [],
    }
    try:
        for serial in args.serials:
            device = uhd.usrp.MultiUSRP(f"type=b200,serial={serial}")
            for direction in ("rx", "tx"):
                getattr(device, f"set_{direction}_rate")(1e6, 0)
                getattr(device, f"set_{direction}_bandwidth")(1e6, 0)
                getattr(device, f"set_{direction}_freq")(uhd.types.TuneRequest(915e6), 0)
            device.set_tx_antenna("TX/RX", 0)
            devices.append(device)
        for source in (0, 1):
            result = probe(
                devices[source], devices[1 - source], args.rx_port, 1e6, args.tx_gain, args.rx_gain
            )
            result.update(source=args.serials[source], destination=args.serials[1 - source])
            report["results"].append(result)
            print(json.dumps(result), flush=True)
    finally:
        devices.clear()
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
