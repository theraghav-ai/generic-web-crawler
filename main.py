from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from config import Settings, load_settings

logger = logging.getLogger("generic_web_crawler")


def setup_logging(settings: Settings) -> None:
    log_format = "[%(asctime)s] %(levelname)-8s %(name)-24s %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_format, date_format))
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_dir / "crawler.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, settings.log_level, logging.INFO))
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    root.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def _cleanup_old_snapshots(snapshot_dir: Path, site_slug: str, keep_count: int = 10) -> None:
    files = sorted(
        snapshot_dir.glob(f"{site_slug}-*.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old_file in files[keep_count:]:
        old_file.unlink(missing_ok=True)


def _save_run_metadata(
    settings: Settings,
    pages_changed: int,
    total_pages: int,
    markdown_path: Path,
    pages_path: Path,
    elapsed: float,
) -> None:
    payload = {
        "base_url": settings.base_url,
        "pages_changed": pages_changed,
        "total_pages": total_pages,
        "markdown_path": str(markdown_path),
        "pages_json_path": str(pages_path),
        "elapsed_seconds": round(elapsed, 2),
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    settings.last_run_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_crawl_pipeline(settings: Settings) -> bool:
    from converter.html_to_markdown import pages_to_markdown
    from crawler.spider import PageContent, Spider
    from crawler.state_manager import CrawlStateManager, PageContentCache

    start_time = time.monotonic()
    slug = settings.site_slug

    spider = Spider(settings)
    try:
        pages = spider.crawl()
    except Exception as exc:
        logger.error("Crawl failed: %s", exc, exc_info=True)
        spider.close()
        return False

    content_cache = PageContentCache(settings.content_cache_file)
    content_cache.load()
    if pages:
        content_cache.update_pages([asdict(page) for page in pages])

    state = CrawlStateManager(settings.state_file)
    state.load()
    content_cache.remove_gone_urls(state.gone_urls)
    content_cache.save()

    all_pages_raw = content_cache.get_all_pages()
    if not all_pages_raw:
        logger.error("No pages available after crawl")
        return False

    all_pages = [PageContent(**page_data) for page_data in all_pages_raw]
    all_pages.sort(key=lambda page: (page.depth, page.url))

    title = f"Website Crawl - {urlparse(settings.base_url).netloc}"
    markdown = pages_to_markdown(all_pages, title=title, source_url=settings.base_url)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    markdown_path = settings.snapshot_dir / f"{slug}-{today}.md"
    pages_path = settings.latest_pages_file
    markdown_path.write_text(markdown, encoding="utf-8")
    pages_path.write_text(
        json.dumps([asdict(page) for page in all_pages], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _cleanup_old_snapshots(settings.snapshot_dir, settings.site_slug)

    elapsed = time.monotonic() - start_time
    _save_run_metadata(settings, len(pages), len(all_pages), markdown_path, pages_path, elapsed)
    logger.info("Pipeline complete in %.1fs", elapsed)
    logger.info("Pages changed: %d", len(pages))
    logger.info("Total pages in cache: %d", len(all_pages))
    logger.info("Markdown snapshot: %s", markdown_path)
    logger.info("Pages JSON: %s", pages_path)
    return True


def show_status(settings: Settings) -> int:
    if not settings.last_run_file.exists():
        print("No crawl has been run yet.")
        return 0
    data = json.loads(settings.last_run_file.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generic headless web crawler")
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl = subparsers.add_parser("crawl", help="Run a crawl and write markdown/json outputs")
    crawl.add_argument("base_url", nargs="?", help="Base URL to crawl")
    crawl.add_argument("--max-pages", type=int)
    crawl.add_argument("--max-depth", type=int)
    crawl.add_argument("--delay", type=float, dest="request_delay")
    crawl.add_argument("--timeout", type=int, dest="request_timeout")
    crawl.add_argument("--wait-ms", type=int, dest="page_load_wait_ms")
    crawl.add_argument("--min-text-length", type=int)
    crawl.add_argument("--seed-url", action="append", dest="seed_urls")
    crawl.add_argument("--allowed-domain", action="append", dest="allowed_domains")
    crawl.add_argument("--allowed-prefix", action="append", dest="allowed_path_prefixes")
    crawl.add_argument("--blocked-pattern", action="append", dest="blocked_url_patterns")
    crawl.add_argument("--no-sitemap", action="store_false", dest="use_sitemap")
    crawl.add_argument("--no-dynamic-expand", action="store_false", dest="expand_dynamic")
    crawl.add_argument("--ignore-robots", action="store_true", dest="ignore_robots",
                       help="Ignore robots.txt restrictions")
    crawl.add_argument("--no-path-filter", action="store_true", dest="no_path_filter",
                       help="Crawl entire domain regardless of base URL path")
    crawl.add_argument("--proxy", action="append", dest="proxy_list",
                       help="Proxy server URL (can be specified multiple times)")
    crawl.add_argument("--cookie-file", dest="cookie_file",
                       help="Path to JSON cookie file for session seeding")
    crawl.add_argument("--save-cookies", dest="save_cookies_file",
                       help="Path to save session cookies after crawl")
    crawl.set_defaults(use_sitemap=None, expand_dynamic=None, ignore_robots=None, no_path_filter=None)

    status = subparsers.add_parser("status", help="Show metadata from the last crawl")
    status.add_argument("base_url", nargs="?", help="Base URL namespace for config resolution")
    return parser


def _collect_overrides(args: argparse.Namespace) -> dict:
    overrides = {
        "base_url": getattr(args, "base_url", None),
        "max_pages": getattr(args, "max_pages", None),
        "max_depth": getattr(args, "max_depth", None),
        "request_delay": getattr(args, "request_delay", None),
        "request_timeout": getattr(args, "request_timeout", None),
        "page_load_wait_ms": getattr(args, "page_load_wait_ms", None),
        "min_text_length": getattr(args, "min_text_length", None),
        "seed_urls": getattr(args, "seed_urls", None),
        "allowed_domains": getattr(args, "allowed_domains", None),
        "allowed_path_prefixes": getattr(args, "allowed_path_prefixes", None),
        "blocked_url_patterns": getattr(args, "blocked_url_patterns", None),
    }
    if getattr(args, "use_sitemap", None) is not None:
        overrides["use_sitemap"] = args.use_sitemap
    if getattr(args, "expand_dynamic", None) is not None:
        overrides["expand_dynamic"] = args.expand_dynamic
    if getattr(args, "ignore_robots", None):
        overrides["ignore_robots"] = True
    if getattr(args, "no_path_filter", None):
        overrides["no_path_filter"] = True
    if getattr(args, "proxy_list", None):
        overrides["proxy_list"] = args.proxy_list
    if getattr(args, "cookie_file", None):
        overrides["cookie_file"] = args.cookie_file
    if getattr(args, "save_cookies_file", None):
        overrides["save_cookies_file"] = args.save_cookies_file
    return {key: value for key, value in overrides.items() if value is not None}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings(_collect_overrides(args))
    setup_logging(settings)

    if args.command == "crawl":
        return 0 if run_crawl_pipeline(settings) else 1
    if args.command == "status":
        return show_status(settings)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
