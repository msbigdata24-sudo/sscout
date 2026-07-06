from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from server.config import AGGREGATOR_DOMAINS, CATALOG_DOMAINS, HTTP_TIMEOUT, USER_AGENT
from server.phones import extract_phones, extract_phones_from_text

_YEAR_2026_RE = re.compile(r"\b2026\b")
_TOKEN_RE = re.compile(r"[а-яёa-z0-9]{4,}", re.IGNORECASE)

# Инфо-сайты: пропускаем только если в сниппете есть корень ниши
_INFO_DOMAINS = (
    "habr.com", "vc.ru", "opennet.ru", "wikipedia.org", "tjournal.ru",
    "reddit.com", "pikabu.ru", "lenta.ru", "rbc.ru",
)

# Региональные СМИ в выдаче по коммерческим запросам
_MEDIA_DOMAIN_RE = re.compile(
    r"(^|\.)((mk|rg|news|gazeta|vesti|press)\.[a-z]{2,3}|"
    r"73\.ru|66\.ru|59\.ru|161\.ru|e1\.ru|ngs\.ru)",
    re.IGNORECASE,
)

# Синонимы для B2B / лидгена — одно слово в запросе → несколько в сниппете
_QUERY_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("лидоген", ("лидоген", "лидген", "лиды", "лид ", "lead", "заявк", "спрос")),
    ("лидген", ("лидоген", "лидген", "лиды", "заявк")),
    ("b2b", ("b2b", "бизнес", "компан", "корпорат")),
    ("колл", ("колл", "call", "обзвон", "телемарк", "контакт-центр", "контакт центр")),
    ("обзвон", ("обзвон", "колл", "call", "телемарк")),
    ("аутсорс", ("аутсорс", "аутстафф", "удаленн", "удалённ")),
    ("продаж", ("продаж", "sales", "отдел продаж", "коммерч")),
    ("маркетинг", ("маркетинг", "продвижен", "реклам", "агентств")),
    ("опалуб", ("опалуб", "опалубк")),
)


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
    if ".google." in d or d.endswith(".google.com"):
        return True
    return False


def _niche_stems(queries: list[str], niche: str) -> list[str]:
    stems: set[str] = set()
    stop = {
        "аренда", "продажа", "москва", "область", "нижний", "новгород",
        "ярославль", "владимир", "щелково", "склад", "крупнощитовая",
        "мелкощитовая", "перекрытия", "колонны", "система", "услуги",
        "компания", "бизнеса", "под ключ",
    }
    for text in queries + ([niche] if niche else []):
        low = (text or "").lower()
        if "опалуб" in low:
            stems.add("опалуб")
        for token in _TOKEN_RE.findall(low):
            if len(token) >= 4 and token not in stop:
                stems.add(token[:6] if len(token) > 6 else token)
    return sorted(stems)


def _synonym_match(query_low: str, blob: str) -> bool:
    for needle, variants in _QUERY_SYNONYMS:
        if needle in query_low:
            if any(v in blob for v in variants):
                return True
    return False


def _query_words_match(query: str, blob: str) -> bool:
    q_low = (query or "").lower()
    words = [w for w in _TOKEN_RE.findall(q_low) if len(w) >= 4]
    if not words:
        return True
    hits = sum(1 for w in words if w in blob or w[:5] in blob)
    if hits >= 1:
        return True
    return _synonym_match(q_low, blob)


def serp_hit_relevant(meta: dict, queries: list[str], niche: str) -> bool:
    """Мягкая проверка: доверяем выдаче Яндекса, режем только инфо/СМИ без темы."""
    title = str(meta.get("title") or "")
    snippet = str(meta.get("snippet") or "")
    domain = str(meta.get("domain") or "").lower()
    blob = f"{title} {snippet} {domain}".lower()

    hit_queries = meta.get("queries") or []
    if isinstance(hit_queries, set):
        hit_queries = sorted(hit_queries)
    for q in hit_queries:
        if _query_words_match(str(q), blob):
            return True

    for q in queries:
        if _query_words_match(q, blob):
            return True

    stems = _niche_stems(queries, niche)
    if stems and any(s in blob for s in stems):
        return True

    if any(d in domain for d in _INFO_DOMAINS):
        return False
    if _MEDIA_DOMAIN_RE.search(domain):
        return False

    # Коммерческий домен из выдачи по запросу — оставляем (Яндекс уже отфильтровал)
    return bool(queries or niche)


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
    hits = sum(1 for r in regions if _region_in_text(hay, r))
    if mode == "include":
        return hits > 0
    return hits == 0


def serp_passes_region_filter(
    meta: dict,
    regions: list[str],
    region_mode: str,
) -> bool:
    """На этапе SERP регион режет только в режиме «исключить»."""
    if not regions or region_mode != "exclude":
        return True
    return region_matches(serp_meta_region_text(meta), regions, "exclude")


def _region_in_text(hay: str, region: str) -> bool:
    r = (region or "").lower().strip()
    if not r:
        return False
    if r in hay:
        return True
    for alias in _REGION_ALIASES.get(r, ()):
        if alias in hay:
            return True
    for suffix in (
        "ская область",
        "ский край",
        " автономная область",
        " автономный округ",
        "ская республика",
        " народная республика",
    ):
        if r.endswith(suffix):
            stem = r[: -len(suffix)].strip()
            if len(stem) >= 4 and stem in hay:
                return True
    if r == "москва" and "москв" in hay:
        return True
    if r == "санкт-петербург" and ("петербург" in hay or "спб" in hay):
        return True
    return False


_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "еврейская автономная область": ("еао", "еврейск", "биробиджан"),
    "ненецкий автономный округ": ("ненец", "нао"),
    "ханты-мансийский автономный округ — югра": ("югра", "хмао", "ханты", "сургут"),
    "чукотский автономный округ": ("чукот", "анадыр"),
    "ямало-ненецкий автономный округ": ("янао", "ямал", "салехард"),
}


def serp_meta_region_text(meta: dict) -> str:
    queries = meta.get("queries") or []
    if isinstance(queries, set):
        queries = sorted(queries)
    return " ".join(
        str(meta.get(k) or "")
        for k in ("title", "snippet")
    ) + " " + " ".join(str(q) for q in queries)


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


async def check_site_alive(
    url: str,
    *,
    require_phone: bool = False,
    timeout: float | None = None,
) -> tuple[bool, str, int]:
    headers = {"User-Agent": USER_AGENT}
    req_timeout = float(timeout if timeout is not None else HTTP_TIMEOUT)
    try:
        async with httpx.AsyncClient(
            timeout=req_timeout, headers=headers, follow_redirects=True,
        ) as client:
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
