"""Пересобрать экспорт Сигнал-Скаут: каждый телефон в отдельной ячейке, только цифры 7XXXXXXXXXX."""
from __future__ import annotations

import re
import sys
import xml.sax.saxutils as xml_esc
from pathlib import Path

_PHONE_IN_CELL = re.compile(r"(\d{11})")


def normalize_digits(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 10:
        d = "7" + d
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    return d if len(d) == 11 and d.startswith("7") else ""


def extract_phones(cell: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _PHONE_IN_CELL.finditer(cell or ""):
        n = normalize_digits(m.group(1))
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    if not out and cell:
        n = normalize_digits(cell)
        if n:
            out.append(n)
    return out


def parse_xls(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rows = re.findall(r"<Row>(.*?)</Row>", text, re.S)
    parsed: list[list[str]] = []
    for row in rows:
        cells = re.findall(r"<Data[^>]*>(.*?)</Data>", row)
        parsed.append(cells)
    return parsed


def rebuild_rows(parsed: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    if not parsed:
        return [], []

    header = parsed[0]
    # Старый формат: Сайт, Компания, Регион, Контакты, Источник, Статус
    data_rows = parsed[1:]
    rebuilt: list[list[str]] = []
    max_phones = 1

    for row in data_rows:
        while len(row) < 6:
            row.append("")
        site, name, region, contacts, source, status = row[:6]
        phones = extract_phones(contacts)
        max_phones = max(max_phones, len(phones))
        rebuilt.append([site, name, region, phones, source, status])

    # Без искусственного потолка — столько колонок, сколько нужно по данным.
    out_header = (
        ["Сайт", "Компания", "Регион"]
        + [f"Телефон {i}" for i in range(1, max_phones + 1)]
        + ["Источник", "Статус"]
    )
    out_data: list[list[str]] = []
    for site, name, region, phones, source, status in rebuilt:
        line = [site, name, region]
        for i in range(max_phones):
            line.append(phones[i] if i < len(phones) else "")
        line += [source, status]
        out_data.append(line)
    return out_header, out_data


def write_xls(path: Path, header: list[str], data: list[list[str]]) -> None:
    esc = xml_esc.escape
    phone_start = 3
    phone_end = phone_start + sum(1 for h in header if h.startswith("Телефон"))
    text_cols = set(range(phone_start, phone_end)) | {len(header) - 1}

    xml = '<?xml version="1.0" encoding="UTF-8"?><?mso-application progid="Excel.Sheet"?>'
    xml += '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
    xml += '<Styles><Style ss:ID="Text"><NumberFormat ss:Format="@"/></Style></Styles>'
    xml += '<Worksheet ss:Name="Сигнал-Скаут"><Table>'
    for row in [header] + data:
        xml += "<Row>"
        for i, cell in enumerate(row):
            st = ' ss:StyleID="Text"' if i in text_cols else ""
            xml += f"<Cell{st}><Data ss:Type=\"String\">{esc(str(cell))}</Data></Cell>"
        xml += "</Row>"
    xml += "</Table></Worksheet></Workbook>"
    path.write_text(xml, encoding="utf-8")


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"c:\Users\Admin\Downloads\signal-scout-138d7fcde751.xls"
    )
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
        r"c:\Users\Admin\Desktop\WORKCURSOR\Результаты\signal-scout-138d7fcde751-phones.xls"
    )
    parsed = parse_xls(src)
    header, data = rebuild_rows(parsed)
    write_xls(out, header, data)
    with_phones = sum(1 for r in data if any(r[3:3 + sum(1 for h in header if h.startswith("Телефон"))]))
    print(f"OK: {len(data)} строк -> {out}")
    print(f"Колонок телефонов: {sum(1 for h in header if h.startswith('Телефон'))}")


if __name__ == "__main__":
    main()
