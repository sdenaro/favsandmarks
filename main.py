"""FavsAndMarks — Bluesky likes & bookmarks as RSS feeds."""

import os
import re
from datetime import datetime, timezone
from html import escape as html_escape, unescape as html_unescape
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx
from atproto import Client
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

load_dotenv()

BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD", "")
MASTODON_SERVER = os.getenv("MASTODON_SERVER", "")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN", "")
DEFAULT_LIMIT = min(max(int(os.getenv("LIMIT", "10")), 1), 25)

app = FastAPI(
    title="FavsAndMarks",
    description="Bluesky and Mastodon likes/favorites and bookmarks served as RSS feeds.",
)


def get_mastodon_credentials(username: str | None = None) -> tuple[str, str]:
    """Retrieve Mastodon server URL and access token.

    When *username* is provided, credentials are read from
    ``{USERNAME}_MASTODON_SERVER`` and ``{USERNAME}_MASTODON_ACCESS_TOKEN``.
    Otherwise the default ``MASTODON_SERVER`` / ``MASTODON_ACCESS_TOKEN`` are used.
    """
    if username:
        prefix = username.upper()
        server = os.getenv(f"{prefix}_MASTODON_SERVER", "")
        token = os.getenv(f"{prefix}_MASTODON_ACCESS_TOKEN", "")
        if not server or not token:
            raise HTTPException(
                status_code=404,
                detail=f"{prefix}_MASTODON_SERVER and {prefix}_MASTODON_ACCESS_TOKEN must be set in .env",
            )
    else:
        server = os.getenv("MASTODON_SERVER", MASTODON_SERVER)
        token = os.getenv("MASTODON_ACCESS_TOKEN", MASTODON_ACCESS_TOKEN)
        if not server or not token:
            raise HTTPException(
                status_code=500,
                detail="MASTODON_SERVER and MASTODON_ACCESS_TOKEN must be set in .env",
            )
    server = server.rstrip("/")
    if not server.startswith("http://") and not server.startswith("https://"):
        server = f"https://{server}"
    return server, token


def get_mastodon_account(server: str, token: str) -> dict:
    """Fetch authenticated Mastodon account info."""
    url = f"{server}/api/v1/accounts/verify_credentials"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error verifying Mastodon credentials: {str(e)}",
        )


def fetch_mastodon_statuses(endpoint: str, server: str, token: str, limit: int) -> list[dict]:
    """Fetch statuses from a Mastodon API endpoint (e.g. /api/v1/favourites)."""
    url = f"{server}{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": limit}
    try:
        response = httpx.get(url, headers=headers, params=params, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error fetching data from Mastodon API: {str(e)}",
        )


def clean_html_text(html_content: str) -> str:
    """Strip HTML tags and unescape HTML entities for title creation."""
    if not html_content:
        return ""
    text = re.sub(r"<[^>]+>", "", html_content)
    return html_unescape(text).strip()


def extract_mastodon_media(media_attachments: list[dict]) -> list[dict]:
    """Extract media attachments from Mastodon status into {url, mime_type, type, preview_url} dicts."""
    media: list[dict] = []
    for att in media_attachments or []:
        url = att.get("url") or att.get("remote_url")
        if not url:
            continue
        att_type = att.get("type", "image")
        preview_url = att.get("preview_url")

        if att_type == "image":
            mime_type = "image/jpeg"
            if url.lower().endswith(".png"):
                mime_type = "image/png"
            elif url.lower().endswith(".gif"):
                mime_type = "image/gif"
            elif url.lower().endswith(".webp"):
                mime_type = "image/webp"
            media.append({"url": url, "mime_type": mime_type, "type": "image"})
        elif att_type in ("video", "gifv"):
            media.append({"url": url, "mime_type": "video/mp4", "type": "video", "preview_url": preview_url})
        elif att_type == "audio":
            media.append({"url": url, "mime_type": "audio/mpeg", "type": "audio"})
        else:
            media.append({"url": url, "mime_type": "application/octet-stream", "type": "file"})
    return media


def build_mastodon_rss(title: str, description: str, statuses: list[dict], server: str, acct: str) -> str:
    """Build an RSS 2.0 XML string from a list of Mastodon status dicts."""
    rss = Element("rss", version="2.0", attrib={
        "xmlns:atom": "http://www.w3.org/2005/Atom",
        "xmlns:media": "http://search.yahoo.com/mrss/",
    })
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = title
    SubElement(channel, "description").text = description
    SubElement(channel, "link").text = f"{server}/@{acct}" if acct else server
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    for status in statuses:
        account = status.get("account") or {}
        display_name = account.get("display_name") or account.get("username") or "Unknown"
        raw_content = status.get("content", "")
        clean_text = clean_html_text(raw_content)
        created_at = status.get("created_at")
        link = status.get("url") or status.get("uri", "")

        spoiler_text = status.get("spoiler_text", "")
        desc_content = raw_content
        if spoiler_text:
            desc_content = f"<p><strong>[CW: {html_escape(spoiler_text)}]</strong></p>" + raw_content

        item = SubElement(channel, "item")
        SubElement(item, "title").text = f"{display_name}: {clean_text[:100]}"
        SubElement(item, "link").text = link
        SubElement(item, "guid", isPermaLink="true").text = link
        SubElement(item, "description").text = desc_content

        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                SubElement(item, "pubDate").text = dt.strftime(
                    "%a, %d %b %Y %H:%M:%S +0000"
                )
            except (ValueError, TypeError):
                pass

        attachments = extract_mastodon_media(status.get("media_attachments", []))
        for att in attachments:
            SubElement(
                item,
                "enclosure",
                url=att["url"],
                type=att["mime_type"],
                length="0",
            )
            medium_attr = "image"
            if att["type"] == "video":
                medium_attr = "video"
            elif att["type"] == "audio":
                medium_attr = "audio"

            SubElement(
                item,
                "media:content",
                url=att["url"],
                type=att["mime_type"],
                medium=medium_attr,
            )
            if att.get("preview_url") and att["type"] == "video":
                SubElement(
                    item,
                    "media:thumbnail",
                    url=att["preview_url"],
                )

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
        rss, encoding="unicode"
    )


def get_client(username: str | None = None) -> tuple[Client, str]:
    """Create and authenticate a Bluesky AT Protocol client.

    When *username* is provided, credentials are read from
    ``{USERNAME}_BLUESKY_HANDLE`` and ``{USERNAME}_BLUESKY_APP_PASSWORD``.
    Otherwise the default ``BLUESKY_HANDLE`` / ``BLUESKY_APP_PASSWORD`` are
    used.

    Returns a ``(client, handle)`` tuple.
    """
    if username:
        prefix = username.upper()
        handle = os.getenv(f"{prefix}_BLUESKY_HANDLE", "")
        app_password = os.getenv(f"{prefix}_BLUESKY_APP_PASSWORD", "")
        if not handle or not app_password:
            raise HTTPException(
                status_code=404,
                detail=f"{prefix}_BLUESKY_HANDLE and {prefix}_BLUESKY_APP_PASSWORD must be set in .env",
            )
    else:
        handle = BLUESKY_HANDLE
        app_password = BLUESKY_APP_PASSWORD
        if not handle or not app_password:
            raise HTTPException(
                status_code=500,
                detail="BLUESKY_HANDLE and BLUESKY_APP_PASSWORD must be set in .env",
            )
    client = Client()
    client.login(handle, app_password)
    return client, handle


# ---------------------------------------------------------------------------
# Helpers for building post URLs and extracting media
# ---------------------------------------------------------------------------

def post_uri_to_url(uri: str, author_handle: str) -> str:
    """Convert an AT URI like at://did:plc:xxx/app.bsky.feed.post/abc
    into a web URL like https://bsky.app/profile/handle/post/abc."""
    rkey = uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{author_handle}/post/{rkey}"


def extract_media(embed) -> list[dict]:
    """Return a list of {url, mime_type, type} dicts from a post embed."""
    media: list[dict] = []
    if embed is None:
        return media

    py_type = getattr(embed, "py_type", "") or ""

    # Direct images
    if py_type == "app.bsky.embed.images#view":
        for img in getattr(embed, "images", []):
            url = getattr(img, "fullsize", None) or getattr(img, "thumb", None)
            if url:
                media.append({"url": url, "mime_type": "image/jpeg", "type": "image"})

    # Direct video
    elif py_type == "app.bsky.embed.video#view":
        playlist = getattr(embed, "playlist", None)
        thumbnail = getattr(embed, "thumbnail", None)
        if playlist:
            media.append({"url": playlist, "mime_type": "application/x-mpegURL", "type": "video"})
        if thumbnail:
            media.append({"url": thumbnail, "mime_type": "image/jpeg", "type": "video_thumb"})

    # External link with thumbnail
    elif py_type == "app.bsky.embed.external#view":
        ext = getattr(embed, "external", None)
        if ext:
            thumb = getattr(ext, "thumb", None)
            if thumb:
                media.append({"url": thumb, "mime_type": "image/jpeg", "type": "image"})

    # Record-with-media (quote post that also has images/video)
    elif py_type == "app.bsky.embed.recordWithMedia#view":
        inner_media = getattr(embed, "media", None)
        if inner_media:
            media.extend(extract_media(inner_media))
        # Also extract media from the quoted post's embeds
        inner_record = getattr(embed, "record", None)
        if inner_record:
            record_view = getattr(inner_record, "record", None)
            if record_view:
                for inner_embed in getattr(record_view, "embeds", []) or []:
                    media.extend(extract_media(inner_embed))

    # Quote post (no additional media on the outer post)
    elif py_type == "app.bsky.embed.record#view":
        record_view = getattr(embed, "record", None)
        if record_view:
            for inner_embed in getattr(record_view, "embeds", []) or []:
                media.extend(extract_media(inner_embed))

    return media


def extract_external_link(embed) -> dict | None:
    """Extract external link URL, title, and description from a post embed.

    Returns a dict with ``uri``, ``title``, and ``description`` keys, or
    *None* when the embed is not (or does not contain) an external link.
    """
    if embed is None:
        return None

    py_type = getattr(embed, "py_type", "") or ""

    if py_type == "app.bsky.embed.external#view":
        ext = getattr(embed, "external", None)
        if ext:
            uri = getattr(ext, "uri", None)
            if uri:
                return {
                    "uri": uri,
                    "title": getattr(ext, "title", None) or "",
                    "description": getattr(ext, "description", None) or "",
                }

    # recordWithMedia may have an external link as its media side
    if py_type == "app.bsky.embed.recordWithMedia#view":
        inner_media = getattr(embed, "media", None)
        if inner_media:
            return extract_external_link(inner_media)

    return None


def extract_quote(embed) -> dict | None:
    """Extract quoted post text, author, and URL from a record embed.

    Works for both ``app.bsky.embed.record#view`` (pure quote) and
    ``app.bsky.embed.recordWithMedia#view`` (quote + media).  Returns
    *None* for any other embed type or when the quoted record is not
    a viewRecord (e.g. viewNotFound, viewBlocked).
    """
    if embed is None:
        return None

    py_type = getattr(embed, "py_type", "") or ""

    record_view = None

    if py_type == "app.bsky.embed.record#view":
        record_view = getattr(embed, "record", None)
    elif py_type == "app.bsky.embed.recordWithMedia#view":
        inner_record = getattr(embed, "record", None)
        if inner_record:
            record_view = getattr(inner_record, "record", None)

    if record_view is None:
        return None

    # Only process actual viewRecord objects (skip viewNotFound, viewBlocked)
    rv_type = getattr(record_view, "py_type", "") or ""
    if rv_type and "viewRecord" not in rv_type:
        return None

    author = getattr(record_view, "author", None)
    value = getattr(record_view, "value", None)
    uri = getattr(record_view, "uri", "")

    author_handle = getattr(author, "handle", "unknown") if author else "unknown"
    author_name = (
        getattr(author, "display_name", author_handle) if author else author_handle
    )
    text = getattr(value, "text", "") if value else ""

    quote_url = post_uri_to_url(uri, author_handle) if uri else ""

    return {
        "text": text,
        "author_handle": author_handle,
        "author_name": author_name,
        "url": quote_url,
    }


def _build_description(text: str, embed) -> str:
    """Build an item description, enriched with external link and quote info.

    When the post has no external link or quote, the plain *text* is
    returned unchanged (preserving existing behaviour).  Otherwise an
    HTML string is returned so RSS readers can render clickable links
    and blockquotes.
    """
    ext_link = extract_external_link(embed)
    quote = extract_quote(embed)

    if not ext_link and not quote:
        return text

    parts: list[str] = []
    if text:
        parts.append(f"<p>{html_escape(text)}</p>")

    if ext_link:
        uri = html_escape(ext_link["uri"], quote=True)
        title = html_escape(ext_link["title"]) if ext_link["title"] else ""
        desc = html_escape(ext_link["description"]) if ext_link["description"] else ""
        link_html = "<p>\U0001f517 "
        if title:
            link_html += f'<a href="{uri}">{title}</a>'
        else:
            link_html += f'<a href="{uri}">{html_escape(ext_link["uri"])}</a>'
        if desc:
            link_html += f"<br/>{desc}"
        link_html += "</p>"
        parts.append(link_html)

    if quote:
        q_author = html_escape(quote["author_name"])
        q_handle = html_escape(quote["author_handle"])
        q_text = html_escape(quote["text"]) if quote["text"] else ""
        q_url = html_escape(quote["url"], quote=True) if quote["url"] else ""
        header = f"<strong>{q_author}</strong> (@{q_handle})"
        if q_url:
            header = f'<a href="{q_url}">{header}</a>'
        quote_html = f"<blockquote><p>{header}:</p>"
        if q_text:
            quote_html += f"<p>{q_text}</p>"
        quote_html += "</blockquote>"
        parts.append(quote_html)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# RSS generation
# ---------------------------------------------------------------------------

def build_rss(title: str, description: str, posts: list, handle: str = "") -> str:
    """Build an RSS 2.0 XML string from a list of post view objects."""
    profile_handle = handle or BLUESKY_HANDLE
    rss = Element("rss", version="2.0", attrib={
        "xmlns:atom": "http://www.w3.org/2005/Atom",
        "xmlns:media": "http://search.yahoo.com/mrss/",
    })
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = title
    SubElement(channel, "description").text = description
    SubElement(channel, "link").text = f"https://bsky.app/profile/{profile_handle}"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    for post_view in posts:
        post = post_view.post if hasattr(post_view, "post") else post_view
        record = getattr(post, "record", None)
        author = getattr(post, "author", None)
        handle = getattr(author, "handle", "unknown") if author else "unknown"
        display_name = getattr(author, "display_name", handle) if author else handle

        text = getattr(record, "text", "") if record else ""
        created_at = getattr(record, "created_at", None) if record else None
        uri = getattr(post, "uri", "")
        embed = getattr(post, "embed", None)

        link = post_uri_to_url(uri, handle)

        item = SubElement(channel, "item")
        SubElement(item, "title").text = f"{display_name}: {text[:100]}"
        SubElement(item, "link").text = link
        SubElement(item, "guid", isPermaLink="true").text = link
        SubElement(item, "description").text = _build_description(text, embed)

        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                SubElement(item, "pubDate").text = dt.strftime(
                    "%a, %d %b %Y %H:%M:%S +0000"
                )
            except (ValueError, TypeError):
                pass

        # Attach media via Media RSS namespace
        attachments = extract_media(embed)
        for att in attachments:
            SubElement(
                item,
                "enclosure",
                url=att["url"],
                type=att["mime_type"],
                length="0",
            )
            media_content = SubElement(
                item,
                "media:content",
                url=att["url"],
                type=att["mime_type"],
                medium=att["type"] if att["type"] in ("image", "video") else "image",
            )
            # For video thumbnails, add media:thumbnail instead
            if att["type"] == "video_thumb":
                media_content.tag = "media:thumbnail"

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
        rss, encoding="unicode"
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/fav", response_class=Response)
def get_favorites(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=25),
    username: str | None = Query(default=None),
):
    """Return liked/favorited posts as RSS."""
    client, handle = get_client(username)
    response = client.app.bsky.feed.get_actor_likes(
        params={"actor": client.me.did, "limit": limit}
    )
    rss_xml = build_rss(
        title=f"Bluesky Likes — @{handle}",
        description=f"Last {limit} liked posts by @{handle}",
        posts=response.feed,
        handle=handle,
    )
    return Response(content=rss_xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/marks", response_class=Response)
def get_bookmarks(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=25),
    username: str | None = Query(default=None),
):
    """Return bookmarked posts as RSS.

    Bookmarks return only URIs, so we hydrate them via getPosts.
    """
    client, handle = get_client(username)
    bookmarks_response = client.app.bsky.bookmark.get_bookmarks(
        params={"limit": limit}
    )

    # Extract the post URIs from bookmark items
    uris: list[str] = []
    for bm in bookmarks_response.bookmarks:
        subject = getattr(bm, "subject", None)
        if subject:
            uri = getattr(subject, "uri", None) or str(subject)
            uris.append(uri)

    if not uris:
        rss_xml = build_rss(
            title=f"Bluesky Bookmarks — @{handle}",
            description=f"Last {limit} bookmarked posts by @{handle}",
            posts=[],
            handle=handle,
        )
        return Response(
            content=rss_xml, media_type="application/rss+xml; charset=utf-8"
        )

    # Hydrate: fetch full post data for each bookmarked URI
    hydrated = client.app.bsky.feed.get_posts(params={"uris": uris})

    rss_xml = build_rss(
        title=f"Bluesky Bookmarks — @{handle}",
        description=f"Last {limit} bookmarked posts by @{handle}",
        posts=hydrated.posts,
        handle=handle,
    )
    return Response(content=rss_xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/combo", response_class=Response)
def get_combo(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=25),
    username: str | None = Query(default=None),
):
    """Return both liked and bookmarked posts combined as a single RSS feed."""
    client, handle = get_client(username)

    # Fetch likes
    likes_response = client.app.bsky.feed.get_actor_likes(
        params={"actor": client.me.did, "limit": limit}
    )
    like_posts = [
        pv.post if hasattr(pv, "post") else pv for pv in likes_response.feed
    ]

    # Fetch bookmarks
    bookmarks_response = client.app.bsky.bookmark.get_bookmarks(
        params={"limit": limit}
    )
    uris: list[str] = []
    for bm in bookmarks_response.bookmarks:
        subject = getattr(bm, "subject", None)
        if subject:
            uri = getattr(subject, "uri", None) or str(subject)
            uris.append(uri)

    bookmark_posts: list = []
    if uris:
        hydrated = client.app.bsky.feed.get_posts(params={"uris": uris})
        bookmark_posts = list(hydrated.posts)

    # Merge and deduplicate by URI, preserving order (likes first)
    seen: set[str] = set()
    combined: list = []
    for post in like_posts + bookmark_posts:
        post_uri = getattr(post, "uri", "")
        if post_uri not in seen:
            seen.add(post_uri)
            combined.append(post)

    rss_xml = build_rss(
        title=f"Bluesky Likes & Bookmarks — @{handle}",
        description=f"Last {limit} liked and bookmarked posts by @{handle}",
        posts=combined,
        handle=handle,
    )
    return Response(content=rss_xml, media_type="application/rss+xml; charset=utf-8")


# ---------------------------------------------------------------------------
# Mastodon Endpoints
# ---------------------------------------------------------------------------

@app.get("/mastodon/fav", response_class=Response)
def get_mastodon_favorites(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=25),
    username: str | None = Query(default=None),
):
    """Return favorited Mastodon posts as RSS."""
    server, token = get_mastodon_credentials(username)
    acct_info = get_mastodon_account(server, token)
    acct = acct_info.get("acct", "")
    statuses = fetch_mastodon_statuses("/api/v1/favourites", server, token, limit)
    rss_xml = build_mastodon_rss(
        title=f"Mastodon Favorites — @{acct}",
        description=f"Last {limit} favorited posts by @{acct}",
        statuses=statuses,
        server=server,
        acct=acct,
    )
    return Response(content=rss_xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/mastodon/marks", response_class=Response)
def get_mastodon_bookmarks(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=25),
    username: str | None = Query(default=None),
):
    """Return bookmarked Mastodon posts as RSS."""
    server, token = get_mastodon_credentials(username)
    acct_info = get_mastodon_account(server, token)
    acct = acct_info.get("acct", "")
    statuses = fetch_mastodon_statuses("/api/v1/bookmarks", server, token, limit)
    rss_xml = build_mastodon_rss(
        title=f"Mastodon Bookmarks — @{acct}",
        description=f"Last {limit} bookmarked posts by @{acct}",
        statuses=statuses,
        server=server,
        acct=acct,
    )
    return Response(content=rss_xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/mastodon/combo", response_class=Response)
def get_mastodon_combo(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=25),
    username: str | None = Query(default=None),
):
    """Return both favorited and bookmarked Mastodon posts combined as RSS."""
    server, token = get_mastodon_credentials(username)
    acct_info = get_mastodon_account(server, token)
    acct = acct_info.get("acct", "")

    fav_statuses = fetch_mastodon_statuses("/api/v1/favourites", server, token, limit)
    mark_statuses = fetch_mastodon_statuses("/api/v1/bookmarks", server, token, limit)

    seen_ids: set = set()
    combined: list = []
    for st in fav_statuses + mark_statuses:
        st_id = st.get("id")
        if st_id and st_id not in seen_ids:
            seen_ids.add(st_id)
            combined.append(st)
        elif not st_id:
            combined.append(st)

    rss_xml = build_mastodon_rss(
        title=f"Mastodon Favorites & Bookmarks — @{acct}",
        description=f"Last {limit} favorited and bookmarked posts by @{acct}",
        statuses=combined,
        server=server,
        acct=acct,
    )
    return Response(content=rss_xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/")
def root():
    """Health check / index."""
    return {
        "app": "FavsAndMarks",
        "endpoints": {
            "/fav": "Bluesky liked posts as RSS (?limit=1..25, ?username=<name>)",
            "/marks": "Bluesky bookmarked posts as RSS (?limit=1..25, ?username=<name>)",
            "/combo": "Bluesky likes & bookmarks combined as RSS (?limit=1..25, ?username=<name>)",
            "/mastodon/fav": "Mastodon favorited posts as RSS (?limit=1..25, ?username=<name>)",
            "/mastodon/marks": "Mastodon bookmarked posts as RSS (?limit=1..25, ?username=<name>)",
            "/mastodon/combo": "Mastodon favorites & bookmarks combined as RSS (?limit=1..25, ?username=<name>)",
        },
    }
