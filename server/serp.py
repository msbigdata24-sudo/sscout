from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx

from server.config import SERPAPI_KEY, SERP_PAGES
from server.phones import domain_from_url

SERPAPI_URL = "https://serpapi.com/search.json"


class SerpError(Exception):
    pass


def _extract_url(item: dict) -> str:
    for key in ("link", "url", "visible_link"):
        val = item.get(key)
        if val and str(val).startswith("http"):
            return str(val)
    return ""


async def _serp_page(
    client: httpx.AsyncClient,
    *,
    engine: str,
    query: str,
    page: int,
    api_key: str,
) -> list[dict]:
    params: dict[str, str | int] = {
        "api_key": api_key,
        "engine": engine,
        "text" if engine == "yandex" else "q": query,
    }
    if engine == "google":
        params["google_domain"] = "google.ru"
        params["gl"] = "ru"
        params["hl"] = "ru"
        params["num"] = 10
        params["start"] = page * 10
    elif engine == "yandex":
        params["yandex_domain"] = "yandex.ru"
        params["lang"] = "ru"
        params["p"] = page
    else:
        return []

    resp = await client.get(SERPAPI_URL, params=params)
    if resp.status_code != 200:
        raise SerpError(f"SerpAPI HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("error"):
        raise SerpError(str(data["error"]))

    results: list[dict] = []
    if engine == "google":
        organic = data.get("organic_results") or []
    else:
        organic = data.get("organic_results") or data.get("results") or []

    for item in organic:
        url = _extract_url(item)
        if not url:
            continue
        domain = domain_from_url(url)
        if not domain:
            continue
        results.append({
            "url": url,
            "domain": domain,
            "title": str(item.get("title") or ""),
            "snippet": str(item.get("snippet") or item.get("description") or ""),
            "engine": engine,
            "page": page + 1,
            "query": query,
        })
    return results


async def collect_serp(
    queries: list[str],
    *,
    pages: int = SERP_PAGES,
    api_key: str | None = None,
) -> tuple[list[dict], dict]:
    key = (api_key or SERPAPI_KEY).strip()
    if not key:
        raise SerpError("Нет ключа SERPAPI_KEY — добавьте в .env (см. .env.example)")

    stats = {"google_pages": 0, "yandex_pages": 0, "raw_hits": 0, "unique_domains": 0}
    all_hits: list[dict] = []
    seen_pair: set[tuple[str, str]] = set()

    async with httpx.AsyncClient(timeout=45.0) as client:
        tasks = []
        for query in queries:
            for page in range(pages):
                tasks.append(("google", query, page))
                tasks.append(("yandex", query, page))

        sem = asyncio.Semaphore(4)

        async def run_one(engine: str, query: str, page: int) -> list[dict]:
            async with sem:
                try:
                    return await _serp_page(client, engine=engine, query=query, page=page, api_key=key)
                except Exception:
                    return []

        batches = await asyncio.gather(*[run_one(e, q, p) for e, q, p in tasks])

    for hits in batches:
        for hit in hits:
            stats["raw_hits"] += 1
            if hit["engine"] == "google":
                stats["google_pages"] = max(stats["google_pages"], hit["page"])
            else:
                stats["yandex_pages"] = max(stats["yandex_pages"], hit["page"])
            pair = (hit["domain"], hit["query"])
            if pair in seen_pair:
                continue
            seen_pair.add(pair)
            all_hits.append(hit)

    domains = {h["domain"] for h in all_hits}
    stats["unique_domains"] = len(domains)
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
