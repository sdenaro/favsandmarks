# FavsAndMarks

A lightweight FastAPI service that exposes your Bluesky and Mastodon likes/favorites and bookmarks as RSS 2.0 feeds. Point any RSS reader at the endpoints and stay up to date with the posts you've saved — images and videos included as attachments.

## Features

- **`/fav`** — Returns your Bluesky liked posts as an RSS feed
- **`/marks`** — Returns your Bluesky bookmarked posts as an RSS feed
- **`/combo`** — Returns both Bluesky likes and bookmarks in a single combined RSS feed (deduplicated)
- **`/mastodon/fav`** — Returns your Mastodon favorited posts as an RSS feed
- **`/mastodon/marks`** — Returns your Mastodon bookmarked posts as an RSS feed
- **`/mastodon/combo`** — Returns both Mastodon favorites and bookmarks in a single combined RSS feed (deduplicated)
- **`?limit=N`** — All feed endpoints accept a `limit` query parameter (1–25) to control how many items are returned
- **`?username=name`** — All feed endpoints accept a `username` query parameter to handle multiple accounts
- Media attachments (images, video, external link thumbnails) are included via `<enclosure>` and [Media RSS](http://www.rssboard.org/media-rss) tags
- Authenticates with Bluesky using an app password and Mastodon using an access token — no OAuth flow required

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

## Setup

1. **Clone the repo and install dependencies:**

   ```bash
   git clone <repo-url> && cd favsandmarks
   uv venv
   uv sync
   ```

2. **Configure your credentials:**

   Copy the example environment file and fill in your values:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`:

   ```
   BLUESKY_HANDLE=yourhandle.bsky.social
   BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
   MASTODON_SERVER=https://mastodon.social
   MASTODON_ACCESS_TOKEN=your_access_token
   LIMIT=10

   # Additional accounts (optional)
   FOO_BLUESKY_HANDLE=otherhandle.bsky.social
   FOO_BLUESKY_APP_PASSWORD=yyyy-yyyy-yyyy-yyyy
   FOO_MASTODON_SERVER=https://mastodon.social
   FOO_MASTODON_ACCESS_TOKEN=other_access_token
   ```

   | Variable | Required | Description |
   |----------|----------|-------------|
   | `BLUESKY_HANDLE` | Yes (for Bluesky) | Your default Bluesky handle |
   | `BLUESKY_APP_PASSWORD` | Yes (for Bluesky) | App password for the default Bluesky account |
   | `MASTODON_SERVER` | Yes (for Mastodon) | Your Mastodon instance URL (e.g. `https://mastodon.social`) |
   | `MASTODON_ACCESS_TOKEN` | Yes (for Mastodon) | Access token for the default Mastodon account |
   | `LIMIT` | No | Default number of items returned (1–25, defaults to 10). Values outside this range are clamped. |
   | `<NAME>_BLUESKY_HANDLE` | No | Bluesky handle for an additional account (selected via `?username=<name>`) |
   | `<NAME>_BLUESKY_APP_PASSWORD` | No | App password for the additional Bluesky account |
   | `<NAME>_MASTODON_SERVER` | No | Mastodon server for an additional account |
   | `<NAME>_MASTODON_ACCESS_TOKEN` | No | Access token for the additional Mastodon account |

   You can generate a Bluesky app password at [bsky.app/settings/app-passwords](https://bsky.app/settings/app-passwords) and a Mastodon access token from your server's settings under **Preferences > Development**.

3. **Run the server:**

   ```bash
   uv run uvicorn main:app --reload
   ```

   The API is now available at `http://127.0.0.1:8000`.

## Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Health check — returns JSON listing available endpoints |
| `/fav` | GET | Bluesky liked posts as RSS 2.0 XML |
| `/marks` | GET | Bluesky bookmarked posts as RSS 2.0 XML |
| `/combo` | GET | Bluesky likes & bookmarks combined as RSS 2.0 XML (deduplicated) |
| `/mastodon/fav` | GET | Mastodon favorited posts as RSS 2.0 XML |
| `/mastodon/marks` | GET | Mastodon bookmarked posts as RSS 2.0 XML |
| `/mastodon/combo` | GET | Mastodon favorites & bookmarks combined as RSS 2.0 XML (deduplicated) |

All feed endpoints accept these optional query parameters:

| Parameter | Description |
|-----------|-------------|
| `?limit=N` | Number of items to return (1–25). Defaults to the `LIMIT` env var or 10. |
| `?username=<name>` | Use alternate account credentials (`<NAME>_BLUESKY_HANDLE` / `<NAME>_BLUESKY_APP_PASSWORD` from `.env`). The name is case-insensitive. |

Examples:

```
GET /fav                        # default account, default limit
GET /fav?limit=5                # 5 liked posts from default account
GET /marks?username=foo         # bookmarks from the FOO account
GET /combo?username=foo&limit=3 # 3 combined posts from FOO account
```

Interactive API docs are available at `/docs` (Swagger UI) and `/redoc`.

## RSS Output

Each feed item includes:

- **Title** — Author display name + post text preview
- **Link** — Direct URL to the post on bsky.app
- **Description** — Full post text
- **pubDate** — Original post timestamp
- **Attachments** — Images and videos via both `<enclosure>` (broad reader support) and `<media:content>` (richer metadata)

### Media handling

| Embed type | What's included |
|------------|----------------|
| Images | Full-size image URL (`image/jpeg`) |
| Video | HLS playlist URL (`application/x-mpegURL`) + thumbnail |
| External link | Thumbnail image, if present |
| Quote post with media | Recursively extracts media from the quoted post |

## Project Structure

```
favsandmarks/
├── main.py            # FastAPI application
├── .env               # Bluesky credentials (git-ignored)
├── .env.example       # Reference for required env vars
├── pyproject.toml     # Project metadata and dependencies
├── uv.lock            # Locked dependency versions
└── .gitignore
```

## Dependencies

| Package | Purpose |
|---------|---------|
| [FastAPI](https://fastapi.tiangolo.com/) | Web framework |
| [uvicorn](https://www.uvicorn.org/) | ASGI server |
| [atproto](https://atproto.blue/) | Bluesky AT Protocol SDK |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | Loads `.env` into environment |

## License

MIT
