# instagram-client

Klien Python (logged-out) untuk data publik Instagram: profil, post, dan oEmbed.
Tanpa login, tanpa API key. Hasil reverse engineering ada di
[`../instagram-re`](../instagram-re) (endpoint, header, `doc_id`, risiko).

Dipakai oleh scraper [`crawlerfox/scraper-instagram`](https://github.com/crawlerfox/scraper-instagram).

## Install

```bash
uv add "instagram-client @ git+ssh://git@github.com/scripthon/instagram-client.git@v0.1.0"
```

Memakai `httpx[http2]` — Instagram membalas `429` kosong untuk HTTP/1.1, jadi HTTP/2
wajib.

## Pakai (CLI)

```bash
uv run instagram-client profile nasa
uv run instagram-client post Dbd3EBdnW_u
uv run instagram-client by-id 528817151
uv run instagram-client oembed "https://www.instagram.com/p/Dbd3EBdnW_u/"

# opsi global
uv run instagram-client --proxy http://user:pass@host:8000 --timeout 20 profile nasa
```

Output JSON mentah dari Instagram (objek yang diminta), ke stdout.

## Library

```python
from instagram_client import InstagramClient

ig = InstagramClient()                  # atau InstagramClient(proxy=..., timeout=...)
try:
    user = ig.profile("nasa")           # dict user + 12 post terbaru
    media = ig.post("Dbd3EBdnW_u")      # satu post (image/video/carousel)
    user = ig.profile_by_id("528817151")
    oe = ig.oembed("https://www.instagram.com/p/Dbd3EBdnW_u/")
finally:
    ig.close()
```

Atau sebagai context manager:

```python
with InstagramClient(proxy="http://user:pass@host:8000") as ig:
    print(ig.profile("nasa")["follower_count"])
```

### Opsi constructor

| Opsi | Default | Keterangan |
|---|---|---|
| `proxy` | `None` | Proxy per instance (`http`, `socks5`, `socks5h`). Scraper membuat satu client per `X-Proxy-URL`. |
| `timeout` | `30.0` | Timeout per request (detik). |
| `min_interval` | `6.0` | Jeda minimum antar request (detik) + jitter — Instagram memblok request beruntun. |
| `max_retries` | `3` | Percobaan ulang untuk gate / `429` / `5xx`. |
| `transport` | `None` | Transport alternatif (untuk test/injeksi). |

### Method

| Method | Mengembalikan |
|---|---|
| `profile(username)` | `data.user`: bio, follower/following, `edge_owner_to_timeline_media` (12 post). |
| `profile_by_id(user_id)` | `data.user` untuk id numerik. |
| `post(shortcode)` | Satu `items[0]` post: media, caption, like/komentar, owner. |
| `oembed(post_url)` | Payload oEmbed resmi (title/caption, author, thumbnail). |

### Error

Semua error mewarisi `ClientError`:

- `NotFoundError` — user/post/tidak ada (termasuk respons `200` dengan `data: null`).
- `StatusError` — status HTTP non-2xx; `.status` membawa kodenya.
  Scraper memetakannya lewat kontrak: `401/403` → `503 UPSTREAM_BLOCKED`,
  `429` → `429 UPSTREAM_RATE_LIMITED`, sisanya → `502 UPSTREAM_ERROR`.

Retry/backoff dilakukan di transport untuk gate `401 require_login` / `igweb_rollout`,
`429`, dan `5xx` (tanpa retry untuk `4xx` lain).

## Test

```bash
uv run pytest
uv run ruff check .
```

Test mem-parse sample JSON di `tests/data/` (dari `instagram-re/data-samples/`) dan
memeriksa pemetaan error — tanpa jaringan.
