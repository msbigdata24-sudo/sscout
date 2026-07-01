from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import httpx

from server.config import SERP_PAGES, XMLRIVER_KEY, XMLRIVER_USER, YANDEX_XML_KEY, YANDEX_XML_USER
from server.phones import domain_from_url

YANDEX_XML_URL = "https://yandex.ru/search/xml"
XMLRIVER_YANDEX_URL = "http://xmlriver.com/yandex/xml"
XMLRIVER_GOOGLE_URL = "http://xmlriver.com/search/xml"

_REGION_LR = {
    "москва": 213,
    "московск": 1,
    "ярослав": 16,
    "владимир": 192,
    "нижегород": 47,
    "санкт-петербург": 2,
    "спб": 2,
}


class SerpError(Exception):
    pass


def parse_xmlriver_credentials(*, api_user: str = "", api_key: str = "") -> tuple[str, str]:
    user = (api_user or XMLRIVER_USER or "").strip()
    key = (api_key or XMLRIVER_KEY or "").strip()
    if ":" in key and not user:
        user, key = key.split(":", 1)
        user, key = user.strip(), key.strip()
    return user, key


def region_to_lr(regions_text: str) -> int | None:
    low = (regions_text or "").lower()
    for token, lr in _REGION_LR.items():
        if token in low:
            return lr
    return None


def query_to_lr(query: str, regions_text: str = "") -> int | None:
    """Регион выдачи: сначала из текста запроса, иначе из брифа."""
    low = (query or "").lower()
    for token, lr in _REGION_LR.items():
        if token in low:
            return lr
    return region_to_lr(regions_text)


def _parse_xmlriver_response(xml_text: str, *, engine: str, query: str, page: int) -> list[dict]:
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SerpError(f"XMLRiver: неверный XML ({exc})") from exc

    err = root.find(".//error")
    if err is not None and (err.text or "").strip():
        raise SerpError(f"XMLRiver: {err.text.strip()}")

    hits: list[dict] = []
    for group in root.findall(".//group"):
        doc = group.find("doc")
        if doc is None:
            continue
        url = (doc.findtext("url") or "").strip()
        if not url:
            continue
        domain = domain_from_url(url)
        if not domain:
            continue
        hits.append({
            "url": url,
            "domain": domain,
            "title": (doc.findtext("title") or "").strip(),
            "snippet": (doc.findtext("snippet") or doc.findtext("passage") or "").strip(),
            "engine": engine,
            "page": page,
            "query": query,
            "source": "xmlriver",
        })
    return hits


async def _xmlriver_request(
    client: httpx.AsyncClient,
    base_url: str,
    user: str,
    key: str,
    query: str,
    api_page: int,
    *,
    engine: str,
    extra: dict | None = None,
) -> list[dict]:
    params: dict = {
        "user": user,
        "key": key,
        "query": query,
        "page": api_page,
        "groupby": 10,
    }
    if extra:
        params.update(extra)
    resp = await client.get(base_url, params=params)
    if resp.status_code >= 400:
        raise SerpError(f"XMLRiver HTTP {resp.status_code}")
    return _parse_xmlriver_response(resp.text, engine=engine, query=query, page=api_page)


async def _yandex_xml_fallback(
    client: httpx.AsyncClient,
    query: str,
    page: int,
    lr: int | None,
) -> list[dict]:
    user = YANDEX_XML_USER.strip()
    key = YANDEX_XML_KEY.strip()
    if not user or not key:
        return []
    params: dict = {"user": user, "key": key, "query": query, "page": page, "groupby": 10}
    if lr:
        params["lr"] = lr
    try:
        resp = await client.get(YANDEX_XML_URL, params=params)
        if resp.status_code >= 400:
            return []
        return _parse_xmlriver_response(resp.text, engine="yandex", query=query, page=page + 1)
    except Exception:
        return []


async def search_via_xmlriver(
    query: str,
    *,
    user: str,
    key: str,
    pages: int = SERP_PAGES,
    lr: int | None = None,
) -> list[dict]:
    if not user or not key:
        raise SerpError("Укажите ID и API-ключ XMLRiver в брифе")

    hits: list[dict] = []
    async with httpx.AsyncClient(timeout=45.0) as client:
        for page in range(pages):
            y_extra: dict = {"lang": "ru", "domain": "ru", "device": "desktop"}
            if lr:
                y_extra["lr"] = lr
            try:
                hits.extend(await _xmlriver_request(
                    client, XMLRIVER_YANDEX_URL, user, key, query, page,
                    engine="yandex", extra=y_extra,
                ))
            except SerpError:
                if page == 0:
                    raise
            # Google через XMLRiver без точной геонастройки даёт мусор (YouTube, Wiki…).
            # Для РФ используем только Яндекс; Google — позже, когда настроен loc в кабинете.
            await asyncio.sleep(0.3)
    return hits


async def search_competitors(
    query: str,
    *,
    regions_text: str = "",
    max_results: int = 200,
    xmlriver_user: str = "",
    xmlriver_key: str = "",
    pages: int = SERP_PAGES,
) -> list[dict]:
    lr = query_to_lr(query, regions_text)
    try:
        results = await search_via_xmlriver(
            query, user=xmlriver_user, key=xmlriver_key, pages=pages, lr=lr,
        )
    except SerpError:
        results = []

    if not results:
        async with httpx.AsyncClient(timeout=45.0) as client:
            for page in range(pages):
                fb = await _yandex_xml_fallback(client, query, page, lr)
                results.extend(fb)
                if fb:
                    break

    if not results:
        raise SerpError(
            "Поиск недоступен: проверьте XMLRiver (ID + ключ) "
            "или задайте YANDEX_XML_USER / YANDEX_XML_KEY в .env"
        )

    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for hit in results:
        pair = (hit["domain"], hit["query"])
        if pair in seen:
            continue
        seen.add(pair)
        unique.append(hit)
        if len(unique) >= max_results:
            break
    return unique


async def collect_serp(
    queries: list[str],
    *,
    pages: int = SERP_PAGES,
    xmlriver_user: str = "",
    xmlriver_key: str = "",
    regions_text: str = "",
    max_results: int = 200,
) -> tuple[list[dict], dict]:
    stats = {
        "provider": "xmlriver",
        "yandex_pages": pages,
        "google_pages": 0,
        "raw_hits": 0,
        "unique_domains": 0,
        "queries": len(queries),
    }
    all_hits: list[dict] = []
    for query in queries:
        batch = await search_competitors(
            query,
            regions_text=regions_text,
            max_results=max_results,
            xmlriver_user=xmlriver_user,
            xmlriver_key=xmlriver_key,
            pages=pages,
        )
        stats["raw_hits"] += len(batch)
        all_hits.extend(batch)
    stats["unique_domains"] = len({h["domain"] for h in all_hits})
    return all_hits, stats


def group_hits_by_domain(hits: list[dict]) -> dict[str, dict]:
    grouped: dict[str, dict] = {}
    for hit in hits:
        d = hit["domain"]
        if d not in grouped:
            grouped[d] = {
                "domain": d,
                "title": hit.get("title") or "",
                "snippet": hit.get("snippet") or "",
                "urls": [],
                "engines": set(),
                "queries": set(),
            }
        g = grouped[d]
        g["urls"].append(hit["url"])
        g["engines"].add(hit["engine"])
        g["queries"].add(hit["query"])
        if hit.get("title") and len(hit["title"]) > len(g["title"]):
            g["title"] = hit["title"]
        if hit.get("snippet") and len(hit["snippet"]) > len(g["snippet"]):
            g["snippet"] = hit["snippet"]
    for g in grouped.values():
        g["engines"] = sorted(g["engines"])
        g["queries"] = sorted(g["queries"])
    return grouped
