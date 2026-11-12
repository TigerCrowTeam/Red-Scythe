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
    parser.add_argument(
        "--offline", action="store_true", help="skip automatic SDR discovery for a new project"
    )
    parser.add_argument(
        "--node-role",
        choices=("a", "b"),
        help="this computer's role for a new B205 setup; choose the other role on the other computer",
    )
    args = parser.parse_args()
    if args.import_legacy and not args.config:
        parser.error("--import-legacy requires a source config")
    if args.node_role and (args.config or args.offline):
        parser.error("--node-role applies only to a new automatically detected setup")
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
    RadioEditor(
        session, auto_detect=not args.config and not args.offline, node_role=args.node_role
    ).run()


if __name__ == "__main__":
    main()
