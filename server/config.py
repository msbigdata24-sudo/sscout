from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Меняется при каждом значимом релизе — проверка, что Render подтянул новый код.
BUILD_VERSION = "2026-07-03-timeout-fix"

XMLRIVER_USER = os.getenv("XMLRIVER_USER", "").strip()
XMLRIVER_KEY = os.getenv("XMLRIVER_KEY", "").strip()
YANDEX_XML_USER = os.getenv("YANDEX_XML_USER", "").strip()
YANDEX_XML_KEY = os.getenv("YANDEX_XML_KEY", "").strip()
SCRAPINGBEE_API_KEY = os.getenv("SCRAPINGBEE_API_KEY", "").strip()
SCRAPINGFISH_API_KEY = os.getenv("SCRAPINGFISH_API_KEY", "").strip()
PORT = int(os.getenv("PORT") or os.getenv("SIGNAL_SCOUT_PORT") or "8765")
CRAWL_CONCURRENCY = max(1, min(15, int(os.getenv("CRAWL_CONCURRENCY", "5"))))
HTTP_TIMEOUT = max(5, int(os.getenv("HTTP_TIMEOUT", "20")))
SITE_TIMEOUT = max(10, int(os.getenv("SITE_TIMEOUT", "45")))
SITE_CRAWL_TIMEOUT = max(15, int(os.getenv("SITE_CRAWL_TIMEOUT", "90")))
SERP_TIMEOUT = max(30, int(os.getenv("SERP_TIMEOUT", "90")))
FETCH_RETRIES = max(1, min(5, int(os.getenv("FETCH_RETRIES", "3"))))
SERP_PAGES = max(1, min(4, int(os.getenv("SERP_PAGES", "2"))))
DEFAULT_MAX_SITES = 50
DB_PATH = ROOT / "data" / "signal_scout.db"

# Проверенные конкуренты Опалубки — для быстрого теста обхода без XMLRiver
PILOT_SEED_DOMAINS = (
    "best-opalubka.ru",
    "avant-opalubka.ru",
    "arsipro.ru",
    "opalubka-trade.ru",
    "faneratorg.ru",
    "prosto-monolit.ru",
    "myopalubka.ru",
    "egds.ru",
    "opalubkaset.ru",
    "renta76.ru",
    "arendaopalubki52.ru",
    "gora-nn.ru",
)

DEFAULT_PILOT_QUERIES = (
    "аренда опалубки",
    "аренда крупнощитовой опалубки",
    "аренда мелкощитовой опалубки",
    "аренда опалубки Москва",
    "аренда опалубки Щелково",
    "аренда опалубки Ярославль",
    "аренда опалубки Владимир",
    "аренда опалубки Нижний Новгород",
)

AGGREGATOR_DOMAINS = frozenset({
    "avito.ru", "www.avito.ru", "m.avito.ru",
    "2gis.ru", "www.2gis.ru",
    "yell.ru", "www.yell.ru",
    "pulscen.ru", "www.pulscen.ru",
    "kudago.com", "www.kudago.com",
    "yandex.ru", "www.yandex.ru", "ya.ru",
    "google.com", "www.google.com",
    "google.ru", "www.google.ru",
    "wikipedia.org", "ru.wikipedia.org", "uk.wikipedia.org", "en.wikipedia.org",
    "youtube.com", "www.youtube.com", "youtu.be",
    "vk.com", "www.vk.com", "m.vk.com",
    "t.me", "telegram.me", "telegram.org",
    "zen.yandex.ru", "dzen.ru", "dzen.ru",
    "facebook.com", "instagram.com", "ok.ru", "mail.ru",
    "ozon.ru", "wildberries.ru", "market.yandex.ru",
})

CATALOG_DOMAINS = frozenset({
    "kudagid.ru", "www.kudagid.ru",
    "2gis.ru", "www.2gis.ru",
})

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 SignalScout/1.0"
)
