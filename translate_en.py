#!/usr/bin/env python3
"""
One-off: translate the GR Pipeline Google Sheet and the pipeline code from Russian to English
(Nastya, 2026-11-19), and repair mojibake in product names left by the 2026-11-06 migration.

Sheet: only cells that contain Cyrillic or mojibake are rewritten, one range per cell, read with
valueRenderOption=FORMULA so formulas are never replaced by values. Tab «Чистка» → «Cleanup».
Code: exact quoted string literals from TR are replaced in the live files (not build_mockup.py,
which is the obsolete mockup). reason_map.csv reopen triggers are translated too.

Usage: python3 translate_en.py [--apply] [--sheet-only | --code-only]
Without --apply it only prints what would change.
"""
import argparse
import csv
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

TR = {
    # column headers
    "Доп. EAN": "Alt EAN", "Тип": "Type", "Добавлен": "Added", "Первый источник": "First source",
    "Строки в старой таблице": "Old table rows", "Строка в старой таблице": "Old table row",
    "Цена, € excl VAT": "Price, € excl VAT", "Дата цены": "Price date",
    "Можно закупать": "Can buy", "Можно закупать?": "Can buy?", "Источник": "Source",
    "Показывать": "Show", "Что вернёт в работу": "Reopen trigger", "Перепроверить": "Recheck on",
    "Решено": "Decided", "Кто": "By", "Комментарий": "Comment",
    "Старая причина": "Old reason", "Старое решение": "Old decision",
    "Другие строки этого EAN": "Other rows of this EAN",
    "Товаров в каталоге": "Products in catalog", "Следить за новинками": "Watch new releases",
    "Дата": "Date", "Деталь источника": "Source detail", "Маркет сигнала": "Signal market",
    "Сигнал": "Signal", "Автопроверка": "Auto check",
    "Как работает": "How it works", "Частота": "Frequency", "Скрипт": "Script", "Последний запуск": "Last run",
    "Проблема": "Issue", "Деталь": "Detail", "Исправлено?": "Fixed?",
    "Чистка": "Cleanup", "Вкладка": "Tab", "Что внутри": "What's inside",
    # values
    "Старая GR-таблица": "Old GR table", "Keepa: новый ASIN": "Keepa: new ASIN",
    "(не указан)": "(not specified)", "нет в матрице": "not in matrix", "до переноса": "before migration",
    "(по решению)": "(by decision)", "без ASIN": "no ASIN",
    "бренд одобрен в матрице": "brand approved in matrix",
    "цена поставщика упала / BB вырос": "supplier price dropped / BB rose",
    "продажи в Keepa выросли": "Keepa sales grew",
    "листинг появился": "listing appeared",
    "появился листинг этого маркета": "listing appeared on this market",
    "появился новый поставщик": "new supplier appeared",
    "по дате (+7 дней)": "by date (+7 days)", "по дате": "by date",
    "снова появился у поставщика": "back at a supplier",
    "— (вручную)": "— (manual)", "никогда": "never", "→ вкладка Чистка": "→ Cleanup tab",
    "Amazon ушёл с листинга": "Amazon left the listing",
    "Поставщик: новинки": "Supplier: new releases", "Keepa: бренд": "Keepa: brand",
    "Конкурент: витрина": "Competitor: storefront", "Анонс бренда": "Brand announcement",
    "Переоткрыто": "Reopened",
    "Новинки на сайтах поставщиков (PC — только как сигнал)": "New releases on supplier sites (PC = signal only)",
    "ASIN в Fragrance, созданные за 7–30 дней, с продажами (US/CA/UK)": "Fragrance ASINs created in the last 7–30 days, with sales (US/CA/UK)",
    "Новые ASIN брендов с флагом «Следить» в Brands": "New ASINs of brands flagged «Watch» in Brands",
    "ASIN, добавленные продавцами из нашей ниши": "ASINs added by sellers in our niche",
    "Анонсы запусков (Fragrantica, пресс-релизы)": "Launch announcements (Fragrantica, press releases)",
    "Старые решения, у которых сработал повод вернуться в работу": "Old decisions whose reopen trigger fired",
    "ежедневно": "daily", "еженедельно": "weekly", "будет": "planned",
    "novedades_scraper.py (починить)": "novedades_scraper.py (needs fix)",
    "ub-cost-calculator (Вика)": "ub-cost-calculator (Vika)",
    "не закупаем — только как сигнал о новинках": "do not buy — new-release signal only",
    "Цена без поставщика": "Price without supplier", "Нет EAN": "No EAN", "Цена не число": "Price is not a number",
    "Решение противоречит причине": "Decision contradicts reason",
    "строка не перенесена — добавить EAN и перенести вручную": "row not migrated — add the EAN and migrate by hand",
    # README (push_to_sheets.py)
    "1 строка = 1 EAN: постоянные факты о товаре": "1 row = 1 EAN: permanent product facts",
    "1 строка = поставщик × товар: цена, можно ли закупать (perfumes club = no)": "1 row = supplier × product: price, whether we can buy (perfumes club = no)",
    "1 строка = EAN × маркет: решение, причина, что вернёт в работу, дата перепроверки. WM / WM CA / eBay перенесены, Показывать = no":
        "1 row = EAN × market: decision, reason, reopen trigger, recheck date. WM / WM CA / eBay migrated with Show = no",
    "Привычный вид: товар × US/CA/UK/AU. Собирается скриптом, руками не править": "Familiar wide view: product × US/CA/UK/AU. Built by script, do not edit by hand",
    "Gated по маркетам из Brand Approval Matrix (Чад). «нет в матрице» = статус неизвестен": "Gated per market from Chad's Brand Approval Matrix. «not in matrix» = status unknown",
    "Входящие сигналы (заполнят скрипты-источники)": "Incoming signals (filled by source scripts)",
    "Источники сигналов": "Signal sources", "Минимальный ROI по маркетам, поставщики": "Min ROI per market, suppliers",
    "Строки старой таблицы, которые требуют ручной проверки": "Old-table rows that need a manual check",
}
# longest first so "по дате (+7 дней)" wins over "по дате"
KEYS = sorted(TR, key=len, reverse=True)
CYR = re.compile("[А-Яа-яЁё]")
MOJI = re.compile("(вЂ|Г[\u0080-ÿ°-џ]|Р[°-џ]|Ð|Ã)")


def fix_mojibake(s):
    if not MOJI.search(s):
        return s
    try:
        out = s.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    return out if not CYR.search(out) else s


def tr_cell(s):
    if not isinstance(s, str):
        return s
    t = fix_mojibake(s)
    if t in TR:
        return TR[t]
    m = re.fullmatch(r"строка (\d+) \((.*?)\): (.*)", t)
    if m:
        return f"row {m.group(1)} ({TR.get(m.group(2), m.group(2))}): {m.group(3)}"
    for k in KEYS:                         # substrings inside longer text, e.g. comments
        if k in t and len(k) > 6:
            t = t.replace(k, TR[k])
    return t


README_EN = [
    ["GR Pipeline"],
    ["Migrated from the old GR table on 2026-11-06 by product-research/gr-pipeline/push_to_sheets.py. The old table was not changed."],
    [],
    ["Tab", "What's inside"],
    ["Products", "1 row = 1 EAN: permanent product facts"],
    ["Offers", "1 row = supplier × product: price, whether we can buy (perfumes club = no)"],
    ["Decisions", "1 row = EAN × market: decision, reason, reopen trigger, recheck date. WM / WM CA / eBay migrated with Show = no"],
    ["Market view", "Familiar wide view: product × US/CA/UK/AU. Built by script, do not edit by hand"],
    ["Brands", "Gated per market from Chad's Brand Approval Matrix. «not in matrix» = status unknown"],
    ["Inbox", "Incoming signals (filled by source scripts)"],
    ["Sources", "Signal sources"],
    ["Settings", "Min ROI per market, suppliers"],
    ["Cleanup", "Old-table rows that need a manual check"],
]


def col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def do_sheet(apply):
    import gr_data as g
    svc = g.svc()
    meta = svc.spreadsheets().get(spreadsheetId=g.SID, fields="sheets.properties(title,sheetId)").execute()
    tabs = [(s["properties"]["title"], s["properties"]["sheetId"]) for s in meta["sheets"]]
    res = svc.spreadsheets().values().batchGet(
        spreadsheetId=g.SID, ranges=[f"'{t}'" for t, _ in tabs], valueRenderOption="FORMULA").execute()["valueRanges"]
    updates, stats = [], {}
    for (t, _), vr in zip(tabs, res):
        rows = vr.get("values", [])
        if t == "README":
            continue
        new_t = TR.get(t, t)
        n = 0
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                if isinstance(v, str) and v.startswith("="):
                    continue
                nv = tr_cell(v)
                if nv != v:
                    updates.append({"range": f"'{new_t}'!{col(j)}{i + 1}", "values": [[nv]]})
                    n += 1
                    if n <= 3:
                        print(f"  {t}!{col(j)}{i + 1}: {str(v)[:50]!r} → {str(nv)[:50]!r}")
        stats[t] = n
    print("cells to change per tab:", stats, "total", len(updates))
    left = [(u["range"], u["values"][0][0]) for u in updates if CYR.search(str(u["values"][0][0]))]
    print("still Cyrillic after translation:", len(left), left[:10])
    if not apply:
        return
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": sid, "title": TR[t]}, "fields": "title"}}
            for t, sid in tabs if t in TR]
    if reqs:
        svc.spreadsheets().batchUpdate(spreadsheetId=g.SID, body={"requests": reqs}).execute()
    svc.spreadsheets().values().clear(spreadsheetId=g.SID, range="'README'").execute()
    svc.spreadsheets().values().update(spreadsheetId=g.SID, range="'README'!A1", valueInputOption="RAW",
                                       body={"values": README_EN}).execute()
    for k in range(0, len(updates), 500):
        svc.spreadsheets().values().batchUpdate(spreadsheetId=g.SID, body={
            "valueInputOption": "RAW", "data": updates[k:k + 500]}).execute()
    print("sheet updated")


CODE_FILES = (["app.py", "gr_data.py", "push_to_sheets.py", "migrate.py"]
              + sorted(glob.glob(os.path.join(HERE, "sources", "*.py")))
              + sorted(glob.glob(os.path.join(HERE, "apps_script", "*.gs"))))


def do_code(apply):
    total = 0
    for f in CODE_FILES:
        p = f if os.path.isabs(f) else os.path.join(HERE, f)
        src = open(p, encoding="utf-8").read()
        out, n = src, 0
        for k in KEYS:
            for q in ('"', "'"):
                lit = q + k + q
                if lit in out:
                    n += out.count(lit)
                    out = out.replace(lit, q + TR[k] + q)
        if n:
            total += n
            print(f"  {os.path.relpath(p, HERE)}: {n} literals")
            if apply:
                open(p, "w", encoding="utf-8").write(out)
    # reason_map.csv: reopen_trigger + new_reason placeholder
    p = os.path.join(HERE, "reason_map.csv")
    rows = list(csv.reader(open(p, newline="", encoding="utf-8")))
    changed = 0
    for r in rows[1:]:
        for j in (1, 2):
            if r[j] in TR:
                r[j] = TR[r[j]]
                changed += 1
    print(f"  reason_map.csv: {changed} cells")
    if apply:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        open(p, "w", encoding="utf-8").write(buf.getvalue())
    print("code literals:", total)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sheet-only", action="store_true")
    ap.add_argument("--code-only", action="store_true")
    a = ap.parse_args()
    if not a.sheet_only:
        print("== code"); do_code(a.apply)
    if not a.code_only:
        print("== sheet"); do_sheet(a.apply)
