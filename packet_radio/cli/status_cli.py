"""Query the running radio status service.

main fetches status or metrics over HTTP; render_status_response formats JSON
snapshots for human-readable or JSON output.
"""

from __future__ import annotations

import argparse
import json
from urllib.error import URLError
from urllib.request import urlopen

from packet_radio.status import format_text


def render_status_response(body: str, output: str) -> str:
    snapshot = json.loads(body)
    if output == "json":
        return json.dumps(snapshot, indent=2, sort_keys=True)
    return format_text(snapshot)


def main() -> None:
    parser = argparse.ArgumentParser(description="Query a packet-radio status endpoint")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="status server base URL")
    parser.add_argument("--format", choices=("text", "json", "prometheus"), default="text")
    args = parser.parse_args()
    endpoint = "/metrics" if args.format == "prometheus" else "/status"
    try:
        with urlopen(args.url.rstrip("/") + endpoint, timeout=3) as response:
            body = response.read().decode("utf-8")
        print(
            body.rstrip()
            if args.format == "prometheus"
            else render_status_response(body, args.format)
        )
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"packet-radio-status: {exc}\n")


if __name__ == "__main__":
    main()
