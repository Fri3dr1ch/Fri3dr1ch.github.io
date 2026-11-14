"""Bounded, retryable public-source HTTP requests. No credentials are used."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ALLOWED_HOSTS = frozenset({
    'kalender.karlsruhe.de', 'xkale.karlsruhe.de', 'www.karlsruhe.de', 'karlsruhe.de',
    'www.durlacher.de', 'www.weingarten-baden.de', 'pfinztal.de',
    'www.remchingen.de', 'www.kulturhalle-remchingen.de', 'eki-groetzingen.de',
    'www.naturfreunde-groetzingen.de', 'www.naturfreunde-berghausen.de',
    'www.naturfreunde-soellingen.de', 'calendar.google.com',
    'www.walzbachtal.de',
})


def public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Nicht freigegebenes Quellenziel: ' + (parsed.hostname or 'ohne Host'))
    return url


class SourceRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Response:
    body: bytes
    status: int
    headers: dict


class HttpClient:
    def __init__(self, timeout=25, attempts=3, max_bytes=12_000_000, sleep=time.sleep, opener=None):
        self.timeout, self.attempts, self.max_bytes = timeout, attempts, max_bytes
        self.sleep, self.opener = sleep, opener
        self._lock = threading.Lock()
        self._hosts = {}
        self._failures = {}

    def __call__(self, url):
        return self.request(url).body

    def request(self, url, data=None, headers=None):
        public_url(url)
        host = urlsplit(url).hostname
        with self._lock:
            if self._failures.get(host, 0) >= 5:
                raise ConnectionError('Weitere Abrufe dieser Quelle nach wiederholten Netzwerkfehlern ausgesetzt: ' + host)
            limit = self._hosts.setdefault(host, threading.BoundedSemaphore(3))
        outgoing = {'User-Agent': 'RegionalCalendar/2.0 (public event calendar importer)',
                    'Accept': '*/*', **(headers or {})}
        # This client is exclusively for public calendar sources, never secrets.
        if any(k.lower() in {'authorization', 'cookie', 'oai-sites-authorization'} for k in outgoing):
            raise ValueError('Quellenclient darf keine Zugangsdaten übertragen')
        for attempt in range(self.attempts):
            retry_headers = {}
            try:
                with limit:
                    opener = self.opener or build_opener(SourceRedirects())
                    with opener.open(Request(url, data=data, headers=outgoing), timeout=self.timeout) as response:
                        if int(response.headers.get('Content-Length', '0')) > self.max_bytes:
                            raise ValueError('Quelldatei überschreitet Größenlimit')
                        raw = response.read(self.max_bytes + 1)
                        if len(raw) > self.max_bytes:
                            raise ValueError('Quelldatei überschreitet Größenlimit')
                        with self._lock: self._failures[host] = 0
                        return Response(raw, response.status, dict(response.headers.items()))
            except HTTPError as error:
                if error.code == 304:
                    with self._lock: self._failures[host] = 0
                    return Response(b'', 304, dict(error.headers.items()))
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    raise
                retry_headers = error.headers
                failure = error
            except (URLError, TimeoutError, socket.timeout, ConnectionError) as error:
                failure = error
            if attempt + 1 == self.attempts:
                with self._lock: self._failures[host] = self._failures.get(host, 0) + 1
                raise failure
            delay = min(0.5 * 2 ** attempt, 3)
            hint = retry_headers.get('Retry-After', '')
            if hint:
                try:
                    delay = min(max(float(hint), 0), 5)
                except ValueError:
                    try:
                        delay = min(max((parsedate_to_datetime(hint) - datetime.now(timezone.utc)).total_seconds(), 0), 5)
                    except (TypeError, ValueError):
                        pass
            self.sleep(delay)
        raise RuntimeError('HTTP-Abruf ohne Ergebnis')
