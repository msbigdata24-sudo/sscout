from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from server.config import CRAWL_CONCURRENCY, DEFAULT_MAX_SITES, SERP_PAGES
from server.crawler import analyze_client_site, parse_site
from server.db import db
from server.filters import (
    check_site_alive,
    classify_domain,
    is_catalog_domain,
    parse_domain_list,
    parse_regions,
    serp_hit_relevant,
    try_catalog_page,
)
from server.phones import domain_from_url, pick_phones_enriched, validate_phone
from server.serp import SerpError, collect_serp, group_hits_by_domain, parse_xmlriver_credentials

PIPELINE_STEPS = ["analyze", "serp", "filter", "crawl", "catalog", "dedup"]
_running: dict[str, bool] = {}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _empty_pipeline() -> dict[str, Any]:
    state: dict[str, Any] = {"logs": [], "site_status": {}}
    for step in PIPELINE_STEPS:
        state[step] = "pending"
        state[step + "Log"] = ""
    return state


def _log(pipeline: dict, msg: str, site: str | None = None, status: str = "info") -> None:
    pipeline.setdefault("logs", []).append({
        "ts": _ts(),
        "msg": msg,
        "site": site,
        "status": status,
    })
    if site:
        pipeline.setdefault("site_status", {})[site] = status


def _set_step(pipeline: dict, step: str, status: str, log: str = "") -> None:
    pipeline[step] = status
    if log:
        pipeline[step + "Log"] = log


async def _persist(run_id: str, pipeline: dict, **kwargs) -> None:
    db.update_run(run_id, pipeline=pipeline, **kwargs)


async def run_pipeline(run_id: str, brief: dict[str, Any]) -> None:
    _running[run_id] = True
    pipeline = _empty_pipeline()
    await _persist(run_id, pipeline, status="running")

    try:
        client_domain = domain_from_url(brief.get("clientSite", ""))
        exclude = parse_domain_list(brief.get("excludeDomains", ""))
        regions = parse_regions(brief.get("regions", ""))
        region_mode = brief.get("regionMode", "include")
        phone_filter = brief.get("phoneFilter", "business")
        check_alive = brief.get("checkAlive", True)
        sources = set(brief.get("sources") or ["serp", "site", "catalog"])
        queries = [q.strip() for q in (brief.get("queries") or "").splitlines() if q.strip()]
        max_sites = max(10, min(200, int(brief.get("maxSites") or DEFAULT_MAX_SITES)))
        crawl_depth = max(1, min(5, int(brief.get("crawlDepth") or 2)))
        delay_ms = max(0, min(5000, int(brief.get("requestDelayMs") or 500)))
        use_proxy = bool(brief.get("useProxy"))

        # ── 1 analyze ──
        if not _running.get(run_id):
            return
        _set_step(pipeline, "analyze", "running")
        _log(pipeline, "Разбор сайта клиента…")
        await _persist(run_id, pipeline)

        async def client_log(msg, site, status):
            _log(pipeline, msg, site, status)

        analysis = await analyze_client_site(
            brief.get("clientSite", ""),
            depth=1,
            delay_ms=0,
            use_proxy=use_proxy,
            on_log=client_log,
        )
        if not analysis.get("ok"):
            raise RuntimeError(analysis.get("error") or "Не удалось разобрать сайт клиента")
        niche = brief.get("niche") or analysis.get("title") or ""
        _set_step(pipeline, "analyze", "done", f"Сайт: {analysis.get('site_url')} · {niche[:80]}")
        _log(pipeline, f"Ниша: {niche[:100]}", status="success")
        await _persist(run_id, pipeline)

        # ── 2 serp ──
        if not _running.get(run_id):
            return
        _set_step(pipeline, "serp", "running")
        _log(pipeline, f"Поиск Яндекс/Google · {SERP_PAGES} стр. · запросов {len(queries)}")
        await _persist(run_id, pipeline)

        serp_hits: list[dict] = []
        serp_stats: dict = {}
        catalog_urls: dict[str, str] = {}

        if "serp" in sources:
            xr_user, xr_key = parse_xmlriver_credentials(
                api_user=brief.get("xmlRiverUser", ""),
                api_key=brief.get("apiKey", ""),
            )
            serp_hits, serp_stats = await collect_serp(
                queries,
                pages=SERP_PAGES,
                xmlriver_user=xr_user,
                xmlriver_key=xr_key,
                regions_text=brief.get("regions", ""),
                max_results=max(50, max_sites * 4),
            )
            for hit in serp_hits:
                if is_catalog_domain(hit["domain"]):
                    catalog_urls[hit["domain"]] = hit["url"]

        _set_step(
            pipeline, "serp", "done",
            f"XMLRiver · Яндекс+Google · {SERP_PAGES} стр. · "
            f"доменов {serp_stats.get('unique_domains', 0)}",
        )
        _log(
            pipeline,
            f"Поиск XMLRiver · доменов {serp_stats.get('unique_domains', 0)}",
            status="success",
        )
        await _persist(run_id, pipeline)

        grouped = group_hits_by_domain(serp_hits)

        # ── 3 filter ──
        _set_step(pipeline, "filter", "running")
        await _persist(run_id, pipeline)

        candidates: list[dict] = []
        excluded_count = 0
        irrelevant_count = 0
        for domain, meta in grouped.items():
            status = classify_domain(domain, exclude=exclude, client_domain=client_domain)
            if status in ("исключён", "агрегатор"):
                excluded_count += 1
                continue
            if not serp_hit_relevant(meta, queries, brief.get("niche", "")):
                irrelevant_count += 1
                continue
            candidates.append({**meta, "domain": domain})

        alive_candidates: list[dict] = []
        for cand in candidates:
            if not _running.get(run_id):
                return
            domain = cand["domain"]
            url = cand["urls"][0] if cand.get("urls") else f"https://{domain}"
            alive, final_url, _code = await check_site_alive(url)
            if check_alive and not alive:
                _log(pipeline, f"{domain} — сайт не отвечает", domain, "error")
                continue
            cand["final_url"] = final_url
            alive_candidates.append(cand)

        if len(alive_candidates) > max_sites:
            _log(pipeline, f"Достигнут лимит в {max_sites} сайтов — лишние отсечены")
            alive_candidates = alive_candidates[:max_sites]

        _set_step(
            pipeline, "filter", "done",
            f"К обходу: {len(alive_candidates)} · "
            f"отсечено: агрег. {excluded_count}, не по теме {irrelevant_count}",
        )
        _log(
            pipeline,
            f"После фильтра: {len(alive_candidates)} сайтов к обходу "
            f"(агрегаторов {excluded_count}, не по теме {irrelevant_count})",
            status="success",
        )
        await _persist(run_id, pipeline)

        # ── 4 crawl ──
        _set_step(pipeline, "crawl", "running")
        await _persist(run_id, pipeline)

        rows: list[dict] = []
        with_phone = 0

        if "site" in sources:
            for cand in alive_candidates:
                if not _running.get(run_id):
                    return
                domain = cand["domain"]
                pipeline["site_status"][domain] = "pending"
                await _persist(run_id, pipeline)

                async def site_log(msg, site, status):
                    _log(pipeline, msg, site, status)
                    if site:
                        pipeline["site_status"][site] = status
                    await _persist(run_id, pipeline)

                cr = await parse_site(
                    domain,
                    depth=crawl_depth,
                    use_proxy=use_proxy,
                    delay_ms=delay_ms,
                    on_log=site_log,
                )

                phones_meta = cr.get("phones_meta") or []
                p1, p2, t1, t2 = pick_phones_enriched(phones_meta, phone_filter)
                name = cr.get("title") or cand.get("title") or domain
                offer = _offer_line(cand, regions)
                region_tag = _detect_region(offer, regions)
                status = "найден" if p1 else "без телефона"
                crawl_st = "success" if cr.get("ok") else "error"
                if p1:
                    with_phone += 1

                rows.append({
                    "site": domain,
                    "name": name[:120],
                    "offer": offer,
                    "region": region_tag,
                    "p1": p1,
                    "p2": p2,
                    "p1_type": t1,
                    "p2_type": t2,
                    "p1_valid": validate_phone(p1)["valid"] if p1 else True,
                    "p2_valid": validate_phone(p2)["valid"] if p2 else True,
                    "source": "сайт" if phones_meta else "SERP",
                    "status": status,
                    "crawl_status": crawl_st,
                })
                _log(
                    pipeline,
                    f"Парсинг {domain}… Найдено {len(phones_meta)} телефонов",
                    domain,
                    crawl_st,
                )

        _set_step(pipeline, "crawl", "done", f"Обойдено {len(rows)} · с телефоном {with_phone}")
        await _persist(run_id, pipeline)

        # ── 5 catalog ──
        _set_step(pipeline, "catalog", "running")
        catalog_filled = 0
        if "catalog" in sources and catalog_urls:
            for cat_url in set(catalog_urls.values()):
                phones, page_text = await try_catalog_page(cat_url)
                if not phones:
                    continue
                label = "KudaGid" if "kudagid" in cat_url else "каталог"
                for row in rows:
                    if row["p1"]:
                        continue
                    site = row["site"].lower()
                    if site in page_text or site.split(".")[0] in page_text:
                        enriched = [{"phone": p, "type": "mobile"} for p in phones]
                        p1, p2, t1, t2 = pick_phones_enriched(enriched, phone_filter)
                        if p1:
                            row.update({
                                "p1": p1, "p2": p2, "p1_type": t1, "p2_type": t2,
                                "source": label, "status": "найден",
                            })
                            catalog_filled += 1
        _set_step(pipeline, "catalog", "done", f"Из каталогов добавлено {catalog_filled}")
        await _persist(run_id, pipeline)

        # ── 6 dedup ──
        _set_step(pipeline, "dedup", "running")
        all_rows = _dedupe_rows(rows)
        _set_step(pipeline, "dedup", "done", f"Итого строк {len(all_rows)}")
        _log(pipeline, f"Сбор завершён · {len(all_rows)} строк · телефонов {sum(1 for r in all_rows if r.get('p1'))}", status="success")

        await _persist(run_id, pipeline, status="done", results=all_rows, is_demo=False)
    except SerpError as exc:
        _log(pipeline, str(exc), status="error")
        await _persist(run_id, pipeline, status="error", error=str(exc))
    except Exception as exc:
        _log(pipeline, str(exc), status="error")
        pl = db.get_run(run_id)
        pipeline = (pl or {}).get("pipeline") or pipeline
        await _persist(run_id, pipeline, status="error", error=str(exc))
    finally:
        _running.pop(run_id, None)


def stop_pipeline(run_id: str) -> None:
    _running[run_id] = False


async def start_pipeline_background(brief: dict[str, Any]) -> str:
    run_id = uuid.uuid4().hex[:12]
    db.create_run(run_id, brief)
    asyncio.create_task(run_pipeline(run_id, brief))
    return run_id


def _detect_region(offer: str, regions: list[str]) -> str:
    low = (offer or "").lower()
    for r in regions:
        if r.lower() in low:
            return r
    return regions[0] if regions else ""


def _offer_line(cand: dict, regions: list[str]) -> str:
    snippet = (cand.get("snippet") or "")[:160]
    reg = ", ".join(regions[:2]) if regions else ""
    engines = "/".join(cand.get("engines") or [])
    base = snippet or f"Выдача {engines}"
    return f"{base}; {reg}"[:200] if reg else base[:200]


def _row(domain: str, meta: dict, status: str, source: str, regions: list[str]) -> dict:
    offer = (meta.get("snippet") or "")[:200]
    return {
        "site": domain,
        "name": (meta.get("title") or domain)[:120],
        "offer": offer,
        "region": _detect_region(offer, regions),
        "p1": "", "p2": "", "p1_type": "", "p2_type": "",
        "p1_valid": True, "p2_valid": True,
        "source": source,
        "status": status,
        "crawl_status": "skipped",
    }


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    seen_sites: set[str] = set()
    seen_phones: set[str] = set()
    out: list[dict] = []
    for row in rows:
        site = row.get("site", "").lower()
        if site in seen_sites:
            continue
        seen_sites.add(site)
        p1 = row.get("p1") or ""
        if p1 and p1 in seen_phones and not row.get("p2"):
            continue
        if p1:
            seen_phones.add(p1)
        p2 = row.get("p2") or ""
        if p2:
            seen_phones.add(p2)
        out.append(row)
    return out
