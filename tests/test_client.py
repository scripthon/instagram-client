from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from instagram_client import InstagramClient, NotFoundError, StatusError, Transport

DATA = Path(__file__).parent / "data"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


# ------------------------------------------------------------- data samples


def test_profile_sample() -> None:
    user = load("profile-nasa.json")["data"]["user"]
    assert user["username"] == "nasa"
    assert user["id"] == "528817151"
    assert user["edge_owner_to_timeline_media"]["edges"]


def test_post_sample() -> None:
    payload = load("post-Dbd3EBdnW_u.json")
    items = payload["data"]["xdt_api__v1__media__shortcode__web_info"]["items"]
    assert items[0]["code"] == "Dbd3EBdnW_u"


def test_user_by_id_sample() -> None:
    user = load("user-by-id-528817151.json")["data"]["user"]
    assert user["pk"] == "528817151"
    assert user["username"] == "nasa"


def test_oembed_sample() -> None:
    payload = load("oembed-Dbd3EBdnW_u.json")
    assert payload["author_name"] == "nasa"
    assert payload["author_id"] == 528817151


# --------------------------------------------------------------- error map


def test_parse_404_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        Transport().parse(httpx.Response(404, text="Page Not Found"))


def test_parse_503_is_status_error() -> None:
    with pytest.raises(StatusError) as exc:
        Transport().parse(httpx.Response(503, text="blocked"))
    assert exc.value.status == 503


def test_parse_strips_for_loop_prefix() -> None:
    resp = httpx.Response(200, text='for (;;);{"status": "ok"}')
    assert Transport().parse(resp) == {"status": "ok"}


# ---------------------------------------------------------- logic, no network


class _FakeTransport:
    """Stands in for Transport so client logic can be tested offline."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.max_retries = 1
        self.rev = "0"
        self.hsi = ""
        self.lsd = ""
        self.csrf = ""

    def _bootstrap(self) -> None:  # pragma: no cover - no-op
        pass

    def request(self, *args: object, **kwargs: object) -> None:
        return None

    def parse(self, resp: object) -> object:
        return self.payload

    def close(self) -> None:  # pragma: no cover - no-op
        pass


def test_post_unknown_shortcode_is_not_found() -> None:
    client = InstagramClient(transport=_FakeTransport({"data": None, "errors": [{"code": 1675030}]}))
    with pytest.raises(NotFoundError):
        client.post("nope")


def test_profile_by_id_unknown_is_not_found() -> None:
    client = InstagramClient(transport=_FakeTransport({"errors": [{"code": 1675030}]}))
    with pytest.raises(NotFoundError):
        client.profile_by_id("1")
