"""Быстрая проверка ключевых функций перед деплоем. Запуск: python scripts/smoke_check.py"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import BUILD_VERSION, MAX_EXPORT_PHONES
from server.filters import is_junk_serp_result, region_matches, serp_hit_relevant, serp_passes_region_filter
from server.main import normalize_client_site, _phones_for_export


def check_js_constants() -> None:
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'const DEPLOY_VERSION_KEY = "signal-scout-deploy-version"' in app_js, "DEPLOY_VERSION_KEY missing"
    assert "rememberDeployVersion" in app_js
    m = re.search(r'const EXPECTED_BUILD_VERSION = "([^"]+)"', app_js)
    assert m, "EXPECTED_BUILD_VERSION missing"
    assert m.group(1) == BUILD_VERSION, f"app.js version {m.group(1)} != config {BUILD_VERSION}"
    assert "suggestBriefFromSite" in app_js
    assert "/api/brief/suggest" in app_js
    assert "buildRegionTree" in app_js
    index_html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="region-tree"' in index_html
    assert 'id="region-tree-filter"' in index_html
    assert "region-preset-add" not in index_html
    assert 'id="btn-instructions"' in index_html
    assert 'id="instructions-modal"' in index_html
    assert 'id="operator-name"' in index_html
    assert 'id="btn-download-brief"' in index_html
    assert 'id="btn-upload-brief"' in index_html
    assert "openHistoryRun" in app_js
    assert "stopHistoryRun" in app_js
    assert "data-open-run" in app_js
    assert "data-stop-run" in app_js


def check_history_fields() -> None:
    from server.db import _parse_queries, _status_label, db

    assert _parse_queries("а\nб, в") == ["а", "б", "в"]
    assert _status_label("done") == "готово"
    assert _status_label("error") == "ошибка"
    total = db.count_runs()
    items = db.list_runs(5)
    assert isinstance(total, int)
    if items:
        it = items[0]
        assert "operator_name" in it
        assert "queries" in it
        assert "queries_count" in it
        assert "status_label" in it


def check_humanize_fetch_error() -> None:
    from server.fetcher import humanize_fetch_error

    assert "пустую страницу" in humanize_fetch_error("error:empty")
    assert "HTTP 403" in humanize_fetch_error("error:HTTP 403")
    assert humanize_fetch_error("unreachable").startswith("Сайт")


def check_analyze_skip_logic() -> None:
    from server.fetcher import humanize_fetch_error

    err = humanize_fetch_error("error:empty")
    assert "бриф" in err.lower() or "запрос" in err.lower()


def check_admin_history() -> None:
    import uuid

    from server.admin_auth import admin_configured, admin_token, verify_admin_token
    from server.db import db, normalize_operator_name

    assert normalize_operator_name("") == "не указан"
    assert normalize_operator_name(" Иван ") == "Иван"
    token = admin_token("test-admin-pass")
    assert token
    assert verify_admin_token(token) is False  # ADMIN_PASSWORD not set in smoke env

    suffix = uuid.uuid4().hex[:8]
    db.create_run(f"smoke-admin-a-{suffix}", {"operatorName": "Иван", "clientSite": "https://example-a.ru"})
    db.create_run(f"smoke-admin-b-{suffix}", {"operatorName": "Пётр", "clientSite": "https://example-b.ru"})
    ivan = db.list_runs(10, operator="Иван")
    assert any("smoke-admin-a-" in it["id"] for it in ivan)
    assert all(it["operator_name"].casefold() == "иван" for it in ivan)
    assert isinstance(admin_configured(), bool)


def check_filter_alive_budget() -> None:
    from server import config

    assert config.FILTER_ALIVE_TIMEOUT <= 15
    assert config.FILTER_ALIVE_BUDGET_SEC >= 30
    assert "FILTER_ALIVE_BUDGET_SEC" in (ROOT / "server" / "pipeline.py").read_text(encoding="utf-8")
    assert "_filter_alive_candidates" in (ROOT / "server" / "pipeline.py").read_text(encoding="utf-8")


def check_ssl_weak_cert_fallback() -> None:
    import asyncio

    from server.fetcher import fetch_page

    async def _run() -> None:
        html, final, code, method = await fetch_page("https://hsmzavod.ru/")
        assert code == 200, f"expected 200, got {code} ({method})"
        assert len(html) > 1000, f"expected html body, got {len(html)} bytes"
        assert "http://" in final or "hsmzavod.ru" in final

    asyncio.run(_run())


def check_federal_districts() -> None:
    from server.regions_ru import REGIONS_RU

    js = (ROOT / "static" / "js" / "regions-ru.js").read_text(encoding="utf-8")
    assert "SS_FEDERAL_DISTRICTS" in js
    assert "Центральный федеральный округ" in js
    assert "Сибирский федеральный округ" in js
    assert "Дальневосточный федеральный округ" in js
    blocks = re.findall(r"regions:\s*\[(.*?)\]", js, re.S)
    assert len(blocks) == 8, f"expected 8 FO, got {len(blocks)}"
    names: list[str] = []
    for block in blocks:
        names.extend(re.findall(r'"([^"]+)"', block))
    assert len(names) == 89, f"expected 89 regions in FO tree, got {len(names)}"
    assert set(names) == set(REGIONS_RU)
    assert len(names) == len(set(names)), "duplicate regions across districts"


def check_client_site_urls() -> None:
    cases = [
        ("https://angary-stroy.ru/service/angary/", "https://angary-stroy.ru/service/angary/"),
        ("angary-stroy.ru/service/angary/", "https://angary-stroy.ru/service/angary/"),
        ("https://opalubka-domstroy.ru", "https://opalubka-domstroy.ru/"),
        ("opalubka-domstroy.ru", "https://opalubka-domstroy.ru/"),
    ]
    for raw, expected in cases:
        got = normalize_client_site(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"


def check_region_exclude() -> None:
    assert region_matches("компания в казани", ["московская область"], "exclude") is True
    assert region_matches("компания в московской области", ["московская область"], "exclude") is False
    assert region_matches("склад в московской области", ["московская область"], "include") is True


def check_serp_filters() -> None:
    meta_moscow = {
        "domain": "salecraft.ru",
        "title": "Построение отдела продаж под ключ в Москве",
        "snippet": "Аутсорсинг продаж для B2B",
        "queries": ["аутсорсинг отдела продаж"],
    }
    meta_news = {
        "domain": "tver.mk.ru",
        "title": "Новости Твери",
        "snippet": "Городские новости",
        "queries": ["лидогенерация B2B"],
    }
    meta_opalubka = {
        "domain": "opennet.ru",
        "title": "Форум",
        "snippet": "обсуждение Linux",
        "queries": ["аренда опалубки"],
    }
    queries_b2b = ["лидогенерация B2B", "аутсорсинг отдела продаж"]
    queries_opalubka = ["аренда опалубки Москва"]

    assert serp_hit_relevant(meta_moscow, queries_b2b, "B2B услуги") is True
    assert serp_hit_relevant(meta_news, queries_b2b, "") is False
    assert serp_hit_relevant(meta_opalubka, queries_opalubka, "опалубка") is False

    # Режим «включить регионы» не режет на этапе SERP
    meta_no_region = {
        "title": "Лидогенерация для бизнеса",
        "snippet": "Работаем по всей России",
        "queries": ["лидогенерация"],
    }
    assert serp_passes_region_filter(
        meta_no_region, ["алтайский край", "амурская область"], "include"
    ) is True
    assert serp_passes_region_filter(
        {"title": "Компания в Казани", "snippet": "", "queries": []},
        ["московская область"],
        "exclude",
    ) is True
    assert serp_passes_region_filter(
        {"title": "Склад в Московской области", "snippet": "", "queries": []},
        ["московская область"],
        "exclude",
    ) is False

    assert is_junk_serp_result("vc.ru", "vc.ru — стартапы", "") is True
    assert is_junk_serp_result("salecraft.ru", "Отдел продаж под ключ", "") is False
    assert serp_hit_relevant(
        {"domain": "klerk.ru", "title": "Что такое упущенная выгода", "snippet": "", "queries": ["упущенная выгода"]},
        ["упущенная выгода"],
        "",
    ) is False
    assert serp_hit_relevant(
        {"domain": "salecraft.ru", "title": "Отдел продаж под ключ Москва", "snippet": "аутсорсинг", "queries": ["отдел продаж под ключ"]},
        ["отдел продаж под ключ"],
        "",
    ) is True


def check_export_phones() -> None:
    assert MAX_EXPORT_PHONES == 6
    row = {"phones": [f"7900123456{i}" for i in range(10)], "p1": "79000000000"}
    assert len(_phones_for_export(row)) == 6


def check_export_filename() -> None:
    from server.main import _export_filename, _safe_export_basename

    assert _safe_export_basename("missing") == "signal-scout-missing"
    name = _export_filename("abc12345", "xlsx")
    assert name.endswith(".xlsx")
    date_part = name.rsplit(" ", 1)[1].replace(".xlsx", "")
    assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", date_part)


def check_brief_suggest_frameclub() -> None:
    from server.brief_suggest import suggest_brief_from_analysis

    r = suggest_brief_from_analysis(
        site_url="http://frameclub.ru/",
        title="Главная | Frameclub",
        text_sample=(
            "Философия каркасного дома Frame club производство каркасные дома под ключ "
            "внешняя отделка ИНН 332807583351 ИП Усик Тимофей Сергеевич"
        ),
        meta_description="Дом с полной готовностью к проживанию за 2 месяца",
        headings=[
            {"level": "h1", "text": "Дом с полной готовностью к проживанию за 2 месяца"},
            {"level": "h2", "text": "Собираем дома на собственном производстве"},
            {"level": "h3", "text": "Каркасные дома под ключ"},
        ],
        nav_labels=["Каталог", "Портфолио", "Производство"],
    )
    assert "каркас" in r["niche"].lower()
    assert "каркас" in r["queries"].lower()
    assert "углеволок" not in r["queries"].lower()
    assert r["clientName"] == "ИП Усик Тимофей Сергеевич"
    assert r.get("source") == "site_survey"


def check_brief_suggest_opalubka() -> None:
    from server.brief_suggest import suggest_brief_from_analysis

    r = suggest_brief_from_analysis(
        site_url="https://www.opalubka-domstroy.ru/",
        title="Продажа строительной опалубки в Москве | Купить новую или БУ",
        text_sample="Аренда и продажа опалубки крупнощитовая мелкощитовая Щелково Москва",
        meta_description="Аренда, продажа опалубки и комплектующих в Москве",
        headings=[
            {"level": "h2", "text": "Аренда, продажа опалубки"},
            {"level": "h3", "text": "Крупнощитовая опалубка"},
        ],
        nav_labels=["Аренда оборудования"],
        footer_text="© 2020-2026, ООО «Опалубка\u2011Домстрой»",
        brand_hints=["Опалубка Домстрой"],
    )
    assert "опалуб" in r["niche"].lower()
    assert "Опалубка" in r["clientName"]
    assert "аренда аренда" not in r["queries"].lower()
    assert "opalubka-domstroy.ru" in r["excludeDomains"]


def check_brief_suggest_strateix() -> None:
    from server.brief_suggest import suggest_brief_from_analysis

    r = suggest_brief_from_analysis(
        site_url="https://strateix.ru/",
        title="Стратеикс — архитектура прибыли",
        text_sample=(
            "Система управляемой прибыли для бизнеса B2B лидогенерация горячий спрос "
            "отдел продаж воронка сигналы спроса пилот 30 дней"
        ),
        meta_description="Система управляемой прибыли для бизнеса от 50 млн выручки",
        headings=[
            {"level": "h1", "text": "СТРАТЕИКС (STRATEIX)"},
            {"level": "h1", "text": "СТРАТЕИКС АРХИТЕКТУРА ПРИБЫЛИ С ОБРАТНОЙ СВЯЗЬЮ"},
            {"level": "h2", "text": "Для кого"},
            {"level": "h2", "text": "КТО ЗА ЭТИМ СТОИТ"},
            {"level": "h2", "text": "4 проблемы — 4 решения"},
            {"level": "h3", "text": "Решение: Система привлечения горячего спроса"},
            {"level": "h3", "text": "Решение: Система возврата упущенных сделок"},
            {"level": "h3", "text": "Решение: Система управления прибылью"},
            {"level": "h2", "text": "РЕЗУЛЬТАТЫ ПИЛОТОВ"},
        ],
        nav_labels=["Для кого", "Форматы старта", "Команда", "FAQ"],
        brand_hints=["Strateix", "Стратеикс"],
    )
    qlow = r["queries"].lower()
    assert "для кого" not in qlow
    assert "продажа стратеикс" not in qlow
    assert "купить стратеикс" not in qlow
    assert "кто за этим" not in qlow
    assert "4 проблемы" not in qlow
    assert "результаты пилотов" not in qlow
    assert "завод " not in qlow
    assert "решение:" not in qlow
    assert any(x in qlow for x in ("спрос", "прибыл", "лидоген", "b2b", "аутсорсинг"))
    assert "strateix.ru" in r["excludeDomains"]


def main() -> None:
    check_js_constants()
    check_federal_districts()
    check_history_fields()
    check_humanize_fetch_error()
    check_analyze_skip_logic()
    check_admin_history()
    check_filter_alive_budget()
    check_ssl_weak_cert_fallback()
    check_client_site_urls()
    check_region_exclude()
    check_serp_filters()
    check_export_phones()
    check_export_filename()
    check_brief_suggest_frameclub()
    check_brief_suggest_opalubka()
    check_brief_suggest_strateix()
    print(f"OK smoke_check · BUILD_VERSION={BUILD_VERSION}")


if __name__ == "__main__":
    main()
