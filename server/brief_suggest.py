"""Универсальный подбор брифа по главной странице сайта (без профилей под отдельные ниши)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

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
    ("москве", "Москва"),
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

# UI-мусор: точное совпадение или фрагмент внутри фразы
_SKIP_PHRASE = re.compile(
    r"^(главная|каталог|контакты|о компании|о нас|услуги|портфолио|производство|"
    r"новости|статьи|акции|проекты|скачать|отзывы|доставка|оплата|ещё|меню|"
    r"подробнее|узнать стоимость|оставить заявку|заказать|получить консультацию|"
    r"заказать звонок|отправить|согласен|принимаю|загрузка|все права|copyright|"
    r"сертификат|лицензи|реализованн.*проект|"
    r"для кого|кто за этим стоит|форматы старта|физика прибыли|технологии|взаимодействие|"
    r"команда|faq|связаться|результаты пилотов|"
    r"\d{1,3}\s*м²|\d+$|0\d$)$",
    re.IGNORECASE,
)

_SECTION_JUNK_RE = re.compile(
    r"(кто за этим|"
    r"\d+\s*проблем.*\d+\s*реш|"
    r"результат.*пилот|"
    r"^решение\s*:|^проблема\s*:|"
    r"система прибыли\s*:\s*\d+|"
    r":\s*\d+\s*этап|"
    r"^\d+\s*проблем|"
    r"^\d+\s*этап)",
    re.IGNORECASE,
)

_LABEL_PREFIX_RE = re.compile(r"^(?:решение|проблема|этап)\s*:\s*", re.IGNORECASE)

# Фраза похожа на услугу/нишу, а не на слоган бренда
_SERVICE_HINTS = (
    "аренда", "продаж", "опалуб", "каркас", "дом", "ангар", "строитель",
    "монтаж", "производ", "оборудован", "металл", "бетон", "опт",
    "лидоген", "продаж", "b2b", "маркетинг", "клиент", "воронк", "спрос",
    "прибыл", "консалт", "аутсорс", "crm", "лид", "обзвон", "выручк",
    "машин", "станк", "спецодеж", "кровл", "фасад", "дистриб",
    "усилен", "углеволок", "армирован", "aeo", "jtbd",
)

_QUERY_STARTERS = (
    "аренда", "продаж", "купить", "строитель", "монтаж", "лидоген", "систем",
    "управлен", "привлечен", "аутсорс", "консалт", "маркетинг", "обслужив",
    "изготовлен", "доставк", "ремонт", "проектирован", "обследован", "каркас",
    "опалуб", "ангар", "дом ", "усилен",
)

_BAD_QUERY_START = frozenset({
    "производство", "завод", "результат", "результаты", "спроса", "решение",
    "проблема", "этап", "этапы", "пилот", "пилоты", "команда", "технологии",
})

_PROMO_RE = re.compile(
    r"акци[!]|скидк|%|специальн.*цен|оформлении.*месяц|б/у\s|рассрочк",
    re.IGNORECASE,
)

_STOP_WORDS = frozenset({
    "компания", "услуги", "подробнее", "контакты", "главная", "каталог", "новости",
    "статьи", "политика", "обработку", "персональных", "данных", "согласен",
    "принимаю", "отправить", "заявку", "заказать", "звонок", "смотреть", "разделы",
    "связаться", "вопросы", "загрузка", "home", "menu", "blog", "read", "more",
})

_INTENT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("аренда", ("аренда {}", "аренда {} цена")),
    ("продаж", ("продажа {}", "купить {}")),
    ("купить", ("купить {}", "цена {}")),
    ("строительств", ("строительство {}", "{} под ключ")),
    ("монтаж", ("монтаж {}", "услуги {}")),
    ("производств", ("завод {}", "производство {}")),
    ("изготовлен", ("изготовление {}", "заказать {}")),
    ("усилени", ("усиление {}", "услуги {}")),
    ("ремонт", ("ремонт {}", "{} цена")),
    ("проектирован", ("проектирование {}", "проект {}")),
    ("обследован", ("обследование {}", "диагностика {}")),
    ("доставк", ("доставка {}", "{} с доставкой")),
)

_FOOTER_STOP = re.compile(
    r"\s*(политика|инн|огrn|оферт|конфиденциаль|пользовательск|©|copyright)\b",
    re.IGNORECASE,
)


def _brand_tokens(
    site_url: str,
    title: str,
    brand_hints: list[str] | None,
    org_names: list[str] | None,
) -> set[str]:
    tokens: set[str] = set()
    host = _domain_from_url(site_url)
    if host:
        base = host.split(".")[0]
        tokens.add(base.lower())
        if len(base) > 4:
            tokens.add(base[: max(4, len(base) - 2)].lower())
    for raw in [title, *(brand_hints or []), *(org_names or [])]:
        for word in re.findall(r"[a-zа-яё]{4,}", (raw or "").lower()):
            tokens.add(word)
    return {t for t in tokens if len(t) >= 4}


def _is_mostly_caps(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 6:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) > 0.65


def _contains_brand(phrase: str, brand_tokens: set[str]) -> bool:
    low = phrase.lower()
    words = re.findall(r"[a-zа-яё0-9]+", low)
    if not words:
        return False
    hits = sum(1 for w in words if w in brand_tokens or any(w in b or b in w for b in brand_tokens))
    return hits >= max(1, len(words) // 2)


def _has_service_semantics(phrase: str) -> bool:
    low = phrase.lower()
    if any(h in low for h in _SERVICE_HINTS):
        return True
    words = [w for w in re.findall(r"[а-яёa-z]{4,}", low) if w not in _STOP_WORDS]
    return len(words) >= 2


def _looks_like_search_query(phrase: str) -> bool:
    low = phrase.lower().strip()
    words = low.split()
    if len(words) < 2:
        return False
    if words[0] in _BAD_QUERY_START:
        return False
    if any(low.startswith(s) for s in _QUERY_STARTERS):
        return True
    return len(words) >= 3 and _has_service_semantics(low)


def _is_section_junk(phrase: str) -> bool:
    low = phrase.lower().strip()
    if _SECTION_JUNK_RE.search(low):
        return True
    if _is_mostly_caps(phrase) and len(phrase) >= 10:
        return True
    words = low.split()
    if len(words) <= 4 and any(w in low for w in ("результат", "пилот", "этап", "проблем")):
        if not any(h in low for h in ("лидоген", "b2b", "прибыл", "спрос", "продаж", "маркетинг")):
            return True
    return False


def _is_query_worthy(raw: str, brand_tokens: set[str]) -> bool:
    p = _clean_phrase(raw)
    if not p:
        return False
    if _is_section_junk(p):
        return False
    if _is_mostly_caps(p) and len(p) > 18:
        return False
    if _contains_brand(p, brand_tokens) and not _has_service_semantics(p):
        return False
    if len(p.split()) == 1 and p.lower() in _STOP_WORDS:
        return False
    if not _has_service_semantics(p) and len(p) < 14:
        return False
    return True


def _domain_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        host = urlparse(raw if "://" in raw else f"https://{raw}").netloc.lower()
    except Exception:
        return raw.lower().split("/")[0]
    return host[4:] if host.startswith("www.") else host


def _clean_phrase(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "").strip())
    s = _LABEL_PREFIX_RE.sub("", s)
    s = s.strip("·|-—–:;,.")
    if len(s) < 4 or len(s) > 90:
        return ""
    if _SKIP_PHRASE.match(s):
        return ""
    if _SECTION_JUNK_RE.search(s):
        return ""
    if _PROMO_RE.search(s):
        return ""
    low = s.lower()
    if low in _STOP_WORDS:
        return ""
    if re.fullmatch(r"[\d\s+().-]+", s):
        return ""
    return s


def _title_topic(title: str) -> str:
    if not title:
        return ""
    part = title.split("|")[0].split("—")[0].split("-")[0].strip()
    return _clean_phrase(part) or ""


def _clean_entity_name(name: str) -> str:
    s = (name or "").strip().strip(",")
    s = _FOOTER_STOP.split(s, maxsplit=1)[0].strip()
    s = re.sub(r"(?i)(политика|инн|оферт).*$", "", s).strip()
    return s[:120]


def _normalize_dashes(s: str) -> str:
    return re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", s or "")


def _extract_company_name(
    title: str,
    text: str,
    *,
    footer_text: str = "",
    brand_hints: list[str] | None = None,
    org_names: list[str] | None = None,
) -> str:
    blob = _normalize_dashes(f"{title}\n{footer_text}\n{text[:6000]}")
    patterns = (
        r"(ООО\s+[«\"][^»\"]+[»\"])",
        r"(ООО\s+[A-Za-zА-Яа-яёЁ0-9\-«»\"'\s]{3,60})",
        r"©[^\n]{0,120}(ООО\s+[«\"][^»\"]+[»\"])",
        r"ИНН\s+\d+\s+(ИП\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})",
        r"(ИП\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})",
    )
    for pat in patterns:
        m = re.search(pat, blob, flags=re.IGNORECASE)
        if m:
            name = _clean_entity_name(m.group(1))
            if len(name) >= 4:
                return name

    for raw in (org_names or []) + (brand_hints or []):
        val = _clean_entity_name(_normalize_dashes(raw))
        if re.search(r"^(ООО|ИП|АО)\s", val, re.I):
            return val
        if re.search(r"опалубка|домстрой", val, re.I) and "ооо" in blob.lower():
            m = re.search(r"(ООО\s+[«\"][^»\"]+[»\"])", blob, re.I)
            if m:
                return _clean_entity_name(m.group(1))
        # Бренд из логотипа (alt): «Опалубка Домстрой» → как на сайте в подвале
        if 5 <= len(val) <= 60 and re.search(r"[а-яёa-z]", val, re.I):
            if not re.search(r"logo|icon|картин|image|banner", val, re.I):
                if re.search(r"опалубка|строй|дом|клуб|инноватор", val, re.I):
                    return val

    return ""


def _detect_geo(text: str) -> list[str]:
    low = (text or "").lower()
    found: list[str] = []
    for needle, label in _GEO_CITIES:
        if needle in low and label not in found:
            found.append(label)
    return found[:3]


def _heading_texts(headings: list) -> list[str]:
    out: list[str] = []
    for item in headings or []:
        if isinstance(item, dict):
            t = item.get("text") or ""
        else:
            t = str(item)
        if t:
            out.append(t)
    return out


def _headings_by_level(headings: list) -> tuple[list[str], list[str], list[str]]:
    h1, h2, h3 = [], [], []
    for item in headings or []:
        if isinstance(item, dict):
            level = item.get("level") or "h2"
            text = item.get("text") or ""
        else:
            level, text = "h2", str(item)
        if not text:
            continue
        if level == "h1":
            h1.append(text)
        elif level == "h3":
            h3.append(text)
        else:
            h2.append(text)
    return h1, h2, h3


def _collect_topics(
    *,
    title: str,
    meta_description: str,
    headings: list,
    nav_labels: list[str],
    text: str,
    brand_tokens: set[str],
) -> list[str]:
    """Кандидаты в запросы: h2/h3 с услугами, meta, текст — без бренда и слоганов."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str, *, max_len: int = 80) -> None:
        p = _clean_phrase(raw)
        if not p or len(p) > max_len:
            if p and "," in raw:
                for piece in raw.split(","):
                    add(piece.strip(), max_len=max_len)
            return
        if not _is_query_worthy(p, brand_tokens):
            return
        key = p.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(p)
        if "," in p and 10 < len(p) < 55:
            for piece in p.split(","):
                sub = _clean_phrase(piece.strip())
                if sub and sub.lower() not in seen and _is_query_worthy(sub, brand_tokens):
                    seen.add(sub.lower())
                    out.append(sub)

    h1, h2, h3 = _headings_by_level(headings)
    for h in h2 + h3:
        add(h)
    for h in h1:
        if _has_service_semantics(h) and not _contains_brand(h, brand_tokens):
            add(h)
    if meta_description:
        for chunk in re.split(r"[.!?]\s+", meta_description):
            add(chunk, max_len=70)
    title_part = _title_topic(title)
    if title_part and _is_query_worthy(title_part, brand_tokens):
        add(title_part, max_len=70)

    low = text.lower()
    for m in re.finditer(
        r"[а-яёa-z][а-яёa-z\-]{2,}(?:\s+[а-яёa-z][а-яёa-z\-]{2,}){1,3}",
        low,
    ):
        phrase = m.group(0).strip()
        if any(w in phrase for w in _STOP_WORDS):
            continue
        if 14 <= len(phrase) <= 55:
            if _looks_like_search_query(phrase):
                add(phrase)

    return out[:20]


def _site_sells_goods(body_low: str) -> bool:
    """Сайт про товар/аренду — можно шаблоны «купить», «продажа»."""
    return any(
        p in body_low
        for p in (
            "аренда ", "купить ", "продажа ", "цена ", "каталог",
            "в наличии", "доставк", "б/у ", "комплектующ",
        )
    )


def _topic_core(topic: str) -> str:
    """Короткая тема для шаблонов «аренда …», «купить …»."""
    t = topic.lower().strip()
    for prefix in (
        "аренда ", "продажа ", "купить ", "строительство ", "монтаж ",
        "изготовление ", "усиление ", "ремонт ", "проектирование ",
    ):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t.strip() or topic.lower()


def _expand_queries(topics: list[str], body_low: str, brand_tokens: set[str]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if len(q) < 4 or len(q) > 80:
            return
        if not _is_query_worthy(q, brand_tokens):
            return
        key = q.lower()
        if key in seen:
            return
        seen.add(key)
        queries.append(q)

    site_commerce = _site_sells_goods(body_low)
    for topic in topics[:12]:
        add(topic)
        if not site_commerce:
            continue
        if len(topic) > 38 or "," in topic:
            continue
        core = _topic_core(topic)
        if not core or len(core) > 40:
            continue
        if _is_section_junk(core):
            continue
        if not _has_service_semantics(core):
            continue
        if _contains_brand(core, brand_tokens):
            continue
        topic_low = topic.lower()
        for marker, templates in _INTENT_MARKERS:
            if marker in ("продаж", "купить") and not site_commerce:
                continue
            if marker not in body_low:
                continue
            if marker in topic_low:
                continue
            for tpl in templates:
                add(tpl.format(core))
            break

    if site_commerce and "под ключ" in body_low:
        for topic in topics[:5]:
            if len(topic) > 45:
                continue
            core = _topic_core(topic)
            if core and len(core) <= 40 and _has_service_semantics(core):
                if not _contains_brand(core, brand_tokens):
                    add(f"{core} под ключ")

    return queries


def _build_niche(
    *,
    title: str,
    meta_description: str,
    headings: list,
    topics: list[str],
) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    h1, h2, h3 = _headings_by_level(headings)

    def add_part(raw: str) -> None:
        p = _clean_phrase(raw) or raw.strip()
        if not p or len(p) < 8:
            return
        key = p.lower()[:60]
        if key in seen:
            return
        seen.add(key)
        parts.append(p)

    for h in (h2[:2] + h1[:1] + h3[:1]):
        add_part(h)
    add_part(_title_topic(title))
    if meta_description:
        add_part(meta_description.split(".")[0])
    if not parts and topics:
        add_part(topics[0])

    niche = ". ".join(parts)
    if len(niche) > 300:
        niche = niche[:297].rsplit(" ", 1)[0] + "…"
    return niche


def _build_queries(
    *,
    title: str,
    meta_description: str,
    headings: list[str],
    nav_labels: list[str],
    text: str,
    brand_tokens: set[str],
) -> list[str]:
    topics = _collect_topics(
        title=title,
        meta_description=meta_description,
        headings=headings,
        nav_labels=nav_labels,
        text=text,
        brand_tokens=brand_tokens,
    )
    body_low = f"{title} {meta_description} {text}".lower()
    queries = _expand_queries(topics, body_low, brand_tokens)

    geos = _detect_geo(text)
    if geos and queries:
        base = queries[0]
        for city in geos[:2]:
            gq = f"{base} {city}"
            if gq.lower() not in {q.lower() for q in queries}:
                queries.append(gq)

    if not queries and topics:
        queries = topics[:8]

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out[:14]


def _build_exclude(client_domain: str) -> str:
    domains: list[str] = []
    if client_domain:
        domains.append(client_domain)
    for d in _STANDARD_EXCLUDE:
        if d not in domains:
            domains.append(d)
    return ", ".join(domains)


def suggest_brief_from_analysis(
    *,
    site_url: str,
    title: str,
    text_sample: str,
    meta_description: str = "",
    headings: list | None = None,
    nav_labels: list[str] | None = None,
    footer_text: str = "",
    brand_hints: list[str] | None = None,
    org_names: list[str] | None = None,
    list_items: list[str] | None = None,
    schema_offerings: list[str] | None = None,
) -> dict:
    """Обёртка для тестов и обратной совместимости — делегирует в site_survey."""
    from server.site_survey import SiteSurveyData, suggest_from_survey

    data = SiteSurveyData(
        site_url=site_url,
        title=title,
        meta_description=meta_description,
        headings=headings or [],
        nav_labels=nav_labels or [],
        footer_text=footer_text,
        brand_hints=brand_hints or [],
        org_names=org_names or [],
        body_text=text_sample or "",
        list_items=list_items or [],
        schema_offerings=schema_offerings or [],
        pages_surveyed=1,
    )
    return suggest_from_survey(data)
