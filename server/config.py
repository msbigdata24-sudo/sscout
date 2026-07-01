from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()
SCRAPINGBEE_API_KEY = os.getenv("SCRAPINGBEE_API_KEY", "").strip()
SCRAPINGFISH_API_KEY = os.getenv("SCRAPINGFISH_API_KEY", "").strip()
PORT = int(os.getenv("SIGNAL_SCOUT_PORT", "8765"))
CRAWL_CONCURRENCY = max(1, min(15, int(os.getenv("CRAWL_CONCURRENCY", "8"))))
HTTP_TIMEOUT = max(5, int(os.getenv("HTTP_TIMEOUT", "15")))
SITE_TIMEOUT = max(10, int(os.getenv("SITE_TIMEOUT", "30")))
FETCH_RETRIES = max(1, min(5, int(os.getenv("FETCH_RETRIES", "3"))))
SERP_PAGES = 4
DEFAULT_MAX_SITES = 50
DB_PATH = ROOT / "data" / "signal_scout.db"

AGGREGATOR_DOMAINS = frozenset({
    "avito.ru", "www.avito.ru",
    "2gis.ru", "www.2gis.ru",
    "yell.ru", "www.yell.ru",
    "pulscen.ru", "www.pulscen.ru",
    "kudago.com", "www.kudago.com",
    "yandex.ru", "www.yandex.ru",
    "google.com", "www.google.com",
    "wikipedia.org", "ru.wikipedia.org",
    "youtube.com", "www.youtube.com",
    "vk.com", "www.vk.com",
    "t.me", "telegram.me",
    "zen.yandex.ru", "dzen.ru",
})

CATALOG_DOMAINS = frozenset({
    "kudagid.ru", "www.kudagid.ru",
    "2gis.ru", "www.2gis.ru",
})

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 SignalScout/1.0"
)
