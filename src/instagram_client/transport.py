"""HTTP transport for Instagram (logged-out): sessions, headers, throttle,
retries and error mapping.

Findings that shape this file (see ../instagram-re/stack.md):
- ``web_profile_info`` needs a **mobile** User-Agent + mobile X-IG-App-ID;
  a desktop UA / web app id answers ``401 require_login``.
- Instagram answers an empty ``429`` to HTTP/1.1 — every session uses HTTP/2.
- The relay GraphQL calls need ``lsd``/``hsi``/``__rev``/``csrftoken`` taken
  from the landing page; ``_bootstrap`` extracts them once per session.
- A burst of requests trips a soft gate (``igweb_rollout`` / ``require_login``),
  which is retried with backoff like a 429.
"""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

import httpx

from .errors import NotFoundError, StatusError

MOBILE_UA = "Instagram 101.0.0.15.120 Android"
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
WEB_APP_ID = "936619743392459"
MOBILE_APP_ID = "124024574287414"

HOME_URL = "https://www.instagram.com/"
OEMBED_URL = "https://www.instagram.com/api/v1/oembed/"
PROFILE_URL = "https://i.instagram.com/api/v1/users/web_profile_info/"
GRAPHQL_DIST = "https://www.instagram.com/graphql/query"
GRAPHQL_WWW = "https://www.instagram.com/api/graphql"

RETRYABLE = {429, 500, 502, 503, 504}
FOR_LOOP_PREFIX = "for (;;);"


class Transport:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout: float = 30.0,
        min_interval: float = 6.0,
        max_retries: int = 3,
    ) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last_request = 0.0
        self._bootstrapped = False
        self.lsd = ""
        self.hsi = ""
        self.rev = "0"
        self.csrf = ""

        kwargs: dict[str, Any] = {
            "http2": True,  # Instagram answers empty 429 to HTTP/1.1
            "follow_redirects": True,
            "timeout": timeout,
        }
        if proxy:
            kwargs["proxy"] = proxy
        self.web = httpx.Client(**kwargs)
        self.web.headers.update(
            {
                "User-Agent": WEB_UA,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "X-IG-App-ID": WEB_APP_ID,
                "X-ASBD-ID": "129477",
                "X-IG-WWW-Claim": "0",
            }
        )
        self.mobile = httpx.Client(**kwargs)
        self.mobile.headers.update(
            {
                "User-Agent": MOBILE_UA,
                "Accept": "*/*",
                "X-IG-App-ID": MOBILE_APP_ID,
            }
        )

    def close(self) -> None:
        self.web.close()
        self.mobile.close()

    # ------------------------------------------------------------------ utils
    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait + random.uniform(0.2, 1.2))
        self._last_request = time.time()

    @staticmethod
    def _is_gate(resp: httpx.Response) -> bool:
        return (
            resp.status_code == 401
            and (b'"require_login":true' in resp.content or b'"igweb_rollout":true' in resp.content)
        ) or b'"igweb_rollout":true' in resp.content

    def _bootstrap(self) -> None:
        """Read csrftoken + lsd/hsi/rev from the landing page (once)."""
        if self._bootstrapped:
            return
        text = self.request("GET", HOME_URL).text
        m = re.search(r'lsd":"([^"]+)"', text)
        self.lsd = m.group(1) if m else ""
        m = re.search(r'"hsi":"(\d+)"', text)
        self.hsi = m.group(1) if m else ""
        m = re.search(r'"client_revision":(\d+)', text)
        self.rev = m.group(1) if m else "0"
        self.csrf = self.web.cookies.get("csrftoken") or ""
        self._bootstrapped = True

    # ---------------------------------------------------------------- request
    def request(self, method: str, url: str, *, mobile: bool = False, **kw: Any) -> httpx.Response:
        """Send a request, retrying the soft gate / 429 / 5xx with backoff.

        Returns the last response; the caller maps its status. 4xx that is not
        the gate are not retried.
        """
        session = self.mobile if mobile else self.web
        resp: httpx.Response | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            resp = session.request(method, url, timeout=self.timeout, **kw)
            if (self._is_gate(resp) or resp.status_code in RETRYABLE) and attempt < self.max_retries - 1:
                time.sleep(4.0 * (2**attempt) + random.uniform(0, 2))
                continue
            return resp
        assert resp is not None
        return resp

    def parse(self, resp: httpx.Response) -> Any:
        """Validate the status and parse the body, stripping the ``for (;;);``
        anti-JSON prefix. Raises NotFoundError / StatusError on failure."""
        if resp.status_code == 404:
            raise NotFoundError("Not found on Instagram.")
        if resp.status_code >= 400:
            raise StatusError(resp.status_code, f"Instagram returned HTTP {resp.status_code}.")
        text = resp.text
        text = text.removeprefix(FOR_LOOP_PREFIX)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise StatusError(resp.status_code, "Instagram returned a non-JSON response.") from None
