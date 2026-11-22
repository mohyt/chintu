"""chintu-ataria: profile and template tools.

  chintu-ataria profile check [--profile PATH]
  chintu-ataria render TEMPLATE [--profile PATH] [--set NAME=VALUE ...]
  chintu-ataria leaks [ROOT] [--deny FILE]
"""

import argparse
import os
import sys
from pathlib import Path

from . import leaks, profile, templates


def main(argv=None):
    parser = argparse.ArgumentParser(prog="chintu-ataria")
    sub = parser.add_subparsers(dest="command", required=True)
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
    args = parser.parse_args(argv)

    try:
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
        found = leaks.scan_tree(args.root, leaks.deny_list(args.deny))
        for leak in found:
            print(f"{leak.path}:{leak.line}: {leak.kind}: {leak.text}")
        print(f"{len(found)} possible personal values found" if found else "ok: no personal values found")
        return 1 if found else 0
    except (profile.ProfileError, templates.TemplateError, OSError) as error:
        print(f"chintu-ataria: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
