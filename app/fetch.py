"""Fetch a web page / PDF by URL for ingestion - with SSRF protection.

Disabled unless ALLOW_URL_INGEST=1. Every hop (including redirects) is validated: http(s) only,
standard ports, and the host must resolve ONLY to public IP addresses (no localhost, private
ranges, link-local/cloud-metadata addresses...). Size and time are capped.

Known limit: the name is resolved once to check it and again by the HTTP client when it connects,
so a hostile DNS server could in theory answer differently (DNS rebinding). If you enable this on a
public deployment, also restrict the container's outbound network access.
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 4
TIMEOUT = 15
ALLOWED_PORTS = {None, 80, 443}


class FetchError(ValueError):
    pass


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def validate_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchError("Only http(s) URLs are allowed.")
    if parsed.username or parsed.password:
        raise FetchError("URLs with credentials are not allowed.")
    if parsed.port not in ALLOWED_PORTS:
        raise FetchError("Only the standard http/https ports are allowed.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError("Could not resolve that host.") from None
    ips = {i[4][0] for i in infos}
    if not ips or not all(is_public_ip(ip) for ip in ips):
        raise FetchError("That address is not allowed (it does not resolve to a public IP).")
    return parsed.geturl()


def fetch(url: str) -> tuple[bytes, str, str]:
    """Return (content, content_type, final_url). Raises FetchError for anything unsafe."""
    current = validate_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            resp = requests.get(
                current,
                timeout=TIMEOUT,
                stream=True,
                allow_redirects=False,
                headers={"User-Agent": "DocQA-ingest/1.0"},
            )
        except requests.RequestException as exc:
            raise FetchError(f"Could not fetch the URL: {exc}") from None
        try:
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise FetchError("Redirect without a target.")
                current = validate_url(urljoin(current, location))
                continue
            if resp.status_code != 200:
                raise FetchError(f"The server answered with HTTP {resp.status_code}.")
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            data = bytearray()
            for part in resp.iter_content(65536):
                data += part
                if len(data) > MAX_BYTES:
                    raise FetchError("The page is larger than the 10 MB limit.")
            return bytes(data), ctype, current
        finally:
            resp.close()
    raise FetchError("Too many redirects.")
