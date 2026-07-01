from __future__ import annotations

import re
from html import unescape
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from server.fetcher import fetch_page
from server.phones import extract_phones, pick_phones_enriched

LogFn = Callable[[str, str | None, str], Awaitable[None] | None]

PRIORITY_PATHS = (
    "",
    "/contacts",
    "/contact",
    "/kontakty",
    "/kontakt",
    "/o-kompanii",
    "/o-nas",
    "/about",
    "/about-us",
)

_PATH_HINT_RE = re.compile(
    r"(contact|kontakt|контакт|about|o-nas|o_nas|kompan|company|svyaz)",
    re.IGNORECASE,
)


def normalize_url(url: str, base: str | None = None) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if base:
        raw = urljoin(base, raw)
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    return parsed.geturl()


def _title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return unescape(soup.title.string.strip())
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


def _same_host(url: str, root_host: str) -> bool:
    host = urlparse(url).netloc.lower().lstrip("www.")
    root = root_host.lower().lstrip("www.")
    return host == root or host.endswith("." + root)


def _discover_links(html: str, base_url: str, root_host: str, depth: int) -> list[str]:
    if depth <= 0:
        return []
    soup = BeautifulSoup(html, "html.parser")
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = normalize_url(a.get("href") or "", base_url)
        if not href or not _same_host(href, root_host):
            continue
        if href in seen:
            continue
        seen.add(href)
        score = 0
        if _PATH_HINT_RE.search(urlparse(href).path):
            score += 15
        scored.append((score, href))
    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:8]]


async def parse_site(
    site: str,
    *,
    depth: int = 2,
    use_proxy: bool = False,
    delay_ms: int = 500,
    on_log: LogFn | None = None,
) -> dict:
    """Парсинг сайта: главная, контакты, footer, глубина 1–5."""
    root = normalize_url(f"https://{site}" if "://" not in site else site)
    if not root:
        return {"ok": False, "site": site, "error": "bad url", "phones": [], "phones_meta": []}

    root_host = urlparse(root).netloc.lower()
    parsed_root = urlparse(root)
    base_origin = f"{parsed_root.scheme}://{parsed_root.netloc}"

    urls_to_fetch: list[str] = []
    for path in PRIORITY_PATHS:
        u = normalize_url(path or "/", base_origin) if path else base_origin + "/"
        if u not in urls_to_fetch:
            urls_to_fetch.append(u)

    all_phones_meta: list[dict] = []
    all_text: list[str] = []
    title = ""
    pages_ok = 0
    last_error = ""

    async def log(msg: str, status: str = "info") -> None:
        if on_log:
            result = on_log(msg, site, status)
            if hasattr(result, "__await__"):
                await result

    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(u, 0) for u in urls_to_fetch]

    while queue and len(visited) < max(4, depth * 3):
        url, d = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        await log(f"Парсинг {urlparse(url).netloc}{urlparse(url).path or '/'}…", "pending")
        html, final_url, code, method = await fetch_page(
            url, use_proxy=use_proxy, delay_ms=delay_ms
        )
        if not html:
            last_error = method
            await log(f"Ошибка {site}: {method}", "error")
            continue

        pages_ok += 1
        if not title:
            title = _title_from_html(html)
        phones = extract_phones(html, final_url)
        all_phones_meta.extend(phones)
        soup = BeautifulSoup(html, "html.parser")
        all_text.append(soup.get_text("\n", strip=True)[:8000])

        await log(f"{site} · найдено {len(phones)} номеров ({method})", "success")

        if d < depth - 1:
            for link in _discover_links(html, final_url, root_host, depth):
                if link not in visited:
                    queue.append((link, d + 1))

    seen_p: set[str] = set()
    unique_meta: list[dict] = []
    for item in all_phones_meta:
        p = item["phone"]
        if p in seen_p:
            continue
        seen_p.add(p)
        unique_meta.append(item)

    if not pages_ok:
        return {
            "ok": False,
            "site": domain_from_crawl(root, site),
            "error": last_error or "unreachable",
            "phones": [],
            "phones_meta": [],
            "title": "",
            "text": "",
        }

    return {
        "ok": True,
        "site": domain_from_crawl(root, site),
        "phones": [p["phone"] for p in unique_meta],
        "phones_meta": unique_meta,
        "title": title,
        "text": "\n".join(all_text)[:6000],
        "pages": pages_ok,
    }


def domain_from_crawl(final_url: str, fallback: str) -> str:
    host = urlparse(final_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or fallback


async def analyze_client_site(site_url: str, **kwargs) -> dict:
    root = normalize_url(site_url)
    if not root:
        return {"ok": False, "error": "Некорректный URL"}
    host = urlparse(root).netloc.lower().lstrip("www.")
    depth = int(kwargs.pop("depth", 1))
    delay_ms = int(kwargs.pop("delay_ms", 0))
    data = await parse_site(host, depth=depth, delay_ms=delay_ms, **kwargs)
    if not data.get("ok"):
        return {"ok": False, "error": data.get("error") or "Сайт недоступен"}
    return {
        "ok": True,
        "site_url": root,
        "title": data.get("title") or "",
        "text_sample": (data.get("text") or "")[:4000],
        "phones_on_client": data.get("phones") or [],
        "h1": data.get("title") or "",
    }


async def crawl_competitor_site(site: str, **kwargs) -> dict:
    return await parse_site(site, **kwargs)


async def crawl_many(
    sites: list[str],
    *,
    concurrency: int = 8,
    on_site_done: Callable[[dict], Awaitable[None] | None] | None = None,
    **kwargs,
) -> list[dict]:
    import asyncio

    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []

    async def one(site: str) -> dict:
        async with sem:
            data = await parse_site(site, on_log=kwargs.get("on_log"), **{
                k: v for k, v in kwargs.items() if k != "on_log"
            })
            if on_site_done:
                r = on_site_done(data)
                if hasattr(r, "__await__"):
                    await r
            return data

    return list(await asyncio.gather(*[one(s) for s in sites]))
