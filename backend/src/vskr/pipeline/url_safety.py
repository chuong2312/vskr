"""Kiểm tra URL trước khi yt-dlp phân giải hoặc tải nguồn từ xa (chống SSRF).

Chỉ nhận http/https, không chứa thông tin đăng nhập, và mọi địa chỉ IP mà tên miền phân giải ra
đều phải là địa chỉ công khai. Lỗi được báo bằng ``UnsafeUrlError``; tầng API đổi thành HTTP 400.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit



class UnsafeUrlError(ValueError):
    """URL bị từ chối: sai định dạng, chứa thông tin đăng nhập hoặc trỏ vào mạng nội bộ.

    Không phụ thuộc FastAPI; tầng API đổi lỗi này thành HTTP 400 với cùng thông báo.
    """


def resolve_ingest_target(url: str) -> tuple[str, int, str]:
    """Kiểm tra URL và trả về ``(host, cổng, IP)``; ném ``UnsafeUrlError`` nếu URL không an toàn.

    Tên miền được phân giải DNS ngay tại đây; chỉ cần một địa chỉ trả về thuộc mạng nội bộ là bị
    từ chối. IP trả về được dùng để ghim kết nối lúc tải (xem ``safe_ytdlp``).
    """
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise UnsafeUrlError("URL video không hợp lệ.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("URL video phải bắt đầu bằng http:// hoặc https://.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL video không được chứa thông tin đăng nhập.")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("Cổng trong URL không hợp lệ.") from exc
    if not 1 <= port <= 65535:
        raise UnsafeUrlError("Cổng trong URL không hợp lệ.")

    host = parsed.hostname.rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = list({ipaddress.ip_address(record[4][0].split("%", 1)[0]) for record in records})
        except (OSError, ValueError) as exc:
            raise UnsafeUrlError("Không thể phân giải máy chủ trong URL video.") from exc
    if not addresses:
        raise UnsafeUrlError("Máy chủ trong URL video không có địa chỉ IP hợp lệ.")
    for address in addresses:
        checked = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
        if not checked.is_global:
            raise UnsafeUrlError("URL video trỏ tới địa chỉ mạng nội bộ hoặc không công khai.")
    return host, port, str(sorted(addresses, key=lambda address: (address.version, int(address)))[0])


def validate_ingest_url(url: str) -> str:
    """Kiểm tra URL như ``resolve_ingest_target`` rồi trả lại chính URL đó."""
    resolve_ingest_target(url)
    return url


def validate_learner_ingest_url(url: str, allowed_domains=None) -> str:
    """Link do người học dán: chỉ nhận tên miền trong ``allowed_domains`` (mặc định là YouTube), sau đó
    kiểm tra như ``validate_ingest_url``.
    """
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except (TypeError, ValueError) as exc:
        raise UnsafeUrlError("URL video không hợp lệ.") from exc
    domains = allowed_domains or ("youtube.com", "youtu.be", "youtube-nocookie.com")
    if not any(host == domain or host.endswith("." + domain) for domain in domains):
        raise UnsafeUrlError("Người học hiện chỉ có thể dán link YouTube hoặc tải video lên.")
    return validate_ingest_url(url)
