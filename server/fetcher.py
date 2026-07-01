from __future__ import annotations

import asyncio
import random
from urllib.parse import quote

import httpx

from server.config import FETCH_RETRIES, HTTP_TIMEOUT, SCRAPINGBEE_API_KEY, SCRAPINGFISH_API_KEY, SITE_TIMEOUT

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]


def _headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }


async def _via_scrapingbee(client: httpx.AsyncClient, url: str) -> tuple[str, int]:
    if not SCRAPINGBEE_API_KEY:
        return "", 0
    api = (
        "https://app.scrapingbee.com/api/v1/"
        f"?api_key={SCRAPINGBEE_API_KEY}&url={quote(url, safe='')}&country_code=ru"
    )
    resp = await client.get(api)
    if resp.status_code >= 400:
        return "", resp.status_code
    return resp.text or "", resp.status_code


async def _via_scrapingfish(client: httpx.AsyncClient, url: str) -> tuple[str, int]:
    if not SCRAPINGFISH_API_KEY:
        return "", 0
    api = (
        "https://api.scrapingfish.com/api/v1/"
        f"?api_key={SCRAPINGFISH_API_KEY}&url={quote(url, safe='')}"
    )
    resp = await client.get(api)
    if resp.status_code >= 400:
        return "", resp.status_code
    return resp.text or "", resp.status_code


async def fetch_page(
    url: str,
    *,
    use_proxy: bool = False,
    delay_ms: int = 0,
) -> tuple[str, str, int, str]:
    """Возвращает (html, final_url, status_code, fetch_method)."""
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)

    timeout = httpx.Timeout(SITE_TIMEOUT, connect=min(10, SITE_TIMEOUT))
    last_error = ""

    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                if not use_proxy:
                    resp = await client.get(url, headers=_headers())
                    if resp.status_code < 400:
                        html = resp.text or ""
                        if html and ("<html" in html.lower() or len(html) > 400):
                            return html, str(resp.url), resp.status_code, "direct"
                    if attempt < FETCH_RETRIES:
                        await asyncio.sleep(0.8 * attempt)
                        continue

                html, code = await _via_scrapingbee(client, url)
                if html:
                    return html, url, code, "scrapingbee"

                html, code = await _via_scrapingfish(client, url)
                if html:
                    return html, url, code, "scrapingfish"

                last_error = f"HTTP {code}" if code else "empty"
        except Exception as exc:
            last_error = str(exc)
            if attempt < FETCH_RETRIES:
                await asyncio.sleep(0.8 * attempt)
                continue

    return "", url, 0, f"error:{last_error}"
