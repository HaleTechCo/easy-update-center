"""Minimal Jenkins script-console client for integration tests (unsecured controller, CSRF crumb + session)."""

from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.parse
import urllib.request


class Jenkins:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip('/')
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def wait_ready(self, timeout: float = 600) -> None:
        deadline = time.monotonic() + timeout
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
        with self.opener.open(self.base + '/login', timeout=10) as response:
            return response.headers['X-Jenkins']

    def script(self, groovy: str, timeout: float = 900) -> str:
        with self.opener.open(self.base + '/crumbIssuer/api/json', timeout=30) as response:
            crumb = json.loads(response.read())
        request = urllib.request.Request(
            self.base + '/scriptText',
            data=urllib.parse.urlencode({'script': groovy}).encode(),
            headers={crumb['crumbRequestField']: crumb['crumb']},
        )
        with self.opener.open(request, timeout=timeout) as response:
            return response.read().decode()
