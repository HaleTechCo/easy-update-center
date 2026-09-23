"""Refuse commits containing credentials that look like real provider secrets.

pre-commit-hooks ships `detect-private-key`, but it only matches PEM `BEGIN ... PRIVATE KEY`
blocks. That leaves the far more common shape - an opaque provider token pasted into a
compose file or a config - completely uncovered.

The list covers the usual provider shapes plus the ones this repo's own upstreams hand out: an
Artifactory API key, an Artifactory reference token, a JWT, and - the general case, not
vendor-specific - any URL carrying a `user:password@` credential. The last collides with how the
docs and tests DEMONSTRATE credential handling (`https://user:token@host`, ...), so it is filtered
by a RULE (`_looks_like_placeholder_secret`) rather than by marking every one of those lines
individually - see that function's docstring.

The patterns below are deliberately high-signal prefixes rather than entropy heuristics, so
this stays quiet enough to leave on. It is a backstop, not a guarantee: keep secrets in the
secrets/ directory and out of the tree.

Usage (from a hook entry):
  python tools/check_secrets.py PATH [PATH ...]

Add an inline `pragma: allowlist secret` comment on a line that is a deliberate placeholder
or test fixture. Exits non-zero when anything matches.
"""

import re
import sys
from collections.abc import Iterator
from pathlib import Path

ALLOWLIST_MARKER = 'pragma: allowlist secret'

# (label, compiled pattern). Anchored on vendor-specific prefixes so false positives are rare.
PATTERNS = [
    ('Google OAuth client secret', re.compile(r'GOCSPX-[A-Za-z0-9_-]{10,}')),
    ('Google API key', re.compile(r'AIza[A-Za-z0-9_-]{35}')),
    ('AWS access key ID', re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('GitHub token', re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}')),
    ('Slack token', re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}')),
    ('Stripe secret key', re.compile(r'\b[sr]k_(?:live|test)_[A-Za-z0-9]{20,}')),
    ('OpenAI API key', re.compile(r'\bsk-[A-Za-z0-9]{20,}')),
    ('Anthropic API key', re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}')),
    ('JSON private_key field', re.compile(r'"private_key"\s*:\s*"-----BEGIN')),
    # Excludes % $ < { from the user and password so this does not fire on DSN *templates* -
    # a printf-style placeholder, shell "${DB_USER}:${DB_PASS}", or "<user>:<pass>" - which
    # carry no actual credential. (Deliberately not written out as a full example URL with a
    # printf placeholder in BOTH the user and password slots here: joined with "://" and "@",
    # that is exactly the shape CREDENTIALED_URL below matches, and this comment is itself
    # inside a tracked file check_secrets.py scans.)
    (
        'Postgres URL with password',
        re.compile(r'postgres(?:ql)?://[^:/\s%$<{]+:[^@/\s%$<{]+@'),
    ),
    ('Artifactory API key', re.compile(r'AKCp[A-Za-z0-9]{69}')),
    # JFrog does not publish the exact length of the base64 body that follows the `cmVmdGtu`
    # ("reftkn", base64-encoded) prefix - `{50,}` is a long floor, the same anchored-prefix,
    # high-signal shape every entry above already uses.
    ('Artifactory reference token', re.compile(r'cmVmdGtu[A-Za-z0-9+/=]{50,}')),
    # Both the header and the payload of a JWT are base64 of a JSON object starting `{"`, which
    # is why BOTH segments are anchored on `eyJ` rather than only the first - matching
    # `eyJ....eyJ....sig` catches the two-segment shape reliably without decoding anything.
    ('JWT', re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}')),
]

# A credentialed URL (scheme://user:pass@host) gets its own check rather than a PATTERNS entry,
# because unlike every prefix-anchored shape above, it collides with how the docs and tests
# spell out credential handling: examples like `https://user:token@host` show what a URL must not
# carry. Marking every one of those lines with the allowlist pragma would be exactly the per-line
# noise this tool exists to avoid, so the exemption is a RULE instead - see
# `_looks_like_placeholder_secret` below.
CREDENTIALED_URL = re.compile(r'[a-z][a-z0-9+.-]*://[^/\s:@\'"]+:([^/\s@\'"]+)@')

_LEET_DIGITS = str.maketrans({'0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't'})

# Words commonly spelled out (plain or leetspoken) to demonstrate a credentialed URL without using
# a real secret: pass, secret, token, hidden ("h1ddenS3cret"), vendor ("v3ndorS3cret"). `sekrit` (a
# phonetic misspelling, not a digit substitution) and `hunter2` (the well-known "this is definitely
# not my real password" joke) are named outright because neither survives the digit-only leet table
# above. `pword` is what `pw0rd` normalises to. `redacted` covers a placeholder that IS ALREADY a
# redaction marker (`{REDACTED}` in an f-string source line).
_PLACEHOLDER_WORDS = ('pass', 'secret', 'token', 'hidden', 'vendor', 'sekrit', 'hunter2', 'pword', 'redacted')

# Deliberately NOT `word in normalised` - a placeholder word found ANYWHERE in the password,
# however long - which would accept a real, high-entropy credential the moment it happened to
# start or end with a common word: `svc:passwXk9fQ2mZ7pLr@...` would read as a placeholder purely
# because it starts with "pass". Every real placeholder this pattern needs to match -
# `v3ndorS3cret` -> "vendorsecret", `h1ddenS3cret` -> "hiddensecret", `S3CRET` -> "secret",
# `PW0RD` -> "pword", `sekrit` -> "sekrit" - is an EXACT, zero-leftover concatenation of one or
# more of the words above, so `fullmatch` against "one or more of these words back to back" is the
# rule: it accepts every placeholder shape above and rejects a word merely glued onto real
# entropy, because a `fullmatch` leaves no room for a leftover, non-word fragment on either side.
_PLACEHOLDER_CONCATENATION = re.compile('(?:' + '|'.join(_PLACEHOLDER_WORDS) + ')+')

# Below this length, a "the WHOLE password is a fragment of one word" check (the OTHER direction
# from the concatenation above - `tok` is a fragment of `token`, not the reverse) is too easy to
# satisfy by accident - a one- or two-character password would match almost any word in the list.
# 3 is `tok`'s own length, the shortest genuine fragment worth accepting.
_MIN_FRAGMENT_LEN = 3


def _looks_like_placeholder_secret(password: str) -> bool:
    """Decide whether `password` (the text between ':' and '@') is an obviously-fake stand-in.

    Two shapes are recognised. First, a redaction marker and an f-string interpolation
    placeholder both read as a bare run of `*` or a `{...}` - `***`, `{SECRET}`, `{REDACTED}` -
    which carries no character of a real secret at all. Second, the WHOLE password, read through
    a handful of leetspeak digit substitutions, is either a fragment of a single short dictionary
    word for "this is not real" (`tok` of `token`) or an exact, zero-leftover concatenation of one
    or more of them (`vendorsecret` is `vendor` + `secret`) - see `_PLACEHOLDER_WORDS` above.
    Deliberately NOT "contains one of these words as a substring anywhere": that accepts a real,
    high-entropy secret merely prefixed or suffixed with a common word (`passwXk9fQ2mZ7pLr`) as a
    placeholder, which is the opposite of what this function is for.

    This is a heuristic backstop, not a guarantee, same as the rest of this module: a REAL secret
    that happens, in its ENTIRETY, to be nothing but placeholder words concatenated back to back
    (literally `passsecret`, say) would still be missed - a shape no real generated credential is
    remotely likely to take. Keeping a real secret out of the tree - never in a committed URL at
    all - is the invariant this whole tool exists to backstop, not replace.
    """
    stripped = password.strip('{}')
    if stripped and set(stripped) <= {'*'}:
        return True
    normalised = re.sub(r'[^a-z]', '', stripped.lower().translate(_LEET_DIGITS))
    if not normalised:
        return False
    if _PLACEHOLDER_CONCATENATION.fullmatch(normalised):
        return True
    return len(normalised) >= _MIN_FRAGMENT_LEN and any(normalised in word for word in _PLACEHOLDER_WORDS)


# Number of leading characters echoed when reporting a PATTERNS match. The point is to identify the
# line without copying the secret into logs and terminal scrollback. A credentialed URL is not
# shown this way - see `_masked_userinfo` - because its secret does not start at a fixed offset.
REDACT_AFTER = 12

# What stands in for a credentialed URL's password in a finding line.
MASK = '***'


def _prefix(text: str) -> str:
    """Return the first `REDACT_AFTER` characters of a prefix-anchored match, marked as cut."""
    return text[:REDACT_AFTER] + '...' if len(text) > REDACT_AFTER else text


def _masked_userinfo(match: re.Match[str]) -> str:
    """Return a CREDENTIALED_URL match as `scheme://user:***@` - the whole password blanked.

    A fixed `REDACT_AFTER` cut suits the vendor shapes above, every one of which STARTS with
    its published prefix (`GOCSPX-`, `AKCp`, ...) - the cut shows that prefix and a few characters
    past it, the recorded cost of saying which shape matched. For a URL the same cut lands on no
    boundary at all: the password starts wherever the account name ends, so `https://a:<secret>@`
    would print `https://a:` plus two characters of the secret, and a shorter scheme or account
    name would print more. The scheme and account are kept - with the path and line number they
    locate the finding - and nothing from the password's own span is.
    """
    whole = match.group(0)
    return f'{whole[: match.start(1) - match.start(0)]}{MASK}@'


def scan_file(path: str) -> Iterator[tuple[int, str, str]]:
    """Yield (lineno, label, shown) for each secret found in path - `shown` already safe to print.

    Masking happens HERE rather than in `main`, because only this function knows where inside a
    match the secret sits: a prefix for the vendor shapes, the userinfo password for a URL.
    """
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return  # binary or unreadable; types:[text] should exclude these
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOWLIST_MARKER in line:
            continue
        for label, pattern in PATTERNS:
            match = pattern.search(line)
            if match:
                yield lineno, label, _prefix(match.group(0))
        # `finditer`, not `search`: a docs line legitimately shows more than one example URL, and
        # a placeholder earlier on the line must not hide a real credential later on the same one.
        for cred_match in CREDENTIALED_URL.finditer(line):
            if not _looks_like_placeholder_secret(cred_match.group(1)):
                yield lineno, 'credentialed URL', _masked_userinfo(cred_match)


def main(argv: list[str]) -> int:
    """Check every path in argv; return a shell exit code."""
    findings = 0
    for path in argv:
        for lineno, label, shown in scan_file(path):
            # `shown` is already cut or masked - the whole point is not to copy the secret around.
            print(f'{path}:{lineno}: possible {label}: {shown}')
            findings += 1

    if findings:
        print(
            f'\n{findings} possible secret(s) found. Move them to secrets/ or the environment (e.g. ${{VAR}} interpolation), or mark a deliberate placeholder with an inline "{ALLOWLIST_MARKER}" comment.',
        )
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
