from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

# Городские коды для общих телефонов (Москва, СПб и др.)
_CITY_CODES = ("495", "499", "812", "343", "831", "846", "351", "383")

_SKIP_LINK_RE = re.compile(r"(wa\.me|whatsapp|t\.me|telegram|viber|max\.ru)", re.I)

_CONTEXT_RE = re.compile(
    r"(?:телефон|тел\.|звоните|контакт|call|phone)[:\s\-—]{0,12}"
    r"([+\d()\s\-—]{10,24})",
    re.IGNORECASE,
)

_RAW_PHONE_RE = re.compile(
    r"(?:\+7|8)[\s\-()]*(?:\d[\s\-()]*){10}|"
    r"(?<!\d)9\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)"
)


def normalize_digits(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits if len(digits) == 11 and digits.startswith("7") else ""


def phone_type(digits: str) -> str:
    if not digits or len(digits) != 11:
        return "invalid"
    if digits.startswith("79"):
        return "mobile"
    if digits.startswith("7800") or digits.startswith("7900"):
        return "excluded"
    for code in _CITY_CODES:
        if digits.startswith("7" + code):
            return "city"
    if digits[1] in "345678":
        return "city"
    return "invalid"


def is_allowed_phone(digits: str) -> bool:
    t = phone_type(digits)
    return t in ("mobile", "city")


def validate_phone(digits: str) -> dict:
    if not digits:
        return {"valid": False, "type": "invalid", "reason": "пусто"}
    if digits.startswith("7800"):
        return {"valid": False, "type": "excluded", "reason": "8-800"}
    if digits.startswith("7900"):
        return {"valid": False, "type": "excluded", "reason": "код 900"}
    t = phone_type(digits)
    if t == "mobile" and re.fullmatch(r"79\d{9}", digits):
        return {"valid": True, "type": "mobile", "reason": ""}
    if t == "city":
        for code in ("495", "499", "812"):
            if digits.startswith("7" + code) and len(digits) == 11:
                return {"valid": True, "type": "city", "reason": ""}
        if digits[1] in "345678":
            return {"valid": True, "type": "city", "reason": ""}
    return {"valid": False, "type": "invalid", "reason": "формат"}


def normalize_phone(raw: str) -> str:
    digits = normalize_digits(raw)
    return digits if is_allowed_phone(digits) else ""


def _add_phone(store: list[dict], raw: str, source: str) -> None:
    digits = normalize_phone(raw)
    if not digits:
        return
    meta = validate_phone(digits)
    if not meta["valid"]:
        return
    if any(x["phone"] == digits for x in store):
        return
    store.append({"phone": digits, "type": meta["type"], "source": source})


def extract_phones(html: str, url: str = "") -> list[dict]:
    """Извлечь мобильные и городские номера из HTML."""
    if not html:
        return []

    found: list[dict] = []
    soup = BeautifulSoup(html, "html.parser")

    # Шапка и навигация — телефоны часто только там (как на sk-teremok.ru).
    for tag_name in ("header", "nav"):
        for block in soup.find_all(tag_name):
            _add_from_text(found, block.get_text(" ", strip=True), tag_name)
            for a in block.find_all("a", href=True):
                href = a.get("href") or ""
                low = href.lower()
                if _SKIP_LINK_RE.search(href):
                    continue
                if low.startswith("tel:"):
                    _add_phone(found, href[4:], "tel")
                elif low.startswith("callto:"):
                    _add_phone(found, href[7:], "callto")

    for el in soup.select('[itemprop="telephone"], [class*="phone"], [class*="tel"]'):
        _add_from_text(found, el.get_text(" ", strip=True), "header-class")
        for attr in ("data-phone", "data-tel", "data-telephone", "data-href"):
            val = el.get(attr) or ""
            if val:
                _add_from_text(found, str(val), "data-attr")

    # footer
    for footer in soup.find_all("footer"):
        _add_from_text(found, footer.get_text(" ", strip=True), "footer")

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        _add_from_ld(found, data)

    # meta telephone
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        if "telephone" in name or "phone" in name:
            _add_phone(found, meta.get("content") or "", "meta")

    # tel: и callto: (не messengers)
    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        if _SKIP_LINK_RE.search(href):
            continue
        low = href.lower()
        if low.startswith("tel:"):
            _add_phone(found, href[4:], "tel")
        elif low.startswith("callto:"):
            _add_phone(found, href[7:], "callto")

    # текст + контекстные фразы
    body_text = soup.get_text("\n", strip=True)
    _add_from_text(found, body_text, "text")
    for m in _CONTEXT_RE.finditer(body_text):
        _add_phone(found, m.group(1), "context")

    for m in _RAW_PHONE_RE.finditer(html):
        _add_phone(found, m.group(0), "html")

    return found


def _add_from_text(store: list[dict], text: str, source: str) -> None:
    for m in _RAW_PHONE_RE.finditer(text or ""):
        _add_phone(store, m.group(0), source)


def _add_from_ld(store: list[dict], data: object) -> None:
    if isinstance(data, list):
        for item in data:
            _add_from_ld(store, item)
        return
    if not isinstance(data, dict):
        return
    for key in ("telephone", "phone", "contactPoint"):
        val = data.get(key)
        if isinstance(val, str):
            _add_phone(store, val, "json-ld")
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, str):
                    _add_phone(store, v, "json-ld")
                elif isinstance(v, dict) and v.get("telephone"):
                    _add_phone(store, str(v["telephone"]), "json-ld")
        elif isinstance(val, dict) and val.get("telephone"):
            _add_phone(store, str(val["telephone"]), "json-ld")
    for v in data.values():
        if isinstance(v, (dict, list)):
            _add_from_ld(store, v)


def extract_phones_from_text(text: str) -> list[str]:
    return [p["phone"] for p in extract_phones(text, "")]


def is_mobile(num: str) -> bool:
    return phone_type(num) == "mobile"


def is_city(num: str) -> bool:
    return phone_type(num) == "city"


def is_toll_free(num: str) -> bool:
    return num.startswith("7800")


def filter_phones_list(phones: list[str], phone_filter: str) -> list[str]:
    out: list[str] = []
    for p in phones:
        t = phone_type(p)
        if phone_filter == "mobile" and t == "mobile":
            out.append(p)
        elif phone_filter == "city" and t == "city":
            out.append(p)
        elif phone_filter == "business" and t in ("mobile", "city"):
            out.append(p)
        elif phone_filter == "no-800" and not is_toll_free(p):
            out.append(p)
        elif phone_filter == "all" and is_allowed_phone(p):
            out.append(p)
    return out


def pick_phones_enriched(
    enriched: list[dict],
    phone_filter: str = "business",
) -> tuple[str, str, str, str]:
    phones = pick_phones_list(enriched, phone_filter)
    types = {e["phone"]: e["type"] for e in enriched}
    p1 = phones[0] if phones else ""
    p2 = phones[1] if len(phones) > 1 else ""
    return p1, p2, types.get(p1, ""), types.get(p2, "")


def pick_phones_list(
    enriched: list[dict],
    phone_filter: str = "business",
) -> list[str]:
    phones = [e["phone"] for e in enriched]
    filtered = filter_phones_list(phones, phone_filter)
    if not filtered and phone_filter == "mobile":
        filtered = filter_phones_list(phones, "business")
    return filtered


def format_phone_display(digits: str) -> str:
    d = normalize_digits(digits)
    if not d:
        return digits or ""
    if d.startswith("79"):
        return f"+7 ({d[1:4]}) {d[4:7]}-{d[7:9]}-{d[9:11]}"
    return f"+7 ({d[1:4]}) {d[4:7]}-{d[7:9]}-{d[9:11]}"


def pick_phones(phones: list[str], phone_filter: str) -> tuple[str, str]:
    p1, p2, _, _ = pick_phones_enriched(
        [{"phone": p, "type": phone_type(p)} for p in phones if is_allowed_phone(p)],
        phone_filter,
    )
    return p1, p2


def domain_from_url(url: str) -> str:
    try:
        host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""
