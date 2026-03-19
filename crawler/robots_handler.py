from __future__ import annotations

import logging
from urllib.robotparser import RobotFileParser

import requests

from config import Settings

logger = logging.getLogger(__name__)


class RobotsHandler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._parser = RobotFileParser()
        self._loaded = False
        self._allow_all = False
        self._crawl_delay: float | None = None

    def load(self) -> None:
        robots_url = self.settings.base_url.rstrip("/") + "/robots.txt"
        try:
            response = requests.get(
                robots_url,
                timeout=self.settings.request_timeout,
                headers={"User-Agent": self.settings.user_agent},
            )
            if response.status_code == 200:
                self._parser.parse(response.text.splitlines())
                self._crawl_delay = self._parser.crawl_delay(self.settings.user_agent)
                self._allow_all = False
                logger.info("robots.txt loaded from %s", robots_url)
            else:
                self._allow_all = True
                logger.info(
                    "robots.txt returned %d, proceeding fail-open",
                    response.status_code,
                )
        except requests.RequestException as exc:
            self._allow_all = True
            logger.warning("robots.txt fetch failed for %s: %s", robots_url, exc)
        self._loaded = True

    def is_allowed(self, url: str) -> bool:
        if not self.settings.respect_robots:
            return True
        if not self._loaded:
            self.load()
        if self._allow_all:
            return True
        try:
            return self._parser.can_fetch(self.settings.user_agent, url)
        except Exception:
            return True

    @property
    def crawl_delay(self) -> float | None:
        if not self.settings.respect_robots:
            return None
        if not self._loaded:
            self.load()
        return self._crawl_delay
