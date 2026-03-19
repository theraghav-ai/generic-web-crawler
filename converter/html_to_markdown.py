from __future__ import annotations

import re
from datetime import datetime, timezone

from markdownify import markdownify as md

from crawler.spider import PageContent


def _clean_markdown(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"(---\n?){2,}", "---\n", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _downgrade_headings(markdown_text: str) -> str:
    output: list[str] = []
    for line in markdown_text.split("\n"):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not match:
            output.append(line)
            continue
        hashes, value = match.groups()
        output.append(f"{'#' * min(len(hashes) + 2, 6)} {value}")
    return "\n".join(output)


def _html_to_markdown(html: str) -> str:
    markdown = md(
        html,
        heading_style="ATX",
        bullets="-",
        strip=["img", "script", "style", "noscript", "iframe", "svg", "button", "form"],
        newline_style="backslash",
    )
    return _clean_markdown(markdown)


def pages_to_markdown(pages: list[PageContent], title: str, source_url: str) -> str:
    crawled_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"# {title}",
        "",
        f"**Crawled on:** {crawled_at}",
        f"**Source:** {source_url}",
        f"**Total pages:** {len(pages)}",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
    ]

    for index, page in enumerate(pages, start=1):
        heading = page.title.strip() or page.url
        parts.append(f"{index}. {heading}")

    parts.extend(["", "---", ""])

    for index, page in enumerate(pages, start=1):
        heading = page.title.strip() or page.url
        parts.extend([
            f"## {index}. {heading}",
            "",
            f"**URL:** {page.url}",
            "",
        ])
        if page.structured_md:
            parts.extend([page.structured_md, ""])
        section_markdown = _html_to_markdown(page.content_html)
        if section_markdown:
            parts.append(_downgrade_headings(section_markdown))
        else:
            parts.append(page.content_text.strip() or "*(No extractable content)*")
        parts.extend(["", "---", ""])

    return "\n".join(parts)
