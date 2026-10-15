#!/usr/bin/env python3
"""Instagram client (logged-out) — hasil reverse engineering 14 Okt 2026.

Semua method MENGEMBALIKAN JSON MENTAH (raw response) dari endpoint, tanpa
normalisasi/apapun. Error tingkat API (gate, login_required, graphql errors)
ikut dikirim apa adanya sebagai isi response.

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

Pakai:
    instagram-client profile <username>
    instagram-client post <shortcode>
    instagram-client by-id <user_id>
    instagram-client oembed <url-post>
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time

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


class InstagramScraper:
    def __init__(self, min_interval: float = 6.0, timeout: float = 20.0,
                 max_retries: int = 3):
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
    def _raw(resp: httpx.Response) -> dict:
        """Parse body apa adanya; hanya prefix for (;;); dilepas supaya valid
        JSON. Non-JSON dibungkus {"__raw__": ...} supaya tetap terbaca."""
        text = resp.text
        if text.startswith("for (;;);"):
            text = text[len("for (;;);"):]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"__raw__": text, "__http_status__": resp.status_code}

    def _bootstrap(self) -> None:
        """Ambil cookie (csrftoken/mid) + token lsd/hsi/rev dari landing page."""
        if self._bootstrapped:
            return
        self._throttle()
        text = self.web.get("https://www.instagram.com/").text
        m = re.search(r'lsd":"([^"]+)"', text)
        self.lsd = m.group(1) if m else ""
        m = re.search(r'"hsi":"(\d+)"', text)
        self.hsi = m.group(1) if m else ""
        m = re.search(r'"client_revision":(\d+)', text)
        self.rev = m.group(1) if m else "0"
        self.csrf = self.web.cookies.get("csrftoken") or ""
        self._bootstrapped = True

    def _request(self, sess: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
        """Kirim request; gate 401 / 429 (jendela rate limit) di-retry dulu.
        Kalau tetap kena, response terakhir tetap DIKEMBALIKAN (raw)."""
        resp: httpx.Response | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            resp = sess.request(method, url, timeout=self.timeout, **kw)
            if self._is_gate(resp) or resp.status_code == 429:
                time.sleep(20 * (attempt + 1) + random.uniform(0, 8))
                continue
            return resp
        assert resp is not None
        return resp

    # --------------------------------------------------------------- graphql
    def _graphql(self, doc_id: str, variables: dict, friendly: str,
                 endpoint: str = GRAPHQL_DIST, root_field: str | None = None,
                 referer: str = "https://www.instagram.com/") -> dict:
        """Kirim query persisten Relay, kembalikan payload mentah (termasuk
        field errors/extensions bila ada). Envelope error FB yang sementara
        (for (;;);{"__ar":1,...}) tetap di-retry sebelum menyerah."""
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

        payload: dict = {}
        for attempt in range(self.max_retries):
            resp = self._request(self.web, "POST", endpoint, data=form, headers=headers)
            payload = self._raw(resp)
            transient = "__ar" in payload or "__raw__" in payload
            if transient and attempt < self.max_retries - 1:
                time.sleep(20 * (attempt + 1) + random.uniform(0, 8))
                continue
            break
        return payload

    # ------------------------------------------------------------------ API
    def profile(self, username: str) -> dict:
        """Raw response web_profile_info (profil + 12 post terbaru)."""
        url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
        return self._raw(self._request(self.mobile, "GET", url))

    def profile_by_id(self, user_id: str) -> dict:
        """Raw response PolarisProfilePageContentQuery (profil by numeric id)."""
        variables = {
            "id": str(user_id),
            "enable_integrity_filters": False,
            "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": False,
        }
        return self._graphql(DOC_PROFILE_PAGE, variables,
                             "PolarisProfilePageContentQuery", GRAPHQL_WWW,
                             referer="https://www.instagram.com/")

    def post(self, shortcode: str) -> dict:
        """Raw response PolarisPostRootQuery (detail post by shortcode)."""
        variables = {"shortcode": shortcode, PV_SHORTD[0]: PV_SHORTD[1],
                     PV_MULTIC[0]: PV_MULTIC[1]}
        return self._graphql(DOC_POST_ROOT, variables, "PolarisPostRootQuery",
                             GRAPHQL_DIST, root_field=ROOT_POST,
                             referer=f"https://www.instagram.com/p/{shortcode}/")

    def oembed(self, post_url: str) -> dict:
        """Raw response oEmbed resmi."""
        return self._raw(self._request(
            self.web, "GET", "https://www.instagram.com/api/v1/oembed/",
            params={"url": post_url}))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="instagram-client",
        description="Instagram logged-out client — mencetak raw response JSON.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("profile", help="profil + 12 post terbaru (by username)")
    p.add_argument("username")
    p = sub.add_parser("post", help="detail post (by shortcode)")
    p.add_argument("shortcode")
    p = sub.add_parser("by-id", help="profil (by numeric user id)")
    p.add_argument("user_id")
    p = sub.add_parser("oembed", help="oEmbed (by URL post)")
    p.add_argument("url")

    args = parser.parse_args(argv)
    s = InstagramScraper()
    result = {
        "profile": lambda: s.profile(args.username),
        "post": lambda: s.post(args.shortcode),
        "by-id": lambda: s.profile_by_id(args.user_id),
        "oembed": lambda: s.oembed(args.url),
    }[args.cmd]()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
