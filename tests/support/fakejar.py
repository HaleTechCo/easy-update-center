"""Stand-ins for update-center2 and VersionTool in generate() tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from sitefixture import uc_document, write_site
from uc.jar import JarError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from uc.model import Tiers


class SimpleVersions:
    """Dotted-number comparisons without starting a JVM.

    The real VersionTool is covered by test_versions.
    """

    @staticmethod
    def _key(version: str) -> tuple:
        return tuple(
            (0, int(p), '') if p.isdigit() else (1, 0, p)
            for p in re.split(r'[.-]', version)
        )

    def sort(self, versions: Iterable[str]) -> list[str]:
        """Return the versions in ascending order."""
        return sorted(versions, key=self._key)

    def max_by_key(self, items: Iterable[tuple[str, str]]) -> dict[str, str]:
        """Return the highest version for each key among (key, version) items."""
        best: dict[str, str] = {}
        for key, version in items:
            if key not in best or self._key(version) > self._key(best[key]):
                best[key] = version
        return best

    def compare_many(self, pairs: Sequence[tuple[str, str]]) -> list[int]:
        """Compare each (a, b) pair; return -1, 0 or 1 for each, in order."""
        return [
            (self._key(a) > self._key(b)) - (self._key(a) < self._key(b))
            for a, b in pairs
        ]


class FakeJar:
    """A JarRunner that writes the tier list or fixture sites, and records each call.

    It writes them where update-center2 would.
    """

    def __init__(
        self,
        tiers: Tiers,
        cert_b64: str,
        *,
        url_root: str | None = None,
        fail_sites: bool = False,
        **doc_options,
    ) -> None:
        """Report tiers.

        List cert_b64 in each site's signature and root URLs at url_root (default:
        DOWNLOADS_ROOT_URL minus /download).

        fail_sites makes the site run fail; doc_options go to sitefixture.uc_document.
        """
        self.tiers = tiers
        self.cert_b64 = cert_b64
        self.url_root = url_root
        self.fail_sites = fail_sites
        self.doc_options = doc_options
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def __call__(self, cmd: list[str], env: Mapping[str, str], log: Path) -> None:
        """Stand in for one update-center2 run.

        The tier list for --dynamic-tier-list-file, else every site in --arguments-file.
        """
        self.calls.append(list(cmd))
        self.envs.append(dict(env))
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a') as out:
            out.write('fake update-center2\n')
        if '--dynamic-tier-list-file' in cmd:
            target = Path(cmd[cmd.index('--dynamic-tier-list-file') + 1])
            target.write_text(
                json.dumps(
                    {
                        'weeklyCores': list(self.tiers.weekly),
                        'stableCores': list(self.tiers.stable),
                    }
                )
            )
            return
        if self.fail_sites:
            msg = 'update-center2 exited with status 1; see log'
            raise JarError(msg)
        root = self.url_root or env['DOWNLOADS_ROOT_URL'][: -len('/download')]
        arguments = Path(cmd[cmd.index('--arguments-file') + 1])
        for line in arguments.read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            tokens = line.split(' ')
            www = Path(tokens[tokens.index('--www-dir') + 1])
            doc = uc_document(root, cert_b64=self.cert_b64, **self.doc_options)
            write_site(www, doc, current=www.name == 'current')
            if '--download-links-directory' in tokens:
                links = Path(tokens[tokens.index('--download-links-directory') + 1])
                for plugin_id in doc['plugins']:
                    (links / 'plugins' / plugin_id).mkdir(parents=True, exist_ok=True)
                    (links / 'plugins' / plugin_id / 'index.html').write_text(plugin_id)
