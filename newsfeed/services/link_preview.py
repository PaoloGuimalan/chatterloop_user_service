"""
Link preview (URL unfurl) service - fetches metadata (title, description,
image, site name, favicon) for a URL found in user-submitted text, guarding
the outbound fetch against SSRF since the target URL is attacker-controlled
(pasted by any user into a chat message, comment, post caption, or diary
entry).

Consumers: newsfeed (Post/Comment), diary (Entry), and the Node chat server
via LinkPreviewView (an internal-auth caller, see newsfeed/drf_permissions.py).
get_preview() is the only function other modules should call directly.

KNOWN LIMITATION: is_safe_url() resolves the hostname and validates every
returned address is public, and that check is re-run on every redirect hop.
It does NOT pin the outbound connection to the specific IP it validated -
requests/urllib3 re-resolves the hostname when it actually connects a moment
later. This closes the overwhelming majority of real-world SSRF payloads
(direct private/loopback/link-local/metadata-IP URLs, IP-literal encoding
tricks, redirect chains to a different private host) but leaves a narrower
DNS-rebinding window (a hostile authoritative DNS server alternating between
a public answer for the check and a private answer for the connect). Closing
that fully requires pinning the validated IP at the connection/transport
level (or, more commonly in production, network-level egress filtering as
defense-in-depth). Flagged here deliberately rather than silently assumed
solved - treat as a follow-up hardening item before this handles very
high-value/sensitive traffic.
"""

import hashlib
import ipaddress
import re
import socket
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from django.core.cache import cache
from django.urls import reverse

CACHE_TTL_OK = 60 * 60 * 24  # 24h
CACHE_TTL_FAILED = 60 * 60  # 1h

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 5
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2MB cap for the HTML fetch
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB cap for the image proxy

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
ALLOWED_IMAGE_CONTENT_TYPE_PREFIX = "image/"

USER_AGENT = "ChatterloopLinkPreview/1.0 (+https://chatterloop.app)"

URL_REGEX = re.compile(r"https?://[^\s<>\"']+")


def _trim_trailing_punctuation(url):
    """
    Strip sentence-ending punctuation a regex match commonly drags in from
    surrounding prose (e.g. "check this out: https://example.com."). A
    trailing ')' is only stripped when unbalanced - URLs legitimately
    contain parens (e.g. Wikipedia's .../Python_(programming_language)),
    so an equal-or-more count of '(' means the ')' belongs to the URL.
    """
    url = url.rstrip(".,!?;:\"'")
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1].rstrip(".,!?;:\"'")
    return url


def extract_first_url(text):
    """v1 scope: first URL only per message/caption/comment/entry."""
    if not text:
        return None
    match = URL_REGEX.search(text)
    if not match:
        return None
    return _trim_trailing_punctuation(match.group(0))


def extract_first_url_from_html(html):
    """
    Same v1 scope as extract_first_url (first URL only), but for HTML
    content (e.g. Quill-generated diary entries) rather than plain text.
    Prefers an anchor's href - Quill auto-links pasted URLs into
    <a href="...">display text</a>, and the href is authoritative even when
    the visible text differs. Falls back to scanning the stripped plain
    text for anything typed but never auto-linked.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if href and URL_REGEX.match(href):
            return _trim_trailing_punctuation(href)

    return extract_first_url(soup.get_text())


def _cache_key(url):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"chatterloop:linkpreview:meta:{digest}"


def _is_public_ip(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_safe_url(url):
    """
    SSRF guard: scheme allowlist + resolve the hostname and require every
    returned address to be a public IP. Must be re-run on every redirect
    hop's Location header, not just the original URL.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        return False

    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False

    if not addrinfo:
        return False

    return all(_is_public_ip(sockaddr[0]) for *_, sockaddr in addrinfo)


def _guarded_get(url, *, session, timeout):
    """
    Follows redirects manually - never requests' allow_redirects=True - so
    is_safe_url() re-validates every hop before it's requested.
    """
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_safe_url(current_url):
            return None

        response = session.get(
            current_url,
            stream=True,
            timeout=timeout,
            allow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )

        if response.is_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                return None
            current_url = urljoin(current_url, location)
            continue

        return response

    return None


def _tiktok_fallback_embed_url(data):
    video_id = data.get("embed_product_id")
    return f"https://www.tiktok.com/embed/v2/{video_id}" if video_id else None


def _twitter_fallback_embed_url(data):
    """
    Twitter/X's oEmbed, like TikTok's, is a <blockquote>+script snippet
    with no iframe - but unlike TikTok it has no dedicated video-id field,
    so the tweet ID is pulled out of the response's own `url` field (e.g.
    "https://x.com/jack/status/20") to build Twitter's separately-
    documented direct iframe-embed URL (platform.twitter.com/embed/Tweet.html?id=...).
    """
    match = re.search(r"/status/(\d+)", data.get("url") or "")
    return (
        f"https://platform.twitter.com/embed/Tweet.html?id={match.group(1)}"
        if match
        else None
    )


# oEmbed (https://oembed.com) providers for "playable media" links - video/
# audio players (and, for Twitter/X, rich post embeds) rendered as a real
# embedded iframe rather than a static thumbnail card. Each entry maps the
# hostnames that link to that provider onto its oEmbed endpoint (a fixed
# constant, not user-supplied - only the `url` query param is attacker-
# controlled, and it's passed through unmodified for the provider itself
# to resolve).
#
# Third element (optional): most providers' oEmbed `html` field already
# contains a direct <iframe src="...">, extracted generically by
# _extract_iframe_src. TikTok and Twitter/X are exceptions found in
# practice - their oEmbed responses are a <blockquote> + hydration script
# instead, with no iframe at all - so they each need a small fallback that
# builds the provider's separately-documented direct iframe-embed URL.
# Adding a provider that behaves like TikTok/Twitter (blockquote-only
# oEmbed) means adding a similar fallback function here; adding one that
# behaves like YouTube/Vimeo/Spotify (iframe in `html`) needs nothing
# beyond a new tuple.
#
# Fourth element: layout hint for the frontend card, since raw oEmbed
# width/height is unreliable across providers (some report a suggested
# player size, not the content's real shape - see _resolve_embed_dimensions).
# "auto" trusts the reported/resolved width:height ratio as-is (right for
# YouTube/Vimeo/SoundCloud, genuine video content). "landscape" is for
# providers whose embed is a compact, fixed-height player rather than a
# video - scaling their height proportionally with card width (as "auto"
# would) leaves visible empty space below the actual player UI (Spotify,
# and Twitter/X's tweet embed is the same kind of fixed-height widget, not
# a video). "portrait" is for providers whose real content is a vertical
# video (TikTok) - full card width would make the card absurdly tall, so
# the frontend constrains the embed's width instead and lets height follow.
OEMBED_PROVIDERS = [
    (
        {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "youtube-nocookie.com",
            "www.youtube-nocookie.com",
        },
        "https://www.youtube.com/oembed",
        None,
        "auto",
    ),
    (
        {"vimeo.com", "player.vimeo.com"},
        "https://vimeo.com/api/oembed.json",
        None,
        "auto",
    ),
    (
        {"soundcloud.com", "www.soundcloud.com"},
        "https://soundcloud.com/oembed",
        None,
        "auto",
    ),
    (
        {"open.spotify.com"},
        "https://open.spotify.com/oembed",
        None,
        "landscape",
    ),
    (
        {"tiktok.com", "www.tiktok.com", "vm.tiktok.com"},
        "https://www.tiktok.com/oembed",
        _tiktok_fallback_embed_url,
        "portrait",
    ),
    (
        {"twitter.com", "www.twitter.com", "mobile.twitter.com", "x.com", "www.x.com"},
        "https://publish.twitter.com/oembed",
        _twitter_fallback_embed_url,
        "landscape",
    ),
]

MAX_OEMBED_BYTES = 256 * 1024  # oEmbed JSON responses are small; generous cap


def _find_oembed_provider(url):
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    for hostnames, endpoint, fallback_embed_url, embed_layout in OEMBED_PROVIDERS:
        if hostname in hostnames:
            return endpoint, fallback_embed_url, embed_layout
    return None


def _extract_iframe_src(html_snippet):
    """
    Pulls just the iframe `src` out of an oEmbed provider's `html` field -
    the provider's raw HTML is never injected into a page directly (that
    would mean rendering arbitrary third-party-supplied markup), only this
    one attribute value is used, to build a controlled, sandboxed iframe on
    the frontend instead.
    """
    if not html_snippet:
        return None
    soup = BeautifulSoup(html_snippet, "html.parser")
    iframe = soup.find("iframe")
    if not iframe or not iframe.get("src"):
        return None
    src = iframe["src"]
    return src if src.startswith("http") else f"https:{src}"


def fetch_oembed(url):
    """
    Queries a known provider's fixed oEmbed endpoint for `url`. Returns a
    normalized embed dict or None (URL isn't from a recognized provider,
    the provider didn't return a video/rich embed, or any request/parse
    failure - never raises).
    """
    provider = _find_oembed_provider(url)
    if not provider:
        return None
    endpoint, fallback_embed_url, embed_layout = provider

    try:
        response = requests.get(
            endpoint,
            params={"url": url, "format": "json"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            headers={"User-Agent": USER_AGENT},
        )
        if response.status_code != 200 or len(response.content) > MAX_OEMBED_BYTES:
            return None
        data = response.json()
    except (requests.RequestException, ValueError, OSError):
        return None

    if data.get("type") not in ("video", "rich"):
        return None

    embed_src = _extract_iframe_src(data.get("html"))
    if not embed_src and fallback_embed_url:
        embed_src = fallback_embed_url(data)
    if not embed_src:
        return None

    embed_width, embed_height = _resolve_embed_dimensions(data, embed_layout)

    # SoundCloud's `description` (a real oEmbed field, not every provider
    # sets it) contains raw HTML (embedded <a> links) - strip tags since
    # this renders into a plain <span>, not dangerouslySetInnerHTML.
    description = data.get("description")
    if description:
        description = BeautifulSoup(description, "html.parser").get_text().strip()

    return {
        # Not every provider sets `title` (Twitter/X's oEmbed doesn't) -
        # `author_name` is also part of the oEmbed spec and is a reasonable
        # stand-in ("jack (@jack)") rather than showing a blank title.
        "title": data.get("title") or data.get("author_name"),
        "description": description or None,
        "image": data.get("thumbnail_url"),
        "site_name": data.get("provider_name"),
        "embed_type": data.get("type"),
        "embed_url": embed_src,
        "embed_provider": data.get("provider_name"),
        "embed_width": embed_width,
        "embed_height": embed_height,
        "embed_layout": embed_layout,
    }


# Last-resort fallback per embed_layout, used only when a provider gives
# no usable numeric width/height at all (neither top-level nor thumbnail_*
# - true for Twitter/X, which never reports either). "auto"/"portrait"
# consume this as a ratio, so a plain 16:9/9:16 is fine either way. But
# "landscape" (see OEMBED_PROVIDERS) uses embed_height as a literal fixed
# pixel height, not a ratio denominator - falling back to something like
# "9" there would render a 9px-tall player. 200 is a reasonable fixed
# height for a compact-player-shaped embed with no other size signal.
_FALLBACK_DIMENSIONS = {
    "auto": (16, 9),
    "landscape": (600, 200),
    "portrait": (9, 16),
}


def _resolve_embed_dimensions(data, embed_layout):
    """
    oEmbed's top-level width/height are meant to be pixel integers per the
    spec, but not every provider honors that - TikTok returns the literal
    string "100%" (its embed is fluid-width by design), which would produce
    invalid CSS if used directly for an aspect-ratio. Fall back to
    thumbnail_width/thumbnail_height (reliably numeric, and reflects the
    real video's aspect ratio) when width/height aren't usable positive
    integers, then to a layout-appropriate default as a last resort.
    """

    def as_positive_int(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    width = as_positive_int(data.get("width")) or as_positive_int(
        data.get("thumbnail_width")
    )
    height = as_positive_int(data.get("height")) or as_positive_int(
        data.get("thumbnail_height")
    )

    if width and height:
        return width, height
    return _FALLBACK_DIMENSIONS.get(embed_layout, (16, 9))


def fetch_and_parse(url):
    """
    Guarded resolution: tries a known oEmbed "playable media" provider
    first (see OEMBED_PROVIDERS), falling back to a generic OG-tag scrape
    of the page for everything else. Never raises - returns None on total
    failure.
    """
    oembed = fetch_oembed(url)
    if oembed:
        return {
            "url": url,
            "resolved_url": url,
            "title": oembed["title"],
            "description": oembed["description"],
            "image": oembed["image"],
            "site_name": oembed["site_name"],
            "favicon": None,
            "embed_type": oembed["embed_type"],
            "embed_url": oembed["embed_url"],
            "embed_provider": oembed["embed_provider"],
            "embed_width": oembed["embed_width"],
            "embed_height": oembed["embed_height"],
            "embed_layout": oembed["embed_layout"],
        }

    return _scrape_og_tags(url)


def _scrape_og_tags(url):
    """Guarded fetch + HTML parse. Never raises - returns None on any failure."""
    try:
        with requests.Session() as session:
            response = _guarded_get(
                url, session=session, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
            )
            if response is None:
                return None

            with response:
                content_type = (
                    response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                )
                if content_type not in ALLOWED_HTML_CONTENT_TYPES:
                    return None

                body = b""
                for chunk in response.iter_content(chunk_size=8192):
                    body += chunk
                    if len(body) > MAX_BODY_BYTES:
                        break

                resolved_url = response.url
                encoding = response.encoding or "utf-8"
    except (requests.RequestException, OSError):
        return None

    html = body.decode(encoding, errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find(
                "meta", attrs={"name": name}
            )
            if tag and tag.get("content"):
                return tag["content"].strip()
        return None

    title = meta("og:title", "twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    description = meta("og:description", "twitter:description", "description")

    image = meta("og:image", "twitter:image")
    if image:
        image = urljoin(resolved_url, image)

    site_name = meta("og:site_name") or urlparse(resolved_url).hostname

    favicon = None
    for rel in ("icon", "shortcut icon", "apple-touch-icon"):
        tag = soup.find("link", attrs={"rel": rel})
        if tag and tag.get("href"):
            favicon = urljoin(resolved_url, tag["href"])
            break
    if not favicon:
        favicon = urljoin(resolved_url, "/favicon.ico")

    return {
        "url": url,
        "resolved_url": resolved_url,
        "title": title,
        "description": description,
        "image": image,
        "site_name": site_name,
        "favicon": favicon,
        "embed_type": None,
        "embed_url": None,
        "embed_provider": None,
        "embed_width": None,
        "embed_height": None,
        "embed_layout": None,
    }


def build_image_proxy_path(raw_url):
    """
    Relative path (no host) for LinkPreviewImageProxyView, given a raw
    third-party image URL. Relative rather than absolute because the
    resolvers that populate link_preview data (serializers on Post/Comment/
    Entry) aren't reliably given a `request` in this codebase's existing
    call sites - the frontend prepends its own known API base URL.

    Some sites declare `<link rel="icon" href="data:,">` to explicitly opt
    out of favicon requests - a data: URI has no external target to proxy
    (it's inline bytes, no privacy leak either), so pass it through as-is
    rather than wrapping it in a doomed proxy request. Any other
    non-http(s) scheme has nothing safe to fetch, so drop it.
    """
    if not raw_url:
        return None

    scheme = urlparse(raw_url).scheme
    if scheme == "data":
        return raw_url
    if scheme not in ALLOWED_SCHEMES:
        return None

    path = reverse("api-newsfeed:newsfeed-link-preview-image")
    return f"{path}?url={quote(raw_url, safe='')}"


def get_preview(url, force_refresh=False):
    """
    Cache-aside wrapper - the only function other modules should import.
    Failures are cached too (shorter TTL) so a broken/blocked URL isn't
    refetched on every request but does eventually get retried. image/
    favicon are returned as proxy paths (see build_image_proxy_path), never
    the raw third-party URL.
    """
    if not url:
        return None

    key = _cache_key(url)
    if not force_refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached

    parsed = fetch_and_parse(url)

    if parsed:
        result = {
            **parsed,
            "image": build_image_proxy_path(parsed.get("image")),
            "favicon": build_image_proxy_path(parsed.get("favicon")),
            "status": "ok",
        }
        cache.set(key, result, CACHE_TTL_OK)
    else:
        result = {
            "url": url,
            "resolved_url": None,
            "title": None,
            "description": None,
            "image": None,
            "site_name": None,
            "favicon": None,
            "embed_type": None,
            "embed_url": None,
            "embed_provider": None,
            "embed_width": None,
            "embed_height": None,
            "embed_layout": None,
            "status": "failed",
        }
        cache.set(key, result, CACHE_TTL_FAILED)

    return result


def fetch_image(url):
    """
    Guarded image fetch for the preview-card thumbnail proxy. Returns
    (bytes, content_type) or None. Separate content-type allowlist and size
    cap from the HTML fetch - no re-encoding/thumbnailing, just a
    size-capped, content-type-checked passthrough.
    """
    try:
        with requests.Session() as session:
            response = _guarded_get(
                url, session=session, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
            )
            if response is None:
                return None

            with response:
                content_type = (
                    response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                )
                if not content_type.startswith(ALLOWED_IMAGE_CONTENT_TYPE_PREFIX):
                    return None

                body = b""
                for chunk in response.iter_content(chunk_size=8192):
                    body += chunk
                    if len(body) > MAX_IMAGE_BYTES:
                        return None

                return body, content_type
    except (requests.RequestException, OSError):
        return None
