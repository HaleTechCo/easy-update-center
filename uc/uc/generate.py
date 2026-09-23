"""uc generate: build, validate and publish one release (spec §6)."""

from __future__ import annotations

import datetime as dt
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from . import aql, args, doctor, downloadmap, errors, htaccess, http, jar, layout, pki, preflight, publish, resources, status, validate
from .config import Settings
from .timeutil import iso, utcnow
from .versions import VersionTool

AqlQuery = Callable[[str, str, str, str], list[aql.RepoFile]]


@dataclass
class Toolchain:
    """External effects, replaceable in tests."""

    run_jar: jar.JarRunner = jar.run_jar
    aql_query: AqlQuery = aql.query_repository
    http_get: http.Getter = http.get
    versions: VersionTool | None = None


def generate(
    settings: Settings,
    *,
    force: bool = False,
    tools: Toolchain | None = None,
    now: dt.datetime | None = None,
    base_env: Mapping[str, str] | None = None,
) -> str:
    tools = tools or Toolchain()
    now = now or utcnow()
    base_env = os.environ if base_env is None else base_env
    data = settings.data_dir
    release_id = publish.new_release_id(now)
    data.mkdir(parents=True, exist_ok=True)
    with publish.RunLock(data):
        warnings: list[str] = []
        orphans = publish.reclaim_orphans(data, release_id)
        if orphans:
            warnings.append(f'removed {len(orphans)} leftover director(ies) from an interrupted run: {", ".join(orphans)}')
        work = data / 'work' / release_id
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            _build_and_publish(settings, release_id, now, work, force, tools, base_env, warnings)
        except Exception as exc:
            publish.mark_failed(data, release_id)
            status.update_status(
                data,
                mode=settings.mode,
                last_run={
                    'id': release_id,
                    'result': 'failed',
                    'error': errors.public_summary(exc),
                    'finished_at': iso(utcnow()),
                },
                warnings=warnings,
            )
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return release_id


def _build_and_publish(
    settings: Settings,
    release_id: str,
    now: dt.datetime,
    work: Path,
    force: bool,
    tools: Toolchain,
    base_env: Mapping[str, str],
    warnings: list[str],
) -> None:
    data = settings.data_dir
    warnings += preflight.check_public_url(settings.public_url, settings.allow_local_public_url)
    cert = preflight.check_signing(settings, now)
    creds = preflight.load_credentials(settings)
    if creds.github_placeholder:
        warnings.append('GitHub credentials not set: plugin labels from GitHub topics are omitted')
    staging = publish.create_staging(data, release_id)

    prepared = resources.prepare_resources(settings, work, now, get=tools.http_get)
    warnings += prepared.warnings
    env = jar.jar_environment(settings, creds, base_env)
    log = work / 'update-center2.log'
    tiers = jar.generate_tiers(settings, env, prepared.path, work / 'tiers.json', log, run=tools.run_jar)

    (staging / '.htaccess').write_text(
        htaccess.render_htaccess(tiers, settings.htaccess_script, settings.templates_dir / 'htaccess-static'),
        encoding='utf-8',
    )
    arguments = work / 'args.lst'
    args.write_arguments_file(args.build_argument_lines(settings, staging, tiers), arguments)
    jar.generate_sites(settings, env, prepared.path, arguments, log, run=tools.run_jar)

    layout.finalize_layout(staging, tiers, settings.templates_dir)
    shutil.copyfile(work / 'tiers.json', staging / 'tiers.json')
    live = publish.live_path(data)
    if settings.tool_installers_url:
        warnings += layout.mirror_tool_installers(
            settings.tool_installers_url,
            staging / 'updates',
            previous=live / 'updates' if live else None,
            get=tools.http_get,
        )

    listing = None
    mapping: dict[str, str] | None = None
    if settings.download_backend == 'artifactory':
        listing = aql.classify(
            tools.aql_query(
                settings.source_api_url,
                settings.repo_key,
                creds.repo_username,
                creds.repo_password,
            )
        )
        current = validate.load_site_json(staging / 'current' / 'update-center.actual.json')
        mapping, map_warnings = downloadmap.build_download_map(listing, downloadmap.preferred_groups(current))
        warnings += map_warnings
    downloadmap.write_download_map(mapping or {}, staging / '.htdownload-map')

    result = validate.validate_release(
        staging,
        tiers=tiers,
        downloads_root=settings.downloads_root_url,
        signing_cert_b64=pki.cert_der_b64(settings.signing_cert_file),
        previous_plugin_count=validate.count_plugins(live / 'current') if live else None,
        min_plugins=settings.min_plugins,
        min_ratio=settings.min_plugin_ratio,
        force=force,
        download_map=mapping,
    )

    doctor_summary = None
    if listing is not None:
        versions = tools.versions or VersionTool.from_settings(settings)
        report = doctor.analyze(listing, versions, doctor.ArtifactSource.from_settings(settings, creds, allow_network=False))
        doctor.attach_adapter_report(report, settings)
        doctor_summary = report.summary()
        if report.dropped:
            warnings.append(f"{report.dropped} artifact(s) in the source are dropped by the generator; run 'uc doctor'")

    publish.publish(data, staging, release_id)
    status.update_status(data, live_release=release_id)
    resources.save_last_good(prepared, data)
    publish.prune(data, settings.retain_releases)
    finished = utcnow()
    age = prepared.info.age_days(finished)
    status.write_status(
        data,
        {
            'mode': settings.mode,
            'public_url': settings.public_url,
            'download_backend': settings.download_backend,
            'live_release': release_id,
            'last_run': {'id': release_id, 'result': 'success', 'error': None, 'finished_at': iso(finished)},
            'generated_at': iso(now),
            'update_center2': base_env.get('UC2_VERSION', 'unknown'),
            'resources': {
                'source': prepared.info.source,
                'ref': prepared.info.ref,
                'data_date': prepared.info.data_date,
                'fetched_at': prepared.info.fetched_at,
                'age_days': age,
                'stale': age > settings.stale_after_days,
                'overlay': prepared.overlay,
            },
            'signing_cert': {
                'subject': cert.subject,
                'not_after': iso(cert.not_after),
                'days_left': cert.days_left(finished),
            },
            'tiers': {'weekly': list(tiers.weekly), 'stable': list(tiers.stable)},
            'plugin_count': result.plugin_count,
            'warnings': warnings,
            'doctor': doctor_summary,
        },
    )
