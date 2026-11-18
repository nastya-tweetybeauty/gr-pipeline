#!/usr/bin/env python3
"""
Полный каталог SellerSnap по всем нашим магазинам → вкладка «Catalog» в GR Pipeline.

Зачем: GR-таблица = решения, а не реальные листинги. Ежедневные выгрузки seller-snap-reports
содержат ~100–150 строк на маркет (это не весь каталог). Здесь — все листинги всех аккаунтов
маркета (US = BW1…BW6 и т.д.) в статусах Active / Out of Stock / Inactive (Archived не берём —
это удалённые из Seller Central).

Как: Playwright-сессия SellerSnap из tag_automation (логин/магазины — ss_tag_import.py),
внутренний endpoint /listings/data/{shop} (REST API для нас закрыт). Работает только на маке.

Usage:
    python3 sources/ss_catalog.py              # все 4 маркета → вкладка Catalog (перезапись)
    python3 sources/ss_catalog.py --dry-run    # только посчитать
"""
import argparse
import collections
import datetime as dt
import html
import os
import re
import sys
import unicodedata
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
WS = os.path.dirname(os.path.dirname(PIPE))
sys.path.insert(0, PIPE)
sys.path.insert(0, os.path.join(WS, "tag_automation"))

import gr_data as g                                         # noqa: E402
import ss_tag_import as S                                   # noqa: E402
from playwright.sync_api import sync_playwright             # noqa: E402

SHOPS = {"US": 10, "CA": 11, "UK": 41, "AU": 2886}
STATES = "active_amazon,out_of_stock,inactive"   # endpoint это игнорирует — Archived отсекаем при разборе
TAB = "Catalog"
HEAD = ["Market", "SKU", "EAN", "ASIN", "Brand", "Title", "State", "Qty", "Fulfillment", "Updated"]

RE_ASIN = re.compile(r"ASIN:\s*<a[^>]*>([A-Z0-9]{10})</a>")
RE_SKU = re.compile(r"SKU:\s*([^<]+)</span>")
RE_TITLE = re.compile(r"<div class='product-title'><b>(.*?)</b></div>", re.S)
RE_STATE = re.compile(r'<span class="label label-[a-z-]+ label-xs"[^>]*>([^<]+)</span>')
RE_FUL = re.compile(r"fulfillment-label[^>]*>([^<]+)</span>")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def ean_from_sku(sku):
    m = re.search(r"(\d{8,14})$", sku.strip())
    if not m:
        return ""
    d = m.group(1)
    return d.zfill(13) if 8 < len(d) < 13 else d


def fetch_shop(page, shop_id):
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=29)
    f = lambda d: d.strftime("%m/%d/%Y")
    out, pg, last = [], 1, 1
    while pg <= last:
        url = (f"https://app.sellersnap.io/listings/data/{shop_id}?page={pg}&per_page=500"
               f"&listing_state={STATES}&repricing_state=all&fulfillment_channel=all"
               "&buybox_availability=all&buybox_eligibility=all&inventory=all&search="
               f"&startDate={f(start)}&endDate={f(end)}"
               f"&startDatePrev={f(start - dt.timedelta(days=30))}&endDatePrev={f(start - dt.timedelta(days=1))}")
        r = page.request.get(url, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=180000)
        if r.status != 200:
            raise RuntimeError(f"SellerSnap shop {shop_id} page {pg}: HTTP {r.status}")
        d = r.json()
        last = d.get("last_page") or 1
        out += d.get("data") or []
        pg += 1
    return out


def parse(row):
    t = row.get("title") or ""
    a, s, ti, st, fu = RE_ASIN.search(t), RE_SKU.search(t), RE_TITLE.search(t), RE_STATE.search(t), RE_FUL.search(t)
    return {
        "ASIN": a.group(1) if a else "",
        "SKU": html.unescape(s.group(1)).strip() if s else "",
        "Title": html.unescape(re.sub(r"<[^>]+>", "", ti.group(1))).strip() if ti else "",
        "State": st.group(1).strip() if st else "",
        "Fulfillment": fu.group(1).strip() if fu else "",
        "Qty": row.get("quantity") if isinstance(row.get("quantity"), (int, float)) else "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--visible", action="store_true")
    a = ap.parse_args()

    data = g.read_all(("Products", "Brands"))
    brand_by_ean = {p["EAN"]: p["Brand"] for p in data["Products"]["rows"] if p["Brand"]}
    brands = sorted({b["Brand"] for b in data["Brands"]["rows"] if b["Brand"]} | set(brand_by_ean.values()),
                    key=lambda b: -len(b))
    brand_keys = [(f" {norm(b)} ", b) for b in brands if len(norm(b)) >= 3]

    def brand_of(ean, title):
        if ean in brand_by_ean:
            return brand_by_ean[ean]
        t = f" {norm(title)} "
        return next((b for k, b in brand_keys if k in t), "")

    today = g.today()
    rows = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(S.PROFILE_DIR), headless=not a.visible)
        page = ctx.new_page()
        S.login_sellersnap(page, S.USERNAME, S.PASSWORD)
        for mk, shop in SHOPS.items():
            S.switch_market(page, shop)
            raw = fetch_shop(page, shop)
            n0 = len(rows)
            for r in raw:
                x = parse(r)
                if x["State"] == "Archived":      # удалён из Seller Central; фильтр listing_state endpoint игнорирует
                    continue
                ean = ean_from_sku(x["SKU"])
                rows.append({"Market": mk, "SKU": x["SKU"], "EAN": ean, "ASIN": x["ASIN"],
                             "Brand": brand_of(ean, x["Title"]), "Title": x["Title"][:200], "State": x["State"],
                             "Qty": x["Qty"], "Fulfillment": x["Fulfillment"], "Updated": today})
            st = collections.Counter(r["State"] for r in rows[n0:])
            print(f"[{mk}] listings: {len(rows) - n0} · " + ", ".join(f"{k} {v}" for k, v in st.most_common())
                  + f" · with brand: {sum(1 for r in rows[n0:] if r['Brand'])}")
        ctx.close()

    if a.dry_run:
        return
    svc = g.svc()
    meta = svc.spreadsheets().get(spreadsheetId=g.SID, fields="sheets.properties.title").execute()
    if TAB not in [s["properties"]["title"] for s in meta["sheets"]]:
        svc.spreadsheets().batchUpdate(spreadsheetId=g.SID, body={"requests": [{"addSheet": {"properties": {
            "title": TAB, "gridProperties": {"rowCount": len(rows) + 100, "columnCount": len(HEAD), "frozenRowCount": 1}}}}]}).execute()
    else:
        svc.spreadsheets().values().clear(spreadsheetId=g.SID, range=f"'{TAB}'").execute()
    values = [HEAD] + [[r[h] for h in HEAD] for r in rows]
    for i in range(0, len(values), 5000):
        svc.spreadsheets().values().update(
            spreadsheetId=g.SID, range=f"'{TAB}'!A{i + 1}", valueInputOption="RAW",
            body={"values": values[i:i + 5000]}).execute()
    print(f"written to {TAB}: {len(rows)} rows")


if __name__ == "__main__":
    main()
