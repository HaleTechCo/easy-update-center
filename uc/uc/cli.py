"""Command-line entry point: uc generate | doctor | rollback | status | schedule | pki (spec §5)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import publish, status
from .config import load_settings
from .errors import UcError
from .pki import DEFAULT_ORG, ROOT_CERT, ROOT_KEY, SIGNING_CERT, SIGNING_KEY, cert_info, init_pki, rotate_signing
from .timeutil import iso, utcnow

EXIT_LOCKED = 75


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uc", description="Self-hosted Jenkins update center")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="build, validate and publish a new release")
    generate.add_argument("--force", action="store_true", help="skip the plugin-count ratio check once")

    doctor = sub.add_parser("doctor", help="report what the generator will see in the source repository")
    doctor.add_argument("--fetch", action="store_true", help="download POMs and manifests missing from the cache")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")

    rollback = sub.add_parser("rollback", help="point live at an earlier release")
    rollback.add_argument("release", nargs="?", help="release id (default: the one before live)")

    sub.add_parser("status", help="print status.json")
    sub.add_parser("schedule", help="run generate on GENERATE_SCHEDULE (the scheduler service)")

    pki = sub.add_parser("pki", help="manage the signing PKI").add_subparsers(dest="pki_command", required=True)
    init = pki.add_parser("init", help="create a root CA and a signing certificate")
    init.add_argument("--out", type=Path, required=True)
    init.add_argument("--root-days", type=int, default=3650)
    init.add_argument("--signing-days", type=int, default=365)
    init.add_argument("--org", default=DEFAULT_ORG)
    rotate = pki.add_parser("rotate", help="issue a new signing certificate from the root in --dir/root/")
    rotate.add_argument("--dir", type=Path, required=True)
    rotate.add_argument("--days", type=int, default=365)
    rotate.add_argument("--org", default=DEFAULT_ORG)
    show = pki.add_parser("show", help="print subjects, expiry and fingerprints")
    show.add_argument("--dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except publish.LockBusy as exc:
        print(f"uc: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except UcError as exc:
        print(f"uc: error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "pki":
        return _pki(args)
    settings = load_settings()
    if args.command == "generate":
        from .generate import generate

        print(f"published release {generate(settings, force=args.force)}")
        return 0
    if args.command == "doctor":
        from .doctor import format_report, run_doctor
        from .preflight import load_credentials
        from .versions import VersionTool

        report = run_doctor(settings, load_credentials(settings), fetch=args.fetch,
                            versions=VersionTool.from_settings(settings))
        print(json.dumps(report.to_dict(), indent=2) if args.json else format_report(report, settings))
        return report.exit_code()
    if args.command == "rollback":
        from .validate import count_plugins

        with publish.RunLock(settings.data_dir):
            target = publish.rollback(settings.data_dir, args.release)
            live = publish.live_path(settings.data_dir)
            status.update_status(settings.data_dir, live_release=target,
                                 plugin_count=count_plugins(live / "current") if live else None)
        print(f"live release is now {target}")
        return 0
    if args.command == "status":
        print(json.dumps(status.read_status(settings.data_dir), indent=2, sort_keys=True))
        return 0
    if args.command == "schedule":
        from .schedule import run_schedule

        run_schedule(settings)
        return 0
    raise AssertionError(f"unhandled command {args.command}")


def _pki(args: argparse.Namespace) -> int:
    if args.pki_command == "init":
        init_pki(args.out, root_days=args.root_days, signing_days=args.signing_days, org=args.org)
        print(f"created {args.out / ROOT_CERT}: install it in $JENKINS_HOME/update-center-rootCAs/ on every controller")
        print(f"created {args.out / SIGNING_KEY} and {args.out / SIGNING_CERT}: provide them as secrets")
        print(f"move {args.out / 'root' / ROOT_KEY} to offline storage; only 'uc pki rotate' needs it")
    elif args.pki_command == "rotate":
        rotate_signing(args.dir / "root" / ROOT_KEY, args.dir / ROOT_CERT, args.dir, days=args.days, org=args.org)
        print(f"issued a new {args.dir / SIGNING_CERT}; the previous pair is kept as *.old")
    else:
        now = utcnow()
        for name in (ROOT_CERT, SIGNING_CERT):
            path = args.dir / name
            if path.is_file():
                info = cert_info(path)
                print(f"{name}: {info.subject}")
                print(f"  expires {iso(info.not_after)} ({info.days_left(now)} days)")
                print(f"  sha256 {info.fingerprint_sha256}")
    return 0
