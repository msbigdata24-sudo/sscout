from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from server.config import PORT, ROOT, SCRAPINGBEE_API_KEY, SCRAPINGFISH_API_KEY, XMLRIVER_KEY, XMLRIVER_USER, YANDEX_XML_KEY, YANDEX_XML_USER
from server.serp import parse_xmlriver_credentials
from server.crawler import analyze_client_site
from server.db import db
from server.pipeline import start_pipeline_background, stop_pipeline

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


def _search_ready(brief: BriefModel) -> bool:
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
def api_start_run(brief: BriefModel):
    if not brief.clientSite.strip():
        raise HTTPException(400, "Укажите сайт клиента")
    if not _search_ready(brief):
        raise HTTPException(
            400,
            "Укажите ID и API-ключ XMLRiver в брифе (xmlriver.com) "
            "или YANDEX_XML_USER / YANDEX_XML_KEY в .env",
        )
    run_id = start_pipeline_background(brief.model_dump())
    return {"run_id": run_id, "status": "pending"}


@app.get("/api/run/{run_id}")
def api_get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Прогон не найден")
    pipeline = run.get("pipeline") or {}
    done_steps = sum(1 for k, v in pipeline.items() if not k.endswith("Log") and k in {
        "analyze", "serp", "filter", "crawl", "catalog", "dedup"
    } and v == "done")
    progress = int(done_steps / 6 * 100) if pipeline else 0
    return {
        "id": run["id"],
        "status": run["status"],
        "pipeline": pipeline,
        "progress": progress,
        "logs": pipeline.get("logs") or [],
        "site_status": pipeline.get("site_status") or {},
        "results_count": len(run.get("results") or []),
        "error": run.get("error"),
        "is_demo": run.get("is_demo", False),
        "updated_at": run.get("updated_at"),
    }


@app.post("/api/run/{run_id}/stop")
def api_stop_run(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, "Прогон не найден")
    stop_pipeline(run_id)
    db.update_run(run_id, status="stopped")
    return {"ok": True}


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


@app.get("/api/export/{run_id}.csv")
def export_csv(run_id: str):
    import csv
    import io

    rows = _export_rows(run_id)
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Сайт", "Компания", "Регион", "Телефон 1", "Тип 1", "Телефон 2", "Тип 2", "Источник", "Статус"])
    for r in rows:
        w.writerow([
            r.get("site", ""), r.get("name", ""), r.get("region", ""),
            r.get("p1", ""), r.get("p1_type", ""), r.get("p2", ""), r.get("p2_type", ""),
            r.get("source", ""), r.get("status", ""),
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
    header = ["Сайт", "Компания", "Регион", "Телефон 1", "Тип 1", "Телефон 2", "Тип 2", "Источник", "Статус"]
    data = [header] + [
        [r.get("site", ""), r.get("name", ""), r.get("region", ""),
         r.get("p1", ""), r.get("p1_type", ""), r.get("p2", ""), r.get("p2_type", ""),
         r.get("source", ""), r.get("status", "")]
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
