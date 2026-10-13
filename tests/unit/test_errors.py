"""Unit tests for uc.errors: what the public status.json may say."""

import pytest

from uc.errors import public_warnings


def test_public_warnings_replace_every_url():
    warnings = [
        'resources mirror https://mirror.internal.corp/r.tgz is stale',
        'GitHub credentials not set: plugin labels from GitHub topics are omitted',
    ]
    assert public_warnings(warnings) == [
        'resources mirror <url> is stale',
        'GitHub credentials not set: plugin labels from GitHub topics are omitted',
    ]


@pytest.mark.parametrize(
    ('warning', 'public'),
    [
        (
            (
                'tool installers not mirrored '
                '(GET https://tools.internal.corp/a/b.json failed: 500)'
            ),
            'tool installers not mirrored (see the generator log)',
        ),
        (
            'resources fetch failed (http://10.0.0.5:8080/r.tgz); using last-good copy',
            'resources fetch failed (see the generator log); using last-good copy',
        ),
        (
            # an SSL error names the host without a URL, and holds parentheses itself
            (
                'tool installers not refreshed (GET <url> failed: [SSL: '
                'CERTIFICATE_VERIFY_FAILED] certificate verify failed (_ssl.c:1000) '
                "for 'nexus.internal.corp'); kept the previous copy"
            ),
            (
                'tool installers not refreshed (see the generator log); kept the '
                'previous copy'
            ),
        ),
        (
            'resources fetch failed (connection to db.internal.corp reset',
            'resources fetch failed (see the generator log)',
        ),
        (
            'plugins/git/5.2.0 exists under several groupIds (com.a, org.b); using b',
            (
                'plugins/git/5.2.0 exists under several groupIds '
                '(see the generator log); using b'
            ),
        ),
    ],
    ids=['url', 'url-only', 'nested', 'unclosed', 'coordinates'],
)
def test_public_warnings_reduce_each_parenthesised_detail_to_the_log(warning, public):
    # Exception text can name an internal host without a URL (spec 1.7: reduced to
    # what failed).
    assert public_warnings([warning]) == [public]


@pytest.mark.parametrize(
    'warning',
    [
        'removed 2 leftover director(ies) from an interrupted run: releases/a, work/a',
        "3 artifact(s) in the source are dropped by the generator; run 'uc doctor'",
        (
            'PUBLIC_URL uses plain http: HTTPS is required for production '
            '(docs/how-to/enable-tls.md)'
        ),
    ],
    ids=['plural', 'plural-s', 'docs-pointer'],
)
def test_public_warnings_keep_plurals_and_pointers_to_the_docs(warning):
    assert public_warnings([warning]) == [warning]
