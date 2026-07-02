from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

from server.config import PORT, ROOT, SCRAPINGBEE_API_KEY, SCRAPINGFISH_API_KEY, XMLRIVER_KEY, XMLRIVER_USER, YANDEX_XML_KEY, YANDEX_XML_USER, PILOT_SEED_DOMAINS
from server.serp import parse_xmlriver_credentials, probe_xmlriver
from server.crawler import analyze_client_site, normalize_url
from server.db import db
from server.pipeline import (
    _can_resume_run,
    _prepare_resume_pipeline,
    find_running_run_id,
    resume_pipeline_background,
    start_pipeline_background,
    stop_pipeline,
)

app = FastAPI(title="Сигнал-Скаут API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class BriefModel(BaseModel):
    clientName: str = ""
    clientSite: str
    niche: str = ""
    regionMode: str = "include"
    regions: str = ""
    queries: str = ""
    excludeDomains: str = ""
    phoneFilter: str = "business"
    sources: list[str] = Field(default_factory=lambda: ["serp", "site", "catalog"])
    checkAlive: bool = True
    xmlRiverUser: str = ""
    apiKey: str = ""
    maxSites: int = Field(default=50, ge=10, le=200)
    crawlDepth: int = Field(default=2, ge=1, le=5)
    requestDelayMs: int = Field(default=500, ge=0, le=5000)
    useProxy: bool = False
    quickCrawl: bool = False
    seedDomains: str = ""

    @field_validator("clientSite")
    @classmethod
    def validate_client_site(cls, value: str) -> str:
        normalized = normalize_client_site(value)
        if not normalized:
            raise ValueError("Укажите корректный URL сайта, например https://example.ru")
        host = urlparse(normalized).netloc
        if "." not in host:
            raise ValueError("В адресе сайта должно быть доменное имя")
        return normalized


def normalize_client_site(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    url_match = re.search(r"https?://[^\s<>\"'·|]+", text, flags=re.IGNORECASE)
    if url_match:
        text = url_match.group(0).rstrip(".,;)")
    elif re.search(r"[\w.-]+\.(ru|com|рф|org|net|biz)\b", text, flags=re.IGNORECASE):
        domain_match = re.search(
            r"([\w.-]+\.(?:ru|com|рф|org|net|biz))",
            text,
            flags=re.IGNORECASE,
        )
        if domain_match:
            text = domain_match.group(1)
    return normalize_url(text)


@app.get("/api/health")
def health():
    user, key = parse_xmlriver_credentials(api_user=XMLRIVER_USER, api_key=XMLRIVER_KEY)
    return {
        "ok": True,
        "search_provider": "xmlriver",
        "xmlriver_configured": bool(user and key),
        "yandex_xml_fallback": bool(YANDEX_XML_USER and YANDEX_XML_KEY),
        "scraping_configured": bool(SCRAPINGBEE_API_KEY or SCRAPINGFISH_API_KEY),
        "port": PORT,
    }


@app.get("/api/xmlriver/check")
async def xmlriver_check(
    xmlRiverUser: str = Query("", alias="xmlRiverUser"),
    apiKey: str = Query("", alias="apiKey"),
):
    """Тестовый запрос к XMLRiver — показывает реальную ошибку API."""
    user = xmlRiverUser or XMLRIVER_USER
    key = apiKey or XMLRIVER_KEY
    return await probe_xmlriver(xmlriver_user=user, xmlriver_key=key)


def _search_ready(brief: BriefModel) -> bool:
    if brief.quickCrawl:
        return True
    user, key = parse_xmlriver_credentials(
        api_user=brief.xmlRiverUser or XMLRIVER_USER,
        api_key=brief.apiKey or XMLRIVER_KEY,
    )
    if user and key:
        return True
    return bool(YANDEX_XML_USER and YANDEX_XML_KEY)


@app.get("/api/preview")
async def preview_site(url: str = Query(..., min_length=4)):
    data = await analyze_client_site(url, depth=1, delay_ms=0)
    if not data.get("ok"):
        raise HTTPException(400, data.get("error") or "Не удалось открыть сайт")
    return {
        "title": data.get("title") or "",
        "niche_hint": (data.get("title") or "")[:200],
        "sample": (data.get("text_sample") or "")[:500],
    }


@app.post("/api/run")
async def api_start_run(brief: BriefModel):
    if not brief.clientSite.strip():
        raise HTTPException(400, "Укажите сайт клиента")
    if not _search_ready(brief):
        raise HTTPException(
            400,
            "Укажите ID и API-ключ XMLRiver в брифе (xmlriver.com) "
            "или YANDEX_XML_USER / YANDEX_XML_KEY в .env",
        )
    client_site = brief.clientSite.strip()
    if find_running_run_id(client_site) or db.find_active_run(client_site):
        active = db.find_active_run(client_site)
        run_id = active["id"] if active else find_running_run_id(client_site)
        raise HTTPException(409, f"Сбор для этого клиента уже выполняется (прогон {run_id})")
    # Если есть остановленный/упавший прогон по этому же клиенту — продолжаем его,
    # чтобы повторный «Запустить сбор» не начинал всё сначала.
    try:
        for it in db.list_runs(30):
            if (it.get("client_site") or "") != client_site:
                continue
            if it.get("status") not in ("stopped", "error"):
                continue
            run = db.get_run(it["id"])
            if run and _can_resume_run(run):
                db.update_run(run["id"], brief=brief.model_dump())
                await resume_pipeline_background(run["id"])
                return {"run_id": run["id"], "status": "running", "resumed": True}
    except Exception:
        # Авто-resume — best-effort. Если что-то пошло не так, стартуем новый прогон.
        pass

    try:
        run_id = await start_pipeline_background(brief.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id, "status": "pending", "resumed": False}


@app.post("/api/run/quick")
async def api_quick_run(brief: BriefModel):
    """Быстрый обход 12 известных конкурентов — без XMLRiver, чтобы проверить телефоны."""
    if not brief.clientSite.strip():
        raise HTTPException(400, "Укажите сайт клиента")
    payload = brief.model_dump()
    payload["quickCrawl"] = True
    payload["sources"] = ["site"]
    payload["checkAlive"] = False
    payload["maxSites"] = min(int(payload.get("maxSites") or 50), len(PILOT_SEED_DOMAINS))
    if find_running_run_id(payload["clientSite"]) or db.find_active_run(payload["clientSite"]):
        active = db.find_active_run(payload["clientSite"])
        run_id = active["id"] if active else find_running_run_id(payload["clientSite"])
        raise HTTPException(409, f"Сбор для этого клиента уже выполняется (прогон {run_id})")
    try:
        run_id = await start_pipeline_background(payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id, "status": "pending", "quick": True}


PIPELINE_STEP_IDS = ("analyze", "serp", "filter", "crawl", "catalog", "dedup")


def _calc_progress(pipeline: dict, status: str) -> tuple[int, str]:
    if status == "done":
        return 100, ""
    steps = list(PIPELINE_STEP_IDS)
    progress = 0
    current = ""
    for step in steps:
        st = pipeline.get(step, "pending")
        if st == "done":
            progress += 100 // len(steps)
        elif st == "running":
            current = step
            progress += 100 // (len(steps) * 2)
            if step == "crawl":
                site_status = pipeline.get("site_status") or {}
                total = max(1, len(site_status))
                done_sites = sum(1 for v in site_status.values() if v in ("success", "error", "skip"))
                progress += int((100 // len(steps)) * 0.5 * done_sites / total)
    return min(progress, 99 if status in ("running", "pending") else 100), current


@app.get("/api/run/{run_id}")
def api_get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Прогон не найден")
    pipeline = run.get("pipeline") or {}
    status = run.get("status") or "pending"
    progress, current_step = _calc_progress(pipeline, status)
    return {
        "id": run["id"],
        "status": status,
        "pipeline": pipeline,
        "progress": progress,
        "current_step": current_step,
        "logs": pipeline.get("logs") or [],
        "site_status": pipeline.get("site_status") or {},
        "results_count": len(run.get("results") or []),
        "can_resume": _can_resume_run(run),
        "error": run.get("error"),
        "is_demo": run.get("is_demo", False),
        "updated_at": run.get("updated_at"),
    }


@app.post("/api/run/{run_id}/stop")
def api_stop_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Прогон не найден")
    stop_pipeline(run_id)
    pipeline = _prepare_resume_pipeline(run.get("pipeline") or {})
    db.update_run(
        run_id,
        status="stopped",
        pipeline=pipeline,
        results=run.get("results") or [],
    )
    return {"ok": True, "run_id": run_id, "can_resume": _can_resume_run({**run, "status": "stopped", "pipeline": pipeline})}


@app.post("/api/run/{run_id}/resume")
async def api_resume_run(run_id: str, brief: BriefModel | None = Body(default=None)):
    if not db.get_run(run_id):
        raise HTTPException(404, "Прогон не найден")
    try:
        if brief is not None:
            db.update_run(run_id, brief=brief.model_dump())
        await resume_pipeline_background(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"run_id": run_id, "status": "running", "resumed": True}


@app.get("/api/results/{run_id}")
def api_results(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Прогон не найден")
    return {
        "id": run_id,
        "status": run["status"],
        "brief": run["brief"],
        "results": run.get("results") or [],
        "is_demo": run.get("is_demo", False),
        "error": run.get("error"),
        "created_at": run.get("created_at"),
    }


@app.get("/api/history")
def api_history(limit: int = Query(50, ge=1, le=200)):
    return {"items": db.list_runs(limit)}


@app.get("/api/history/compare")
def api_compare(a: str = Query(...), b: str = Query(...)):
    ra, rb = db.get_run(a), db.get_run(b)
    if not ra or not rb:
        raise HTTPException(404, "Сессия не найдена")
    sa = {r["site"] for r in ra.get("results") or []}
    sb = {r["site"] for r in rb.get("results") or []}
    return {
        "a": a,
        "b": b,
        "new_sites": sorted(sb - sa),
        "removed_sites": sorted(sa - sb),
        "common": sorted(sa & sb),
    }


def _export_rows(run_id: str) -> list[dict]:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Прогон не найден")
    return run.get("results") or []


def _format_contacts_export(row: dict) -> str:
    parts: list[str] = []
    for key, type_key in (("p1", "p1_type"), ("p2", "p2_type")):
        phone = row.get(key) or ""
        if not phone:
            continue
        label = row.get(type_key) or ""
        parts.append(f"{phone} ({label})" if label else phone)
    return ", ".join(parts)


@app.get("/api/export/{run_id}.csv")
def export_csv(run_id: str):
    import csv
    import io

    rows = _export_rows(run_id)
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Сайт", "Компания", "Регион", "Контакты", "Источник", "Статус"])
    for r in rows:
        contacts = _format_contacts_export(r)
        w.writerow([
            r.get("site", ""), r.get("name", ""), r.get("region", ""),
            contacts, r.get("source", ""), r.get("status", ""),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="signal-scout-{run_id}.csv"'},
    )


@app.get("/api/export/{run_id}.xls")
def export_xls(run_id: str):
    import xml.sax.saxutils as xml_esc

    rows = _export_rows(run_id)
    esc = xml_esc.escape
    header = ["Сайт", "Компания", "Регион", "Контакты", "Источник", "Статус"]
    data = [header] + [
        [r.get("site", ""), r.get("name", ""), r.get("region", ""),
         _format_contacts_export(r), r.get("source", ""), r.get("status", "")]
        for r in rows
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?><?mso-application progid="Excel.Sheet"?>'
    xml += '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
    xml += '<Styles><Style ss:ID="Text"><NumberFormat ss:Format="@"/></Style></Styles>'
    xml += "<Worksheet ss:Name=\"Сигнал-Скаут\"><Table>"
    text_cols = {3, 5}
    for row in data:
        xml += "<Row>"
        for i, cell in enumerate(row):
            st = ' ss:StyleID="Text"' if i in text_cols else ""
            xml += f"<Cell{st}><Data ss:Type=\"String\">{esc(str(cell))}</Data></Cell>"
        xml += "</Row>"
    xml += "</Table></Worksheet></Workbook>"
    return Response(
        content=xml,
        media_type="application/vnd.ms-excel; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="signal-scout-{run_id}.xls"'},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="7" fill="#3d8bfd"/>'
        '<path d="M16 7l9 16H7z" fill="#fff" opacity=".95"/>'
        "</svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/")
def index_page():
    index = ROOT / "index.html"
    if not index.exists():
        raise HTTPException(404, "index.html не найден")
    return FileResponse(index, media_type="text/html; charset=utf-8")


@app.get("/static/{path:path}")
def static_file(path: str):
    f = ROOT / "static" / path
    if not f.exists() or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)
