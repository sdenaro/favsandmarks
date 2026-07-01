"""FavsAndMarks — Bluesky likes & bookmarks as RSS feeds."""

import os
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from atproto import Client
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

load_dotenv()

BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD", "")
DEFAULT_LIMIT = min(max(int(os.getenv("LIMIT", "10")), 1), 25)

app = FastAPI(
    title="FavsAndMarks",
    description="Bluesky likes and bookmarks served as RSS feeds.",
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

    return media


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

        link = post_uri_to_url(uri, handle)

        item = SubElement(channel, "item")
        SubElement(item, "title").text = f"{display_name}: {text[:100]}"
        SubElement(item, "link").text = link
        SubElement(item, "guid", isPermaLink="true").text = link
        SubElement(item, "description").text = text

        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                SubElement(item, "pubDate").text = dt.strftime(
                    "%a, %d %b %Y %H:%M:%S +0000"
                )
            except (ValueError, TypeError):
                pass

        # Attach media via Media RSS namespace
        embed = getattr(post, "embed", None)
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


@app.get("/")
def root():
    """Health check / index."""
    return {
        "app": "FavsAndMarks",
        "endpoints": {
            "/fav": "Liked posts as RSS (?limit=1..25, ?username=NAME)",
            "/marks": "Bookmarked posts as RSS (?limit=1..25, ?username=NAME)",
            "/combo": "Likes & bookmarks combined as RSS (?limit=1..25, ?username=NAME)",
        },
    }
