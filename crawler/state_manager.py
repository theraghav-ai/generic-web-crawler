from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PageState:
    content_hash: str = ""
    etag: str = ""
    last_modified: str = ""
    last_crawled: str = ""
    status_code: int = 0


class CrawlStateManager:
    def __init__(self, state_file: Path):
        self._state_file = state_file
        self._pages: dict[str, dict[str, Any]] = {}
        self._gone_urls: set[str] = set()
        self._loaded = False

    def load(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._pages = data.get("pages", {})
                self._gone_urls = set(data.get("gone_urls", []))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load crawl state: %s", exc)
                self._pages = {}
                self._gone_urls = set()
        self._loaded = True

    def save(self) -> None:
        data = {
            "pages": self._pages,
            "gone_urls": sorted(self._gone_urls),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        tmp_path = self._state_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(self._state_file)

    def get_page_state(self, url: str) -> PageState | None:
        entry = self._pages.get(url)
        if entry is None:
            return None
        return PageState(**entry)

    def has_content_changed(self, url: str, new_hash: str) -> bool:
        state = self.get_page_state(url)
        if state is None:
            return True
        return state.content_hash != new_hash

    def update_page(
        self,
        url: str,
        content_hash: str,
        etag: str = "",
        last_modified: str = "",
        status_code: int = 200,
    ) -> None:
        self._pages[url] = {
            "content_hash": content_hash,
            "etag": etag,
            "last_modified": last_modified,
            "last_crawled": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status_code": status_code,
        }

    def mark_gone(self, url: str) -> None:
        self._gone_urls.add(url)

    def is_gone(self, url: str) -> bool:
        return url in self._gone_urls

    @property
    def gone_urls(self) -> set[str]:
        return set(self._gone_urls)


class CheckpointManager:
    def __init__(self, checkpoint_file: Path, interval: int = 10):
        self._file = checkpoint_file
        self._interval = interval

    def should_save(self, pages_crawled: int) -> bool:
        return pages_crawled > 0 and pages_crawled % self._interval == 0

    def save(
        self,
        visited_hashes: set[str],
        queue_tasks: list[dict[str, Any]],
        content_hashes: set[str],
        pages_data: list[dict[str, Any]],
        visited_count: int,
    ) -> None:
        payload = {
            "visited_hashes": sorted(visited_hashes),
            "queue": queue_tasks,
            "content_hashes": sorted(content_hashes),
            "pages": pages_data,
            "pages_count": len(pages_data),
            "visited_count": visited_count,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        tmp_path = self._file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._file)

    def load(self) -> dict[str, Any] | None:
        if not self._file.exists():
            return None
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load checkpoint: %s", exc)
            return None

    def clear(self) -> None:
        if self._file.exists():
            self._file.unlink()


class PageContentCache:
    def __init__(self, cache_file: Path):
        self._file = cache_file
        self._pages: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._pages = data.get("pages", {})
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load content cache: %s", exc)
            self._pages = {}

    def save(self) -> None:
        payload = {
            "pages": self._pages,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        tmp_path = self._file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._file)

    def update_pages(self, pages: list[dict[str, Any]]) -> tuple[int, int]:
        added = 0
        updated = 0
        for page in pages:
            url = page["url"]
            if url in self._pages:
                updated += 1
            else:
                added += 1
            self._pages[url] = page
        return added, updated

    def remove_gone_urls(self, gone_urls: set[str]) -> int:
        removed = 0
        for url in gone_urls:
            if url in self._pages:
                del self._pages[url]
                removed += 1
        return removed

    def get_all_pages(self) -> list[dict[str, Any]]:
        return list(self._pages.values())
