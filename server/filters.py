from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from server.config import AGGREGATOR_DOMAINS, CATALOG_DOMAINS, HTTP_TIMEOUT, USER_AGENT
from server.phones import extract_phones, extract_phones_from_text

_YEAR_2026_RE = re.compile(r"\b2026\b")


def parse_domain_list(raw: str) -> set[str]:
    out: set[str] = set()
    for part in (raw or "").replace("\n", ",").split(","):
        d = part.strip().lower()
        if d.startswith("www."):
            d = d[4:]
        if d:
            out.add(d)
    return out


def parse_regions(raw: str) -> list[str]:
    return [r.strip().lower() for r in (raw or "").replace("\n", ",").split(",") if r.strip()]


def is_aggregator(domain: str) -> bool:
    d = domain.lower()
    if d.startswith("www."):
        d = d[4:]
    if d in AGGREGATOR_DOMAINS:
        return True
    for agg in AGGREGATOR_DOMAINS:
        agg_clean = agg[4:] if agg.startswith("www.") else agg
        if d == agg_clean or d.endswith("." + agg_clean):
            return True
    # Сервисы Google: support.google.com, accounts.google.com …
    if ".google." in d or d.endswith(".google.com"):
        return True
    return False


_TOKEN_RE = re.compile(r"[а-яёa-z]{4,}", re.IGNORECASE)


def serp_hit_relevant(meta: dict, queries: list[str], niche: str) -> bool:
    """Сниппет должен быть по теме запроса (опалубка и т.д.), иначе это мусор из выдачи."""
    blob = " ".join([
        str(meta.get("title") or ""),
        str(meta.get("snippet") or ""),
        str(meta.get("domain") or ""),
    ]).lower()
    tokens: set[str] = set()
    for q in queries:
        tokens.update(t.lower() for t in _TOKEN_RE.findall(q))
    if niche:
        tokens.update(t.lower() for t in _TOKEN_RE.findall(niche))
    for token in tokens:
        if len(token) >= 4 and token in blob:
            return True
        if len(token) >= 5 and token[:5] in blob:
            return True
    return False


def is_catalog_domain(domain: str) -> bool:
    d = domain.lower().lstrip("www.")
    return d in CATALOG_DOMAINS or any(d.endswith("." + c) for c in CATALOG_DOMAINS)


def is_excluded(domain: str, exclude: set[str], client_domain: str) -> bool:
    d = domain.lower().lstrip("www.")
    client = client_domain.lower().lstrip("www.")
    if d == client or d.endswith("." + client):
        return True
    return d in exclude or any(d.endswith("." + e) for e in exclude)


def region_matches(text: str, regions: list[str], mode: str) -> bool:
    if not regions:
        return True
    hay = (text or "").lower()
    hits = sum(1 for r in regions if r in hay)
    if mode == "include":
        return hits > 0
    return hits == 0


async def check_site_alive(url: str) -> tuple[bool, str, int]:
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            html = resp.text or ""
            final = str(resp.url)
            if resp.status_code >= 400:
                return False, final, resp.status_code
            if len(html) < 200:
                return False, final, resp.status_code
            parking = ("domain is for sale", "parked", "купить домен", "coming soon")
            low = html[:3000].lower()
            if any(p in low for p in parking):
                return False, final, resp.status_code
            return True, final, resp.status_code
    except Exception:
        return False, url, 0


def has_2026_mark(html_or_text: str) -> bool:
    return bool(_YEAR_2026_RE.search(html_or_text or ""))


def snippet_from_serp(item: dict) -> str:
    return " ".join(
        str(item.get(k) or "")
        for k in ("title", "snippet", "description", "source")
    )


def classify_domain(
    domain: str,
    *,
    exclude: set[str],
    client_domain: str,
    forced_aggregator: bool = False,
) -> str:
    if is_excluded(domain, exclude, client_domain):
        return "исключён"
    if forced_aggregator or is_aggregator(domain):
        return "агрегатор"
    return "кандидат"


async def try_catalog_page(url: str) -> tuple[list[str], str]:
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return [], ""
            text = resp.text or ""
            return [p["phone"] for p in extract_phones(text)], text.lower()
    except Exception:
        return [], ""
