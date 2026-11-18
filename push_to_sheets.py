#!/usr/bin/env python3
"""
Заливает перенос старой GR-таблицы в НОВУЮ Google Sheet «GR Pipeline».

- Авторизация: OAuth-токен Насти (~/.config/sheets-write-credentials.json) — тот же,
  что у sheet_export.py / deviation_notifier.py. Сервисный аккаунт не нужен (вариант А).
- Старая таблица только читается.
- Первый запуск создаёт таблицу и сохраняет её id в config.json; повторный —
  очищает вкладки и перезаливает (идемпотентно).
- Brands заполняется из Brand Approval Matrix (Чад), вкладка «catalog authorization approval».

Usage:
    python3 push_to_sheets.py            # свежая выгрузка старой таблицы → новая Google Sheet
    python3 push_to_sheets.py --csv X    # из уже скачанного CSV
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import migrate                                           # noqa: E402
from sheet_export import _sheets_client, BRAND_MATRIX_ID, CATALOG_TAB, CATALOG_MARKETS, _norm_brand  # noqa: E402

CONFIG = os.path.join(HERE, "config.json")
TITLE = "GR Pipeline"
MARKETS = ["US", "CA", "UK", "AU"]


def load_matrix(svc):
    rows = svc.spreadsheets().values().get(
        spreadsheetId=BRAND_MATRIX_ID, range=f"'{CATALOG_TAB}'!A2:E").execute().get("values", [])
    out = {}
    for r in rows:
        if not r or not r[0].strip():
            continue
        r = r + [""] * 5
        out[_norm_brand(r[0])] = {m: (r[1 + i].strip().lower() or "") for i, m in enumerate(MARKETS)}
    return out


def tabs(products, offers, decisions, cleanup, matrix):
    today = dt.date.today().isoformat()
    d = lambda v: v.isoformat() if isinstance(v, dt.date) else v

    readme = [
        ["GR Pipeline"],
        [f"Перенесено из старой GR-таблицы {today} скриптом product-research/gr-pipeline/push_to_sheets.py. Старая таблица не изменялась."],
        [""],
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

    prod = [["EAN", "Alt EAN", "ASIN", "ASIN 2", "Brand", "Product", "Type", "Google ID", "Added", "First source", "Old table rows"]]
    prod += [[p["ean"], p["alt"], p["asin"], p["asin2"], p["brand"], p["name"], p["type"], p["gid"], "before migration",
              "Old GR table", ", ".join(map(str, p["rows"]))] for p in products.values()]

    off = [["EAN", "Supplier", "Price, € excl VAT", "Price date", "Can buy", "Source", "Old table row"]]
    off += [[o["ean"], o["supplier"], o["price"] if o["price"] is not None else "", "before migration", o["allowed"],
             "Old GR table", o["row"]] for o in offers]

    dec = [["EAN", "Market", "ASIN", "Show", "Decision", "Reason", "Reopen trigger", "Recheck on",
            "Decided", "By", "Comment", "Old reason", "Old decision", "Retail check", "Old table row", "Other rows of this EAN"]]
    dec += [[x["ean"], x["market"], x["asin"], x["shown"], x["decision"], x["reason"], x["trigger"], d(x["recheck"]) or "",
             "before migration", "", "", x["old_reason"], x["old_decision"], x["retail"], x["row"], x["other"]] for x in decisions]

    by = {(x["ean"], x["market"]): x for x in decisions}
    mv = [["EAN", "Brand", "Product"] + sum(([m, f"{m} reason"] for m in MARKETS), [])]
    for p in products.values():
        r = [p["ean"], p["brand"], p["name"]]
        for m in MARKETS:
            x = by.get((p["ean"], m))
            r += [x["decision"] if x else "", x["reason"] if x else ""]
        mv.append(r)

    brand_counts = collections.Counter(p["brand"] for p in products.values() if p["brand"])
    br = [["Brand", "US", "CA", "UK", "AU", "Products in catalog", "Watch new releases", "Comment"]]
    for b, n in sorted(brand_counts.items(), key=lambda kv: -kv[1]):
        g = matrix.get(_norm_brand(b))
        br.append([b] + ([g[m] or "—" for m in MARKETS] if g else ["not in matrix"] * 4) + [n, "", ""])

    settings = [["Market", "Min ROI", "Source", "", "Supplier", "Can buy?", "Comment"]]
    roi = [("US", 0.17), ("CA", 0.20), ("UK", 0.15), ("AU", 0.17)]
    sup = collections.Counter(o["supplier"] for o in offers)
    sups = [s for s, _ in sup.most_common()]
    for i in range(max(len(roi), len(sups))):
        a = roi[i] if i < len(roi) else ("", "")
        s = sups[i] if i < len(sups) else ""
        settings.append([a[0], a[1], "ub-cost-calculator (Vika)" if a[0] else "", "", s,
                         ("no" if s in migrate.BLOCKED_SUPPLIERS else ("?" if s == "(not specified)" else "yes")) if s else "",
                         "do not buy — new-release signal only" if s in migrate.BLOCKED_SUPPLIERS else ""])

    inbox = [["Date", "Source", "Source detail", "EAN", "ASIN", "Brand", "Product", "Signal market", "Signal", "Triage", "Comment"]]
    sources = [["Source", "Type", "How it works", "Frequency", "Script", "Last run"],
               ["Supplier: new releases", "supply", "New releases on supplier sites (PC = signal only)", "daily", "novedades_scraper.py (needs fix)", ""],
               ["Keepa: new ASIN", "demand", "Fragrance ASINs created in the last 7–30 days, with sales (US/CA/UK)", "daily", "planned", ""],
               ["Keepa: brand", "demand", "New ASINs of brands flagged «Watch» in Brands", "daily", "planned", ""],
               ["Competitor: storefront", "demand", "ASINs added by sellers in our niche", "daily", "planned", ""],
               ["Brand announcement", "launch", "Launch announcements (Fragrantica, press releases)", "weekly", "planned", ""],
               ["Reopened", "reopen", "Old decisions whose reopen trigger fired", "daily", "planned", ""]]

    clean = [["Issue", "Old table row", "EAN", "ASIN", "Product", "Detail", "Fixed?"]]
    clean += [list(r) + [""] for r in sorted(cleanup, key=lambda r: (r[0], r[1]))]

    return [("README", readme), ("Market view", mv), ("Products", prod), ("Offers", off), ("Decisions", dec),
            ("Brands", br), ("Inbox", inbox), ("Sources", sources), ("Settings", settings), ("Cleanup", clean)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    a = ap.parse_args()
    text = open(a.csv, encoding="utf-8").read() if a.csv else migrate.fetch_old_csv()
    products, offers, decisions, cleanup, stats = migrate.migrate(text)

    svc = _sheets_client()
    matrix = load_matrix(svc)
    data = tabs(products, offers, decisions, cleanup, matrix)

    cfg = json.load(open(CONFIG)) if os.path.exists(CONFIG) else {}
    sid = cfg.get("spreadsheet_id")
    if not sid:
        body = {"properties": {"title": TITLE, "locale": "en_US"},
                "sheets": [{"properties": {"title": name, "gridProperties": {
                    "rowCount": max(len(rows) + 50, 100), "columnCount": max(len(rows[0]) if rows else 1, 10) + 2,
                    "frozenRowCount": 0 if name == "README" else 1}}} for name, rows in data]}
        ss = svc.spreadsheets().create(body=body, fields="spreadsheetId,spreadsheetUrl,sheets.properties").execute()
        sid = ss["spreadsheetId"]
        cfg = {"spreadsheet_id": sid, "url": ss["spreadsheetUrl"], "created": dt.date.today().isoformat()}
        json.dump(cfg, open(CONFIG, "w"), indent=2)
        print("created", ss["spreadsheetUrl"])
    meta = svc.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties").execute()
    ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    svc.spreadsheets().values().batchClear(spreadsheetId=sid, body={"ranges": [f"'{n}'" for n, _ in data]}).execute()
    svc.spreadsheets().values().batchUpdate(spreadsheetId=sid, body={
        "valueInputOption": "RAW",
        "data": [{"range": f"'{n}'!A1", "values": rows} for n, rows in data]}).execute()

    reqs = []
    for n, rows in data:
        hdr_row = 3 if n == "README" else 0
        reqs.append({"repeatCell": {
            "range": {"sheetId": ids[n], "startRowIndex": hdr_row, "endRowIndex": hdr_row + 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                           "backgroundColor": {"red": 0.12, "green": 0.23, "blue": 0.37}}},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"}})
        reqs.append({"autoResizeDimensions": {"dimensions": {"sheetId": ids[n], "dimension": "COLUMNS",
                                                             "startIndex": 0, "endIndex": len(rows[0]) if rows else 1}}})
    reqs.append({"repeatCell": {"range": {"sheetId": ids["README"], "startRowIndex": 0, "endRowIndex": 1},
                                "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 16}}},
                                "fields": "userEnteredFormat.textFormat"}})
    reqs.append({"repeatCell": {"range": {"sheetId": ids["Settings"], "startRowIndex": 1, "endRowIndex": 6,
                                          "startColumnIndex": 1, "endColumnIndex": 2},
                                "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}},
                                "fields": "userEnteredFormat.numberFormat"}})
    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()

    gated = sum(1 for r in data[5][1][1:] if r[1] != "not in matrix")
    print(f"Products {len(products)} · Offers {len(offers)} · Decisions {len(decisions)} · Чистка {len(cleanup)} · "
          f"Brands {len(data[5][1]) - 1} (в матрице {gated})")
    print(cfg["url"])


if __name__ == "__main__":
    main()
