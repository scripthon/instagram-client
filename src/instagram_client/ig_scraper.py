#!/usr/bin/env python3
"""Instagram scraper (logged-out) — dibangun dari hasil reverse engineering 14 Okt 2026.

Endpoint yang dipakai:
  1. GET  i.instagram.com/api/v1/users/web_profile_info/   (mobile UA + mobile app id)
        -> profil + 12 post terbaru by username
  2. POST www.instagram.com/graphql/query   doc_id PolarisPostRootQuery
        -> detail post by shortcode
  3. POST www.instagram.com/api/graphql     doc_id PolarisProfilePageContentQuery
        -> profil by numeric id
  4. GET  www.instagram.com/api/v1/oembed/                 -> caption/author/thumbnail

Hasil reverse engineering lengkap (endpoint, doc_id, header, risiko) ada di repo
`instagram-re` (NOTES.md).
"""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

import httpx

MOBILE_UA = "Instagram 101.0.0.15.120 Android"
WEB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
WEB_APP_ID = "936619743392459"
MOBILE_APP_ID = "124024574287414"

GRAPHQL_DIST = "https://www.instagram.com/graphql/query"
GRAPHQL_WWW = "https://www.instagram.com/api/graphql"

DOC_POST_ROOT = "28938979505735958"
DOC_PROFILE_PAGE = "28036671149327607"
DOC_PROFILE_POSTS = "28542612348729311"          # butuh login (gated)
DOC_OWNER_TIMELINE = "39002825106027755"          # butuh login (gated)

ROOT_POST = "xdt_api__v1__media__shortcode__web_info"
ROOT_TIMELINE = "xdt_get_owner_to_timeline_media_logged_out"

PV_SHORTD = ("__relay_internal__pv__PolarisShortDramaEnabledrelayprovider", False)
PV_MULTIC = ("__relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider", False)


class GateError(RuntimeError):
    """401 'Please wait a few minutes...' — rate-limit/rollout gate."""


class LoginRequired(RuntimeError):
    """Endpoint menjawab bahwa data hanya untuk sesi login."""


class InstagramScraper:
    def __init__(self, min_interval: float = 6.0, timeout: float = 20.0,
                 max_retries: int = 4):
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request = 0.0
        self._bootstrapped = False
        # WAJIB http2: endpoint mobile membalas 429 untuk HTTP/1.1 (terdeteksi probe)
        self.web = httpx.Client(http2=True, follow_redirects=True,
                                timeout=self.timeout)
        self.web.headers.update({
            "User-Agent": WEB_UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": WEB_APP_ID,
            "X-ASBD-ID": "129477",
            "X-IG-WWW-Claim": "0",
        })
        self.mobile = httpx.Client(http2=True, follow_redirects=True,
                                   timeout=self.timeout)
        self.mobile.headers.update({
            "User-Agent": MOBILE_UA,
            "Accept": "*/*",
            "X-IG-App-ID": MOBILE_APP_ID,
        })

    # ------------------------------------------------------------------ utils
    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait + random.uniform(0.2, 1.2))
        self._last_request = time.time()

    @staticmethod
    def _is_gate(resp: httpx.Response) -> bool:
        return (resp.status_code == 401 and b'"require_login":true' in resp.content) \
            or b'"igweb_rollout":true' in resp.content

    @staticmethod
    def _is_login_required(payload: dict) -> bool:
        return payload.get("message") in ("login_required",) or \
            (payload.get("status") == "fail" and payload.get("error_type") == "login_required")

    def _bootstrap(self) -> None:
        """Ambil cookie (csrftoken/mid) + token lsd dari landing page."""
        if self._bootstrapped:
            return
        self._throttle()
        r = self.web.get("https://www.instagram.com/")
        text = r.text
        m = re.search(r'lsd":"([^"]+)"', text)
        self.lsd = m.group(1) if m else ""
        m = re.search(r'"hsi":"(\d+)"', text)
        self.hsi = m.group(1) if m else ""
        m = re.search(r'"client_revision":(\d+)', text)
        self.rev = m.group(1) if m else "0"
        self.csrf = self.web.cookies.get("csrftoken") or ""
        self._bootstrapped = True

    def _request(self, sess: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
        last = None
        for attempt in range(self.max_retries):
            self._throttle()
            resp = sess.request(method, url, timeout=self.timeout, **kw)
            # 429 (rate limit IP) dan gate "require_login" = masalah jendela waktu
            if self._is_gate(resp) or resp.status_code == 429:
                last = resp
                time.sleep(45 * (attempt + 1) + random.uniform(0, 15))
                continue
            return resp
        raise GateError(
            f"gate/rate-limit bertahan setelah {self.max_retries} retry: {url} "
            f"(HTTP {last.status_code if last is not None else '?'})")

    # --------------------------------------------------------------- graphql
    def _graphql(self, doc_id: str, variables: dict, friendly: str,
                 endpoint: str = GRAPHQL_DIST, root_field: str | None = None,
                 referer: str = "https://www.instagram.com/") -> dict:
        """Kirim query persisten Relay. Retry untuk gate 401/429 dan envelope
        error FB (for (;;);{"__ar":1,"error":...}) yang muncul saat kecepatan."""
        self._bootstrap()
        form = {
            "doc_id": doc_id,
            "variables": json.dumps(variables, separators=(",", ":")),
            "fb_api_req_friendly_name": friendly,
            "fb_api_caller_class": "RelayModern",
            "server_timestamps": "true",
            "__a": "1", "__user": "0", "__req": "q",
            "__rev": self.rev, "hsi": self.hsi, "lsd": self.lsd,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-FB-Friendly-Name": friendly,
            "X-IG-Max-Touch-Points": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": referer,
        }
        if self.csrf:
            headers["X-CSRFToken"] = self.csrf
        if root_field:
            headers["X-Root-Field-Name"] = root_field

        last_err = None
        for attempt in range(self.max_retries):
            resp = self._request(self.web, "POST", endpoint, data=form, headers=headers)
            text = resp.text
            if text.startswith("for (;;);"):
                text = text[len("for (;;);"):]
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                last_err = f"respons bukan JSON ({resp.status_code}): {text[:200]}"
                payload = None

            if isinstance(payload, dict) and "__ar" in payload:
                # envelope error FB (mis. 1357054) -> masalah jendela kecepatan
                last_err = payload.get("errorDescription") or payload.get("error")
                time.sleep(30 * (attempt + 1) + random.uniform(0, 10))
                continue
            if payload is None:
                time.sleep(30 * (attempt + 1))
                continue

            if payload.get("errors"):
                code = payload["errors"][0].get("code")
                msg = payload["errors"][0].get("message", "")
                if code == 1675002 or "logged out" in msg:
                    raise LoginRequired(f"{friendly}: {msg}")
                if code == 1675012 or "missing_required_variable" in msg:
                    raise RuntimeError(f"{friendly}: {msg} (code={code}) — cek variabel")
                raise RuntimeError(f"{friendly}: {msg} (code={code})")
            if "data" not in payload:
                raise RuntimeError(f"{friendly}: struktur tak dikenal: {str(payload)[:200]}")
            return payload["data"]

        raise GateError(f"graphql gagal setelah {self.max_retries} retry: {last_err}")

    # ------------------------------------------------------------------ API
    def profile(self, username: str) -> dict:
        """Profil publik + 12 post terbaru (tanpa login)."""
        url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
        resp = self._request(self.mobile, "GET", url)
        payload = resp.json()
        if self._is_login_required(payload) or payload.get("status") == "fail":
            raise LoginRequired(f"web_profile_info menolak {username}: {payload.get('message')}")
        user = payload["data"]["user"]
        timeline = user.get("edge_owner_to_timeline_media") or {}
        posts = [self._normalize_legacy_node(e.get("node", {})) for e in timeline.get("edges", [])]
        return {
            "id": user.get("id"),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "biography": user.get("biography"),
            "external_url": user.get("external_url"),
            "followers": (user.get("edge_followed_by") or {}).get("count"),
            "following": (user.get("edge_follow") or {}).get("count"),
            "posts_count": timeline.get("count"),
            "is_private": user.get("is_private"),
            "is_verified": user.get("is_verified"),
            "is_business": user.get("is_business_account"),
            "profile_pic_url": user.get("profile_pic_url_hd") or user.get("profile_pic_url"),
            "recent_posts": posts,
        }

    def profile_by_id(self, user_id: str) -> dict:
        """Profil publik by numeric id (GraphQL www)."""
        variables = {
            "id": str(user_id),
            "enable_integrity_filters": False,
            "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": False,
        }
        data = self._graphql(DOC_PROFILE_PAGE, variables,
                             "PolarisProfilePageContentQuery", GRAPHQL_WWW,
                             referer=f"https://www.instagram.com/p/instagram/")
        user = data.get("user") or {}
        return {
            "id": user.get("pk"),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "biography": user.get("biography"),
            "followers": user.get("follower_count"),
            "posts_count": user.get("media_count"),
            "is_private": user.get("is_private"),
            "is_verified": user.get("is_verified"),
            "bio_links": [b.get("url") for b in user.get("bio_links") or []],
        }

    def post(self, shortcode: str) -> dict:
        """Detail post/reel by shortcode (GraphQL distillery)."""
        variables = {"shortcode": shortcode, PV_SHORTD[0]: PV_SHORTD[1],
                     PV_MULTIC[0]: PV_MULTIC[1]}
        data = self._graphql(DOC_POST_ROOT, variables, "PolarisPostRootQuery",
                             GRAPHQL_DIST, root_field=ROOT_POST,
                             referer=f"https://www.instagram.com/p/{shortcode}/")
        items = (data.get("xdt_api__v1__media__shortcode__web_info") or {}).get("items") or []
        if not items:
            raise RuntimeError(f"items kosong untuk {shortcode}: {str(data)[:300]}")
        m = items[0]
        owner = m.get("owner") or {}
        caption = ((m.get("caption") or {}))
        return {
            "shortcode": m.get("code"),
            "id": m.get("pk"),
            "taken_at": m.get("taken_at"),
            "media_type": m.get("media_type"),
            "is_video": bool(m.get("video_versions")),
            "caption": caption.get("text") if isinstance(caption, dict) else caption,
            "like_count": m.get("like_count") or m.get("like_and_view_counts_disabled"),
            "comment_count": m.get("comment_count"),
            "view_count": m.get("view_count"),
            "media_url": ((m.get("image_versions2") or {}).get("candidates") or [{}])[0].get("url"),
            "video_url": ((m.get("video_versions") or [{}])[0]).get("url"),
            "owner": {"id": owner.get("pk") or owner.get("id"),
                      "username": owner.get("username"),
                      "is_verified": owner.get("is_verified")},
            "found": True,
        }

    def oembed(self, post_url: str) -> dict:
        """oEmbed resmi: caption, author, author_id (butuh URL post)."""
        resp = self._request(self.web, "GET",
                             "https://www.instagram.com/api/v1/oembed/",
                             params={"url": post_url})
        return resp.json()

    # ------------------------------------------------------------ normalizers
    @staticmethod
    def _normalize_legacy_node(n: dict) -> dict:
        caption = ((n.get("edge_media_to_caption") or {}).get("edges") or [{}])[0] \
            .get("node", {}).get("text")
        return {
            "shortcode": n.get("shortcode"),
            "id": n.get("id"),
            "typename": n.get("__typename"),
            "is_video": n.get("is_video"),
            "taken_at": n.get("taken_at"),
            "caption": caption,
            "likes": (n.get("edge_media_preview_like") or {}).get("count"),
            "comments": (n.get("edge_media_to_comment") or {}).get("count"),
            "thumbnail": n.get("display_url"),
            "dimensions": n.get("dimensions"),
        }


def _demo() -> None:
    s = InstagramScraper()
    prof = s.profile("nasa")
    print("PROFIL nasa:", {k: prof[k] for k in
                           ("id", "username", "followers", "posts_count", "is_verified")})
    if prof["recent_posts"]:
        sc = prof["recent_posts"][0]["shortcode"]
        print("POST terbaru:", sc)
        detail = s.post(sc)
        print("DETAIL:", {k: detail[k] for k in
                          ("shortcode", "media_type", "caption", "comment_count") if k in detail})
        print("OEMBED:", {k: v for k, v in
                          s.oembed(f"https://www.instagram.com/p/{sc}/").items()
                          if k in ("author_name", "author_id", "version")})


def main() -> None:
    _demo()


if __name__ == "__main__":
    main()
