"""yt-dlp HTTP transport that validates every hop and pins each socket to its DNS answer."""

from __future__ import annotations

import functools
import http.client
import socket
import urllib.request
from urllib.parse import urlsplit


from .url_safety import UnsafeUrlError, resolve_ingest_target, validate_ingest_url


class UnsafeNetworkDownloaderError(RuntimeError):
    """Raised when a selected downloader can bypass the pinned HTTP transport."""


def resolve_download_target(url: str) -> tuple[str, int, str]:
    return resolve_ingest_target(url)


def create_pinned_connection(http_class, pinned_ip, host, *args, source_address=None, **kwargs):
    connection = http_class(host, *args, **kwargs)
    if source_address is not None:
        connection.source_address = (source_address, 0)

    def connect(_address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, local_address=None):
        return socket.create_connection(
            (pinned_ip, connection.port), timeout=timeout, source_address=local_address,
        )

    connection._create_connection = connect
    return connection


def make_pinned_redirect_handler():
    from yt_dlp.networking._urllib import RedirectHandler
    from yt_dlp.networking.exceptions import RequestError

    class PinnedRedirectHandler(RedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, new_url):
            try:
                validate_ingest_url(new_url)
            except UnsafeUrlError as exc:
                raise RequestError(str(exc), cause=exc) from exc
            return super().redirect_request(request, fp, code, message, headers, new_url)

    return PinnedRedirectHandler


def make_safe_youtube_dl(yt_dlp):
    from yt_dlp.networking._urllib import (
        DataHandler,
        FTPHandler,
        HTTPDefaultErrorHandler,
        HTTPErrorProcessor,
        HTTPHandler,
        ProxyHandler,
        UnknownHandler,
        UrllibRH,
    )
    from yt_dlp.networking.exceptions import RequestError
    PinnedRedirectHandler = make_pinned_redirect_handler()

    class PinnedHTTPHandler(HTTPHandler):
        def _open(self, request, connection_class, *, context=None):
            try:
                _host, _port, pinned_ip = resolve_download_target(request.full_url)
            except UnsafeUrlError as exc:
                raise RequestError(str(exc), cause=exc) from exc
            factory = functools.partial(
                create_pinned_connection,
                connection_class,
                pinned_ip,
                source_address=self._source_address,
            )
            if context is None:
                return self.do_open(factory, request)
            return self.do_open(factory, request, context=context)

        def http_open(self, request):
            return self._open(request, http.client.HTTPConnection)

        def https_open(self, request):
            return self._open(request, http.client.HTTPSConnection, context=self._context)

    class PinnedUrllibRH(UrllibRH):
        _SUPPORTED_URL_SCHEMES = ("http", "https")
        _SUPPORTED_PROXY_SCHEMES = ()
        _SUPPORTED_FEATURES = ()

        def _get_proxies(self, request):
            return {}

        def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
            opener = urllib.request.OpenerDirector()
            handlers = [
                ProxyHandler({}),
                PinnedHTTPHandler(
                    debuglevel=int(bool(self.verbose)),
                    context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support),
                    source_address=self.source_address,
                ),
                urllib.request.HTTPCookieProcessor(cookiejar),
                DataHandler(),
                UnknownHandler(),
                HTTPDefaultErrorHandler(),
                FTPHandler(),
                HTTPErrorProcessor(),
                PinnedRedirectHandler(),
            ]
            for handler in handlers:
                opener.add_handler(handler)
            opener.addheaders = []
            return opener

    class SafeYoutubeDL(yt_dlp.YoutubeDL):
        def __init__(self, params=None):
            params = dict(params or {})
            params["external_downloader"] = "native"
            params["hls_prefer_native"] = True
            super().__init__(params)

        @staticmethod
        def _uses_only_local_urls(info):
            urls = []
            if info.get("url"):
                urls.append(info["url"])
            for item in info.get("requested_formats") or ():
                if item.get("url"):
                    urls.append(item["url"])
            for fragment in info.get("fragments") or ():
                if isinstance(fragment, dict) and fragment.get("url"):
                    urls.append(fragment["url"])
            return bool(urls) and all(
                urlsplit(url).scheme.lower() in {"file", "data"}
                for url in urls
            )

        def dl(self, name, info, subtitle=False, test=False):
            from yt_dlp.downloader import get_suitable_downloader
            from yt_dlp.downloader.dash import DashSegmentsFD
            from yt_dlp.downloader.external import FFmpegFD
            from yt_dlp.downloader.http import HttpFD

            params = self.params
            selected = get_suitable_downloader(info, params, to_stdout=name == "-")
            if selected not in {HttpFD, DashSegmentsFD} and not (
                selected is FFmpegFD and self._uses_only_local_urls(info)
            ):
                raise UnsafeNetworkDownloaderError(
                    f"yt-dlp selected an unapproved network downloader: {selected.__name__}"
                )
            return super().dl(name, info, subtitle=subtitle, test=test)

        def build_request_director(self, handlers, preferences=None):
            director = super().build_request_director([PinnedUrllibRH], preferences)
            director.preferences.add(
                lambda handler, _request: 10_000 if handler.RH_KEY == "PinnedUrllib" else 0
            )
            return director

    return SafeYoutubeDL
