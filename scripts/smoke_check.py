"""Быстрая проверка ключевых функций перед деплоем. Запуск: python scripts/smoke_check.py"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import BUILD_VERSION, MAX_EXPORT_PHONES
from server.filters import region_matches
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
    assert r.get("source") == "homepage"


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
            {"level": "h3", "text": "Мелкощитовая опалубка"},
        ],
        nav_labels=["Аренда оборудования", "Продажа оборудования"],
    )
    assert "опалуб" in r["niche"].lower()
    assert "опалуб" in r["queries"].lower()
    assert "opalubka-domstroy.ru" in r["excludeDomains"]


def main() -> None:
    check_js_constants()
    check_client_site_urls()
    check_region_exclude()
    check_export_phones()
    check_export_filename()
    check_brief_suggest_frameclub()
    check_brief_suggest_opalubka()
    print(f"OK smoke_check · BUILD_VERSION={BUILD_VERSION}")


if __name__ == "__main__":
    main()
