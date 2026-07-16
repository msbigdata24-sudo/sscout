from __future__ import annotations

import asyncio
import random
from urllib.parse import quote, urlparse, urlunparse

import httpx

from server.config import FETCH_RETRIES, SCRAPINGBEE_API_KEY, SCRAPINGFISH_API_KEY, SITE_TIMEOUT

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]


def _headers(url: str = "") -> dict[str, str]:
    parsed = urlparse(url) if url else None
    origin = ""
    if parsed and parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    }
    if origin:
        headers["Referer"] = origin + "/"
    return headers


def humanize_fetch_error(raw: str) -> str:
    """Понятное сообщение вместо error:empty / error:HTTP 403."""
    msg = (raw or "").strip()
    if msg.startswith("error:"):
        msg = msg[6:].strip()
    lowered = msg.lower()
    if msg == "empty" or lowered == "unreachable":
        return (
            "Сайт не ответил или отдал пустую страницу "
            "(часто блокирует облачные серверы Render). "
            "Если в брифе есть запросы — сбор продолжится без разбора сайта."
        )
    if lowered.startswith("http "):
        code = msg.split()[-1] if msg.split() else ""
        if code in ("403", "401"):
            return f"Сайт вернул HTTP {code} — доступ с сервера запрещён."
        if code == "404":
            return "Страница не найдена (HTTP 404)."
        if code.isdigit() and int(code) >= 500:
            return f"Сайт недоступен (HTTP {code})."
        return f"Сайт недоступен ({msg})."
    if "timeout" in lowered or "timed out" in lowered:
        return "Таймаут — сайт не ответил вовремя."
    return msg or "Сайт недоступен"


def _is_ssl_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "certificate_verify_failed",
            "ssl:",
            "sslerror",
            "certificate verify failed",
            "ee certificate key too weak",
            "certificate has expired",
            "self signed certificate",
            "self-signed certificate",
        )
    )


def _with_scheme(url: str, scheme: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    return urlunparse((scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, parsed.fragment))


def _candidate_urls(url: str) -> list[str]:
    """HTTPS со слабым сертификатом → сначала https, затем http."""
    out: list[str] = []
    for candidate in (url, _with_scheme(url, "http"), _with_scheme(url, "https")):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


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


def _page_ok(resp: httpx.Response, *, method: str) -> tuple[str, str, int, str] | None:
    if resp.status_code >= 400:
        return None
    # Берём сырые байты: у старых сайтов body иногда «пустой» в text из‑за кодировки
    raw = resp.content or b""
    if len(raw) < 200 and not (resp.text and "<html" in (resp.text or "").lower()):
        return None
    html = resp.text or ""
    if not html and raw:
        html = raw.decode(resp.encoding or "utf-8", errors="replace")
    if html and ("<html" in html.lower() or len(html) > 400):
        return html, str(resp.url), resp.status_code, method
    return None


async def _direct_get(
    url: str,
    *,
    verify: bool,
    timeout: httpx.Timeout,
    method: str,
) -> tuple[str, str, int, str] | None:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=verify) as client:
        resp = await client.get(url, headers=_headers(url))
        return _page_ok(resp, method=method)


async def _via_proxy_apis(
    client: httpx.AsyncClient,
    candidates: list[str],
) -> tuple[str, str, int, str] | None:
    code = 0
    for candidate in candidates:
        html, code = await _via_scrapingbee(client, candidate)
        if html:
            return html, candidate, code, "scrapingbee"

        html, code = await _via_scrapingfish(client, candidate)
        if html:
            return html, candidate, code, "scrapingfish"
    return None


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
    candidates = _candidate_urls(url)

    proxy_ready = bool(SCRAPINGBEE_API_KEY or SCRAPINGFISH_API_KEY)

    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            if not use_proxy:
                for candidate in candidates:
                    # 1) обычный HTTPS/HTTP
                    try:
                        got = await _direct_get(
                            candidate, verify=True, timeout=timeout, method="direct",
                        )
                        if got:
                            return got
                    except Exception as exc:
                        last_error = str(exc)
                        if not _is_ssl_error(exc):
                            # таймаут/сеть — пробуем следующий URL (часто http после https)
                            continue
                        # 2) слабый/битый SSL
                        try:
                            got = await _direct_get(
                                candidate,
                                verify=False,
                                timeout=timeout,
                                method="direct-insecure-ssl",
                            )
                            if got:
                                return got
                        except Exception as exc2:
                            last_error = str(exc2)
                            continue

            if proxy_ready:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    got = await _via_proxy_apis(client, candidates)
                    if got:
                        return got
                    last_error = last_error or "empty"

            if attempt < FETCH_RETRIES:
                await asyncio.sleep(0.8 * attempt)
                continue
        except Exception as exc:
            last_error = str(exc)
            if attempt < FETCH_RETRIES:
                await asyncio.sleep(0.8 * attempt)
                continue

    return "", url, 0, f"error:{last_error}"
