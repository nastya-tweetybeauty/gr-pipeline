#!/usr/bin/env python3
"""
Перенос старой GR-таблицы в новую структуру GR Pipeline.

Старая таблица: 1 строка = 1 EAN, 7 маркетов в ширину (Do we proceed / Research comment).
Новая: Products (EAN) · Offers (EAN × поставщик) · Decisions (EAN × маркет) · Чистка.

Сейчас пишет ТОЛЬКО локальный xlsx-превью для проверки. Запись в Google Sheet — следующий шаг,
после ок Насти.

Usage:
    python3 migrate.py                       # скачать свежую выгрузку и собрать превью
    python3 migrate.py --csv path/to/old.csv # взять уже скачанный CSV
"""
import argparse
import collections
import csv
import datetime as dt
import io
import os
import re
import urllib.request

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
OLD_SHEET_ID = "1qGW-VaUwKce7-DTH6XD9L1CYciUjI8nNmQ25sgWemyI"
OLD_GID = "1564114521"
MIGRATION_DATE = dt.date(2026, 11, 6)
RECHECK_START = dt.date(2026, 11, 9)       # первый понедельник после переноса
RECHECK_PER_DAY = 30

# Колонки старой таблицы (0-based, строка заголовков = 2-я строка листа)
C_EAN, C_EAN1, C_EAN2, C_ASIN, C_ASIN2, C_BRAND, C_NAME, C_TYPE, C_GID, C_PRICE, C_SUP = range(11)
MARKETS = {  # market: (decision col, reason col, retail-check col | None)
    "US": (11, 12, None), "WM": (13, 14, None), "CA": (15, 16, 17), "eBay": (18, 19, None),
    "WM CA": (20, 21, None), "UK": (22, 23, 24), "AU": (25, 26, 27),
}
SHOWN_MARKETS = {"US", "CA", "UK", "AU"}   # WM / WM CA / eBay переносим, но пока не показываем
BLOCKED_SUPPLIERS = {"perfumes club"}
RANK = {"yes": 3, "recheck": 2, "no": 1}       # не закупаем (см. feedback про PC)


def fetch_old_csv():
    url = f"https://docs.google.com/spreadsheets/d/{OLD_SHEET_ID}/export?format=csv&gid={OLD_GID}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def norm_ean(raw):
    d = re.sub(r"\D", "", raw or "")
    if not d:
        return ""
    return d.zfill(13) if 8 < len(d) < 13 else d


def load_reason_map():
    m = {}
    with open(os.path.join(HERE, "reason_map.csv"), newline="") as f:
        for r in csv.DictReader(f):
            m[r["old_reason"].strip()] = (r["new_reason"], r["reopen_trigger"])
    return m


def map_reason(text, decision, rmap):
    """(new_reason, reopen_trigger) for an old Research comment."""
    t = text.strip()
    if t in rmap:
        new, trig = rmap[t]
        if new == "(by decision)":
            return ("Approved", "—") if decision == "yes" else ("Other", "—")
        return new, trig
    low = t.lower()                         # свободный текст с ценами/заметками
    if "low roi" in low:
        return rmap["Declined: low roi"]
    if "no listing" in low:
        return rmap["no listing"]
    return "Other", "—"


def norm_decision(v, new_reason):
    v = v.strip().lower()
    if v in ("yes", "no", "recheck"):
        return v
    if v == "?":
        return "recheck"
    # решения нет, но есть причина — выводим из причины
    if new_reason.startswith("Approved"):
        return "yes"
    if new_reason == "Recheck":
        return "recheck"
    return "no"


def contradicts(decision, new_reason):
    if new_reason.startswith("Approved") and decision == "no":
        return True
    if decision == "yes" and new_reason in ("Gated", "Low ROI", "Low sales", "Tester / travel size", "Old version"):
        return True
    return False


def migrate(text):
    rows = list(csv.reader(io.StringIO(text)))[2:]
    rmap = load_reason_map()
    products, offers, decisions, cleanup = {}, [], {}, []
    stats = collections.Counter()

    for i, x in enumerate(rows):
        sheet_row = i + 3                     # номер строки в старой таблице
        x = x + [""] * (44 - len(x))
        if not any(c.strip() for c in x[:7]):
            continue
        stats["rows"] += 1
        ean = norm_ean(x[C_EAN])
        if not ean:
            cleanup.append(("No EAN", sheet_row, x[C_EAN].strip(), x[C_ASIN].strip(), x[C_NAME].strip(),
                            "row not migrated — add the EAN and migrate by hand"))
            stats["no_ean"] += 1
            continue

        alt = " / ".join(s.strip().replace("\n", " ") for s in (x[C_EAN1], x[C_EAN2]) if s.strip())
        if ean in products:
            stats["dup_rows"] += 1
            p = products[ean]
            p["rows"].append(sheet_row)
            for k, c in (("asin", C_ASIN), ("asin2", C_ASIN2), ("brand", C_BRAND), ("name", C_NAME), ("type", C_TYPE), ("gid", C_GID)):
                if not p[k] and x[c].strip():
                    p[k] = x[c].strip()
            if alt:
                p["alt"] = " / ".join(filter(None, (p["alt"], alt)))
        else:
            products[ean] = dict(ean=ean, alt=alt, asin=x[C_ASIN].strip(), asin2=x[C_ASIN2].strip(),
                                 brand=x[C_BRAND].strip(), name=x[C_NAME].strip(), type=x[C_TYPE].strip(),
                                 gid=x[C_GID].strip(), rows=[sheet_row])

        sup, price = x[C_SUP].strip().lower(), x[C_PRICE].strip()
        if sup or price:
            try:
                pv = float(price.replace(",", ".")) if price else None
            except ValueError:
                pv = None
                cleanup.append(("Price is not a number", sheet_row, ean, x[C_ASIN].strip(), x[C_NAME].strip(), f"Buy Price = {price!r}"))
            if not sup:
                cleanup.append(("Price without supplier", sheet_row, ean, x[C_ASIN].strip(), x[C_NAME].strip(), f"Buy Price = {price}"))
            offers.append(dict(ean=ean, supplier=sup or "(not specified)", price=pv,
                               allowed="no" if sup in BLOCKED_SUPPLIERS else ("?" if not sup else "yes"), row=sheet_row))

        for mkt, (cd, cr, crc) in MARKETS.items():
            dv, rv = x[cd], x[cr]
            if not (dv.strip() or rv.strip()):
                continue
            new_reason, trigger = map_reason(rv, dv.strip().lower(), rmap)
            dec = norm_decision(dv, new_reason)
            key = (ean, mkt)
            rec = dict(ean=ean, market=mkt, shown="yes" if mkt in SHOWN_MARKETS else "no", decision=dec,
                       reason=new_reason, trigger=trigger, old_reason=rv.strip().replace("\n", " "),
                       old_decision=dv.strip(), retail=(x[crc].strip() if crc else ""), recheck=None, row=sheet_row,
                       asin=x[C_ASIN].strip(), other="")
            if contradicts(dec, new_reason):
                cleanup.append(("Decision contradicts reason", sheet_row, ean, x[C_ASIN].strip(), x[C_NAME].strip(),
                                f"{mkt}: {dv.strip()} + «{rv.strip()}»"))
            if key in decisions:
                # Один EAN в нескольких строках = обычно разные ASIN (US-листинг, CA-листинг…).
                # По маркету берём лучшее решение (yes > recheck > no) вместе с ASIN этой строки.
                prev = decisions[key]
                stats["multi_asin_merges"] += 1
                best, other = (rec, prev) if RANK[dec] > RANK[prev["decision"]] else (prev, rec)
                best["other"] = "; ".join(filter(None, (best["other"],
                                f"строка {other['row']} ({other['asin'] or 'no ASIN'}): {other['decision']} / {other['reason']}")))
                decisions[key] = best
                continue
            decisions[key] = rec

    # Перепроверка по дате — раскладываем по рабочим дням, 30 в день, только показываемые маркеты
    due = [d for d in decisions.values() if d["decision"] == "recheck" and d["trigger"].startswith("by date") and d["shown"] == "yes"]
    day, n = RECHECK_START, 0
    for d in due:
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        d["recheck"] = day
        n += 1
        if n == RECHECK_PER_DAY:
            day, n = day + dt.timedelta(days=1), 0

    return products, offers, list(decisions.values()), cleanup, stats


# ───────────────────────── xlsx preview ─────────────────────────
F = "Arial"
HDR = dict(font=Font(name=F, bold=True, color="FFFFFF", size=10), fill=PatternFill("solid", fgColor="1F3A5F"))


def write_tab(wb, name, headers, rows, widths):
    ws = wb.create_sheet(name)
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = HDR["font"], HDR["fill"]
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for r in rows:
        ws.append(list(r))
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=F, size=10)
            if isinstance(c.value, dt.date):
                c.number_format = "yyyy-mm-dd"
    return ws


def build_preview(products, offers, decisions, cleanup, stats, out):
    wb = Workbook()
    s = wb.active
    s.title = "Сводка"
    shown = [d for d in decisions if d["shown"] == "yes"]
    trig = collections.Counter(d["trigger"] for d in shown if d["decision"] != "yes")
    lines = [
        ("Перенос старой GR-таблицы — превью", ""),
        (f"Собрано {dt.date.today():%Y-%m-%d}. В Google ничего не записано.", ""),
        ("", ""),
        ("Строк в старой таблице", stats["rows"]),
        ("Products (уникальных EAN)", len(products)),
        ("…EAN в нескольких строках (разные ASIN) — склеено", stats["dup_rows"]),
        ("…решений по маркету объединено (взято лучшее)", stats["multi_asin_merges"]),
        ("Строк без EAN (→ Чистка, не перенесены)", stats["no_ean"]),
        ("Offers", len(offers)),
        ("…из них perfumes club (не закупаем)", sum(1 for o in offers if o["allowed"] == "no")),
        ("Товаров, где есть только PC", sum(1 for e in {o["ean"] for o in offers}
                                           if all(o["allowed"] == "no" for o in offers if o["ean"] == e))),
        ("Decisions всего", len(decisions)),
        ("…в US/CA/UK/AU (показываются)", len(shown)),
        ("…в WM / WM CA / eBay (перенесены, скрыты)", len(decisions) - len(shown)),
        ("Строк в Чистке", len(cleanup)),
        ("", ""),
        ("Решения US/CA/UK/AU", ""),
    ]
    for k, v in collections.Counter(d["decision"] for d in shown).most_common():
        lines.append((f"  {k}", v))
    lines += [("", ""), ("Причины US/CA/UK/AU", "")]
    for k, v in collections.Counter(d["reason"] for d in shown).most_common():
        lines.append((f"  {k}", v))
    lines += [("", ""), ("Что вернёт no/recheck в работу (US/CA/UK/AU)", "")]
    for k, v in trig.most_common():
        lines.append((f"  {k}", v))
    dated = [d["recheck"] for d in shown if d["recheck"]]
    if dated:
        lines += [("", ""), ("Перепроверка по дате", f"{len(dated)} шт., {min(dated):%d.%m} – {max(dated):%d.%m}, по {RECHECK_PER_DAY} в рабочий день")]
    for r, (a, b) in enumerate(lines, 1):
        s.cell(row=r, column=1, value=a).font = Font(name=F, size=10, bold=not a.startswith("  ") and bool(b == ""))
        s.cell(row=r, column=2, value=b).font = Font(name=F, size=10)
    s["A1"].font = Font(name=F, size=14, bold=True, color="1F3A5F")
    s.column_dimensions["A"].width = 52
    s.column_dimensions["B"].width = 40

    write_tab(wb, "Products",
              ["EAN", "Alt EAN", "ASIN", "ASIN 2", "Brand", "Product", "Type", "Google ID", "Added", "First source", "Old table rows"],
              [(p["ean"], p["alt"], p["asin"], p["asin2"], p["brand"], p["name"], p["type"], p["gid"], "before migration",
                "Old GR table", ", ".join(map(str, p["rows"]))) for p in products.values()],
              [15, 18, 13, 13, 18, 50, 16, 14, 12, 18, 14])
    write_tab(wb, "Offers",
              ["EAN", "Supplier", "Price, € excl VAT", "Price date", "Can buy", "Old table row"],
              [(o["ean"], o["supplier"], o["price"], "before migration", o["allowed"], o["row"]) for o in offers],
              [15, 20, 12, 12, 12, 12])
    write_tab(wb, "Decisions",
              ["EAN", "Market", "ASIN", "Show", "Decision", "Reason", "Reopen trigger", "Old reason", "Old decision",
               "Retail check", "Recheck on", "Old table row", "Other rows of this EAN"],
              [(d["ean"], d["market"], d["asin"], d["shown"], d["decision"], d["reason"], d["trigger"], d["old_reason"], d["old_decision"],
                d["retail"], d["recheck"], d["row"], d["other"]) for d in decisions],
              [15, 8, 13, 10, 9, 22, 30, 36, 10, 10, 12, 12, 50])
    by = {(d["ean"], d["market"]): d for d in decisions}
    mv_head = ["EAN", "Brand", "Product"]
    for m in ("US", "CA", "UK", "AU"):
        mv_head += [m, f"{m} reason"]
    mv_rows = []
    for p in products.values():
        r = [p["ean"], p["brand"], p["name"]]
        for m in ("US", "CA", "UK", "AU"):
            d = by.get((p["ean"], m))
            r += [d["decision"] if d else "", d["reason"] if d else ""]
        mv_rows.append(r)
    write_tab(wb, "Market view", mv_head, mv_rows, [15, 18, 46] + [8, 20] * 4)

    write_tab(wb, "Cleanup",
              ["Issue", "Old table row", "EAN", "ASIN", "Product", "Detail"],
              sorted(cleanup, key=lambda r: (r[0], r[1])),
              [28, 12, 15, 13, 44, 70])
    wb.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="уже скачанный CSV старой таблицы")
    ap.add_argument("--out", default=os.path.join(HERE, f"migration_preview_{dt.date.today():%Y-%m-%d}.xlsx"))
    a = ap.parse_args()
    text = open(a.csv, encoding="utf-8").read() if a.csv else fetch_old_csv()
    products, offers, decisions, cleanup, stats = migrate(text)
    build_preview(products, offers, decisions, cleanup, stats, a.out)
    print(f"Products {len(products)} · Offers {len(offers)} · Decisions {len(decisions)} · Чистка {len(cleanup)}")
    print("saved", a.out)


if __name__ == "__main__":
    main()
