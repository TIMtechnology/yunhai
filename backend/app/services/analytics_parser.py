from __future__ import annotations

import re
from urllib.parse import urlparse


def classify_channel(referer: str | None, page_url: str | None = None) -> str:
    ref = (referer or "").strip().lower()
    if not ref:
        return "direct"
    host = urlparse(ref).netloc.lower()
    if not host:
        return "direct"
    if "baidu.com" in host:
        return "baidu"
    if any(x in host for x in ("google.", "bing.com", "so.com", "sogou.com", "sm.cn")):
        return "search"
    if "github.com" in host:
        return "github"
    if any(x in host for x in ("weibo.", "zhihu.com", "douyin.com", "bilibili.com", "xiaohongshu.com")):
        return "social"
    if page_url:
        page_host = urlparse(page_url).netloc.lower()
        if page_host and host == page_host:
            return "direct"
    return "other"


def parse_user_agent(ua: str | None) -> tuple[str, str, str]:
    text = ua or ""
    browser = "unknown"
    os_name = "unknown"
    device = "desktop"

    if re.search(r"Mobile|Android|iPhone|iPad|iPod", text, re.I):
        device = "mobile"
    if "iPad" in text or "Tablet" in text:
        device = "tablet"

    if "Edg/" in text:
        browser = "Edge"
    elif "Chrome/" in text and "Edg/" not in text:
        browser = "Chrome"
    elif "Firefox/" in text:
        browser = "Firefox"
    elif "Safari/" in text and "Chrome/" not in text:
        browser = "Safari"
    elif "MSIE" in text or "Trident/" in text:
        browser = "IE"

    if "Windows" in text:
        os_name = "Windows"
    elif "Mac OS X" in text or "Macintosh" in text:
        os_name = "macOS"
    elif "Android" in text:
        os_name = "Android"
    elif "iPhone" in text or "iPad" in text:
        os_name = "iOS"
    elif "Linux" in text:
        os_name = "Linux"

    return browser, os_name, device


def extract_client_ip(forwarded_for: str | None, real_ip: str | None, direct: str | None) -> str:
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if real_ip:
        return real_ip.strip()
    return (direct or "").strip() or "unknown"
