"""Универсальный опрос сайта: понять модель бизнеса и подобрать поисковые запросы."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from server.brief_suggest import (
    _brand_tokens,
    _clean_phrase,
    _contains_brand,
    _detect_geo,
    _extract_company_name,
    _has_service_semantics,
    _is_query_worthy,
    _is_section_junk,
    _looks_like_search_query,
)

# Сигналы модели бизнеса (чем выше сумма — тем вероятнее тип)
_MODEL_SIGNALS: dict[str, tuple[str, ...]] = {
    "goods_rental": (
        "аренда ", "прокат ", "в сутки", "за смену", "посуточн", "арендовать",
    ),
    "goods_sales": (
        "купить ", "продажа ", "каталог", "цена ", "в наличии", "корзин",
        "заказать ", "оптом", "дилер", "поставк",
    ),
    "manufacturing": (
        "производств", "завод ", "изготовлен", "цех ", "выпускаем", "фабрик",
    ),
    "construction_services": (
        "под ключ", "монтаж ", "строительств", "проектирован", "смета ",
        "генподряд",
    ),
    "b2b_services": (
        "b2b", "для бизнеса", "для компан", "отдел продаж", "лидоген", "лиды",
        "выручк", "прибыл", "консалт", "аутсорс", "воронк", "спрос", "crm",
        "маркетинг", "обзвон", "квалификац",
    ),
    "cleaning_equipment": (
        "поломоечн", "подметальн", "уборочн", "клининг", "мойк", "пылесос",
        "складск", "погрузочн",
    ),
    "local_services": (
        "выезд", "на дом", "мастер ", "вызов ", "услуги в ", "обслуживание ",
    ),
}

_MODEL_LABELS = {
    "goods_rental": "аренда оборудования/товаров",
    "goods_sales": "продажа товаров",
    "manufacturing": "производство",
    "construction_services": "строительные услуги",
    "b2b_services": "B2B-услуги для бизнеса",
    "cleaning_equipment": "промышленная техника и оборудование",
    "local_services": "локальные услуги",
    "unknown": "услуги и решения для клиентов",
}

_STOP_TERMS = frozenset({
    "главная", "контакты", "компания", "подробнее", "читать", "далее", "меню",
    "сайт", "страница", "раздел", "блок", "кнопка", "форма", "политика",
    "copyright", "права", "защищены", "согласие", "персональных",
})

_CONTENT_WORD_RE = re.compile(r"[а-яёa-z][а-яёa-z\-]{3,}", re.IGNORECASE)


@dataclass
class SiteSurveyData:
    site_url: str
    title: str = ""
    meta_description: str = ""
    headings: list = field(default_factory=list)
    nav_labels: list[str] = field(default_factory=list)
    footer_text: str = ""
    brand_hints: list[str] = field(default_factory=list)
    org_names: list[str] = field(default_factory=list)
    body_text: str = ""
    list_items: list[str] = field(default_factory=list)
    schema_offerings: list[str] = field(default_factory=list)
    pages_surveyed: int = 1


@dataclass
class SiteProfile:
    business_model: str
    audience: str
    summary: str
    offerings: list[str]
    industry_keywords: list[str]
    geo_cities: list[str]


def _body_low(data: SiteSurveyData) -> str:
    return " ".join(
        filter(
            None,
            [
                data.title,
                data.meta_description,
                data.body_text,
                " ".join(data.list_items),
                " ".join(data.schema_offerings),
            ],
        )
    ).lower()


def _score_models(body_low: str) -> dict[str, float]:
    scores: dict[str, float] = {k: 0.0 for k in _MODEL_SIGNALS}
    for model, patterns in _MODEL_SIGNALS.items():
        for pat in patterns:
            if pat in body_low:
                scores[model] += 1.0 + len(pat) / 20.0
    return scores


def _pick_business_model(scores: dict[str, float]) -> str:
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 1.0:
        return "unknown"
    # Если близко к производству, но явная аренда/продажа — приоритет коммерции
    if scores.get("goods_rental", 0) >= scores.get("manufacturing", 0) and scores["goods_rental"] >= 1:
        return "goods_rental"
    if scores.get("goods_sales", 0) >= scores.get("manufacturing", 0) and scores["goods_sales"] >= 1:
        return "goods_sales"
    return best


def _pick_audience(body_low: str, model: str) -> str:
    b2b = sum(1 for s in ("b2b", "для бизнеса", "для компан", "юрлиц", "оптом") if s in body_low)
    b2c = sum(1 for s in ("для дома", "частных", "физлиц", "семьи") if s in body_low)
    if model == "b2b_services" or b2b > b2c:
        return "b2b"
    if b2c > b2b:
        return "b2c"
    return "mixed"


def _heading_offerings(data: SiteSurveyData, brand_tokens: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in data.headings or []:
        text = item.get("text", "") if isinstance(item, dict) else str(item)
        level = (item.get("level") or "h2") if isinstance(item, dict) else "h2"
        if level == "h1":
            continue
        p = _clean_phrase(text)
        if not p or _is_section_junk(p):
            continue
        if _contains_brand(p, brand_tokens) and not _has_service_semantics(p):
            continue
        if not _has_service_semantics(p):
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _list_offerings(items: Iterable[str], brand_tokens: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        p = _clean_phrase(raw)
        if not p or len(p) < 10 or len(p) > 70:
            continue
        if _is_section_junk(p):
            continue
        if not _looks_like_search_query(p) and not _has_service_semantics(p):
            continue
        if _contains_brand(p, brand_tokens) and not _has_service_semantics(p):
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _extract_keywords(body_low: str, limit: int = 12) -> list[str]:
    counts: dict[str, int] = {}
    for word in _CONTENT_WORD_RE.findall(body_low):
        w = word.lower()
        if w in _STOP_TERMS or len(w) < 5:
            continue
        counts[w] = counts.get(w, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], -len(x[0])))
    return [w for w, _ in ranked[:limit]]


def _normalize_offering(phrase: str) -> str:
    p = _clean_phrase(phrase)
    if not p:
        return ""
    p = re.sub(r"^(?:услуги|сервис)\s+", "", p, flags=re.I).strip()
    return p[:80]


def _build_offerings(data: SiteSurveyData, brand_tokens: set[str]) -> list[str]:
    candidates: list[str] = []
    candidates.extend(_heading_offerings(data, brand_tokens))
    candidates.extend(_list_offerings(data.list_items, brand_tokens))
    candidates.extend(_list_offerings(data.schema_offerings, brand_tokens))

    if data.meta_description:
        for chunk in re.split(r"[.!?]\s+", data.meta_description):
            p = _clean_phrase(chunk)
            if p and _has_service_semantics(p) and not _is_section_junk(p):
                candidates.append(p)

    # Осмысленные фразы из текста (не обрывки)
    for m in re.finditer(
        r"[а-яёa-z][а-яёa-z\-]{3,}(?:\s+[а-яёa-z][а-яёa-z\-]{3,}){1,4}",
        data.body_text.lower(),
    ):
        phrase = m.group(0).strip()
        if 18 <= len(phrase) <= 65 and _looks_like_search_query(phrase):
            p = _clean_phrase(phrase)
            if p and not _is_section_junk(p):
                candidates.append(p)

    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        p = _normalize_offering(raw)
        if not p or not _is_query_worthy(p, brand_tokens):
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out[:10]


def _synthetic_b2b_queries(keywords: list[str], body_low: str) -> list[str]:
    queries: list[str] = []
    blob = " ".join(keywords) + " " + body_low
    if any(x in blob for x in ("лидоген", "лид", "лиды", "заявк")):
        queries.extend(["лидогенерация B2B", "аутсорсинг отдела продаж", "генерация лидов для бизнеса"])
    if any(x in blob for x in ("спрос", "привлечен", "клиент")):
        queries.append("привлечение целевых клиентов B2B")
    if any(x in blob for x in ("прибыл", "выручк", "управлен")):
        queries.extend(["управление прибылью компании", "система управления продажами"])
    if any(x in blob for x in ("обзвон", "call", "колл")):
        queries.append("аутсорсинг телемаркетинга B2B")
    if any(x in blob for x in ("маркетинг", "реклам", "продвижен")):
        queries.append("маркетинг для B2B компаний")
    return queries


def _synthetic_goods_queries(model: str, keywords: list[str], offerings: list[str]) -> list[str]:
    queries: list[str] = []
    base = offerings[:3] or ([" ".join(keywords[:2])] if keywords else [])
    for item in base:
        core = _normalize_offering(item)
        if not core or len(core) > 45:
            continue
        low = core.lower()
        if model == "goods_rental":
            if not low.startswith("аренда"):
                queries.extend([f"аренда {core}", f"{core} аренда"])
        elif model == "goods_sales":
            if not any(low.startswith(p) for p in ("купить", "продажа")):
                queries.extend([f"купить {core}", f"{core} цена", f"{core} оптом"])
        elif model == "manufacturing":
            queries.extend([f"производство {core}", f"{core} завод", f"изготовление {core} на заказ"])
        elif model == "cleaning_equipment":
            queries.extend([f"купить {core}", f"{core} поставщик", f"промышленная {core}"])
        elif model == "construction_services":
            if "под ключ" not in low:
                queries.extend([f"{core} под ключ", f"услуги {core}"])
    return queries


def _queries_from_profile(profile: SiteProfile, brand_tokens: set[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if not _is_query_worthy(q, brand_tokens):
            return
        key = q.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(q)

    model = profile.business_model
    offerings = profile.offerings
    keywords = profile.industry_keywords

    # Прямые формулировки услуг/продуктов — как при ручном разборе
    for off in offerings[:8]:
        add(off)

    if model == "b2b_services":
        for q in _synthetic_b2b_queries(keywords, " ".join(offerings).lower()):
            add(q)
    elif model in ("goods_rental", "goods_sales", "manufacturing", "cleaning_equipment", "construction_services"):
        for q in _synthetic_goods_queries(model, keywords, offerings):
            add(q)
    else:
        for off in offerings[:4]:
            add(f"услуги {off}" if not off.lower().startswith("услуг") else off)

    # Гео для локального бизнеса
    if profile.geo_cities and out:
        if model in ("goods_rental", "goods_sales", "construction_services", "local_services"):
            for city in profile.geo_cities[:2]:
                add(f"{out[0]} {city}")

    return out[:14]


def _build_summary(profile: SiteProfile) -> str:
    label = _MODEL_LABELS.get(profile.business_model, _MODEL_LABELS["unknown"])
    parts = [label]
    if profile.offerings:
        parts.append(profile.offerings[0])
    if len(profile.offerings) > 1:
        parts.append(profile.offerings[1])
    text = ". ".join(parts)
    return text[:300]


def run_site_survey(data: SiteSurveyData) -> SiteProfile:
    brand_tokens = _brand_tokens(
        data.site_url, data.title, data.brand_hints, data.org_names
    )
    body_low = _body_low(data)
    scores = _score_models(body_low)
    model = _pick_business_model(scores)
    audience = _pick_audience(body_low, model)
    offerings = _build_offerings(data, brand_tokens)
    keywords = _extract_keywords(body_low)
    geo = _detect_geo(data.body_text + " " + data.footer_text)

    profile = SiteProfile(
        business_model=model,
        audience=audience,
        summary="",
        offerings=offerings,
        industry_keywords=keywords,
        geo_cities=geo,
    )
    profile.summary = _build_summary(profile)
    return profile


def suggest_from_survey(data: SiteSurveyData) -> dict:
    """Итог опроса сайта для автозаполнения брифа."""
    from server.brief_suggest import _build_exclude, _domain_from_url

    brand_tokens = _brand_tokens(
        data.site_url, data.title, data.brand_hints, data.org_names
    )
    profile = run_site_survey(data)
    queries = _queries_from_profile(profile, brand_tokens)
    client_domain = _domain_from_url(data.site_url)

    return {
        "clientSite": data.site_url,
        "clientName": _extract_company_name(
            data.title,
            data.body_text,
            footer_text=data.footer_text,
            brand_hints=data.brand_hints,
            org_names=data.org_names,
        ),
        "niche": profile.summary,
        "queries": "\n".join(queries),
        "excludeDomains": _build_exclude(client_domain),
        "title": data.title or "",
        "source": "site_survey",
        "survey": {
            "pages": data.pages_surveyed,
            "businessModel": profile.business_model,
            "audience": profile.audience,
            "offerings": profile.offerings[:6],
            "keywords": profile.industry_keywords[:8],
        },
    }
