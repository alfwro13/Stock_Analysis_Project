import ipaddress
import socket
from typing import Iterable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 15
_MAX_ADDRESS_ATTEMPTS = 3


class FetchRefused(ValueError):
    pass


class _Redirected(Exception):
    def __init__(self, location: Optional[str]):
        super().__init__(location)
        self.location = location


class _PinnedAdapter(HTTPAdapter):
    def __init__(self, hostname: str):
        self._hostname = hostname
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, server_hostname=self._hostname, assert_hostname=self._hostname, **kwargs)


def normalise_host(value: str) -> str:
    host = value.strip().strip("[]").rstrip(".").lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def _resolve(host: str, port: int) -> list:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def parse_https_url(url: str):
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None, None, "The URL is not valid."
    if parts.scheme != "https":
        return None, None, "Only https:// URLs are allowed."
    if not parts.hostname:
        return None, None, "The URL has no host name."
    if parts.username is not None or parts.password is not None:
        return None, None, "Put credentials in an Authorization header, not in the URL."
    return parts, port or 443, None


def _get_pinned(hostname: str, port: int, address: str, target: str, headers: dict, max_bytes: int) -> str:
    netloc = f"[{address}]" if ":" in address else address
    with requests.Session() as session:
        session.mount("https://", _PinnedAdapter(hostname))
        resp = session.get(
            f"https://{netloc}:{port}{target}", headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False, stream=True,
        )
        if 300 <= resp.status_code < 400:
            raise _Redirected(resp.headers.get("Location"))
        resp.raise_for_status()
        body = bytearray()
        for chunk in resp.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > max_bytes:
                raise FetchRefused(f"The page is larger than {max_bytes // (1024 * 1024)} MB.")
        # requests reports ISO-8859-1 for text/* without a header charset, which garbles UTF-8 pages.
        declares_charset = "charset" in resp.headers.get("Content-Type", "").lower()
        encoding = resp.encoding if declares_charset and resp.encoding else "utf-8"
        return bytes(body).decode(encoding, errors="replace")


def _fetch_hop(
    url: str, headers: dict, allowed_hosts: frozenset, refused_hint: str, max_bytes: int,
) -> str:
    parts, port, error = parse_https_url(url)
    if error:
        raise FetchRefused(error)
    host = normalise_host(parts.hostname)
    addresses = _resolve(host, port)
    if host not in allowed_hosts and not all(_is_public(a) for a in addresses):
        raise FetchRefused(f"That address is not allowed. {refused_hint}".strip())
    host_header = f"[{host}]" if ":" in host else host
    request_headers = {**headers, "Host": host_header if port == 443 else f"{host_header}:{port}"}
    target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    last_error = None
    for address in addresses[:_MAX_ADDRESS_ATTEMPTS]:
        try:
            return _get_pinned(host, port, address, target, request_headers, max_bytes)
        except requests.ConnectionError as e:
            last_error = e
    raise last_error


def fetch_body(
    url: str,
    headers: dict,
    *,
    allowed_hosts: Iterable[str] = (),
    refused_hint: str = "",
    max_redirects: int = 0,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> str:
    """https-only GET that refuses non-public addresses unless the requested host is allow-listed, connects to the vetted IP and re-vets every redirect hop."""
    allowed = frozenset(normalise_host(h) for h in allowed_hosts)
    for hop in range(max_redirects + 1):
        try:
            return _fetch_hop(url, headers, allowed, refused_hint, max_bytes)
        except _Redirected as redirect:
            if hop == max_redirects:
                raise FetchRefused(
                    "The page redirected. Redirects are not followed, so use the final URL."
                    if max_redirects == 0 else "Too many redirects."
                )
            if not redirect.location:
                raise FetchRefused("The page redirected without saying where.")
            url = urljoin(url, redirect.location)
