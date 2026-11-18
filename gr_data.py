"""
Чтение/запись Google Sheet «GR Pipeline» для приложения.

Авторизация — gcp.py: локально OAuth-токен Насти, в Streamlit Cloud — сервисный аккаунт. Каждая запись строки
адресуется по номеру строки на листе (row = индекс в выгрузке + 2).
"""
import csv
import datetime as dt
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from gcp import sheets_client as _sheets_client  # noqa: E402

SID = json.load(open(os.path.join(HERE, "config.json")))["spreadsheet_id"]
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SID}/edit"
MARKETS = ["US", "CA", "UK", "AU"]
KEEPA_DOMAIN = {"US": 1, "UK": 2, "CA": 6}
AMAZON_TLD = {"US": "com", "CA": "ca", "UK": "co.uk", "AU": "com.au"}
USER = "Nastya"

_svc = None


def svc():
    global _svc
    if _svc is None:
        _svc = _sheets_client()
    return _svc


def col(n):
    """0-based column index → A1 letter."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def read_all(tabs=("Products", "Offers", "Decisions", "Brands", "Inbox", "Settings", "Sources", "Catalog")):
    res = svc().spreadsheets().values().batchGet(
        spreadsheetId=SID, ranges=[f"'{t}'" for t in tabs]).execute()["valueRanges"]
    out = {}
    for t, vr in zip(tabs, res):
        rows = vr.get("values", [])
        head = rows[0] if rows else []
        recs = []
        for i, r in enumerate(rows[1:]):
            d = dict(zip(head, r + [""] * (len(head) - len(r))))
            d["_row"] = i + 2
            recs.append(d)
        out[t] = {"head": head, "rows": recs}
    return out


def update_cells(tab, head, row, values: dict):
    """Записать несколько полей одной строки (по названиям колонок)."""
    data = [{"range": f"'{tab}'!{col(head.index(k))}{row}", "values": [[v]]} for k, v in values.items()]
    svc().spreadsheets().values().batchUpdate(
        spreadsheetId=SID, body={"valueInputOption": "RAW", "data": data}).execute()


def append_rows(tab, head, recs):
    rows = [[r.get(h, "") for h in head] for r in recs]
    svc().spreadsheets().values().append(
        spreadsheetId=SID, range=f"'{tab}'!A1", valueInputOption="RAW",
        insertDataOption="INSERT_ROWS", body={"values": rows}).execute()


def reason_triggers():
    """{new_reason: reopen_trigger} из reason_map.csv + причины, которые ставятся только в приложении."""
    m = {}
    with open(os.path.join(HERE, "reason_map.csv"), newline="") as f:
        for r in csv.DictReader(f):
            if r["new_reason"] != "(by decision)":
                m.setdefault(r["new_reason"], r["reopen_trigger"])
    m.setdefault("Other", "—")
    return m


def today():
    return dt.date.today().isoformat()


def ensure_cols(tab, head, cols):
    """Добавить недостающие колонки в конец листа; вернуть новый заголовок."""
    missing = [c for c in cols if c not in head]
    if not missing:
        return head
    meta = svc().spreadsheets().get(spreadsheetId=SID, fields="sheets.properties").execute()
    sh = next(x["properties"] for x in meta["sheets"] if x["properties"]["title"] == tab)
    need = len(head) + len(missing) - sh["gridProperties"]["columnCount"]
    if need > 0:
        svc().spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": [{"appendDimension": {
            "sheetId": sh["sheetId"], "dimension": "COLUMNS", "length": need}}]}).execute()
    svc().spreadsheets().values().update(spreadsheetId=SID, range=f"'{tab}'!{col(len(head))}1",
                                         valueInputOption="RAW", body={"values": [missing]}).execute()
    return head + missing


def live_set(catalog_rows):
    return {(r["EAN"], r["Market"]) for r in catalog_rows if r.get("State") in ("Active", "Out of Stock") and r.get("EAN")}


def is_backlog(x, live):
    """Approved before the migration but not live on that market in SellerSnap."""
    return (x.get("Show") == "yes" and x.get("Decision") == "yes" and x.get("Decided") == "before migration"
            and (x["EAN"], x["Market"]) not in live)
