"""Command-line entry point: uc generate | doctor | rollback | status | schedule | repo-adapter | pki (docs/reference/cli.md)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import configview, publish, status
from .config import apply_process_env, load_pki_settings, load_settings, load_web_settings
from .doctor import check_since, format_report, inspect_repository, record
from .errors import UcError
from .examples import example_env, example_toml
from .generate import generate
from .pki import ROOT_CERT, ROOT_KEY, SIGNING_CERT, SIGNING_KEY, cert_info, init_pki, rotate_signing
from .preflight import load_credentials
from .procure.clean import clean
from .procure.download import check_download_allowed, download
from .procure.local import stage_local
from .procure.plan import check_war_args
from .procure.push import push
from .repo_adapter.server import healthcheck, serve
from .schedule import run_schedule
from .settings import MODES
from .timeutil import iso, utcnow
from .validate import count_plugins
from .versions import VersionTool
from .webenv import web_env, write_web_env

if TYPE_CHECKING:
    from .config import Settings

EXIT_LOCKED = 75


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every uc subcommand."""
    parser = argparse.ArgumentParser(prog='uc', description='Self-hosted Jenkins update center')
    sub = parser.add_subparsers(dest='command', required=True)

    generate_cmd = sub.add_parser('generate', help='build, validate and publish a new release')
    generate_cmd.add_argument('--force', action='store_true', help='skip the plugin-count ratio check once')
    generate_cmd.add_argument('--wait', action='store_true', help='wait for a running generation or rollback instead of exiting 75 (the scheduler uses it)')

    doctor = sub.add_parser('doctor', help='report what the generator will see in the source repository')
    doctor.add_argument('--fetch', action=argparse.BooleanOptionalAction, help='download POMs and manifests missing from the cache (default: DOCTOR_FETCH, off)')
    doctor.add_argument('--json', action=argparse.BooleanOptionalAction, help='print the report as JSON (default: DOCTOR_JSON, off)')
    doctor.add_argument('--download-poms', action='store_true', help='stage the real .pom of every plugin whose POM is missing, derived or unusable')
    doctor.add_argument('--download-dependencies', action='store_true', help='stage missing required dependencies, transitively, each with its .pom')
    doctor.add_argument('--download-war', action='append', default=[], metavar='VERSION', help='stage jenkins-war VERSION, or with "required" every required core the repository lacks; repeatable')
    doctor.add_argument('--since', metavar='VERSION', help='floor for required cores (default: DOCTOR_SINCE, else the oldest core in the repository)')
    doctor.add_argument('--record', action='store_true', help='also save the report as the latest: /data/doctor/report.json, and its counts in status.json')
    doctor.add_argument('--staging', type=Path, default=Path('/procurement'), help='the staging folder (default: /procurement)')
    doctor.add_argument('--push', action='store_true', help='upload the files procurement.json lists to the repository, never overwriting')
    doctor.add_argument('--dry-run', action='store_true', help='with --push or --clean: check each file, change nothing')
    doctor.add_argument('--stage-local', action='store_true', help='record the files placed in the staging folder by hand, after the checks a download gets')
    doctor.add_argument('--fix', action='store_true', help='with --stage-local: first move each file to the Maven path it declares')
    doctor.add_argument('--clean', action='store_true', help='delete the staged files the repository already holds with the same content')

    rollback = sub.add_parser('rollback', help='point live at an earlier release')
    rollback.add_argument('release', nargs='?', help='release id (default: the one before live)')

    sub.add_parser('status', help='print status.json')
    config_cmd = sub.add_parser('config', help='show every setting, its value and where it came from')
    shows = config_cmd.add_mutually_exclusive_group()
    shows.add_argument('--json', action='store_true', help='print the settings as JSON')
    shows.add_argument('--check', action='store_true', help='only check the settings')
    shows.add_argument('--example', choices=('toml', 'env'), help='print an example settings file that lists every setting')
    shows.add_argument('--web-env', action='store_true', help="print the web container's settings, as the web-config service writes them")
    config_cmd.add_argument('--out', type=Path, help='with --web-env: write them to this file, atomically')
    config_cmd.add_argument('--mode', choices=MODES, help='with --example: the mode the example is for (default: online)')
    sub.add_parser('schedule', help='run generate on GENERATE_SCHEDULE (the scheduler service)')

    adapter = sub.add_parser('repo-adapter', help='serve the repository to the generator (the repo-adapter service)')
    adapter.add_argument('--port', type=int, help='port to listen on (default: REPO_ADAPTER_PORT, 8081)')
    adapter.add_argument('--healthcheck', action='store_true', help='do not serve: exit 0 when the running adapter answers (on --port, else the port it bound), 1 otherwise; reads no settings (the compose healthcheck)')

    pki = sub.add_parser('pki', help='manage the signing PKI').add_subparsers(dest='pki_command', required=True)
    init = pki.add_parser('init', help='create a root CA and a signing certificate')
    init.add_argument('--out', type=Path, required=True)
    init.add_argument('--root-days', type=int, help='lifetime of the root CA in days (default: PKI_ROOT_DAYS, 3650)')
    init.add_argument('--signing-days', type=int, help='lifetime of the signing certificate in days (default: PKI_SIGNING_DAYS, 365)')
    init.add_argument('--org', help='organization in the subjects (default: PKI_ORG)')
    rotate = pki.add_parser('rotate', help='issue a new signing certificate from the root in --dir/root/')
    rotate.add_argument('--dir', type=Path, required=True)
    rotate.add_argument('--days', type=int, help='lifetime of the new signing certificate in days (default: PKI_SIGNING_DAYS, 365)')
    rotate.add_argument('--org', help='organization in the subject (default: PKI_ORG)')
    show = pki.add_parser('show', help='print subjects, expiry and fingerprints')
    show.add_argument('--dir', type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the uc command line and return its exit status (EXIT_LOCKED when another run holds the lock)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == 'doctor':
        _check_doctor_args(parser, args)
    if args.command == 'config':
        _check_config_args(parser, args)
    try:
        return _dispatch(args)
    except publish.LockBusyError as exc:
        print(f'uc: {exc}', file=sys.stderr)
        return EXIT_LOCKED
    except UcError as exc:
        print(f'uc: error: {exc}', file=sys.stderr)
        return 1


def _check_config_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.out is not None and not args.web_env:
        parser.error('--out only applies to --web-env')
    if args.mode is not None and args.example is None:
        parser.error('--mode only applies to --example')


def _check_doctor_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    downloading = args.download_poms or args.download_dependencies or args.download_war
    if args.push and (downloading or args.stage_local or args.clean or args.fetch or args.json or args.since):
        parser.error('--push runs on its own: review the staged files between downloading and pushing')
    if args.stage_local and (downloading or args.clean or args.fetch or args.json or args.since):
        parser.error('--stage-local runs on its own: it records files already in the staging folder')
    if args.clean and (downloading or args.fetch or args.json or args.since):
        parser.error('--clean runs on its own: it compares the staging folder with the repository')
    if args.dry_run and not (args.push or args.clean):
        parser.error('--dry-run only applies to --push and --clean')
    if args.fix and not args.stage_local:
        parser.error('--fix only applies to --stage-local')
    if args.record and (args.push or args.stage_local or args.clean):
        parser.error('--record only applies to a report run')


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == 'pki':
        return _pki(args)
    if args.command == 'config':
        return _config(args)
    if args.command == 'repo-adapter' and args.healthcheck:
        healthcheck(args.port)  # reads no settings: health is the running server's, not the files'
        return 0
    settings = load_settings()
    exported = apply_process_env(settings)
    if args.command == 'generate':
        print(f'published release {generate(settings, force=args.force, wait=args.wait)}')
        return 0
    if args.command == 'doctor':
        return _doctor(args, settings)
    if args.command == 'rollback':
        with publish.RunLock(settings.data_dir):
            target = publish.rollback(settings.data_dir, args.release)
            live = publish.live_path(settings.data_dir)
            status.update_status(settings.data_dir, live_release=target, plugin_count=count_plugins(live / 'current') if live else None)
        print(f'live release is now {target}')
        return 0
    if args.command == 'status':
        print(json.dumps(status.read_status(settings.data_dir), indent=2, sort_keys=True))
        return 0
    if args.command == 'schedule':
        run_schedule(settings, drop_env=exported)
        return 0
    if args.command == 'repo-adapter':
        serve(settings, port=settings.repo_adapter_port if args.port is None else args.port)
        return 0
    msg = f'unhandled command {args.command}'
    raise AssertionError(msg)


def doctor_options(args: argparse.Namespace, settings: Settings) -> tuple[bool, bool, str | None]:
    """Return fetch, json and since for a doctor run: each option given on the command line, else its setting."""
    fetch = settings.doctor_fetch if args.fetch is None else args.fetch
    as_json = settings.doctor_json if args.json is None else args.json
    since = args.since if args.since is not None else (settings.doctor_since or None)
    return fetch, as_json, since


def _doctor(args: argparse.Namespace, settings: Settings) -> int:
    if args.push:
        return push(settings, args.staging, dry_run=args.dry_run)
    if args.stage_local:
        return stage_local(args.staging, settings.procurement_source_url, fix=args.fix, out=sys.stdout)
    if args.clean:
        return clean(settings, args.staging, dry_run=args.dry_run, out=sys.stdout)
    fetch, as_json, since = doctor_options(args, settings)
    downloading = args.download_poms or args.download_dependencies or bool(args.download_war)
    check_since(since)
    if downloading:
        check_download_allowed(settings)
        check_war_args(args.download_war)
    versions = VersionTool.from_settings(settings)
    inspection = inspect_repository(settings, load_credentials(settings), fetch=fetch, versions=versions, since=since)
    report = inspection.report
    print(json.dumps(report.to_dict(), indent=2) if as_json else format_report(report, settings))
    out = sys.stderr if as_json else sys.stdout  # keep stdout valid JSON
    if args.record:
        print(f'saved the report as {record(report, settings.data_dir, utcnow())}', file=out)
    if not downloading:
        return report.exit_code()
    return download(inspection, settings, poms=args.download_poms, dependencies=args.download_dependencies, wars=args.download_war, staging_dir=args.staging, versions=versions, out=out)


def _config(args: argparse.Namespace) -> int:
    if args.example:
        mode = args.mode or 'online'
        print(example_toml(mode) if args.example == 'toml' else example_env(mode), end='')
        return 0
    if args.web_env:
        text = web_env(load_web_settings())
        if args.out is not None:
            write_web_env(text, args.out)
            print(f'wrote {args.out}:')
        print(text, end='')
        return 0
    return configview.run(as_json=args.json, check=args.check)


def _pki(args: argparse.Namespace) -> int:
    if args.pki_command in {'init', 'rotate'}:
        defaults = load_pki_settings()
        org = args.org or defaults.org
    if args.pki_command == 'init':
        root_days = defaults.root_days if args.root_days is None else args.root_days
        signing_days = defaults.signing_days if args.signing_days is None else args.signing_days
        init_pki(args.out, root_days=root_days, signing_days=signing_days, org=org)
        print(f'created {args.out / ROOT_CERT}: install it in $JENKINS_HOME/update-center-rootCAs/ on every controller')
        print(f'created {args.out / SIGNING_KEY} and {args.out / SIGNING_CERT}: provide them as secrets')
        print(f"move {args.out / 'root' / ROOT_KEY} to offline storage; only 'uc pki rotate' needs it")
    elif args.pki_command == 'rotate':
        days = defaults.signing_days if args.days is None else args.days
        rotate_signing(args.dir / 'root' / ROOT_KEY, args.dir / ROOT_CERT, args.dir, days=days, org=org)
        print(f'issued a new {args.dir / SIGNING_CERT}; the previous pair is kept as *.old')
    else:
        now = utcnow()
        for name in (ROOT_CERT, SIGNING_CERT):
            path = args.dir / name
            if path.is_file():
                info = cert_info(path)
                print(f'{name}: {info.subject}')
                print(f'  expires {iso(info.not_after)} ({info.days_left(now)} days)')
                print(f'  sha256 {info.fingerprint_sha256}')
    return 0
