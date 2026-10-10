"""Command-line entry point: uc generate | doctor | procure | rollback | status | schedule | repo-adapter | pki (docs/reference/cli.md)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import configview, publish, status
from .config import check_staging_outside_data_dir, load_settings
from .doctor import check_since, format_report, inspect_repository, record
from .errors import UcError, UsageError
from .examples import example_env
from .generate import generate
from .pki import DEFAULT_ORG, ROOT_CERT, ROOT_DAYS, ROOT_KEY, SIGNING_CERT, SIGNING_DAYS, SIGNING_KEY, cert_info, init_pki, rotate_signing
from .preflight import check_data_dir, load_credentials
from .procure.clean import clean
from .procure.download import check_download_allowed, download, downloads_allowed
from .procure.local import stage_local
from .procure.plan import check_war_args, suggest_stage
from .procure.push import push
from .repo_adapter.server import PORT, healthcheck, serve
from .schedule import run_schedule
from .settings import MODES
from .timeutil import iso, utcnow
from .validate import count_plugins
from .version import uc_version
from .versions import VersionTool

if TYPE_CHECKING:
    from .config import Settings

EXIT_LOCKED = 75
WRITES_DATA = frozenset({'generate', 'rollback', 'schedule', 'repo-adapter'})
"""The commands that write to the data volume, which uc checks it can write first (preflight.check_data_dir)."""

NOTHING_TO_STAGE = 'nothing to stage: choose --poms, --dependencies or --war VERSION|required'


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every uc subcommand."""
    parser = argparse.ArgumentParser(prog='uc', description='Easy Update Center for Jenkins: a self-hosted update center')
    parser.add_argument('--version', action='version', version=f'uc {uc_version()}', help='print the release this image was built as (dev for a build from source) and exit')
    sub = parser.add_subparsers(dest='command', required=True)

    generate_cmd = sub.add_parser('generate', help='build, validate and publish a new release')
    generate_cmd.add_argument('--force', action='store_true', help='skip the plugin-count ratio check once')
    generate_cmd.add_argument('--wait', action='store_true', help='wait for a running generation or rollback instead of exiting 75 (the scheduler uses it)')

    doctor = sub.add_parser('doctor', help='report what the generator will see in the source repository')
    doctor.add_argument('--fetch', action=argparse.BooleanOptionalAction, help='download POMs and manifests missing from the cache (default: DOCTOR_FETCH, off)')
    doctor.add_argument('--json', action='store_true', help='print the report as JSON')
    doctor.add_argument('--since', metavar='VERSION', help='floor for required cores (default: DOCTOR_SINCE, else the oldest core in the repository)')
    doctor.add_argument('--record', action='store_true', help='also save the report as the latest: /data/doctor/report.json, and its counts in status.json')

    procure = sub.add_parser('procure', help='stage, record, push and clean the files the repository lacks (docs/how-to/procure-missing-files.md)').add_subparsers(dest='procure_command', required=True)
    stage = procure.add_parser('stage', help="print uc doctor's report, then stage what it finds missing for review")
    stage.add_argument('--poms', action='store_true', help='stage the real .pom of every plugin whose POM is missing, derived or unusable')
    stage.add_argument('--dependencies', action='store_true', help='stage missing required dependencies, transitively, each with its .pom')
    stage.add_argument('--war', action='append', default=[], metavar='VERSION', help='stage jenkins-war VERSION, or with "required" every required core the repository lacks; repeatable')
    stage.add_argument('--since', metavar='VERSION', help='floor for required cores (default: DOCTOR_SINCE, else the oldest core in the repository)')
    stage.add_argument('--fetch', action=argparse.BooleanOptionalAction, default=True, help='download POMs and manifests missing from the cache first (default: on)')
    stage.add_argument('--staging', type=Path, help='the staging folder (default: /procurement)')
    record_cmd = procure.add_parser('record', help='record the files placed in the staging folder by hand, after the checks a download gets')
    record_cmd.add_argument('--fix', action='store_true', help='first move each file to the Maven path it declares')
    record_cmd.add_argument('--staging', type=Path, help='the staging folder (default: /procurement)')
    push_cmd = procure.add_parser('push', help='upload the files procurement.json lists to the repository, never overwriting')
    push_cmd.add_argument('--dry-run', action='store_true', help='check each file, change nothing')
    push_cmd.add_argument('--staging', type=Path, help='the staging folder (default: /procurement)')
    clean_cmd = procure.add_parser('clean', help='delete the staged files the repository already holds with the same content')
    clean_cmd.add_argument('--dry-run', action='store_true', help='check each file, change nothing')
    clean_cmd.add_argument('--staging', type=Path, help='the staging folder (default: /procurement)')

    rollback = sub.add_parser('rollback', help='point live at an earlier release')
    rollback.add_argument('release', nargs='?', help='release id (default: the one before live)')

    sub.add_parser('status', help='print status.json')
    config_cmd = sub.add_parser('config', help='show every setting, its value and where it came from')
    shows = config_cmd.add_mutually_exclusive_group()
    shows.add_argument('--json', action='store_true', help='print the settings as JSON')
    shows.add_argument('--check', action='store_true', help='only check the settings')
    shows.add_argument('--example', action='store_true', help='print an example .env that lists every setting')
    config_cmd.add_argument('--mode', choices=MODES, help='with --example: the mode the example is for (default: online)')
    sub.add_parser('schedule', help='run generate on GENERATE_SCHEDULE (the scheduler service)')

    adapter = sub.add_parser('repo-adapter', help='serve the repository to the generator (the repo-adapter service)')
    adapter.add_argument('--port', type=int, help=f'port to listen on (default: {PORT})')
    adapter.add_argument('--healthcheck', action='store_true', help='do not serve: exit 0 when the running adapter answers (on --port, else the port it bound), 1 otherwise; reads no settings (the compose healthcheck)')

    pki = sub.add_parser('pki', help='manage the signing PKI').add_subparsers(dest='pki_command', required=True)
    init = pki.add_parser('init', help='create a root CA and a signing certificate')
    init.add_argument('--out', type=Path, required=True)
    init.add_argument('--display-dir', type=Path, metavar='DIR', help='the directory --out is on the host, used in messages')
    init.add_argument('--root-days', type=int, default=ROOT_DAYS, help=f'lifetime of the root CA in days (default: {ROOT_DAYS})')
    init.add_argument('--signing-days', type=int, default=SIGNING_DAYS, help=f'lifetime of the signing certificate in days (default: {SIGNING_DAYS})')
    init.add_argument('--org', default=DEFAULT_ORG, help=f'organization in the subjects (default: {DEFAULT_ORG})')
    rotate = pki.add_parser('rotate', help='issue a new signing certificate from the root in --dir/root/')
    rotate.add_argument('--dir', type=Path, required=True)
    rotate.add_argument('--display-dir', type=Path, metavar='DIR', help='the directory --dir is on the host, used in messages')
    rotate.add_argument('--root', type=Path, metavar='DIR', help='the directory holding root-ca.key (default: root/ under --dir)')
    rotate.add_argument('--days', type=int, default=SIGNING_DAYS, help=f'lifetime of the new signing certificate in days (default: {SIGNING_DAYS})')
    rotate.add_argument('--org', default=DEFAULT_ORG, help=f'organization in the subject (default: {DEFAULT_ORG})')
    show = pki.add_parser('show', help='print subjects, expiry and fingerprints')
    show.add_argument('--dir', type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the uc command line and return its exit status (EXIT_LOCKED when another run holds the lock)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == 'config':
        _check_config_args(parser, args)
    try:
        return _dispatch(args)
    except publish.LockBusyError as exc:
        print(f'uc: {exc}', file=sys.stderr)
        return EXIT_LOCKED
    except UsageError as exc:
        print(f'uc: error: {exc}', file=sys.stderr)
        return 2
    except UcError as exc:
        print(f'uc: error: {exc}', file=sys.stderr)
        return 1


def _check_config_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.mode is not None and not args.example:
        parser.error('--mode only applies to --example')


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == 'pki':
        return _pki(args)
    if args.command == 'config':
        return _config(args)
    if args.command == 'repo-adapter' and args.healthcheck:
        healthcheck(args.port)  # reads no settings: health is the running server's, not the files'
        return 0
    settings = load_settings()
    if args.command in WRITES_DATA or (args.command == 'doctor' and args.record):
        check_data_dir(settings.data_dir)
    if args.command == 'generate':
        print(f'published release {generate(settings, force=args.force, wait=args.wait)}')
        return 0
    if args.command == 'doctor':
        return _doctor(args, settings)
    if args.command == 'procure':
        return _procure(args, settings)
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
        run_schedule(settings)
        return 0
    if args.command == 'repo-adapter':
        serve(settings, port=PORT if args.port is None else args.port)
        return 0
    msg = f'unhandled command {args.command}'
    raise AssertionError(msg)


def doctor_options(args: argparse.Namespace, settings: Settings) -> tuple[bool, bool, str | None]:
    """Return fetch, json and since for a doctor run: --fetch/--no-fetch else DOCTOR_FETCH, --json, --since else DOCTOR_SINCE."""
    fetch = settings.doctor_fetch if args.fetch is None else args.fetch
    since = args.since if args.since is not None else (settings.doctor_since or None)
    return fetch, args.json, since


def _doctor(args: argparse.Namespace, settings: Settings) -> int:
    fetch, as_json, since = doctor_options(args, settings)
    check_since(since)
    inspection = inspect_repository(settings, load_credentials(settings), fetch=fetch, versions=VersionTool.from_settings(settings), since=since)
    report = inspection.report
    print(json.dumps(report.to_dict(), indent=2) if as_json else format_report(report, settings))
    out = sys.stderr if as_json else sys.stdout  # keep stdout valid JSON
    if args.record:
        print(f'saved the report as {record(report, settings.data_dir, utcnow())}', file=out)
    if (line := suggest_stage(report, downloads=downloads_allowed(settings), since=args.since, staging=settings.procurement_staging_dir)) is not None:
        print(line, file=out)
    return report.exit_code()


@dataclass(frozen=True)
class StageOptions:
    """What uc procure stage does, from its command line (and DOCTOR_SINCE for --since)."""

    fetch: bool
    poms: bool
    dependencies: bool
    wars: tuple[str, ...]
    since: str | None
    staging: Path


def procure_options(args: argparse.Namespace, settings: Settings) -> StageOptions:
    """Return uc procure stage's options."""
    return StageOptions(
        fetch=args.fetch,
        poms=args.poms,
        dependencies=args.dependencies,
        wars=tuple(args.war),
        since=args.since if args.since is not None else (settings.doctor_since or None),
        staging=args.staging or settings.procurement_staging_dir,
    )


def _procure(args: argparse.Namespace, settings: Settings) -> int:
    if args.staging is not None:
        check_staging_outside_data_dir(args.staging, settings.data_dir, '--staging')
    staging = getattr(args, 'staging', None) or settings.procurement_staging_dir
    if args.procure_command == 'push':
        return push(settings, staging, dry_run=args.dry_run)
    if args.procure_command == 'record':
        return stage_local(staging, settings.procurement_source_url, fix=args.fix, out=sys.stdout)
    if args.procure_command == 'clean':
        return clean(settings, staging, dry_run=args.dry_run, out=sys.stdout)
    options = procure_options(args, settings)
    if not (options.poms or options.dependencies or options.wars):
        raise UsageError(NOTHING_TO_STAGE)
    check_since(options.since)
    check_download_allowed(settings)
    check_war_args(options.wars)
    versions = VersionTool.from_settings(settings)
    inspection = inspect_repository(settings, load_credentials(settings), fetch=options.fetch, versions=versions, since=options.since)
    print(format_report(inspection.report, settings))
    return download(inspection, settings, poms=options.poms, dependencies=options.dependencies, wars=options.wars, staging_dir=options.staging, versions=versions, out=sys.stdout)


def _config(args: argparse.Namespace) -> int:
    if args.example:
        print(example_env(args.mode or 'online'), end='')
        return 0
    return configview.run(as_json=args.json, check=args.check)


def _pki(args: argparse.Namespace) -> int:
    if args.pki_command == 'init':
        init_pki(args.out, root_days=args.root_days, signing_days=args.signing_days, org=args.org, display_dir=args.display_dir)
        shown = args.display_dir or args.out
        print(f'created {shown / ROOT_CERT}: install it in $JENKINS_HOME/update-center-rootCAs/ on every controller')
        print(f'created {shown / SIGNING_KEY} and {shown / SIGNING_CERT}: provide them as secrets')
        print(f"move {shown / 'root' / ROOT_KEY} to offline storage; only 'uc pki rotate' needs it")
    elif args.pki_command == 'rotate':
        rotate_signing(args.dir, root_dir=args.root, days=args.days, org=args.org, display_dir=args.display_dir)
        print(f'issued a new {(args.display_dir or args.dir) / SIGNING_CERT}; the previous pair is kept as *.old')
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
