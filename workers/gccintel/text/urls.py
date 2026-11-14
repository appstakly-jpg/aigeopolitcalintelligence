"""URL canonicalisation for exact-duplicate removal.

Rules: lower-case scheme/host, drop default ports, fragments, tracking parameters
(utm_*, fbclid, gclid, ...), sort the remaining query, strip trailing slash (except root),
remove `www.` and AMP variants, collapse duplicate slashes.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Only parameters that are tracking-only by convention; content-bearing params are kept.
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "cmpid",
    "ocid", "ito", "smid", "s_cid", "spm",
}


def canonicalize_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("amp."):
        host = host[4:]
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    for suffix in ("/amp", "/amp/"):
        if path.endswith(suffix) and len(path) > len(suffix):
            path = path[: -len(suffix)]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    query.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_private_host(url: str) -> bool:
    """True if the URL's host resolves to a private/loopback/link-local address (SSRF guard)."""
    host = host_of(url)
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False  # unresolvable: the fetch will fail on its own
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
            return True
    return False
