"""Sanitize internal errors before they reach administrative responses."""

import re


def safe_diagnostic(value):
    text = str(value or "").splitlines()[0].strip()
    text = re.sub(r"(?i)\b(password|token|secret|api[_ -]?key)\s*[:=]\s*\S+", r"\1=[đã ẩn]", text)
    text = re.sub(r"(?i)\b(bearer|authorization)\s+\S+", r"\1 [đã ẩn]", text)
    text = re.sub(r"https?://[^\s/@:]+:[^\s/@]+@", "https://[credential]@", text)
    text = re.sub(r"https?://[^\s/?#]+\?[^\s#]+", lambda match: match.group(0).split("?", 1)[0] + "?[tham số ẩn]", text)
    text = re.sub(r"(?<![\w])(?:[A-Za-z]:)?[/\\](?:[^\s,;]+)", "[đường dẫn ẩn]", text)
    return text[:300]
