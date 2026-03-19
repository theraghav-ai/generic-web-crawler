from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _parse_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item.strip() for item in value if item and item.strip()]
    parts = []
    for chunk in value.replace("\n", ",").split(","):
        item = chunk.strip()
        if item:
            parts.append(item)
    return parts


def _normalize_base_url(url: str) -> str:
    raw = url.strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if not host:
        return ""
    return f"{scheme}://{host}{path}"


def _default_allowed_path_prefixes(base_url: str) -> list[str]:
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    if not path:
        return []
    return [path]


def _slug_from_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.netloc.replace(".", "-")
    path = parsed.path.strip("/").replace("/", "-")
    return f"{host}-{path}".strip("-") or "crawl"


COMMON_BLOCKED_PATTERNS = [
    "javascript:",
    "mailto:",
    "tel:",
    "/wp-json/",
    "/_next/",
    "/cdn-cgi/",
    "logout",
    "signout",
]

_USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

DOCUMENT_EXTENSIONS = [
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".7z",
    ".csv",
    ".xml",
    ".json",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
]


@dataclass(slots=True)
class Settings:
    project_root: Path
    log_dir: Path
    data_dir: Path
    site_data_dir: Path
    snapshot_dir: Path
    state_file: Path
    checkpoint_file: Path
    content_cache_file: Path
    last_run_file: Path
    latest_pages_file: Path
    site_slug: str
    base_url: str
    allowed_domains: list[str]
    seed_urls: list[str]
    allowed_path_prefixes: list[str]
    blocked_url_patterns: list[str]
    document_extensions: list[str]
    max_depth: int
    max_pages: int
    request_delay: float
    request_timeout: int
    page_load_wait_ms: int
    min_text_length: int
    checkpoint_interval: int
    use_sitemap: bool
    expand_dynamic: bool
    log_level: str
    user_agent: str
    respect_robots: bool
    proxy_list: list[str]
    cookie_file: Path | None
    save_cookies_file: Path | None
    no_path_filter: bool


def load_settings(overrides: dict | None = None) -> Settings:
    overrides = overrides or {}
    project_root = Path(__file__).resolve().parent
    log_dir = Path(overrides.get("log_dir") or os.getenv("LOG_DIR", project_root / "logs"))
    data_dir = Path(overrides.get("data_dir") or os.getenv("DATA_DIR", project_root / "data"))
    snapshot_dir = Path(
        overrides.get("snapshot_dir")
        or os.getenv("SNAPSHOT_DIR", data_dir / "snapshots")
    )

    log_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    base_url = _normalize_base_url(
        overrides.get("base_url") or os.getenv("CRAWL_BASE_URL", "")
    )
    if not base_url:
        raise ValueError("A base URL is required. Set CRAWL_BASE_URL or pass one on the CLI.")

    site_slug = _slug_from_base_url(base_url)
    site_data_dir = data_dir / "sites" / site_slug
    site_data_dir.mkdir(parents=True, exist_ok=True)

    parsed = urlparse(base_url)
    default_domains = [parsed.netloc]
    allowed_domains = _parse_list(
        overrides.get("allowed_domains") or os.getenv("CRAWL_ALLOWED_DOMAINS")
    ) or default_domains

    seed_urls = _parse_list(overrides.get("seed_urls") or os.getenv("SEED_URLS")) or [base_url]
    no_path_filter = _parse_bool(
        overrides.get("no_path_filter") if "no_path_filter" in overrides else os.getenv("CRAWL_NO_PATH_FILTER"),
        False,
    )
    if no_path_filter:
        allowed_path_prefixes = []
    else:
        allowed_path_prefixes = _parse_list(
            overrides.get("allowed_path_prefixes") or os.getenv("CRAWL_ALLOWED_PATH_PREFIXES")
        ) or _default_allowed_path_prefixes(base_url)
    blocked_patterns = _parse_list(
        overrides.get("blocked_url_patterns") or os.getenv("CRAWL_BLOCKED_PATTERNS")
    ) or COMMON_BLOCKED_PATTERNS.copy()
    document_extensions = _parse_list(
        overrides.get("document_extensions") or os.getenv("CRAWL_DOCUMENT_EXTENSIONS")
    ) or DOCUMENT_EXTENSIONS.copy()

    return Settings(
        project_root=project_root,
        log_dir=log_dir,
        data_dir=data_dir,
        site_data_dir=site_data_dir,
        snapshot_dir=snapshot_dir,
        state_file=site_data_dir / "crawl_state.json",
        checkpoint_file=site_data_dir / "checkpoint.json",
        content_cache_file=site_data_dir / "page_content_cache.json",
        last_run_file=site_data_dir / "last_run.json",
        latest_pages_file=site_data_dir / "latest_pages.json",
        site_slug=site_slug,
        base_url=base_url,
        allowed_domains=allowed_domains,
        seed_urls=seed_urls,
        allowed_path_prefixes=allowed_path_prefixes,
        blocked_url_patterns=blocked_patterns,
        document_extensions=document_extensions,
        max_depth=int(overrides.get("max_depth") or os.getenv("CRAWL_MAX_DEPTH", "2")),
        max_pages=int(overrides.get("max_pages") or os.getenv("CRAWL_MAX_PAGES", "100")),
        request_delay=float(overrides.get("request_delay") or os.getenv("CRAWL_REQUEST_DELAY", "1.0")),
        request_timeout=int(overrides.get("request_timeout") or os.getenv("CRAWL_REQUEST_TIMEOUT", "30")),
        page_load_wait_ms=int(
            overrides.get("page_load_wait_ms") or os.getenv("CRAWL_PAGE_LOAD_WAIT_MS", "2000")
        ),
        min_text_length=int(
            overrides.get("min_text_length") or os.getenv("CRAWL_MIN_TEXT_LENGTH", "80")
        ),
        checkpoint_interval=int(
            overrides.get("checkpoint_interval") or os.getenv("CRAWL_CHECKPOINT_INTERVAL", "10")
        ),
        use_sitemap=_parse_bool(
            overrides.get("use_sitemap") if "use_sitemap" in overrides else os.getenv("CRAWL_USE_SITEMAP"),
            True,
        ),
        expand_dynamic=_parse_bool(
            overrides.get("expand_dynamic") if "expand_dynamic" in overrides else os.getenv("CRAWL_EXPAND_DYNAMIC"),
            True,
        ),
        log_level=str(overrides.get("log_level") or os.getenv("LOG_LEVEL", "INFO")).upper(),
        user_agent=str(
            overrides.get("user_agent")
            or os.getenv("USER_AGENT", random.choice(_USER_AGENT_POOL))
        ),
        respect_robots=not _parse_bool(
            overrides.get("ignore_robots") if "ignore_robots" in overrides else os.getenv("CRAWL_IGNORE_ROBOTS"),
            False,
        ),
        proxy_list=_parse_list(
            overrides.get("proxy_list") or os.getenv("CRAWL_PROXY_LIST")
        ),
        cookie_file=Path(cf) if (cf := overrides.get("cookie_file") or os.getenv("CRAWL_COOKIE_FILE")) else None,
        save_cookies_file=Path(sc) if (sc := overrides.get("save_cookies_file")) else None,
        no_path_filter=no_path_filter,
    )
