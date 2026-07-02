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


def _niche_stems(queries: list[str], niche: str) -> list[str]:
    """Корни ключевых слов ниши — по ним отсекаем мусор вроде opennet.ru."""
    stems: set[str] = set()
    for text in queries + ([niche] if niche else []):
        low = (text or "").lower()
        if "опалуб" in low:
            stems.add("опалуб")
        for token in _TOKEN_RE.findall(low):
            if len(token) >= 5 and token not in {
                "аренда", "продажа", "москва", "область", "нижний", "новгород",
                "ярославль", "владимир", "щелково", "склад", "крупнощитовая",
                "мелкощитовая", "перекрытия", "колонны",
            }:
                stems.add(token[:6] if len(token) > 6 else token)
    if not stems:
        for text in queries + ([niche] if niche else []):
            for token in _TOKEN_RE.findall(text or ""):
                if len(token) >= 5:
                    stems.add(token[:5])
    return sorted(stems)


def serp_hit_relevant(meta: dict, queries: list[str], niche: str) -> bool:
    """Сниппет/домен должны содержать корень ниши (опалуб…), иначе это мусор из выдачи."""
    title = str(meta.get("title") or "")
    snippet = str(meta.get("snippet") or "")
    domain = str(meta.get("domain") or "").lower()
    text_blob = f"{title} {snippet}".lower()

    stems = _niche_stems(queries, niche)
    for stem in stems:
        if stem in text_blob or stem in domain:
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


_PHONE_HINT_RE = re.compile(
    r"(?:\+7|8)[\s\-()]*(?:\d[\s\-()]*){9,}|tel:\s*\+?\d",
    re.IGNORECASE,
)


def _html_has_phone_hint(html: str) -> bool:
    if not html:
        return False
    if _PHONE_HINT_RE.search(html[:80000]):
        return True
    from server.phones import extract_phones

    return bool(extract_phones(html[:120000], ""))


async def check_site_alive(url: str, *, require_phone: bool = False) -> tuple[bool, str, int]:
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
            if require_phone and not _html_has_phone_hint(html):
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
