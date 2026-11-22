"""chintu-ataria (also `chintu-desktop ataria`): set up, check and use the owner's profile.

  chintu-ataria init [--profile PATH]           ask every question and write ataria.json (re-run to edit)
  chintu-ataria doctor [--profile PATH]         check the profile, vault, agents and secret references
  chintu-ataria profile check [--profile PATH]
  chintu-ataria render TEMPLATE [--profile PATH] [--set NAME=VALUE ...]
  chintu-ataria leaks [ROOT] [--deny FILE]
  chintu-ataria merge [--date YYYY-MM-DD] [--slot morning|midday] [--no-outbox]   build today's board
  chintu-ataria month-review                    the month-in-review message, once per lane and month
  chintu-ataria serve [--port N]                the engine API and dashboard on loopback
"""

import argparse
import os
import sys
from pathlib import Path

from . import leaks, profile, setup, templates


def main(argv=None):
    parser = argparse.ArgumentParser(prog="chintu-ataria")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create or edit ataria.json by answering questions")
    init.add_argument("--profile")
    init.add_argument("--yes", action="store_true", help="save without the final confirmation")
    check_all = sub.add_parser("doctor", help="check the profile and what it depends on")
    check_all.add_argument("--profile")
    check = sub.add_parser("profile", help="check the owner's ataria.json")
    check.add_argument("action", choices=["check", "path"])
    check.add_argument("--profile")
    render = sub.add_parser("render", help="fill a template from the profile and print it")
    render.add_argument("template")
    render.add_argument("--profile")
    render.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="an upper-case runtime value such as ATARIA_ROOT")
    scan = sub.add_parser("leaks", help="find personal information in templates and code")
    scan.add_argument("root", nargs="?", default=str(Path(__file__).resolve().parents[2]))
    scan.add_argument("--deny", default=os.environ.get("ATARIA_DENY_FILE"))
    merge = sub.add_parser("merge", help="build the day's board from the lanes (no model call)")
    merge.add_argument("--profile")
    merge.add_argument("--date", help="YYYY-MM-DD (default today)")
    merge.add_argument("--slot", choices=["morning", "midday"])
    merge.add_argument("--no-outbox", action="store_true", help="write the board only, no messages")
    review = sub.add_parser("month-review", help="write the month-in-review message")
    review.add_argument("--profile")
    serve = sub.add_parser("serve", help="serve the engine API and the dashboard on loopback")
    serve.add_argument("--profile")
    serve.add_argument("--port", type=int)
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            return 0 if setup.init(args.profile, assume_yes=args.yes) is not None else 1
        if args.command == "doctor":
            return setup.doctor(args.profile)[0]
        if args.command == "profile":
            path = Path(args.profile) if args.profile else profile.default_path()
            if args.action == "path":
                print(path)
                return 0
            value = profile.load(path)
            print(f"ok: {path} ({len(value)} top-level keys)")
            return 0
        if args.command == "render":
            value = profile.load(args.profile)
            runtime = dict(item.split("=", 1) for item in args.set)
            sys.stdout.write(templates.render_file(args.template, value, runtime))
            return 0
        if args.command in ("merge", "month-review", "serve"):
            from . import board, server, vault
            path = Path(args.profile) if args.profile else profile.default_path()
            value = profile.load(path)
            v = vault.Vault(value, path)
            if args.command == "serve":
                server.serve(value, v, args.port)
                return 0
            merger = board.Merge(value, v)
            if args.command == "month-review":
                lanes = merger.month_review()
                print("month-review: " + (", ".join(lanes) if lanes else "nothing new"))
                return 0
            if args.date and not __import__("re").match(r"^\d{4}-\d{2}-\d{2}$", args.date):
                raise profile.ProfileError("--date must be YYYY-MM-DD")
            day = v.parse_dt(args.date) if args.date else None
            out, front = merger.run(day, args.slot, outbox=not args.no_outbox)
            print(f"merge: {v.logical(out)} · {front['summary']}")
            return 0
        found = leaks.scan_tree(args.root, leaks.deny_list(args.deny))
        for leak in found:
            print(f"{leak.path}:{leak.line}: {leak.kind}: {leak.text}")
        print(f"{len(found)} possible personal values found" if found else "ok: no personal values found")
        return 1 if found else 0
    except (profile.ProfileError, templates.TemplateError, OSError, EOFError, KeyboardInterrupt) as error:
        print(f"chintu-ataria: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
