from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from config import Settings

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "msclkid",
}


@dataclass(order=True, slots=True)
class CrawlTask:
    depth: int
    url: str = field(compare=False)
    label: str = field(compare=False, default="")
    parent_url: str = field(compare=False, default="")


class URLManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._queue: deque[CrawlTask] = deque()
        self._seen: set[str] = set()
        self._visited_count = 0

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        query_pairs = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=False)
            if key.lower() not in TRACKING_QUERY_KEYS
        ]
        normalized = urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/") or "/",
                parsed.params,
                urlencode(query_pairs, doseq=True),
                "",
            )
        )
        return normalized

    def _url_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _is_allowed_domain(self, host: str) -> bool:
        host = host.lower()
        for domain in self.settings.allowed_domains:
            normalized = domain.lower()
            if host == normalized or host.endswith(f".{normalized}"):
                return True
        return False

    def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if not self._is_allowed_domain(parsed.netloc):
            return False

        path_lower = parsed.path.lower()
        for extension in self.settings.document_extensions:
            if path_lower.endswith(extension):
                return False

        full_url_lower = url.lower()
        for pattern in self.settings.blocked_url_patterns:
            if pattern.lower() in full_url_lower:
                return False

        if self.settings.allowed_path_prefixes:
            if not any(path_lower.startswith(prefix.lower()) for prefix in self.settings.allowed_path_prefixes):
                return False

        return True

    def add_seed(self, url: str, label: str = "seed") -> bool:
        normalized = self.normalize_url(url)
        if not self.is_allowed(normalized):
            return False
        url_hash = self._url_hash(normalized)
        if url_hash in self._seen:
            return False
        self._seen.add(url_hash)
        self._queue.append(CrawlTask(depth=0, url=normalized, label=label))
        return True

    def add_discovered(self, url: str, parent_url: str, parent_depth: int) -> bool:
        candidate = url.strip()
        if not candidate or candidate.startswith("#"):
            return False
        depth = parent_depth + 1
        if depth > self.settings.max_depth:
            return False

        resolved = urljoin(parent_url, candidate)
        normalized = self.normalize_url(resolved)
        if not self.is_allowed(normalized):
            return False

        url_hash = self._url_hash(normalized)
        if url_hash in self._seen:
            return False

        self._seen.add(url_hash)
        self._queue.append(CrawlTask(depth=depth, url=normalized, parent_url=parent_url))
        return True

    def next(self) -> CrawlTask | None:
        if self._visited_count >= self.settings.max_pages:
            return None
        if not self._queue:
            return None
        task = self._queue.popleft()
        self._visited_count += 1
        return task

    def stats(self) -> dict[str, int]:
        return {
            "visited": self._visited_count,
            "queued": len(self._queue),
            "discovered": len(self._seen),
            "max_pages": self.settings.max_pages,
        }
