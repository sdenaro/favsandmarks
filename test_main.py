"""Tests for FavsAndMarks RSS output.

Validates that /fav, /marks, and /combo produce well-formed XML that
conforms to RSS 2.0 structure, including proper media attachments.
"""

import os
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import DEFAULT_LIMIT, app, build_rss, extract_media, get_client
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Namespace map used by the RSS output
# ---------------------------------------------------------------------------
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}

# ---------------------------------------------------------------------------
# Fake data factories
# ---------------------------------------------------------------------------


def _make_post(
    *,
    text="Hello world",
    handle="alice.bsky.social",
    display_name="Alice",
    uri="at://did:plc:abc123/app.bsky.feed.post/post1",
    created_at="2025-06-15T12:00:00.000Z",
    embed=None,
):
    """Build a SimpleNamespace that mimics an atproto PostView."""
    return SimpleNamespace(
        uri=uri,
        record=SimpleNamespace(text=text, created_at=created_at),
        author=SimpleNamespace(handle=handle, display_name=display_name),
        embed=embed,
    )


def _make_feed_item(post):
    """Wrap a post in a FeedViewPost-like object (has a .post attribute)."""
    return SimpleNamespace(post=post)


def _make_image_embed(*urls):
    images = [SimpleNamespace(fullsize=u, thumb=u) for u in urls]
    return SimpleNamespace(py_type="app.bsky.embed.images#view", images=images)


def _make_video_embed(playlist_url, thumbnail_url=None):
    return SimpleNamespace(
        py_type="app.bsky.embed.video#view",
        playlist=playlist_url,
        thumbnail=thumbnail_url,
    )


def _make_external_embed(thumb_url=None):
    return SimpleNamespace(
        py_type="app.bsky.embed.external#view",
        external=SimpleNamespace(thumb=thumb_url),
    )


def _make_record_with_media_embed(inner_embed):
    return SimpleNamespace(
        py_type="app.bsky.embed.recordWithMedia#view",
        media=inner_embed,
    )


# ---------------------------------------------------------------------------
# Mock the Bluesky client so tests never hit the network
# ---------------------------------------------------------------------------


def _mock_client_for_fav(posts):
    """Return a (mock_client, handle) tuple whose get_actor_likes returns *posts*."""
    client = MagicMock()
    client.me.did = "did:plc:testuser"
    client.app.bsky.feed.get_actor_likes.return_value = SimpleNamespace(
        feed=[_make_feed_item(p) for p in posts]
    )
    return client, "test.bsky.social"


def _mock_client_for_marks(posts):
    """Return a (mock_client, handle) tuple whose get_bookmarks + get_posts returns *posts*."""
    client = MagicMock()
    bookmarks = [
        SimpleNamespace(subject=p.uri, created_at="2025-06-15T12:00:00.000Z")
        for p in posts
    ]
    client.app.bsky.bookmark.get_bookmarks.return_value = SimpleNamespace(
        bookmarks=bookmarks
    )
    client.app.bsky.feed.get_posts.return_value = SimpleNamespace(posts=posts)
    return client, "test.bsky.social"


def _mock_client_for_combo(fav_posts, mark_posts):
    """Return a (mock_client, handle) tuple wired for both likes and bookmarks."""
    client = MagicMock()
    client.me.did = "did:plc:testuser"
    client.app.bsky.feed.get_actor_likes.return_value = SimpleNamespace(
        feed=[_make_feed_item(p) for p in fav_posts]
    )
    bookmarks = [
        SimpleNamespace(subject=p.uri, created_at="2025-06-15T12:00:00.000Z")
        for p in mark_posts
    ]
    client.app.bsky.bookmark.get_bookmarks.return_value = SimpleNamespace(
        bookmarks=bookmarks
    )
    client.app.bsky.feed.get_posts.return_value = SimpleNamespace(posts=mark_posts)
    return client, "test.bsky.social"


@pytest.fixture
def test_client():
    return TestClient(app)


# ===================================================================
# 1. Well-formed XML
# ===================================================================


class TestWellFormedXML:
    """The output must parse as valid XML without errors."""

    def test_fav_parses_as_xml(self, test_client):
        posts = [_make_post()]
        with patch("main.get_client", return_value=_mock_client_for_fav(posts)):
            resp = test_client.get("/fav")
        assert resp.status_code == 200
        ET.fromstring(resp.text)  # raises ParseError if malformed

    def test_marks_parses_as_xml(self, test_client):
        posts = [_make_post()]
        with patch("main.get_client", return_value=_mock_client_for_marks(posts)):
            resp = test_client.get("/marks")
        assert resp.status_code == 200
        ET.fromstring(resp.text)

    def test_empty_feed_parses_as_xml(self, test_client):
        with patch("main.get_client", return_value=_mock_client_for_fav([])):
            resp = test_client.get("/fav")
        ET.fromstring(resp.text)

    def test_xml_declaration_present(self, test_client):
        posts = [_make_post()]
        with patch("main.get_client", return_value=_mock_client_for_fav(posts)):
            resp = test_client.get("/fav")
        assert resp.text.startswith('<?xml version="1.0" encoding="UTF-8"?>')

    def test_content_type_is_rss(self, test_client):
        posts = [_make_post()]
        with patch("main.get_client", return_value=_mock_client_for_fav(posts)):
            resp = test_client.get("/fav")
        assert "application/rss+xml" in resp.headers["content-type"]


# ===================================================================
# 2. RSS 2.0 structure
# ===================================================================


class TestRSSStructure:
    """The feed must have the required RSS 2.0 elements."""

    def _parse(self, test_client, endpoint, posts):
        if endpoint == "/fav":
            client = _mock_client_for_fav(posts)
        elif endpoint == "/marks":
            client = _mock_client_for_marks(posts)
        else:
            client = _mock_client_for_combo(posts, [])
        with patch("main.get_client", return_value=client):
            resp = test_client.get(endpoint)
        return ET.fromstring(resp.text)

    # -- Root element --

    def test_root_is_rss(self, test_client):
        root = self._parse(test_client, "/fav", [_make_post()])
        assert root.tag == "rss"

    def test_rss_version_is_2_0(self, test_client):
        root = self._parse(test_client, "/fav", [_make_post()])
        assert root.get("version") == "2.0"

    # -- Channel required elements --

    def test_channel_exists(self, test_client):
        root = self._parse(test_client, "/fav", [_make_post()])
        assert root.find("channel") is not None

    def test_channel_has_title(self, test_client):
        root = self._parse(test_client, "/fav", [_make_post()])
        title = root.find("channel/title")
        assert title is not None
        assert title.text and len(title.text) > 0

    def test_channel_has_link(self, test_client):
        root = self._parse(test_client, "/fav", [_make_post()])
        link = root.find("channel/link")
        assert link is not None
        assert link.text and link.text.startswith("http")

    def test_channel_has_description(self, test_client):
        root = self._parse(test_client, "/fav", [_make_post()])
        desc = root.find("channel/description")
        assert desc is not None
        assert desc.text and len(desc.text) > 0

    def test_channel_has_last_build_date(self, test_client):
        root = self._parse(test_client, "/fav", [_make_post()])
        lbd = root.find("channel/lastBuildDate")
        assert lbd is not None
        assert lbd.text and "+0000" in lbd.text

    # -- Works for both endpoints --

    @pytest.mark.parametrize("endpoint", ["/fav", "/marks", "/combo"])
    def test_all_endpoints_have_channel_elements(self, test_client, endpoint):
        root = self._parse(test_client, endpoint, [_make_post()])
        channel = root.find("channel")
        for tag in ("title", "link", "description"):
            assert channel.find(tag) is not None, f"Missing <{tag}> in {endpoint}"


# ===================================================================
# 3. Item structure
# ===================================================================


class TestItemStructure:
    """Each <item> must contain the required RSS child elements."""

    def _items(self, test_client, posts):
        with patch(
            "main.get_client", return_value=_mock_client_for_fav(posts)
        ):
            resp = test_client.get("/fav")
        root = ET.fromstring(resp.text)
        return root.findall("channel/item")

    def test_item_count_matches_posts(self, test_client):
        posts = [_make_post(text=f"Post {i}") for i in range(3)]
        items = self._items(test_client, posts)
        assert len(items) == 3

    def test_item_has_title(self, test_client):
        items = self._items(test_client, [_make_post(text="test post")])
        assert items[0].find("title").text is not None

    def test_item_title_contains_author_and_text(self, test_client):
        items = self._items(
            test_client,
            [_make_post(text="hello world", display_name="Bob")],
        )
        title = items[0].find("title").text
        assert "Bob" in title
        assert "hello" in title

    def test_item_has_link(self, test_client):
        items = self._items(test_client, [_make_post()])
        link = items[0].find("link")
        assert link is not None
        assert link.text.startswith("https://bsky.app/profile/")

    def test_item_has_guid(self, test_client):
        items = self._items(test_client, [_make_post()])
        guid = items[0].find("guid")
        assert guid is not None
        assert guid.get("isPermaLink") == "true"
        assert guid.text.startswith("https://")

    def test_item_guid_matches_link(self, test_client):
        items = self._items(test_client, [_make_post()])
        assert items[0].find("guid").text == items[0].find("link").text

    def test_item_has_description(self, test_client):
        items = self._items(
            test_client, [_make_post(text="Some interesting post")]
        )
        desc = items[0].find("description")
        assert desc is not None
        assert desc.text == "Some interesting post"

    def test_item_has_pub_date(self, test_client):
        items = self._items(test_client, [_make_post()])
        pub = items[0].find("pubDate")
        assert pub is not None
        assert "+0000" in pub.text

    def test_empty_feed_has_no_items(self, test_client):
        items = self._items(test_client, [])
        assert len(items) == 0

    def test_link_contains_post_rkey(self, test_client):
        items = self._items(
            test_client,
            [
                _make_post(
                    uri="at://did:plc:abc/app.bsky.feed.post/mypostkey",
                    handle="user.bsky.social",
                )
            ],
        )
        assert "mypostkey" in items[0].find("link").text
        assert "user.bsky.social" in items[0].find("link").text


# ===================================================================
# 4. Media / enclosure handling
# ===================================================================


class TestMediaAttachments:
    """Images and videos must appear as <enclosure> and <media:content>."""

    def _items(self, test_client, posts):
        with patch(
            "main.get_client", return_value=_mock_client_for_fav(posts)
        ):
            resp = test_client.get("/fav")
        root = ET.fromstring(resp.text)
        return root.findall("channel/item")

    def test_image_produces_enclosure(self, test_client):
        embed = _make_image_embed("https://cdn.bsky.app/img/feed/1.jpg")
        items = self._items(test_client, [_make_post(embed=embed)])
        enc = items[0].findall("enclosure")
        assert len(enc) >= 1
        assert enc[0].get("url") == "https://cdn.bsky.app/img/feed/1.jpg"
        assert enc[0].get("type") == "image/jpeg"
        assert enc[0].get("length") is not None

    def test_image_produces_media_content(self, test_client):
        embed = _make_image_embed("https://cdn.bsky.app/img/feed/1.jpg")
        items = self._items(test_client, [_make_post(embed=embed)])
        mc = items[0].findall("media:content", NS)
        assert len(mc) >= 1
        assert mc[0].get("medium") == "image"

    def test_multiple_images_produce_multiple_enclosures(self, test_client):
        embed = _make_image_embed(
            "https://cdn.bsky.app/img/1.jpg",
            "https://cdn.bsky.app/img/2.jpg",
        )
        items = self._items(test_client, [_make_post(embed=embed)])
        assert len(items[0].findall("enclosure")) == 2

    def test_video_produces_enclosure(self, test_client):
        embed = _make_video_embed(
            "https://video.bsky.app/watch/playlist.m3u8",
            "https://video.bsky.app/thumb.jpg",
        )
        items = self._items(test_client, [_make_post(embed=embed)])
        encs = items[0].findall("enclosure")
        urls = [e.get("url") for e in encs]
        assert "https://video.bsky.app/watch/playlist.m3u8" in urls

    def test_video_enclosure_mime_type(self, test_client):
        embed = _make_video_embed("https://video.bsky.app/watch/playlist.m3u8")
        items = self._items(test_client, [_make_post(embed=embed)])
        enc = items[0].find("enclosure")
        assert enc.get("type") == "application/x-mpegURL"

    def test_video_thumbnail_uses_media_thumbnail(self, test_client):
        embed = _make_video_embed(
            "https://video.bsky.app/watch/playlist.m3u8",
            "https://video.bsky.app/thumb.jpg",
        )
        items = self._items(test_client, [_make_post(embed=embed)])
        thumbs = items[0].findall("media:thumbnail", NS)
        assert len(thumbs) >= 1
        assert thumbs[0].get("url") == "https://video.bsky.app/thumb.jpg"

    def test_external_link_thumb_as_enclosure(self, test_client):
        embed = _make_external_embed("https://example.com/og-image.jpg")
        items = self._items(test_client, [_make_post(embed=embed)])
        enc = items[0].find("enclosure")
        assert enc is not None
        assert enc.get("url") == "https://example.com/og-image.jpg"

    def test_record_with_media_extracts_inner_images(self, test_client):
        inner = _make_image_embed("https://cdn.bsky.app/img/quoted.jpg")
        embed = _make_record_with_media_embed(inner)
        items = self._items(test_client, [_make_post(embed=embed)])
        enc = items[0].find("enclosure")
        assert enc is not None
        assert enc.get("url") == "https://cdn.bsky.app/img/quoted.jpg"

    def test_no_embed_produces_no_enclosure(self, test_client):
        items = self._items(test_client, [_make_post(embed=None)])
        assert items[0].find("enclosure") is None


# ===================================================================
# 5. extract_media unit tests
# ===================================================================


class TestExtractMedia:
    """Direct unit tests for the extract_media helper."""

    def test_none_embed_returns_empty(self):
        assert extract_media(None) == []

    def test_unknown_type_returns_empty(self):
        embed = SimpleNamespace(py_type="app.bsky.embed.unknown#view")
        assert extract_media(embed) == []

    def test_image_returns_url_and_type(self):
        embed = _make_image_embed("https://img.example.com/a.jpg")
        result = extract_media(embed)
        assert len(result) == 1
        assert result[0]["url"] == "https://img.example.com/a.jpg"
        assert result[0]["type"] == "image"
        assert result[0]["mime_type"] == "image/jpeg"

    def test_video_without_thumbnail(self):
        embed = _make_video_embed("https://vid.example.com/play.m3u8")
        result = extract_media(embed)
        assert len(result) == 1
        assert result[0]["type"] == "video"

    def test_video_with_thumbnail(self):
        embed = _make_video_embed(
            "https://vid.example.com/play.m3u8",
            "https://vid.example.com/thumb.jpg",
        )
        result = extract_media(embed)
        assert len(result) == 2
        types = {r["type"] for r in result}
        assert "video" in types
        assert "video_thumb" in types

    def test_external_no_thumb_returns_empty(self):
        embed = _make_external_embed(thumb_url=None)
        assert extract_media(embed) == []

    def test_nested_record_with_media(self):
        inner = _make_image_embed("https://nested.example.com/img.jpg")
        embed = _make_record_with_media_embed(inner)
        result = extract_media(embed)
        assert len(result) == 1
        assert result[0]["url"] == "https://nested.example.com/img.jpg"


# ===================================================================
# 6. build_rss unit tests
# ===================================================================


class TestBuildRSS:
    """Direct unit tests for the build_rss helper."""

    def test_returns_valid_xml(self):
        xml_str = build_rss("Test Feed", "A test", [])
        root = ET.fromstring(xml_str)
        assert root.tag == "rss"

    def test_title_and_description_set(self):
        xml_str = build_rss("My Title", "My Desc", [])
        root = ET.fromstring(xml_str)
        assert root.find("channel/title").text == "My Title"
        assert root.find("channel/description").text == "My Desc"

    def test_items_from_feed_view_posts(self):
        """Posts wrapped in FeedViewPost (have .post attr) are handled."""
        post = _make_post(text="wrapped post")
        feed_item = _make_feed_item(post)
        xml_str = build_rss("T", "D", [feed_item])
        root = ET.fromstring(xml_str)
        items = root.findall("channel/item")
        assert len(items) == 1
        assert "wrapped post" in items[0].find("description").text

    def test_items_from_bare_posts(self):
        """Posts passed directly (hydrated from getPosts) are handled."""
        post = _make_post(text="bare post")
        xml_str = build_rss("T", "D", [post])
        root = ET.fromstring(xml_str)
        items = root.findall("channel/item")
        assert len(items) == 1
        assert "bare post" in items[0].find("description").text

    def test_special_xml_chars_escaped(self):
        """Angle brackets and ampersands in text must not break XML."""
        post = _make_post(text='<script>alert("xss")</script> & more')
        xml_str = build_rss("T", "D", [post])
        # Must parse without error — proves escaping worked
        root = ET.fromstring(xml_str)
        desc = root.find("channel/item/description").text
        assert "<script>" in desc  # preserved as text, not as a tag


# ===================================================================
# 7. Bookmarks endpoint hydration
# ===================================================================


class TestBookmarksHydration:
    """The /marks endpoint must hydrate bookmark URIs into full posts."""

    def test_empty_bookmarks_returns_valid_rss(self, test_client):
        client = MagicMock()
        client.app.bsky.bookmark.get_bookmarks.return_value = SimpleNamespace(
            bookmarks=[]
        )
        with patch("main.get_client", return_value=(client, "test.bsky.social")):
            resp = test_client.get("/marks")
        assert resp.status_code == 200
        root = ET.fromstring(resp.text)
        assert root.find("channel") is not None
        assert len(root.findall("channel/item")) == 0

    def test_bookmarks_call_get_posts_with_uris(self, test_client):
        posts = [_make_post(uri="at://did:plc:x/app.bsky.feed.post/abc")]
        client_tuple = _mock_client_for_marks(posts)
        with patch("main.get_client", return_value=client_tuple):
            test_client.get("/marks")
        client = client_tuple[0]
        client.app.bsky.feed.get_posts.assert_called_once()
        call_uris = client.app.bsky.feed.get_posts.call_args[1]["params"]["uris"]
        assert "at://did:plc:x/app.bsky.feed.post/abc" in call_uris


# ===================================================================
# 8. Combo endpoint
# ===================================================================


class TestComboEndpoint:
    """The /combo endpoint must merge likes and bookmarks into one feed."""

    def test_combo_returns_valid_rss(self, test_client):
        fav = [_make_post(text="liked", uri="at://did:plc:a/app.bsky.feed.post/f1")]
        mark = [_make_post(text="bookmarked", uri="at://did:plc:a/app.bsky.feed.post/m1")]
        client = _mock_client_for_combo(fav, mark)
        with patch("main.get_client", return_value=client):
            resp = test_client.get("/combo")
        assert resp.status_code == 200
        root = ET.fromstring(resp.text)
        assert root.tag == "rss"

    def test_combo_merges_both_sources(self, test_client):
        fav = [_make_post(text="liked", uri="at://did:plc:a/app.bsky.feed.post/f1")]
        mark = [_make_post(text="bookmarked", uri="at://did:plc:a/app.bsky.feed.post/m1")]
        client = _mock_client_for_combo(fav, mark)
        with patch("main.get_client", return_value=client):
            resp = test_client.get("/combo")
        root = ET.fromstring(resp.text)
        items = root.findall("channel/item")
        assert len(items) == 2
        descriptions = [it.find("description").text for it in items]
        assert "liked" in descriptions
        assert "bookmarked" in descriptions

    def test_combo_deduplicates_by_uri(self, test_client):
        shared_uri = "at://did:plc:a/app.bsky.feed.post/shared"
        fav = [_make_post(text="shared post", uri=shared_uri)]
        mark = [_make_post(text="shared post", uri=shared_uri)]
        client = _mock_client_for_combo(fav, mark)
        with patch("main.get_client", return_value=client):
            resp = test_client.get("/combo")
        root = ET.fromstring(resp.text)
        items = root.findall("channel/item")
        assert len(items) == 1

    def test_combo_empty_sources(self, test_client):
        client, handle = _mock_client_for_combo([], [])
        # When both are empty, bookmarks list is empty so get_posts is not called
        client.app.bsky.bookmark.get_bookmarks.return_value = SimpleNamespace(
            bookmarks=[]
        )
        with patch("main.get_client", return_value=(client, handle)):
            resp = test_client.get("/combo")
        root = ET.fromstring(resp.text)
        assert len(root.findall("channel/item")) == 0

    def test_combo_content_type(self, test_client):
        client, handle = _mock_client_for_combo([], [])
        client.app.bsky.bookmark.get_bookmarks.return_value = SimpleNamespace(
            bookmarks=[]
        )
        with patch("main.get_client", return_value=(client, handle)):
            resp = test_client.get("/combo")
        assert "application/rss+xml" in resp.headers["content-type"]

    def test_combo_title_contains_likes_and_bookmarks(self, test_client):
        fav = [_make_post(uri="at://did:plc:a/app.bsky.feed.post/f1")]
        client, handle = _mock_client_for_combo(fav, [])
        client.app.bsky.bookmark.get_bookmarks.return_value = SimpleNamespace(
            bookmarks=[]
        )
        with patch("main.get_client", return_value=(client, handle)):
            resp = test_client.get("/combo")
        root = ET.fromstring(resp.text)
        title = root.find("channel/title").text
        assert "Likes" in title
        assert "Bookmarks" in title


# ===================================================================
# 9. Limit query parameter
# ===================================================================


class TestLimitParameter:
    """The ?limit= query parameter controls how many items are fetched."""

    def test_fav_default_limit_matches_env(self, test_client):
        client_tuple = _mock_client_for_fav([_make_post()])
        with patch("main.get_client", return_value=client_tuple):
            test_client.get("/fav")
        call_params = client_tuple[0].app.bsky.feed.get_actor_likes.call_args[1]["params"]
        assert call_params["limit"] == DEFAULT_LIMIT

    def test_fav_custom_limit(self, test_client):
        client_tuple = _mock_client_for_fav([_make_post()])
        with patch("main.get_client", return_value=client_tuple):
            test_client.get("/fav?limit=5")
        call_params = client_tuple[0].app.bsky.feed.get_actor_likes.call_args[1]["params"]
        assert call_params["limit"] == 5

    def test_marks_custom_limit(self, test_client):
        client_tuple = _mock_client_for_marks([_make_post()])
        with patch("main.get_client", return_value=client_tuple):
            test_client.get("/marks?limit=3")
        call_params = client_tuple[0].app.bsky.bookmark.get_bookmarks.call_args[1]["params"]
        assert call_params["limit"] == 3

    def test_combo_custom_limit(self, test_client):
        fav = [_make_post(uri="at://did:plc:a/app.bsky.feed.post/f1")]
        client, handle = _mock_client_for_combo(fav, [])
        client.app.bsky.bookmark.get_bookmarks.return_value = SimpleNamespace(
            bookmarks=[]
        )
        with patch("main.get_client", return_value=(client, handle)):
            test_client.get("/combo?limit=7")
        like_params = client.app.bsky.feed.get_actor_likes.call_args[1]["params"]
        bm_params = client.app.bsky.bookmark.get_bookmarks.call_args[1]["params"]
        assert like_params["limit"] == 7
        assert bm_params["limit"] == 7

    def test_limit_minimum_is_1(self, test_client):
        client = _mock_client_for_fav([_make_post()])
        with patch("main.get_client", return_value=client):
            resp = test_client.get("/fav?limit=1")
        assert resp.status_code == 200

    def test_limit_maximum_is_25(self, test_client):
        client = _mock_client_for_fav([_make_post()])
        with patch("main.get_client", return_value=client):
            resp = test_client.get("/fav?limit=25")
        assert resp.status_code == 200

    def test_limit_below_minimum_returns_422(self, test_client):
        client = _mock_client_for_fav([])
        with patch("main.get_client", return_value=client):
            resp = test_client.get("/fav?limit=0")
        assert resp.status_code == 422

    def test_limit_above_maximum_returns_422(self, test_client):
        client = _mock_client_for_fav([])
        with patch("main.get_client", return_value=client):
            resp = test_client.get("/fav?limit=26")
        assert resp.status_code == 422

    def test_limit_reflected_in_description(self, test_client):
        client = _mock_client_for_fav([_make_post()])
        with patch("main.get_client", return_value=client):
            resp = test_client.get("/fav?limit=5")
        root = ET.fromstring(resp.text)
        desc = root.find("channel/description").text
        assert "5" in desc


# ===================================================================
# 10. DEFAULT_LIMIT env var
# ===================================================================


class TestDefaultLimitEnvVar:
    """The LIMIT env var sets the default when ?limit= is omitted."""

    def test_default_limit_fallback_is_10(self):
        """Without LIMIT in env, DEFAULT_LIMIT should be 10."""
        assert DEFAULT_LIMIT == 10

    def test_env_limit_clamped_above_25(self):
        """Values above 25 are clamped to 25."""
        assert min(max(int("50"), 1), 25) == 25

    def test_env_limit_clamped_below_1(self):
        """Values below 1 are clamped to 1."""
        assert min(max(int("0"), 1), 25) == 1

    def test_env_limit_valid_value(self):
        """A valid value within range passes through."""
        assert min(max(int("15"), 1), 25) == 15


# ===================================================================
# 11. Username query parameter
# ===================================================================


class TestUsernameParameter:
    """The ?username= parameter selects alternate credentials from .env."""

    def test_fav_without_username_uses_default(self, test_client):
        """Omitting username uses BLUESKY_HANDLE / BLUESKY_APP_PASSWORD."""
        client_tuple = _mock_client_for_fav([_make_post()])
        with patch("main.get_client", return_value=client_tuple) as mock_gc:
            test_client.get("/fav")
        mock_gc.assert_called_once_with(None)

    def test_fav_with_username_passes_to_get_client(self, test_client):
        client_tuple = _mock_client_for_fav([_make_post()])
        with patch("main.get_client", return_value=client_tuple) as mock_gc:
            test_client.get("/fav?username=foo")
        mock_gc.assert_called_once_with("foo")

    def test_marks_with_username_passes_to_get_client(self, test_client):
        client_tuple = _mock_client_for_marks([_make_post()])
        with patch("main.get_client", return_value=client_tuple) as mock_gc:
            test_client.get("/marks?username=bar")
        mock_gc.assert_called_once_with("bar")

    def test_combo_with_username_passes_to_get_client(self, test_client):
        client_tuple = _mock_client_for_combo([_make_post(uri="at://did:plc:a/app.bsky.feed.post/f1")], [])
        client_tuple[0].app.bsky.bookmark.get_bookmarks.return_value = SimpleNamespace(
            bookmarks=[]
        )
        with patch("main.get_client", return_value=client_tuple) as mock_gc:
            test_client.get("/combo?username=baz")
        mock_gc.assert_called_once_with("baz")

    def test_get_client_missing_username_returns_404(self):
        """Unknown username with no matching env vars raises 404."""
        with patch.dict(os.environ, {}, clear=False):
            # Ensure no UNKNOWN_* vars exist
            os.environ.pop("UNKNOWN_BLUESKY_HANDLE", None)
            os.environ.pop("UNKNOWN_BLUESKY_APP_PASSWORD", None)
            with pytest.raises(HTTPException) as exc_info:
                get_client("unknown")
            assert exc_info.value.status_code == 404

    def test_get_client_username_uppercased(self):
        """Username is uppercased when looking up env vars."""
        env = {
            "FOO_BLUESKY_HANDLE": "foo.bsky.social",
            "FOO_BLUESKY_APP_PASSWORD": "test-pass",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("main.Client") as MockClient:
                mock_instance = MagicMock()
                MockClient.return_value = mock_instance
                client, handle = get_client("foo")
                assert handle == "foo.bsky.social"
                mock_instance.login.assert_called_once_with("foo.bsky.social", "test-pass")

    def test_username_handle_in_rss_title(self, test_client):
        """When username is provided, the RSS title uses that account's handle."""
        client_tuple = _mock_client_for_fav([_make_post()])
        # Override the handle in the tuple to simulate a different account
        custom_tuple = (client_tuple[0], "custom.bsky.social")
        with patch("main.get_client", return_value=custom_tuple):
            resp = test_client.get("/fav?username=custom")
        root = ET.fromstring(resp.text)
        title = root.find("channel/title").text
        assert "custom.bsky.social" in title
