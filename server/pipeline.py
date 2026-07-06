from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from server.config import (
    CRAWL_CONCURRENCY,
    DEFAULT_MAX_SITES,
    DEFAULT_PILOT_QUERIES,
    PHONES_OVERFLOW_THRESHOLD,
    PILOT_SEED_DOMAINS,
    SERP_MAX_RESULTS_PER_QUERY,
    SERP_PAGES,
    SITE_CRAWL_TIMEOUT,
)
from server.crawler import analyze_client_site, parse_site
from server.db import db
from server.filters import (
    check_site_alive,
    classify_domain,
    is_catalog_domain,
    parse_domain_list,
    parse_regions,
    serp_passes_region_filter,
    serp_hit_relevant,
    try_catalog_page,
)
from server.phones import domain_from_url, pick_phones_enriched, pick_phones_list, validate_phone
from server.regions_ru import detect_region_in_text
from server.serp import SerpError, collect_serp, group_hits_by_domain, parse_xmlriver_credentials

PIPELINE_STEPS = ["analyze", "serp", "filter", "crawl", "catalog", "dedup"]
_running: dict[str, bool] = {}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _empty_pipeline() -> dict[str, Any]:
    state: dict[str, Any] = {"logs": [], "site_status": {}, "checkpoint": {}}
    for step in PIPELINE_STEPS:
        state[step] = "pending"
        state[step + "Log"] = ""
    return state


def _error_message(exc: BaseException) -> str:
    msg = str(exc).strip()
    name = exc.__class__.__name__
    if name in ("ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout"):
        return "Таймаут сети — сайт или XMLRiver не ответил вовремя. Нажмите «Продолжить сбор»."
    if "ReadTimeout" in msg or "ConnectTimeout" in msg:
        return "Таймаут сети — сайт или XMLRiver не ответил вовремя. Нажмите «Продолжить сбор»."
    return msg or name


def _parse_queries(brief: dict[str, Any]) -> list[str]:
    raw = brief.get("queries") or ""
    if isinstance(raw, list):
        items = [str(q).strip() for q in raw if str(q).strip()]
    else:
        text = str(raw).replace(",", "\n")
        items = [q.strip() for q in text.splitlines() if q.strip()]
    if items:
        return items
    if brief.get("quickCrawl"):
        return []
    return list(DEFAULT_PILOT_QUERIES)


def _seed_domains_from_brief(brief: dict[str, Any]) -> list[str]:
    if not brief.get("quickCrawl"):
        return []
    raw = (brief.get("seedDomains") or "").strip()
    if raw:
        return [d.strip().lower().lstrip("www.") for d in raw.replace("\n", ",").split(",") if d.strip()]
    return list(PILOT_SEED_DOMAINS)


def _step_done(pipeline: dict, step: str) -> bool:
    return pipeline.get(step) == "done"


def _prepare_resume_pipeline(pipeline: dict) -> dict:
    """Шаг running при остановке сбрасываем в pending — resume повторит его."""
    prepared = dict(pipeline)
    for step in PIPELINE_STEPS:
        if prepared.get(step) == "running":
            prepared[step] = "pending"
    return prepared


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


def _is_stopped(run_id: str) -> bool:
    return not _running.get(run_id, False)


async def _save_stopped(run_id: str, pipeline: dict, rows: list[dict]) -> None:
    _log(pipeline, "Остановлено пользователем", status="error")
    prepared = _prepare_resume_pipeline(pipeline)
    await _persist(run_id, prepared, status="stopped", results=rows)


def _crawled_domains(rows: list[dict]) -> set[str]:
    return {str(r.get("site", "")).lower() for r in rows if r.get("site")}


def _build_row(
    cand: dict,
    cr: dict,
    *,
    phone_filter: str,
    regions: list[str],
    region_mode: str = "include",
) -> dict:
    domain = cand["domain"]
    phones_meta = cr.get("phones_meta") or []
    phone_list = pick_phones_list(phones_meta, phone_filter)
    p1, p2, t1, t2 = pick_phones_enriched(phones_meta, phone_filter)
    name = cr.get("title") or cand.get("title") or domain
    offer = _offer_line(cand, regions, region_mode=region_mode)
    region_tag = _detect_region(cand)
    crawl_st = "success" if cr.get("ok") else "error"
    return {
        "site": domain,
        "name": name[:120],
        "offer": offer,
        "region": region_tag,
        "phones": phone_list,
        "p1": p1,
        "p2": p2,
        "p1_type": t1,
        "p2_type": t2,
        "p1_valid": validate_phone(p1)["valid"] if p1 else True,
        "p2_valid": validate_phone(p2)["valid"] if p2 else True,
        "source": "сайт" if phones_meta else "SERP",
        "status": "найден" if p1 else "без телефона",
        "crawl_status": crawl_st,
    }


async def _crawl_candidates(
    run_id: str,
    pipeline: dict,
    alive_candidates: list[dict],
    rows: list[dict],
    *,
    phone_filter: str,
    regions: list[str],
    region_mode: str = "include",
    crawl_depth: int,
    delay_ms: int,
    use_proxy: bool,
) -> list[dict]:
    done = _crawled_domains(rows)
    todo = [c for c in alive_candidates if c["domain"].lower() not in done]
    if done:
        _log(
            pipeline,
            f"Продолжение обхода: осталось {len(todo)} из {len(alive_candidates)} сайтов",
            status="info",
        )

    lock = asyncio.Lock()
    sem = asyncio.Semaphore(CRAWL_CONCURRENCY)

    async def site_log(msg: str, site: str | None, status: str) -> None:
        _log(pipeline, msg, site, status)
        if site:
            pipeline.setdefault("site_status", {})[site] = status
        async with lock:
            await _persist(run_id, pipeline, results=rows)

    async def one(cand: dict) -> None:
        if _is_stopped(run_id):
            return
        domain = cand["domain"]
        async with sem:
            if _is_stopped(run_id):
                return
            pipeline.setdefault("site_status", {})[domain] = "pending"
            async with lock:
                await _persist(run_id, pipeline, results=rows)

            try:
                cr = await asyncio.wait_for(
                    parse_site(
                        domain,
                        depth=crawl_depth,
                        use_proxy=use_proxy,
                        delay_ms=delay_ms,
                        on_log=site_log,
                    ),
                    timeout=SITE_CRAWL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                cr = {"ok": False, "error": "timeout", "phones_meta": [], "title": domain}
                await site_log(f"{domain} — таймаут {SITE_CRAWL_TIMEOUT} сек", domain, "error")
            except Exception as exc:
                cr = {
                    "ok": False,
                    "error": _error_message(exc),
                    "phones_meta": [],
                    "title": domain,
                }
                await site_log(f"{domain} — {_error_message(exc)}", domain, "error")

            row = _build_row(
                cand,
                cr,
                phone_filter=phone_filter,
                regions=regions,
                region_mode=region_mode,
            )
            phones_meta = cr.get("phones_meta") or []
            raw_unique = len({e.get("phone") for e in phones_meta if e.get("phone")})
            crawl_st = row["crawl_status"]
            kept = len(row.get("phones") or [])
            if raw_unique > PHONES_OVERFLOW_THRESHOLD:
                msg = (
                    f"Парсинг {domain}… на сайте {raw_unique} номеров — "
                    f"оставлено {kept} мобильных (лимит при переполнении)"
                )
            else:
                msg = f"Парсинг {domain}… Найдено {kept} телефонов"
            await site_log(msg, domain, crawl_st)

            async with lock:
                rows.append(row)
                await _persist(run_id, pipeline, results=rows)

    await asyncio.gather(*[one(c) for c in todo])
    return rows


async def run_pipeline(run_id: str, brief: dict[str, Any], *, resume: bool = False) -> None:
    _running[run_id] = True
    existing = db.get_run(run_id) if resume else None
    resuming = bool(resume and existing and existing.get("pipeline"))

    if resuming:
        pipeline = _prepare_resume_pipeline(dict(existing["pipeline"]))
        pipeline.setdefault("logs", [])
        pipeline.setdefault("site_status", {})
        pipeline.setdefault("checkpoint", {})
        rows = list(existing.get("results") or [])
        _log(pipeline, f"Продолжение прогона · уже собрано {len(rows)} строк", status="info")
        await _persist(run_id, pipeline, status="running")
    else:
        pipeline = _empty_pipeline()
        rows: list[dict] = []
        await _persist(run_id, pipeline, status="running")

    try:
        client_domain = domain_from_url(brief.get("clientSite", ""))
        exclude = parse_domain_list(brief.get("excludeDomains", ""))
        regions = parse_regions(brief.get("regions", ""))
        region_mode = brief.get("regionMode", "include") or "include"
        if region_mode not in ("include", "exclude"):
            region_mode = "include"
        serp_regions_text = "" if region_mode == "exclude" else (brief.get("regions", "") or "")
        phone_filter = brief.get("phoneFilter", "business")
        check_alive = brief.get("checkAlive", True)
        sources = set(brief.get("sources") or ["serp", "site", "catalog"])
        queries = _parse_queries(brief)
        had_queries_in_brief = bool(
            (isinstance(brief.get("queries"), list) and any(str(q).strip() for q in brief.get("queries")))
            or str(brief.get("queries") or "").strip()
        )
        if not brief.get("quickCrawl") and not had_queries_in_brief and queries:
            _log(pipeline, f"Запросы в брифе пустые — подставлены пилотные ({len(queries)} шт.)", status="info")
        if regions and region_mode == "exclude":
            _log(
                pipeline,
                f"Регионы: все субъекты РФ, кроме {len(regions)} перечисленных в брифе",
                status="info",
            )
        elif regions and region_mode == "include":
            _log(
                pipeline,
                f"Регионы (включить): {len(regions)} — влияют на выдачу Яндекса, "
                "не отсекают сайты без региона в сниппете",
                status="info",
            )
        max_sites = max(10, min(200, int(brief.get("maxSites") or DEFAULT_MAX_SITES)))
        serp_cap = max(SERP_MAX_RESULTS_PER_QUERY, max_sites * 3)
        crawl_depth = max(1, min(5, int(brief.get("crawlDepth") or 2)))
        delay_ms = max(0, min(5000, int(brief.get("requestDelayMs") or 500)))
        use_proxy = bool(brief.get("useProxy"))

        checkpoint = pipeline.get("checkpoint") or {}
        alive_candidates: list[dict] = list(checkpoint.get("alive_candidates") or [])
        catalog_urls: dict[str, str] = dict(checkpoint.get("catalog_urls") or {})
        seed_domains = _seed_domains_from_brief(brief)
        quick_crawl = bool(seed_domains)

        # ── 1 analyze ──
        if not _step_done(pipeline, "analyze"):
            if _is_stopped(run_id):
                await _save_stopped(run_id, pipeline, rows)
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
            _set_step(
                pipeline, "analyze", "done",
                f"Сайт: {analysis.get('site_url')} · {niche.strip()}",
            )
            _log(pipeline, f"Ниша: {niche.strip()}", status="success")
            await _persist(run_id, pipeline)

        # ── 2 serp ──
        if quick_crawl and not _step_done(pipeline, "serp"):
            _set_step(pipeline, "serp", "done", f"Пропущен · быстрый обход {len(seed_domains)} сайтов")
            _log(
                pipeline,
                f"Быстрый режим: обход {len(seed_domains)} известных конкурентов (без XMLRiver)",
                status="info",
            )
            await _persist(run_id, pipeline)
        elif not _step_done(pipeline, "serp"):
            if _is_stopped(run_id):
                await _save_stopped(run_id, pipeline, rows)
                return
            if "serp" in sources and not queries:
                raise SerpError(
                    "Нет поисковых запросов. Откройте «Бриф» → заполните поле запросов → «Сохранить бриф»."
                )
            _set_step(pipeline, "serp", "running")
            _log(pipeline, f"Поиск Яндекс · {SERP_PAGES} стр. · запросов {len(queries)}")
            await _persist(run_id, pipeline)

            serp_hits: list[dict] = []
            serp_stats: dict = {}

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
                    regions_text=serp_regions_text,
                    max_results=serp_cap,
                )
                for hit in serp_hits:
                    if is_catalog_domain(hit["domain"]):
                        catalog_urls[hit["domain"]] = hit["url"]

            checkpoint["serp_hits"] = serp_hits
            checkpoint["catalog_urls"] = catalog_urls
            checkpoint["serp_stats"] = serp_stats
            pipeline["checkpoint"] = checkpoint

            _set_step(
                pipeline, "serp", "done",
                f"XMLRiver · Яндекс · {SERP_PAGES} стр. · "
                f"доменов {serp_stats.get('unique_domains', 0)}",
            )
            _log(
                pipeline,
                f"Поиск XMLRiver · сырьё {serp_stats.get('raw_hits', 0)} · "
                f"уник. доменов {serp_stats.get('unique_domains', 0)}",
                status="success",
            )
            await _persist(run_id, pipeline)

        # ── 3 filter ──
        if not _step_done(pipeline, "filter") or not alive_candidates:
            if _is_stopped(run_id):
                await _save_stopped(run_id, pipeline, rows)
                return
            _set_step(pipeline, "filter", "running")
            await _persist(run_id, pipeline)

            if quick_crawl and not alive_candidates:
                for domain in seed_domains:
                    if _is_stopped(run_id):
                        await _save_stopped(run_id, pipeline, rows)
                        return
                    d = domain.lower().lstrip("www.")
                    if d in exclude or d == client_domain:
                        continue
                    alive_candidates.append({
                        "domain": d,
                        "urls": [f"https://{d}"],
                        "title": d,
                        "snippet": "быстрый обход",
                        "engines": ["seed"],
                        "queries": ["quick"],
                    })
                alive_candidates = alive_candidates[:max_sites]
                checkpoint["alive_candidates"] = alive_candidates
                pipeline["checkpoint"] = checkpoint
                _set_step(
                    pipeline, "filter", "done",
                    f"К обходу: {len(alive_candidates)} (быстрый режим)",
                )
                _log(
                    pipeline,
                    f"Быстрый обход: {len(alive_candidates)} сайтов в очереди",
                    status="success",
                )
                await _persist(run_id, pipeline)
            else:
                serp_hits = checkpoint.get("serp_hits") or []
                if not serp_hits and "serp" in sources:
                    xr_user, xr_key = parse_xmlriver_credentials(
                        api_user=brief.get("xmlRiverUser", ""),
                        api_key=brief.get("apiKey", ""),
                    )
                    serp_hits, _ = await collect_serp(
                        queries,
                        pages=SERP_PAGES,
                        xmlriver_user=xr_user,
                        xmlriver_key=xr_key,
                        regions_text=serp_regions_text,
                        max_results=serp_cap,
                    )
                    for hit in serp_hits:
                        if is_catalog_domain(hit["domain"]):
                            catalog_urls[hit["domain"]] = hit["url"]
                    checkpoint["serp_hits"] = serp_hits

                grouped = group_hits_by_domain(serp_hits)
                candidates: list[dict] = []
                excluded_count = 0
                irrelevant_count = 0
                region_skipped = 0
                for domain, meta in grouped.items():
                    status = classify_domain(domain, exclude=exclude, client_domain=client_domain)
                    if status in ("исключён", "агрегатор"):
                        excluded_count += 1
                        continue
                    if not serp_passes_region_filter(meta, regions, region_mode):
                        region_skipped += 1
                        continue
                    if not serp_hit_relevant(meta, queries, brief.get("niche", "")):
                        irrelevant_count += 1
                        continue
                    candidates.append({**meta, "domain": domain})

                alive_candidates = []
                for cand in candidates:
                    if _is_stopped(run_id):
                        checkpoint["alive_candidates"] = alive_candidates
                        checkpoint["catalog_urls"] = catalog_urls
                        pipeline["checkpoint"] = checkpoint
                        await _save_stopped(run_id, pipeline, rows)
                        return
                    domain = cand["domain"]
                    url = cand["urls"][0] if cand.get("urls") else f"https://{domain}"
                    alive, final_url, _code = await check_site_alive(url, require_phone=False)
                    if check_alive and not alive:
                        _log(
                            pipeline,
                            f"{domain} — не отвечает или парковка",
                            domain,
                            "error",
                        )
                        continue
                    cand["final_url"] = final_url
                    alive_candidates.append(cand)

                if len(alive_candidates) > max_sites:
                    _log(pipeline, f"Достигнут лимит в {max_sites} сайтов — лишние отсечены")
                    alive_candidates = alive_candidates[:max_sites]

                if len(alive_candidates) == 0 and grouped:
                    _log(
                        pipeline,
                        "После фильтров не осталось сайтов — проверьте запросы и XMLRiver",
                        status="error",
                    )

                checkpoint["alive_candidates"] = alive_candidates
                checkpoint["catalog_urls"] = catalog_urls
                pipeline["checkpoint"] = checkpoint

                _set_step(
                    pipeline, "filter", "done",
                    f"К обходу: {len(alive_candidates)} · "
                    f"отсечено: агрег. {excluded_count}, не по теме {irrelevant_count}, "
                    f"по региону {region_skipped}",
                )
                _log(
                    pipeline,
                    f"После фильтра: {len(alive_candidates)} сайтов к обходу "
                    f"(агрегаторов {excluded_count}, не по теме {irrelevant_count}, "
                    f"по региону {region_skipped})",
                    status="success",
                )
                await _persist(run_id, pipeline)

        if _is_stopped(run_id):
            await _save_stopped(run_id, pipeline, rows)
            return

        # ── 4 crawl ──
        if not _step_done(pipeline, "crawl"):
            _set_step(pipeline, "crawl", "running")
            await _persist(run_id, pipeline, results=rows)

            if "site" in sources and alive_candidates:
                rows = await _crawl_candidates(
                    run_id,
                    pipeline,
                    alive_candidates,
                    rows,
                    phone_filter=phone_filter,
                    regions=regions,
                    region_mode=region_mode,
                    crawl_depth=crawl_depth,
                    delay_ms=delay_ms,
                    use_proxy=use_proxy,
                )

            if _is_stopped(run_id):
                await _save_stopped(run_id, pipeline, rows)
                return

            with_phone = sum(1 for r in rows if r.get("p1"))
            _set_step(pipeline, "crawl", "done", f"Обойдено {len(rows)} · с телефоном {with_phone}")
            await _persist(run_id, pipeline, results=rows)

        if _is_stopped(run_id):
            await _save_stopped(run_id, pipeline, rows)
            return

        # ── 5 catalog ──
        if not _step_done(pipeline, "catalog"):
            _set_step(pipeline, "catalog", "running")
            catalog_filled = 0
            if "catalog" in sources and catalog_urls:
                for cat_url in set(catalog_urls.values()):
                    if _is_stopped(run_id):
                        await _save_stopped(run_id, pipeline, rows)
                        return
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
                            phone_list = pick_phones_list(enriched, phone_filter)
                            if p1:
                                row.update({
                                    "phones": phone_list,
                                    "p1": p1, "p2": p2, "p1_type": t1, "p2_type": t2,
                                    "source": label, "status": "найден",
                                })
                                catalog_filled += 1
            _set_step(pipeline, "catalog", "done", f"Из каталогов добавлено {catalog_filled}")
            await _persist(run_id, pipeline, results=rows)

        if _is_stopped(run_id):
            await _save_stopped(run_id, pipeline, rows)
            return

        # ── 6 dedup ──
        _set_step(pipeline, "dedup", "running")
        all_rows = _dedupe_rows(rows)
        _set_step(pipeline, "dedup", "done", f"Итого строк {len(all_rows)}")
        _log(
            pipeline,
            f"Сбор завершён · {len(all_rows)} строк · "
            f"телефонов {sum(1 for r in all_rows if r.get('p1'))}",
            status="success",
        )

        await _persist(run_id, pipeline, status="done", results=all_rows, is_demo=False)
    except SerpError as exc:
        err = _error_message(exc)
        _log(pipeline, err, status="error")
        await _persist(run_id, pipeline, status="error", error=err, results=rows)
    except Exception as exc:
        err = _error_message(exc)
        _log(pipeline, err, status="error")
        pl = db.get_run(run_id)
        pipeline = (pl or {}).get("pipeline") or pipeline
        await _persist(run_id, pipeline, status="error", error=err, results=rows)
    finally:
        _running.pop(run_id, None)


def stop_pipeline(run_id: str) -> None:
    _running[run_id] = False


def find_running_run_id(client_site: str) -> str | None:
    site = (client_site or "").strip().lower().rstrip("/")
    if not site:
        return None
    for run_id, active in _running.items():
        if not active:
            continue
        run = db.get_run(run_id)
        if not run:
            continue
        cs = (run.get("brief") or {}).get("clientSite", "").strip().lower().rstrip("/")
        if cs == site:
            return run_id
    return None


async def start_pipeline_background(brief: dict[str, Any]) -> str:
    client_site = (brief.get("clientSite") or "").strip()
    mem_id = find_running_run_id(client_site)
    if mem_id:
        raise ValueError(f"Сбор для этого клиента уже выполняется ({mem_id})")
    active = db.find_active_run(client_site)
    if active:
        raise ValueError(f"Сбор для этого клиента уже выполняется ({active['id']})")
    run_id = uuid.uuid4().hex[:12]
    db.create_run(run_id, brief)
    asyncio.create_task(run_pipeline(run_id, brief))
    return run_id


async def resume_pipeline_background(run_id: str) -> str:
    run = db.get_run(run_id)
    if not run:
        raise ValueError("Прогон не найден")
    status = run.get("status") or ""
    if status == "running":
        if _running.get(run_id):
            raise ValueError("Сбор уже выполняется на сервере")
    elif status not in ("stopped", "error"):
        raise ValueError("Продолжить можно только остановленный или упавший прогон")
    if not _can_resume_run(run):
        raise ValueError("Нет сохранённого прогресса для продолжения")
    db.update_run(run_id, status="running")
    asyncio.create_task(run_pipeline(run_id, run["brief"], resume=True))
    return run_id


def _can_resume_run(run: dict) -> bool:
    status = run.get("status") or ""
    run_id = run.get("id") or ""
    if status == "running":
        return not _running.get(run_id)
    if status not in ("stopped", "error"):
        return False
    pipeline = run.get("pipeline") or {}
    checkpoint = pipeline.get("checkpoint") or {}
    if checkpoint.get("alive_candidates") or run.get("results"):
        return True
    if pipeline.get("filter") == "done":
        return True
    if pipeline.get("serp") == "done" and checkpoint.get("serp_hits"):
        return True
    # Остановили на шаге 2–3 (например 24%): analyze уже done, serp ещё running.
    if pipeline.get("analyze") == "done":
        return True
    if any(pipeline.get(step) == "done" for step in PIPELINE_STEPS):
        return True
    return len(pipeline.get("logs") or []) >= 2


def _detect_region(cand: dict) -> str:
    queries = cand.get("queries") or []
    if isinstance(queries, set):
        queries = sorted(queries)
    text = " ".join(
        str(cand.get(k) or "")
        for k in ("title", "snippet")
    ) + " " + " ".join(str(q) for q in queries)
    return detect_region_in_text(text)


def _offer_line(cand: dict, regions: list[str], *, region_mode: str = "include") -> str:
    snippet = (cand.get("snippet") or "")[:160]
    engines = "/".join(cand.get("engines") or [])
    return (snippet or f"Выдача {engines}")[:200]


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
