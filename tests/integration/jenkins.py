"""Minimal Jenkins script-console client for integration tests.

Unsecured controller, CSRF crumb + session.
"""

from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.parse
import urllib.request

READY_TIMEOUT = 600
SCRIPT_TIMEOUT = 900


class Jenkins:
    """One Jenkins controller, reached over HTTP with a cookie-keeping opener."""

    def __init__(self, base_url: str) -> None:
        """Talk to the controller at base_url."""
        self.base = base_url.rstrip('/')
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def wait_ready(self) -> None:
        """Poll /login until it answers 200.

        Raise TimeoutError after READY_TIMEOUT seconds.
        """
        deadline = time.monotonic() + READY_TIMEOUT
        while True:
            try:
                with self.opener.open(self.base + '/login', timeout=10) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            if time.monotonic() > deadline:
                msg = f'{self.base} did not become ready'
                raise TimeoutError(msg)
            time.sleep(5)

    def version(self) -> str:
        """Return the core version from the X-Jenkins header."""
        with self.opener.open(self.base + '/login', timeout=10) as response:
            return response.headers['X-Jenkins']

    def script(self, groovy: str) -> str:
        """Run groovy in the script console and return its output.

        It sends a fresh CSRF crumb.
        """
        with self.opener.open(
            self.base + '/crumbIssuer/api/json', timeout=30
        ) as response:
            crumb = json.loads(response.read())
        request = urllib.request.Request(
            self.base + '/scriptText',
            data=urllib.parse.urlencode({'script': groovy}).encode(),
            headers={crumb['crumbRequestField']: crumb['crumb']},
        )
        with self.opener.open(request, timeout=SCRIPT_TIMEOUT) as response:
            return response.read().decode()
