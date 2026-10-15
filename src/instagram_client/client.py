"""Instagram client (logged-out) — profiles, posts and oEmbed.

Reverse engineering: https://github.com/scripthon/instagram-re.
Everything here is public data fetched as a guest; no account or token.

    from instagram_client import InstagramClient

    ig = InstagramClient()
    user = ig.profile("nasa")          # profile + 12 latest posts
    media = ig.post("Dbd3EBdnW_u")      # one post by shortcode
    ig.close()
"""

from __future__ import annotations

import json
import time
from typing import Any, Self
from urllib.parse import quote

from .errors import NotFoundError
from .transport import (
    GRAPHQL_DIST,
    GRAPHQL_WWW,
    OEMBED_URL,
    PROFILE_URL,
    Transport,
)

DOC_POST_ROOT = "28938979505735958"
DOC_PROFILE_PAGE = "28036671149327607"

ROOT_POST = "xdt_api__v1__media__shortcode__web_info"

PV_SHORT_DRAMA = "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider"
PV_MULTI_CAPTION = "__relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider"
# Provided variables the profile-page query requires (server checks presence,
# not the value); omitting them fails with missing_required_variable_value.
PV_PROFILE = {
    "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": False,
    "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
    "__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider": False,
    "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": False,
}


class InstagramClient:
    """One client per session; keeps its HTTP sessions and tokens on the instance."""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout: float = 30.0,
        min_interval: float = 6.0,
        max_retries: int = 3,
        transport: Transport | None = None,
    ) -> None:
        self.t = transport or Transport(
            proxy=proxy,
            timeout=timeout,
            min_interval=min_interval,
            max_retries=max_retries,
        )

    def close(self) -> None:
        self.t.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --------------------------------------------------------------- graphql
    def _graphql(
        self,
        doc_id: str,
        variables: dict[str, Any],
        friendly: str,
        endpoint: str = GRAPHQL_DIST,
        root_field: str | None = None,
        referer: str | None = None,
    ) -> dict[str, Any]:
        self.t._bootstrap()
        form = {
            "doc_id": doc_id,
            "variables": json.dumps(variables, separators=(",", ":")),
            "fb_api_req_friendly_name": friendly,
            "fb_api_caller_class": "RelayModern",
            "server_timestamps": "true",
            "__a": "1",
            "__user": "0",
            "__req": "q",
            "__rev": self.t.rev,
            "hsi": self.t.hsi,
            "lsd": self.t.lsd,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-FB-Friendly-Name": friendly,
            "X-IG-Max-Touch-Points": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": referer or "https://www.instagram.com/",
        }
        if self.t.csrf:
            headers["X-CSRFToken"] = self.t.csrf
        if root_field:
            headers["X-Root-Field-Name"] = root_field

        payload: dict[str, Any] = {}
        for attempt in range(self.t.max_retries):
            payload = self.t.parse(self.t.request("POST", endpoint, data=form, headers=headers))
            # A transient Facebook error envelope ("__ar") is retried once more.
            if isinstance(payload, dict) and "__ar" in payload and attempt < self.t.max_retries - 1:
                time.sleep(4.0 * (attempt + 1))
                continue
            break
        return payload

    @staticmethod
    def _require(payload: dict[str, Any], message: str) -> dict[str, Any]:
        """A 200 whose ``data`` is missing/empty for an unknown id is a 404."""
        if not isinstance(payload, dict) or not payload.get("data"):
            raise NotFoundError(message)
        return payload

    # ------------------------------------------------------------------ API
    def profile(self, username: str) -> dict[str, Any]:
        """Public profile + the 12 latest posts, by username (without ``@``)."""
        username = username.lstrip("@")
        url = f"{PROFILE_URL}?username={quote(username)}"
        payload = self.t.parse(self.t.request("GET", url, mobile=True))
        user = (payload.get("data") or {}).get("user") if isinstance(payload, dict) else None
        if not user:
            raise NotFoundError(f"Instagram user {username!r} not found.")
        return user

    def profile_by_id(self, user_id: str) -> dict[str, Any]:
        """Public profile by numeric user id."""
        variables = {
            "id": str(user_id),
            "enable_integrity_filters": False,
            PV_SHORT_DRAMA: False,
            **PV_PROFILE,
        }
        payload = self._graphql(
            DOC_PROFILE_PAGE,
            variables,
            "PolarisProfilePageContentQuery",
            GRAPHQL_WWW,
        )
        user = (payload.get("data") or {}).get("user")
        if not user:
            raise NotFoundError(f"Instagram user id {user_id!r} not found.")
        return user

    def post(self, shortcode: str) -> dict[str, Any]:
        """One post (image/video/carousel), by shortcode."""
        variables = {"shortcode": shortcode, PV_SHORT_DRAMA: False, PV_MULTI_CAPTION: False}
        payload = self._graphql(
            DOC_POST_ROOT,
            variables,
            "PolarisPostRootQuery",
            GRAPHQL_DIST,
            root_field=ROOT_POST,
            referer=f"https://www.instagram.com/p/{shortcode}/",
        )
        self._require(payload, f"Instagram post {shortcode!r} not found.")
        items = (payload["data"].get(ROOT_POST) or {}).get("items") or []
        if not items:
            raise NotFoundError(f"Instagram post {shortcode!r} not found.")
        return items[0]

    def oembed(self, post_url: str) -> dict[str, Any]:
        """Official oEmbed for a public post URL."""
        resp = self.t.request("GET", OEMBED_URL, params={"url": post_url})
        payload = self.t.parse(resp)
        if not isinstance(payload, dict):
            raise NotFoundError(f"Instagram oEmbed for {post_url!r} not found.")
        return payload
