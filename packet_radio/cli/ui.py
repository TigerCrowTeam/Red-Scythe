"""Launch the optional terminal editor, hardware preflight and owned radio controls."""

import argparse


def main():
    parser = argparse.ArgumentParser(
        description="Edit radio projects offline, check hardware and start/stop owned radios"
    )
    parser.add_argument(
        "config", nargs="?", help="schema-2 node YAML; omit for a new simple 2FSK draft"
    )
    parser.add_argument(
        "--import-legacy",
        action="store_true",
        help="explicitly convert a schema-1 source to a new draft; original stays read-only",
    )
    args = parser.parse_args()
    if args.import_legacy and not args.config:
        parser.error("--import-legacy requires a source config")
    try:
        from packet_radio.tui.app import RadioEditor
    except ModuleNotFoundError as exc:
        if exc.name != "textual":
            raise
        parser.exit(
            1,
            'Terminal UI requires the optional dependency: python -m pip install "packet-radio[ui]"\n',
        )
    from packet_radio.configuration.draft import RadioDraft
    from packet_radio.configuration.forms import FormSession
    from packet_radio.configuration.storage import import_legacy

    try:
        session = (
            FormSession(
                import_legacy(args.config) if args.import_legacy else RadioDraft.load(args.config)
            )
            if args.config
            else FormSession.new()
        )
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Cannot open radio project: {exc}\n")
    RadioEditor(session).run()


if __name__ == "__main__":
    main()
