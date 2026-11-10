import socket

import pytest
import requests

from app import fetch


@pytest.mark.parametrize(
    "ip,public",
    [
        ("8.8.8.8", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("192.168.0.1", False),
        ("172.16.5.5", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
        ("224.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_ip(ip, public):
    assert fetch.is_public_ip(ip) is public


def _resolve(monkeypatch, mapping):
    def fake(host, port, type=0):
        if host not in mapping:
            raise socket.gaierror("nope")
        return [(2, 1, 6, "", (ip, port)) for ip in mapping[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://",
        "http://user:pw@example.com/",
        "http://example.com:8080/",
        "http://example.com:22/",
        "http://localhost/",
        "http://metadata.internal/",
        "http://nowhere.invalid/",
        "http://mixed.example/",
    ],
)
def test_unsafe_urls_rejected(monkeypatch, url):
    _resolve(
        monkeypatch,
        {
            "localhost": ["127.0.0.1"],
            "metadata.internal": ["169.254.169.254"],
            "mixed.example": ["8.8.8.8", "10.0.0.1"],
            "example.com": ["93.184.216.34"],
        },
    )
    with pytest.raises(fetch.FetchError):
        fetch.validate_url(url)


def test_public_url_accepted(monkeypatch):
    _resolve(monkeypatch, {"example.com": ["93.184.216.34"]})
    assert fetch.validate_url("https://example.com/a?b=1") == "https://example.com/a?b=1"


class Resp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status_code, self.headers, self._body = status, headers or {}, body

    @property
    def is_redirect(self):
        return "Location" in self.headers

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass


def test_redirect_to_private_address_is_blocked(monkeypatch):
    _resolve(monkeypatch, {"example.com": ["93.184.216.34"], "evil.test": ["127.0.0.1"]})
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: Resp(302, {"Location": "http://evil.test/secret"})
    )
    with pytest.raises(fetch.FetchError, match="not allowed"):
        fetch.fetch("https://example.com/")


def test_redirect_loop_and_size_limit(monkeypatch):
    _resolve(monkeypatch, {"example.com": ["93.184.216.34"]})
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: Resp(302, {"Location": "https://example.com/"})
    )
    with pytest.raises(fetch.FetchError, match="redirects"):
        fetch.fetch("https://example.com/")
    monkeypatch.setattr(fetch, "MAX_BYTES", 10)
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: Resp(200, {"Content-Type": "text/html"}, b"x" * 50)
    )
    with pytest.raises(fetch.FetchError, match="10 MB"):
        fetch.fetch("https://example.com/")


def test_successful_fetch_returns_content_type(monkeypatch):
    _resolve(monkeypatch, {"example.com": ["93.184.216.34"]})
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: Resp(200, {"Content-Type": "text/html; charset=utf-8"}, b"<p>hi</p>"),
    )
    assert fetch.fetch("https://example.com/") == (
        b"<p>hi</p>",
        "text/html",
        "https://example.com/",
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(404))
    with pytest.raises(fetch.FetchError, match="404"):
        fetch.fetch("https://example.com/")
