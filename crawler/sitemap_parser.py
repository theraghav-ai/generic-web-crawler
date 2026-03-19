from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.etree import ElementTree

import requests

from config import Settings

logger = logging.getLogger(__name__)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@dataclass(slots=True)
class SitemapEntry:
    loc: str
    lastmod: str = ""
    priority: float = 0.5


def fetch_sitemap(settings: Settings) -> list[SitemapEntry]:
    sitemap_urls = _discover_sitemap_urls(settings)
    seen: set[str] = set()
    entries: list[SitemapEntry] = []
    for sitemap_url in sitemap_urls:
        for entry in _fetch_and_parse(settings, sitemap_url, depth=0):
            if entry.loc not in seen:
                seen.add(entry.loc)
                entries.append(entry)
    entries.sort(key=lambda item: item.priority, reverse=True)
    if entries:
        logger.info("Discovered %d sitemap URLs", len(entries))
    return entries


def _discover_sitemap_urls(settings: Settings) -> list[str]:
    urls = [settings.base_url.rstrip("/") + "/sitemap.xml"]
    robots_url = settings.base_url.rstrip("/") + "/robots.txt"
    try:
        response = requests.get(
            robots_url,
            timeout=settings.request_timeout,
            headers={"User-Agent": settings.user_agent},
        )
        if response.status_code == 200:
            for line in response.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    candidate = line.split(":", 1)[1].strip()
                    if candidate and candidate not in urls:
                        urls.append(candidate)
    except requests.RequestException:
        pass
    return urls


def _fetch_and_parse(settings: Settings, url: str, depth: int) -> list[SitemapEntry]:
    if depth > 3:
        return []
    try:
        response = requests.get(
            url,
            timeout=min(settings.request_timeout, 20),
            headers={"User-Agent": settings.user_agent},
        )
    except requests.RequestException as exc:
        logger.debug("Sitemap fetch failed for %s: %s", url, exc)
        return []

    if response.status_code != 200:
        return []

    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError:
        return []

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag == "sitemapindex":
        entries: list[SitemapEntry] = []
        for child in root.findall("sm:sitemap", SITEMAP_NS):
            loc = child.find("sm:loc", SITEMAP_NS)
            if loc is not None and loc.text:
                entries.extend(_fetch_and_parse(settings, loc.text.strip(), depth + 1))
        return entries

    if root_tag != "urlset":
        return []

    entries = []
    for url_node in root.findall("sm:url", SITEMAP_NS):
        loc = url_node.find("sm:loc", SITEMAP_NS)
        if loc is None or not loc.text:
            continue
        entry = SitemapEntry(loc=loc.text.strip())
        lastmod = url_node.find("sm:lastmod", SITEMAP_NS)
        if lastmod is not None and lastmod.text:
            entry.lastmod = lastmod.text.strip()
        priority = url_node.find("sm:priority", SITEMAP_NS)
        if priority is not None and priority.text:
            try:
                entry.priority = float(priority.text.strip())
            except ValueError:
                pass
        entries.append(entry)
    return entries
