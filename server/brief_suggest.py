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

# UI-мусор в заголовках и меню
_SKIP_PHRASE = re.compile(
    r"^(главная|каталог|контакты|о компании|о нас|услуги|портфолио|производство|"
    r"новости|статьи|акции|проекты|скачать|отзывы|доставка|оплата|ещё|меню|"
    r"подробнее|узнать стоимость|оставить заявку|заказать|получить консультацию|"
    r"отправить|согласен|принимаю|загрузка|все права|copyright|"
    r"\d{1,3}\s*м²|\d+$|0\d$)$",
    re.IGNORECASE,
)

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
    s = s.strip("·|-—–:;,.")
    if len(s) < 4 or len(s) > 90:
        return ""
    if _SKIP_PHRASE.match(s):
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


def _extract_company_name(title: str, text: str) -> str:
    blob = f"{title}\n{text[:5000]}"
    patterns = (
        r"(ООО\s+[«\"][^»\"]+[»\"])",
        r"ИНН\s+\d+\s+(ИП\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})",
        r"(ИП\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})",
        r"©[^\n]{0,80}(ООО\s+[«\"][^»\"]+[»\"])",
    )
    for pat in patterns:
        m = re.search(pat, blob, flags=re.IGNORECASE)
        if m:
            name = _clean_entity_name(m.group(1))
            if len(name) >= 4:
                return name
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
) -> list[str]:
    """Кандидаты в запросы: сначала короткие h2/h3 (услуги), потом title и текст."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str, *, max_len: int = 80) -> None:
        p = _clean_phrase(raw)
        if not p or len(p) > max_len:
            if p and "," in raw:
                for piece in raw.split(","):
                    add(piece.strip(), max_len=max_len)
            return
        key = p.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(p)
        if "," in p and 10 < len(p) < 55:
            for piece in p.split(","):
                sub = _clean_phrase(piece.strip())
                if sub and sub.lower() not in seen:
                    seen.add(sub.lower())
                    out.append(sub)

    h1, h2, h3 = _headings_by_level(headings)
    for h in h2 + h3 + h1:
        add(h)
    add(_title_topic(title), max_len=70)
    if meta_description:
        for chunk in re.split(r"[.!?]\s+", meta_description):
            add(chunk, max_len=70)

    for label in nav_labels:
        add(label, max_len=50)

    low = text.lower()
    for m in re.finditer(
        r"[а-яёa-z][а-яёa-z\-]{2,}(?:\s+[а-яёa-z][а-яёa-z\-]{2,}){1,2}",
        low,
    ):
        phrase = m.group(0).strip()
        if any(w in phrase for w in _STOP_WORDS):
            continue
        if 12 <= len(phrase) <= 55:
            add(phrase)

    return out[:20]


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


def _expand_queries(topics: list[str], body_low: str) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if len(q) < 4 or len(q) > 80:
            return
        key = q.lower()
        if key in seen:
            return
        seen.add(key)
        queries.append(q)

    for topic in topics[:12]:
        add(topic)
        if len(topic) > 45:
            continue
        core = _topic_core(topic)
        if not core or len(core) > 40:
            continue
        for marker, templates in _INTENT_MARKERS:
            if marker in body_low:
                for tpl in templates:
                    add(tpl.format(core))
                break

    if "под ключ" in body_low:
        for topic in topics[:5]:
            if len(topic) > 45:
                continue
            core = _topic_core(topic)
            if core and len(core) <= 40:
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
) -> list[str]:
    topics = _collect_topics(
        title=title,
        meta_description=meta_description,
        headings=headings,
        nav_labels=nav_labels,
        text=text,
    )
    body_low = f"{title} {meta_description} {text}".lower()
    queries = _expand_queries(topics, body_low)

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
) -> dict:
    text = text_sample or ""
    headings = headings or []
    nav_labels = nav_labels or []
    client_domain = _domain_from_url(site_url)

    topics = _collect_topics(
        title=title,
        meta_description=meta_description,
        headings=headings,
        nav_labels=nav_labels,
        text=text,
    )
    niche = _build_niche(
        title=title,
        meta_description=meta_description,
        headings=headings,
        topics=topics,
    )
    if not niche and text:
        niche = re.sub(r"\s+", " ", text[:220]).strip()

    queries = _build_queries(
        title=title,
        meta_description=meta_description,
        headings=headings,
        nav_labels=nav_labels,
        text=text,
    )

    return {
        "clientSite": site_url,
        "clientName": _extract_company_name(title, text),
        "niche": niche[:300],
        "queries": "\n".join(queries),
        "excludeDomains": _build_exclude(client_domain),
        "title": title or "",
        "source": "homepage",
    }
