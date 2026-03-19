from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StructuredData:
    json_ld: list[dict] = field(default_factory=list)
    open_graph: dict[str, str] = field(default_factory=dict)
    meta_description: str = ""
    faq_pairs: list[dict[str, str]] = field(default_factory=list)

    def to_markdown(self) -> str:
        parts: list[str] = []
        if self.meta_description:
            parts.append(f"**Description:** {self.meta_description}")
        if self.open_graph:
            items = "\n".join(f"- {key}: {value}" for key, value in self.open_graph.items())
            parts.append(f"**Open Graph**\n{items}")
        if self.faq_pairs:
            faq_lines: list[str] = []
            for pair in self.faq_pairs:
                faq_lines.append(f"**Q:** {pair['question']}")
                faq_lines.append(f"**A:** {pair['answer']}")
            parts.append("**FAQ**\n" + "\n".join(faq_lines))
        if self.json_ld:
            for entry in self.json_ld[:3]:
                entry_type = entry.get("@type", "Unknown")
                serialized = json.dumps(entry, ensure_ascii=False, default=str)
                parts.append(f"**Schema.org ({entry_type})** {serialized[:500]}")
        return "\n\n".join(parts)


def extract_structured_data(soup: BeautifulSoup) -> StructuredData:
    data = StructuredData()

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string
            if not raw:
                continue
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                data.json_ld.extend(item for item in parsed if isinstance(item, dict))
            elif isinstance(parsed, dict):
                data.json_ld.append(parsed)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.debug("Failed to parse JSON-LD: %s", exc)

    for meta in soup.find_all("meta", attrs={"property": True}):
        prop = meta.get("property", "")
        content = meta.get("content", "")
        if prop.startswith("og:") and content:
            data.open_graph[prop] = content

    meta_description = soup.find("meta", attrs={"name": "description"})
    if meta_description and meta_description.get("content"):
        data.meta_description = meta_description["content"].strip()

    for entry in data.json_ld:
        if entry.get("@type") == "FAQPage":
            for question in entry.get("mainEntity", []):
                if question.get("@type") != "Question":
                    continue
                question_text = question.get("name", "")
                answer = question.get("acceptedAnswer", {})
                answer_text = answer.get("text", "") if isinstance(answer, dict) else ""
                if question_text and answer_text:
                    data.faq_pairs.append({"question": question_text, "answer": answer_text})

    for details in soup.find_all("details"):
        summary = details.find("summary")
        if not summary:
            continue
        question = summary.get_text(strip=True)
        answer_parts = []
        for child in details.children:
            if child == summary or not hasattr(child, "get_text"):
                continue
            text = child.get_text(" ", strip=True)
            if text:
                answer_parts.append(text)
        answer = " ".join(answer_parts)
        if question and answer:
            data.faq_pairs.append({"question": question, "answer": answer})

    return data
