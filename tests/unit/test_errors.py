"""Unit tests for uc.errors: what the public status.json may say."""

from uc.errors import public_warnings


def test_public_warnings_replace_every_url():
    warnings = [
        (
            'tool installers not mirrored '
            '(GET https://tools.internal.corp/a/b.json failed: 500)'
        ),
        'resources fetch failed (http://10.0.0.5:8080/r.tgz); using last-good copy',
        'GitHub credentials not set: plugin labels from GitHub topics are omitted',
    ]
    assert public_warnings(warnings) == [
        'tool installers not mirrored (GET <url> failed: 500)',
        'resources fetch failed (<url>); using last-good copy',
        'GitHub credentials not set: plugin labels from GitHub topics are omitted',
    ]
