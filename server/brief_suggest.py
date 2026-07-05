"""Подбор ниши, запросов и исключений по сайту клиента (без ИИ — по тексту страницы)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from server.config import AGGREGATOR_DOMAINS

_STANDARD_EXCLUDE = (
    "avito.ru",
    "2gis.ru",
    "yell.ru",
    "pulscen.ru",
    "wikipedia.org",
    "youtube.com",
    "ozon.ru",
    "wildberries.ru",
    "market.yandex.ru",
)

_GEO_CITIES = (
    ("москва", "Москва"),
    ("московск", "Москва"),
    ("щелково", "Щелково"),
    ("ярославл", "Ярославль"),
    ("владимир", "Владимир"),
    ("нижний новгород", "Нижний Новгород"),
    ("санкт-петербург", "Санкт-Петербург"),
    ("петербург", "Санкт-Петербург"),
    ("казань", "Казань"),
    ("екатеринбург", "Екатеринбург"),
    ("новосибирск", "Новосибирск"),
)

_NICHE_PROFILES: list[dict] = [
    {
        "triggers": ("опалуб",),
        "niche": "Аренда и продажа строительной опалубки (крупнощитовая, мелкощитовая, перекрытия, колонны).",
        "queries": [
            "аренда опалубки",
            "аренда крупнощитовой опалубки",
            "аренда мелкощитовой опалубки",
            "аренда опалубки перекрытий",
            "продажа опалубки",
            "опалубка для колонн",
            "аренда опалубки для фундамента",
            "аренда строительных лесов",
        ],
        "if_text": {
            "продаж": "продажа опалубки",
            "крупнощит": "аренда крупнощитовой опалубки",
            "мелкощит": "аренда мелкощитовой опалубки",
            "перекрыт": "аренда опалубки перекрытий",
            "cup-lock": "аренда опалубки Cup-Lock",
            "колонн": "опалубка для колонн",
            "фундамент": "аренда опалубки для фундамента",
            "лесов": "аренда строительных лесов",
            "щелково": "аренда опалубки Щелково",
        },
        "exclude_extra": ("opalubka.ru", "peri.ru", "snab-str.ru", "opalubka-market.ru"),
    },
    {
        "triggers": ("углеволок", "wallwrap", "wallgraf", "композитн", "внешн", "армирован"),
        "niche": "Усиление железобетонных конструкций углеволокном (внешнее армирование), материалы, проектирование и монтаж.",
        "queries": [
            "усиление конструкций углеволокном",
            "усиление железобетона углеволокном",
            "внешнее армирование железобетонных конструкций",
            "усиление плит перекрытия углеволокном",
            "усиление балок углеволокном",
            "усиление колонн углеволокном",
            "углеродные ленты для усиления бетона",
            "монтаж углеволокна",
            "проектирование усиления углеволокном",
            "ремонт трещин в бетоне инъекция",
        ],
        "if_text": {
            "мост": "усиление мостов углеволокном",
            "обследован": "обследование несущих конструкций",
        },
        "exclude_extra": (),
    },
    {
        "triggers": ("ангар", "быстровозводим", "металлоконструкц"),
        "niche": "Проектирование и строительство ангаров и быстровозводимых зданий под ключ.",
        "queries": [
            "строительство ангаров под ключ",
            "металлические ангары",
            "ангары из сэндвич панелей",
            "быстровозводимые здания",
            "строительство складов под ключ",
            "изготовление металлоконструкций",
            "монтаж металлоконструкций",
            "проектирование ангаров",
        ],
        "if_text": {
            "склад": "строительство складов под ключ",
            "ферм": "металлические фермы",
        },
        "exclude_extra": (),
    },
]


def _domain_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        host = urlparse(raw if "://" in raw else f"https://{raw}").netloc.lower()
    except Exception:
        return raw.lower().split("/")[0]
    return host[4:] if host.startswith("www.") else host


def _extract_company_name(title: str, text: str) -> str:
    blob = f"{title}\n{text[:4000]}"
    patterns = (
        r"(ООО\s+[«\"][^»\"]+[»\"])",
        r"(ИП\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,3})",
        r"©[^\n]{0,80}(ООО\s+[«\"][^»\"]+[»\"])",
        r"©[^\n]{0,80}(ИП\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,3})",
    )
    for pat in patterns:
        m = re.search(pat, blob, flags=re.IGNORECASE)
        if m:
            name = m.group(1).strip().strip(",")
            if len(name) >= 4:
                return name[:120]
    return ""


def _detect_geo(text: str) -> list[str]:
    low = (text or "").lower()
    found: list[str] = []
    for needle, label in _GEO_CITIES:
        if needle in low and label not in found:
            found.append(label)
    return found[:3]


def _pick_profile(text: str) -> dict | None:
    low = (text or "").lower()
    best: dict | None = None
    best_score = 0
    for profile in _NICHE_PROFILES:
        score = sum(2 for t in profile["triggers"] if t in low)
        if score <= 0:
            continue
        for needle in profile.get("if_text", {}):
            if needle in low:
                score += 1
        if score > best_score:
            best_score = score
            best = profile
    return best


def _generic_queries(title: str, text: str) -> list[str]:
    low = f"{title} {text[:1500]}".lower()
    words = re.findall(r"[а-яёa-z]{5,}", low)
    freq: dict[str, int] = {}
    stop = {
        "компания", "услуги", "подробнее", "контакты", "главная", "каталог", "новости",
        "статьи", "политика", "обработку", "персональных", "данных", "согласен",
    }
    for w in words:
        if w in stop:
            continue
        freq[w] = freq.get(w, 0) + 1
    top = sorted(freq, key=freq.get, reverse=True)[:4]
    queries: list[str] = []
    if top:
        queries.append(top[0])
        if len(top) > 1:
            queries.append(f"{top[0]} {top[1]}")
    if "аренда" in low:
        queries.append(f"аренда {top[0]}" if top else "аренда оборудования")
    if "продаж" in low or "купить" in low:
        queries.append(f"купить {top[0]}" if top else "купить оборудование")
    if "монтаж" in low:
        queries.append("монтаж " + (top[0] if top else "оборудование"))
    if title and len(title) < 60:
        queries.insert(0, title.split("|")[0].strip())
    return queries[:10]


def _build_queries(profile: dict | None, title: str, text: str) -> list[str]:
    low = f"{title} {text}".lower()
    queries: list[str] = []
    if profile:
        queries.extend(profile["queries"])
        for needle, q in profile.get("if_text", {}).items():
            if needle in low and q not in queries:
                queries.append(q)
    else:
        queries.extend(_generic_queries(title, text))

    geos = _detect_geo(text)
    base_for_geo = queries[0] if queries else ""
    for city in geos:
        gq = f"{base_for_geo} {city}".strip()
        if gq and gq not in queries:
            queries.append(gq)

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if not q:
            continue
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out[:14]


def _build_exclude(client_domain: str, profile: dict | None) -> str:
    domains: list[str] = []
    if client_domain:
        domains.append(client_domain)
    for d in _STANDARD_EXCLUDE:
        if d not in domains:
            domains.append(d)
    if profile:
        for d in profile.get("exclude_extra", ()):
            if d not in domains:
                domains.append(d)
    for d in sorted(AGGREGATOR_DOMAINS):
        clean = d[4:] if d.startswith("www.") else d
        if clean in domains:
            continue
        if clean in _STANDARD_EXCLUDE:
            continue
    return ", ".join(domains)


def suggest_brief_from_analysis(
    *,
    site_url: str,
    title: str,
    text_sample: str,
) -> dict:
    text = text_sample or ""
    profile = _pick_profile(text)
    client_domain = _domain_from_url(site_url)
    niche = profile["niche"] if profile else (title.split("|")[0].strip() if title else "")
    if not niche and text:
        niche = re.sub(r"\s+", " ", text[:200]).strip()
    queries = _build_queries(profile, title, text)
    return {
        "clientSite": site_url,
        "clientName": _extract_company_name(title, text),
        "niche": niche[:300],
        "queries": "\n".join(queries),
        "excludeDomains": _build_exclude(client_domain, profile),
        "title": title or "",
    }
