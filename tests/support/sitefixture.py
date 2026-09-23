"""Minimal, schema-realistic update-center2 output for tests (what the jar writes per --www-dir)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from uc.model import Tiers

DEFAULT_PLUGINS: Mapping[str, str] = {'git': '5.2.0', 'credentials': '1415.v831096eb_5534'}
TEST_CERT_B64 = 'VEVTVC1DRVJU'


def uc_document(
    public_url: str,
    *,
    core: str | None = '2.528.1',
    plugins: Mapping[str, str] | None = None,
    cert_b64: str = TEST_CERT_B64,
    required_core: str = '2.479.1',
) -> dict:
    plugins = DEFAULT_PLUGINS if plugins is None else plugins
    download = f'{public_url}/download'
    doc = {
        'connectionCheckUrl': f'{public_url}/uctest.json',
        'deprecations': {},
        'generationTimestamp': '2026-09-22T00:00:00Z',
        'id': 'default',
        'plugins': {
            pid: {
                'name': pid,
                'version': version,
                'url': f'{download}/plugins/{pid}/{version}/{pid}.hpi',
                'gav': f'org.jenkins-ci.plugins:{pid}:{version}',
                'requiredCore': required_core,
                'dependencies': [],
                'sha256': 'c2hh',
            }
            for pid, version in plugins.items()
        },
        'signature': {
            'certificates': [cert_b64],
            'correct_digest': 'ZA==',
            'correct_digest512': 'ZA==',
            'correct_signature': 'cw==',
            'correct_signature512': 'cw==',
        },
        'updateCenterVersion': '1',
        'warnings': [],
    }
    if core:
        doc['core'] = {
            'buildDate': 'Sep 01, 2026',
            'name': 'core',
            'sha256': 'c2hh',
            'url': f'{download}/war/{core}/jenkins.war',
            'version': core,
        }
    else:  # no jenkins-war in the repository: upstream's UpdateCenterCore keeps only its defaults
        doc['core'] = {'name': 'core', 'size': 0}
    return doc


def write_site(site: Path, doc: dict, *, current: bool = False) -> None:
    site.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2)
    (site / 'update-center.actual.json').write_text(text)
    (site / 'update-center.json').write_text(f'updateCenter.post(\n{text}\n);')
    if doc['core'].get('version'):  # upstream skips latestCore.txt when there is no core
        (site / 'latestCore.txt').write_text(doc['core']['version'])
    (site / 'timestamp.txt').write_text(doc['generationTimestamp'])
    if not current:
        return
    versions = {
        'plugins': {pid: {p['version']: {'url': p['url'], 'version': p['version'], 'requiredCore': p['requiredCore'], 'sha256': p['sha256']}} for pid, p in doc['plugins'].items()},
        'signature': doc['signature'],
        'updateCenterVersion': '1',
    }
    (site / 'plugin-versions.json').write_text(json.dumps(versions, indent=2))
    (site / 'release-history.json').write_text(json.dumps({'releaseHistory': []}))
    (site / 'plugin-documentation-urls.json').write_text('{}')
    (site / 'pluginCount.txt').write_text(str(len(doc['plugins'])))
    latest = site / 'latest'
    latest.mkdir(exist_ok=True)
    rules = ''.join(f'RewriteRule ^{pid}\\.hpi$ {p["url"]} [R=302,L]\n' for pid, p in doc['plugins'].items())
    if doc['core'].get('url'):
        rules += f'RewriteRule ^jenkins\\.war$ {doc["core"]["url"]} [R=302,L]\n'
    (latest / '.htaccess').write_text('# GENERATED. DO NOT MODIFY.\nRewriteEngine on\n' + rules)


def write_generator_output(staging: Path, public_url: str, tiers: Tiers, **doc_options) -> None:
    for version in tiers.weekly:
        write_site(staging / f'dynamic-{version}', uc_document(public_url, **doc_options))
    for version in tiers.stable:
        write_site(staging / f'dynamic-stable-{version}', uc_document(public_url, **doc_options))
    write_site(staging / 'experimental', uc_document(public_url, **doc_options))
    doc = uc_document(public_url, **doc_options)
    write_site(staging / 'current', doc, current=True)
    for pid in doc['plugins']:
        index = staging / 'download' / 'plugins' / pid
        index.mkdir(parents=True, exist_ok=True)
        (index / 'index.html').write_text(f'<html><body>{pid}</body></html>')
    (staging / 'download' / 'war').mkdir(parents=True, exist_ok=True)
    (staging / 'download' / 'war' / 'index.html').write_text('<html><body>jenkins.war</body></html>')
