from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup, Tag
from playwright.sync_api import Browser, BrowserContext, Page, Request, Route, sync_playwright
import requests as http_requests

from config import Settings
from crawler.robots_handler import RobotsHandler
from crawler.sitemap_parser import fetch_sitemap
from crawler.state_manager import CheckpointManager, CrawlStateManager
from crawler.structured_data import extract_structured_data
from crawler.url_manager import CrawlTask, URLManager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PageContent:
    url: str
    title: str
    content_html: str
    content_text: str
    links: list[str]
    crawled_at: str
    depth: int = 0
    label: str = ""
    content_hash: str = ""
    structured_md: str = ""
    etag: str = ""
    last_modified: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash and self.content_text:
            self.content_hash = hashlib.sha256(self.content_text.encode("utf-8")).hexdigest()


class Spider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._robots = RobotsHandler(settings)
        self._url_manager = URLManager(settings)
        self._state = CrawlStateManager(settings.state_file)
        self._checkpoint = CheckpointManager(settings.checkpoint_file, settings.checkpoint_interval)
        self._pages: list[PageContent] = []
        self._content_hashes: set[str] = set()
        self._last_request_time = 0.0
        self._max_retries = 3
        self._base_backoff = 1.0
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def _route_handler(self, route: Route, request: Request) -> None:
        resource_type = request.resource_type
        url = request.url.lower()
        blocked_types = {"image", "media", "font"}
        blocked_fragments = ["analytics", "doubleclick", "googletagmanager", "google-analytics"]
        if resource_type in blocked_types or any(fragment in url for fragment in blocked_fragments):
            route.abort()
            return
        route.continue_()

    def _init_browser(self) -> None:
        logger.info("Initializing headless Chromium")
        self._playwright = sync_playwright().start()

        proxy_cfg = None
        if self.settings.proxy_list:
            chosen = random.choice(self.settings.proxy_list)
            proxy_cfg = {"server": chosen}

        self._browser = self._playwright.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=self.settings.user_agent,
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
            bypass_csp=True,
            ignore_https_errors=True,
            locale="en-US",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            },
        )
        self._context.route("**/*", self._route_handler)
        self._context.add_init_script("""
            // 1. Remove webdriver flag
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

            // 2. Restore window.chrome so the browser looks like Chrome
            window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {}, app: {} };

            // 3. Plugins — real Chrome has plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });

            // 4. Languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });

            // 5. Permissions — suppress the automation-specific 'denied' behavior
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) =>
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters);

            // 6. Hide CDP leak
            delete window.__playwright;
            delete window.__pw_manual;
            delete window.__PW_inspect;

            // 7. Consistent screen dimensions
            Object.defineProperty(screen, 'availWidth',  {get: () => 1440});
            Object.defineProperty(screen, 'availHeight', {get: () => 900});
        """)

        if self.settings.cookie_file and self.settings.cookie_file.is_file():
            cookies = json.loads(self.settings.cookie_file.read_text())
            self._context.add_cookies(cookies)
            logger.info("Loaded %d cookies from %s", len(cookies), self.settings.cookie_file)

    def close(self) -> None:
        if self.settings.save_cookies_file and self._context:
            try:
                cookies = self._context.cookies()
                self.settings.save_cookies_file.write_text(json.dumps(cookies, indent=2))
                logger.info("Saved %d cookies to %s", len(cookies), self.settings.save_cookies_file)
            except Exception as exc:
                logger.debug("Cookie save error: %s", exc)
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as exc:
            logger.debug("Browser cleanup error: %s", exc)

    def _enforce_delay(self) -> None:
        robots_delay = 0.0 if not self.settings.respect_robots else (self._robots.crawl_delay or 0.0)
        base = max(self.settings.request_delay, robots_delay)
        jitter = base * random.uniform(-0.3, 0.3)
        effective_delay = max(0.0, base + jitter)
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < effective_delay:
            time.sleep(effective_delay - elapsed)

    def _fetch(self, url: str) -> tuple[str | None, int, str, str]:
        last_status = 0
        for attempt in range(1, self._max_retries + 1):
            html, status, etag, last_modified = self._fetch_once(url)
            last_status = status
            if html is not None:
                return html, status, etag, last_modified
            if 400 <= status < 500 and status not in {403, 429}:
                return None, status, "", ""
            if status in {403, 429}:
                # Try HTTP fallback immediately on first 403/429
                logger.info("Playwright got %d, trying HTTP fallback for %s", status, url)
                fb_html, fb_status, fb_etag, fb_lm = self._fetch_fallback(url)
                if fb_html is not None:
                    return fb_html, fb_status, fb_etag, fb_lm
                # Fallback also failed — retry with Playwright
                if attempt < self._max_retries:
                    backoff = 5.0 * attempt + random.uniform(0, 2)
                    logger.warning("Both blocked status=%d, retry %d after %.1fs", status, attempt, backoff)
                    time.sleep(backoff)
                    continue
                return None, last_status, "", ""
            if attempt < self._max_retries:
                backoff = self._base_backoff * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning("Retry %d/%d for %s after status=%d", attempt, self._max_retries, url, status)
                time.sleep(backoff)
        return None, last_status, "", ""

    def _fetch_fallback(self, url: str) -> tuple[str | None, int, str, str]:
        """Plain HTTP GET fallback when Playwright is blocked."""
        try:
            resp = http_requests.get(
                url,
                timeout=self.settings.request_timeout,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Upgrade-Insecure-Requests": "1",
                },
                allow_redirects=True,
            )
            status = resp.status_code
            etag = resp.headers.get("etag", "")
            last_modified = resp.headers.get("last-modified", "")
            if status >= 400:
                return None, status, etag, last_modified
            content_type = resp.headers.get("content-type", "")
            allowed_types = {"text/html", "application/xhtml", "text/xml", "application/xml"}
            if not any(t in content_type for t in allowed_types):
                return None, status, etag, last_modified
            logger.info("HTTP fallback succeeded for %s (status=%d)", url, status)
            return resp.text, status, etag, last_modified
        except Exception as exc:
            logger.error("HTTP fallback failed for %s: %s", url, exc)
            return None, 0, "", ""

    def _fetch_once(self, url: str) -> tuple[str | None, int, str, str]:
        self._enforce_delay()
        self._last_request_time = time.monotonic()
        page: Page | None = None
        try:
            if self._context is None:
                raise RuntimeError("Browser context not initialized")
            page = self._context.new_page()
            response = page.goto(
                url,
                wait_until="networkidle",
                timeout=self.settings.request_timeout * 1000,
            )
            if response is None:
                return None, 0, "", ""

            status = response.status
            etag = response.headers.get("etag", "")
            last_modified = response.headers.get("last-modified", "")
            if status >= 400:
                return None, status, etag, last_modified

            content_type = response.headers.get("content-type", "")
            allowed_types = {"text/html", "application/xhtml", "text/xml", "application/xml"}
            if not any(t in content_type for t in allowed_types):
                return None, status, etag, last_modified

            page.wait_for_timeout(self.settings.page_load_wait_ms)

            if self.settings.expand_dynamic:
                self._expand_dynamic_content(page)

            # JS link harvesting for SPAs
            try:
                js_links = page.evaluate("""
                    () => {
                        const hrefs = new Set();
                        document.querySelectorAll('[href],[data-href],[data-url],[data-link]').forEach(el => {
                            const v = el.getAttribute('href') || el.getAttribute('data-href')
                                       || el.getAttribute('data-url') || el.getAttribute('data-link');
                            if (v && !v.startsWith('javascript:') && !v.startsWith('mailto:')) hrefs.add(v);
                        });
                        return Array.from(hrefs);
                    }
                """)
                if js_links:
                    extra = "".join(f'<a href="{h}"></a>' for h in js_links)
                    page.evaluate(f"""
                        () => {{
                            const d = document.createElement('div');
                            d.id = '__crawler_js_links__';
                            d.style.display = 'none';
                            d.innerHTML = {json.dumps(extra)};
                            document.body.appendChild(d);
                        }}
                    """)
            except Exception:
                pass

            return page.content(), status, etag, last_modified
        except Exception as exc:
            logger.error("Fetch failed for %s: %s", url, exc)
            return None, 0, "", ""
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _expand_dynamic_content(self, page: Page) -> int:
        self._scroll_to_bottom(page)
        fragments: list[str] = []
        captured_hashes: set[str] = set()
        expanded = 0

        tab_locator = page.locator(
            'button[role="tab"], [role="tab"]:not(a), a[role="tab"][href^="#"], button[aria-controls]'
        )
        for index in range(min(tab_locator.count(), 40)):
            try:
                tab = tab_locator.nth(index)
                if not tab.is_visible(timeout=300):
                    continue
                label = tab.inner_text(timeout=300).strip()
                if not label or len(label) > 120:
                    continue
                tab.click(timeout=1500)
                page.wait_for_timeout(250)

                controlled_html = page.evaluate(
                    """(idx) => {
                        const nodes = Array.from(document.querySelectorAll('[role="tabpanel"], [data-tab-panel], [aria-live]'));
                        const node = nodes[idx] || nodes[0];
                        return node ? node.innerHTML : '';
                    }""",
                    index,
                )
                if controlled_html and len(controlled_html.strip()) > 60:
                    fragment_hash = hashlib.sha256(controlled_html.encode("utf-8")).hexdigest()
                    if fragment_hash not in captured_hashes:
                        captured_hashes.add(fragment_hash)
                        safe_label = label.replace("<", "&lt;").replace(">", "&gt;")
                        fragments.append(f"<section data-tab-label=\"{safe_label}\"><h3>{safe_label}</h3>{controlled_html}</section>")
                        expanded += 1
            except Exception:
                continue

        expander_locator = page.locator(
            'button[aria-expanded="false"], [role="button"][aria-expanded="false"], details:not([open]) > summary'
        )
        for index in range(min(expander_locator.count(), 60)):
            try:
                node = expander_locator.nth(index)
                if not node.is_visible(timeout=200):
                    continue
                node.click(timeout=1200)
                page.wait_for_timeout(200)
                expanded += 1
            except Exception:
                continue

        modal_locator = page.locator(
            'button:has-text("Read More"), button:has-text("Learn More"), button:has-text("View More"), button:has-text("Show More")'
        )
        for index in range(min(modal_locator.count(), 20)):
            try:
                node = modal_locator.nth(index)
                if not node.is_visible(timeout=200):
                    continue
                node.click(timeout=1500)
                page.wait_for_timeout(400)
                dialog = page.locator('[role="dialog"], dialog').first
                if dialog.count() > 0 and dialog.is_visible(timeout=300):
                    html = dialog.inner_html(timeout=1000)
                    if html and len(html.strip()) > 80:
                        fragment_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
                        if fragment_hash not in captured_hashes:
                            captured_hashes.add(fragment_hash)
                            fragments.append(f"<section data-modal=\"true\">{html}</section>")
                            expanded += 1
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(150)
                    except Exception:
                        pass
            except Exception:
                continue

        if fragments:
            page.evaluate(
                """(parts) => {
                    const container = document.createElement('div');
                    container.id = '__crawler_expanded__';
                    container.innerHTML = parts.join('');
                    const root = document.querySelector('main') || document.body;
                    root.appendChild(container);
                }""",
                fragments,
            )
        return expanded

    _WAF_SIGNALS = [
        "access denied",
        "forbidden",
        "just a moment",
        "checking your browser",
        "please wait",
        "ddos protection",
        "attention required",
        "ray id",
        "503 service unavailable",
        "captcha",
        "bot detection",
        "are you human",
    ]

    def _is_blocked_page(self, title: str, content_text: str) -> bool:
        combined = (title + " " + content_text[:500]).lower()
        return any(signal in combined for signal in self._WAF_SIGNALS)

    def _scroll_to_bottom(self, page: Page) -> None:
        """Scroll the page in steps to trigger lazy-load and infinite scroll."""
        prev_height = 0
        for _ in range(10):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(600)
            height = page.evaluate("document.body.scrollHeight")
            if height == prev_height:
                break
            prev_height = height
        page.evaluate("window.scrollTo(0, 0)")

    def _extract_title(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        title = soup.find("title")
        if title:
            return title.get_text(strip=True)
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return ""

    def _extract_links(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if href:
                links.append(href)
        return links

    def _pick_main_content(self, soup: BeautifulSoup) -> Tag | None:
        candidates: list[Tag] = []
        for selector in ["main", "article", '[role="main"]']:
            candidates.extend(soup.select(selector))

        if not candidates:
            for div in soup.find_all(["div", "section"]):
                classes = " ".join(div.get("class", [])) if div.get("class") else ""
                identifier = f"{div.get('id', '')} {classes}".lower()
                if any(token in identifier for token in ["content", "main", "article", "post", "page"]):
                    candidates.append(div)

        if not candidates:
            return soup.body

        return max(candidates, key=lambda node: len(node.get_text(" ", strip=True)))

    def _extract_content(self, html: str) -> tuple[str, str]:
        soup = BeautifulSoup(html, "lxml")
        for tag_name in ["script", "style", "noscript", "iframe", "svg", "canvas"]:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        selectors = [
            "nav",
            "header",
            "footer",
            "aside",
            "form[role='search']",
            "[aria-label='breadcrumb']",
            "[class*='breadcrumb']",
            "[class*='cookie']",
            "[id*='cookie']",
            "[class*='newsletter']",
            "[class*='share']",
            "[class*='social']",
            "[class*='pagination']",
            "[class*='sidebar']",
            "[class*='nav']",
        ]
        for selector in selectors:
            for node in soup.select(selector):
                node.decompose()

        main_content = self._pick_main_content(soup)
        if main_content is None:
            return "", ""
        content_html = str(main_content)
        content_text = main_content.get_text("\n", strip=True)
        return content_html, content_text

    def crawl(self) -> list[PageContent]:
        logger.info("Starting crawl for %s", self.settings.base_url)
        self._init_browser()
        self._state.load()
        self._robots.load()

        checkpoint = self._checkpoint.load()
        if checkpoint:
            self._resume_from_checkpoint(checkpoint)
            logger.info("Resumed crawl from checkpoint")
        else:
            seeds_added = 0
            for seed_url in self.settings.seed_urls:
                if self._url_manager.add_seed(seed_url, label="seed"):
                    seeds_added += 1
            if self.settings.use_sitemap:
                sitemap_added = 0
                for entry in fetch_sitemap(self.settings):
                    if self._url_manager.add_seed(entry.loc, label="sitemap"):
                        sitemap_added += 1
                logger.info("Seeded %d URLs and %d sitemap URLs", seeds_added, sitemap_added)
            else:
                logger.info("Seeded %d URLs", seeds_added)

        try:
            while True:
                task = self._url_manager.next()
                if task is None:
                    break

                url = task.url
                if self._state.is_gone(url):
                    continue
                if not self._robots.is_allowed(url):
                    logger.info("Blocked by robots.txt: %s", url)
                    continue

                logger.info(
                    "[%d/%d] Crawling depth=%d %s",
                    self._url_manager.stats()["visited"],
                    self.settings.max_pages,
                    task.depth,
                    url,
                )
                html, status_code, etag, last_modified = self._fetch(url)
                if status_code in {404, 410}:
                    self._state.mark_gone(url)
                    continue
                if html is None:
                    continue

                title = self._extract_title(html)
                content_html, content_text = self._extract_content(html)
                if self._is_blocked_page(title, content_text):
                    logger.warning("WAF/bot-block detected at %s — skipping", url)
                    continue
                if len(content_text.strip()) < self.settings.min_text_length:
                    continue

                content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
                if content_hash in self._content_hashes:
                    continue
                self._content_hashes.add(content_hash)

                if not self._state.has_content_changed(url, content_hash):
                    self._state.update_page(url, content_hash, etag, last_modified, status_code)
                    logger.info("Unchanged content: %s", url)
                    continue

                soup = BeautifulSoup(html, "lxml")
                structured_md = extract_structured_data(soup).to_markdown()
                links = self._extract_links(html)
                discovered = 0
                for link in links:
                    if self._url_manager.add_discovered(link, url, task.depth):
                        discovered += 1

                page = PageContent(
                    url=url,
                    title=title,
                    content_html=content_html,
                    content_text=content_text,
                    links=links,
                    crawled_at=datetime.now(timezone.utc).isoformat(),
                    depth=task.depth,
                    label=task.label,
                    content_hash=content_hash,
                    structured_md=structured_md,
                    etag=etag,
                    last_modified=last_modified,
                )
                self._pages.append(page)
                self._state.update_page(url, content_hash, etag, last_modified, status_code)
                logger.info("Captured %s (%d chars, %d new links)", title[:80] or url, len(content_text), discovered)

                if self._checkpoint.should_save(len(self._pages)):
                    self._save_checkpoint()
        finally:
            self.close()
            self._state.save()

        self._checkpoint.clear()
        stats = self._url_manager.stats()
        logger.info(
            "Crawl complete: %d pages collected, %d visited, %d discovered",
            len(self._pages),
            stats["visited"],
            stats["discovered"],
        )
        return self._pages

    def _save_checkpoint(self) -> None:
        pages_data = [asdict(page) for page in self._pages]
        queue_tasks = [
            {
                "depth": task.depth,
                "url": task.url,
                "label": task.label,
                "parent_url": task.parent_url,
            }
            for task in self._url_manager._queue
        ]
        self._checkpoint.save(
            visited_hashes=self._url_manager._seen,
            queue_tasks=queue_tasks,
            content_hashes=self._content_hashes,
            pages_data=pages_data,
            visited_count=self._url_manager._visited_count,
        )

    def _resume_from_checkpoint(self, data: dict[str, Any]) -> None:
        self._url_manager._seen = set(data.get("visited_hashes", []))
        self._url_manager._visited_count = int(data.get("visited_count", 0))
        self._content_hashes = set(data.get("content_hashes", []))
        for task_dict in data.get("queue", []):
            self._url_manager._queue.append(
                CrawlTask(
                    depth=task_dict["depth"],
                    url=task_dict["url"],
                    label=task_dict.get("label", ""),
                    parent_url=task_dict.get("parent_url", ""),
                )
            )
        self._pages = [PageContent(**page) for page in data.get("pages", [])]
