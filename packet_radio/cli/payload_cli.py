"""Send or receive local radio payloads over UDP.

main submits bytes from text, hexadecimal input or a file, or receives a bounded
number of datagrams and prints their contents without interpreting the payload.
"""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Send or receive local radio payloads over UDP")
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send")
    source = send.add_mutually_exclusive_group(required=True)
    source.add_argument("--hex", dest="hex_data")
    source.add_argument("--text")
    source.add_argument("--file", type=Path)
    receive = commands.add_parser("receive")
    receive.add_argument("--count", type=int, default=1)
    receive.add_argument("--timeout", type=float, default=10)
    for command, default_port in ((send, 50000), (receive, 50001)):
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            if args.command == "send":
                if args.hex_data is not None:
                    payload = bytes.fromhex(args.hex_data)
                elif args.text is not None:
                    payload = args.text.encode("utf-8")
                else:
                    # Read one excess byte so oversized files are rejected cheaply.
                    with args.file.open("rb") as stream:
                        payload = stream.read(1025)
                if not 1 <= len(payload) <= 1024:
                    raise ValueError("payload must contain 1..1024 bytes")
                udp.sendto(payload, (args.host, args.port))
                print(json.dumps({"submitted_bytes": len(payload)}))
            else:
                if args.count < 1 or not 0 < args.timeout <= 3600:
                    raise ValueError("count must be positive and timeout must be 0..3600 seconds")
                udp.bind((args.host, args.port))
                udp.settimeout(args.timeout)
                for _ in range(args.count):
                    payload, _address = udp.recvfrom(65535)
                    print(json.dumps({"bytes": len(payload), "hex": payload.hex()}), flush=True)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"payload: {exc}\n")


if __name__ == "__main__":
    main()
