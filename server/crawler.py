from __future__ import annotations

import re
from html import unescape
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from server.fetcher import fetch_page, humanize_fetch_error
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

_BRIEF_EXTRA_PATHS = (
    "/uslugi",
    "/services",
    "/service",
    "/catalog",
    "/products",
    "/produktsiya",
    "/resheniya",
    "/solutions",
)

_BRIEF_NAV_RE = re.compile(
    r"(услуг|service|catalog|каталог|продукт|product|решени|solution|о\s*компан|about|"
    r"направлен|деятельност|поставк|аренд|производств|оборудован|техник)",
    re.IGNORECASE,
)

_CONTACT_PATHS = ("/contacts", "/contact", "/kontakty", "/kontakt")

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
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    return f"{scheme}://{host}{path}" + (f"?{parsed.query}" if parsed.query else "")


async def check_robots_allowed(url: str, *, user_agent: str = "SignalScoutBot/1.0") -> tuple[bool, str]:
    root = normalize_url(url)
    if not root:
        return True, ""
    parsed = urlparse(root)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        html, _, code, _ = await fetch_page(robots_url, use_proxy=False, delay_ms=0)
        if code >= 400 or not html:
            return True, ""
        rp = RobotFileParser()
        rp.parse(html.splitlines())
        path = parsed.path or "/"
        if not rp.can_fetch(user_agent, path):
            return False, "robots.txt запрещает обход"
        return True, ""
    except Exception:
        return True, ""


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
    paths: tuple[str, ...] | None = None,
) -> dict:
    """Парсинг сайта: главная, контакты, footer, глубина 1–5."""
    root = normalize_url(f"https://{site}" if "://" not in site else site)
    if not root:
        return {"ok": False, "site": site, "error": "bad url", "phones": [], "phones_meta": []}

    root_host = urlparse(root).netloc.lower()
    parsed_root = urlparse(root)
    base_origin = f"{parsed_root.scheme}://{parsed_root.netloc}"

    def _is_home_page(url: str) -> bool:
        return (urlparse(url).path or "/").rstrip("/") in ("", "/")

    path_list = paths if paths is not None else PRIORITY_PATHS
    urls_to_fetch: list[str] = []
    for path in path_list:
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

    allowed, robots_reason = await check_robots_allowed(root)
    if not allowed:
        await log(f"{site} — {robots_reason}", "skip")
        return {
            "ok": False,
            "site": site,
            "error": robots_reason,
            "phones": [],
            "phones_meta": [],
        }

    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(u, 0) for u in urls_to_fetch]
    max_pages = max(4, depth * 3)

    def _unique_phones() -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for item in all_phones_meta:
            p = item["phone"]
            if p in seen:
                continue
            seen.add(p)
            out.append(item)
        return out

    while queue and len(visited) < max_pages:
        url, d = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        await log(f"Парсинг {urlparse(url).netloc}{urlparse(url).path or '/'}…", "pending")
        html, final_url, code, method = await fetch_page(
            url, use_proxy=use_proxy, delay_ms=delay_ms
        )
        if not html:
            last_error = humanize_fetch_error(method)
            page_path = urlparse(url).path or "/"
            if _is_home_page(url):
                await log(f"Ошибка {site}: {method}", "error")
            else:
                await log(f"{site} · {page_path} — нет страницы (пропуск)", "skip")
            continue

        pages_ok += 1
        if not title:
            title = _title_from_html(html)
        phones = extract_phones(html, final_url)
        all_phones_meta.extend(phones)
        soup = BeautifulSoup(html, "html.parser")
        all_text.append(soup.get_text("\n", strip=True)[:8000])

        await log(f"{site} · найдено {len(phones)} номеров ({method})", "success")

        if _unique_phones():
            # Телефон уже есть (часто в шапке) — не обходим весь сайт, только контакты.
            max_pages = min(max_pages, len(visited) + 2)
            if d < 1:
                for path in _CONTACT_PATHS:
                    contact_url = normalize_url(path, base_origin)
                    if contact_url and contact_url not in visited:
                        queue.append((contact_url, 1))
        elif d < depth - 1:
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
            "error": humanize_fetch_error(last_error or "unreachable"),
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


async def analyze_site_homepage(site_url: str) -> dict:
    """Быстрый разбор только главной — устаревший путь, см. analyze_site_for_brief."""
    return await analyze_site_for_brief(site_url)


def _parse_page_content(html: str) -> dict:
    import json as _json

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)
    headings: list[dict[str, str]] = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        line = tag.get_text(" ", strip=True)
        if line:
            headings.append({"level": tag.name, "text": line})

    list_items: list[str] = []
    for block in soup.find_all(["main", "article", "section", "div"], limit=40):
        cls = " ".join(block.get("class") or []).lower()
        if any(x in cls for x in ("nav", "menu", "footer", "header", "cookie", "modal")):
            continue
        for li in block.find_all("li", limit=30):
            line = li.get_text(" ", strip=True)
            if 10 <= len(line) <= 90:
                list_items.append(line)

    meta_description = ""
    for sel in (
        ("meta", {"name": "description"}),
        ("meta", {"property": "og:description"}),
        ("meta", {"name": "twitter:description"}),
    ):
        tag = soup.find(sel[0], attrs=sel[1])
        if tag and tag.get("content"):
            meta_description = tag["content"].strip()
            break

    schema_offerings: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = _json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") in ("Service", "Product", "Offer"):
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    schema_offerings.append(name.strip())
            graph = item.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if isinstance(node, dict) and node.get("@type") in ("Service", "Product"):
                        name = node.get("name")
                        if isinstance(name, str) and name.strip():
                            schema_offerings.append(name.strip())

    return {
        "text": text[:5000],
        "headings": headings[:30],
        "list_items": list_items[:40],
        "meta_description": meta_description[:500],
        "schema_offerings": schema_offerings[:15],
    }


def _extract_brief_meta(soup: BeautifulSoup, html: str) -> dict:
    title = _title_from_html(html)
    nav_labels: list[str] = []
    for block in soup.find_all(["nav", "header"])[:4]:
        for a in block.find_all("a", limit=40):
            label = a.get_text(" ", strip=True)
            if label:
                nav_labels.append(label)

    footer_text = ""
    footer_el = soup.find("footer")
    if footer_el:
        footer_text = footer_el.get_text("\n", strip=True)[:2500]

    brand_hints: list[str] = []
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:site_name", "application-name", "twitter:title"):
            val = (tag.get("content") or "").strip()
            if val:
                brand_hints.append(val)
    for img in soup.find_all("img", limit=40):
        for attr in ("alt", "title", "aria-label"):
            val = (img.get(attr) or "").strip()
            if val and 3 < len(val) < 80:
                brand_hints.append(val)
    for a in soup.select("a[class*='logo'], .logo a, header a[href='/']")[:6]:
        val = a.get_text(" ", strip=True)
        if val and 3 < len(val) < 80:
            brand_hints.append(val)

    org_names: list[str] = []
    import json as _json

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = _json.loads(raw)
        except Exception:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                org_names.append(name.strip())
            if item.get("@type") in ("Organization", "LocalBusiness", "Corporation"):
                legal = item.get("legalName")
                if isinstance(legal, str) and legal.strip():
                    org_names.append(legal.strip())

    return {
        "title": title,
        "nav_labels": nav_labels[:30],
        "footer_text": footer_text,
        "brand_hints": brand_hints[:20],
        "org_names": org_names[:5],
    }


def _brief_extra_urls(soup: BeautifulSoup, base_url: str, root_host: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    base_origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"

    for path in _BRIEF_EXTRA_PATHS:
        url = normalize_url(path, base_origin)
        if url and url not in seen:
            seen.add(url)
            found.append(url)

    for a in soup.find_all("a", href=True):
        href = normalize_url(a.get("href") or "", base_url)
        if not href or not _same_host(href, root_host) or href in seen:
            continue
        label = a.get_text(" ", strip=True).lower()
        path = urlparse(href).path.lower()
        if _BRIEF_NAV_RE.search(label) or _BRIEF_NAV_RE.search(path):
            seen.add(href)
            found.append(href)
        if len(found) >= 5:
            break
    return found[:4]


async def analyze_site_for_brief(site_url: str) -> dict:
    """Опрос сайта для брифа: главная + до 4 страниц (услуги, о компании)."""
    from server.fetcher import fetch_page

    root = normalize_url(site_url)
    if not root:
        return {"ok": False, "error": "Некорректный URL"}
    html, final_url, code, method = await fetch_page(root, use_proxy=False, delay_ms=0)
    if not html or code >= 400:
        return {"ok": False, "error": humanize_fetch_error(method or f"Сайт недоступен ({code or 'нет ответа'})")}

    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_brief_meta(soup, html)
    home = _parse_page_content(html)
    root_host = urlparse(final_url or root).netloc.lower().lstrip("www.")

    pages_text: list[str] = [home["text"]]
    all_headings = list(home["headings"])
    all_list_items = list(home["list_items"])
    all_schema = list(home["schema_offerings"])
    meta_description = home["meta_description"]

    extra_urls = _brief_extra_urls(soup, final_url or root, root_host)
    pages_ok = 1
    for url in extra_urls:
        try:
            page_html, _, page_code, _ = await fetch_page(url, use_proxy=False, delay_ms=200)
        except Exception:
            continue
        if not page_html or page_code >= 400:
            continue
        parsed = _parse_page_content(page_html)
        pages_text.append(parsed["text"])
        all_headings.extend(parsed["headings"])
        all_list_items.extend(parsed["list_items"])
        all_schema.extend(parsed["schema_offerings"])
        if not meta_description and parsed["meta_description"]:
            meta_description = parsed["meta_description"]
        pages_ok += 1

    body_text = "\n".join(pages_text)[:12000]

    return {
        "ok": True,
        "site_url": normalize_url(final_url) or root,
        "title": meta["title"],
        "text_sample": body_text[:8000],
        "meta_description": meta_description[:500],
        "headings": all_headings[:60],
        "nav_labels": meta["nav_labels"],
        "footer_text": meta["footer_text"],
        "brand_hints": meta["brand_hints"],
        "org_names": meta["org_names"],
        "list_items": all_list_items[:60],
        "schema_offerings": all_schema[:20],
        "pages_surveyed": pages_ok,
    }


async def analyze_client_site(site_url: str, **kwargs) -> dict:
    root = normalize_url(site_url)
    if not root:
        return {"ok": False, "error": "Некорректный URL"}
    # Передаём полный URL со схемой: старые заводские сайты часто живут на http,
    # а https падает с «certificate key too weak».
    depth = int(kwargs.pop("depth", 1))
    delay_ms = int(kwargs.pop("delay_ms", 0))
    paths = ("", "/contacts", "/kontakty") if depth <= 1 else None
    data = await parse_site(root, depth=depth, delay_ms=delay_ms, paths=paths, **kwargs)
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
