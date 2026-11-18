#!/usr/bin/env python3
"""
AU prices for decisions — Vika's MTO method (Keepa has no amazon.com.au):
  ASIN from the decision / catalog, or via Keepa US/UK/CA by EAN (ASINs are usually shared) →
  Apify junglee/free-amazon-product-scraper on amazon.com.au/dp/<ASIN> → Buy Box, BSR, seller, «bought in past month».
Writes into Decisions (same columns as Keepa): ASIN · BSR · Offers · Buy Box · Rank drops 30d (= bought/month if shown) · Keepa checked.
Cost: ~$0.008 per product (Apify pay-per-result, Silver tier, Nov 2026). Rows checked in the last 7 days are skipped.

Usage: python3 sources/au_prices.py [--scope due|pending|backlog|all] [--limit N] [--dry-run] [--raw]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
WS = os.path.dirname(os.path.dirname(PIPE))
sys.path.insert(0, PIPE)
sys.path.insert(0, HERE)
import gr_data as g                                                   # noqa: E402
from keepa_new_asins import keepa, keepa_key, load_selling, brand_check   # noqa: E402
from gcp import load_catalog_gating                               # noqa: E402

ACTOR = "junglee~free-amazon-product-scraper"
BATCH = 20
RECHECK_DAYS = 7


def apify_token():
    from gcp import secret
    v = secret("APIFY_TOKEN", "APIFY_API_TOKEN")
    if v and v.isascii() and v.startswith("apify_api_"):
        return v
    for f in (os.path.join(PIPE, ".env"), os.path.join(WS, "product-research", ".env"), os.path.join(WS, "perfume_research", ".env")):
        if os.path.exists(f):
            for line in open(f):
                if line.startswith(("APIFY_TOKEN=", "APIFY_API_TOKEN=")):
                    v = line.split("=", 1)[1].strip()
                    if v.isascii() and v.startswith("apify_api_"):
                        return v
    return None


def apify_run(urls, token):
    body = json.dumps({"categoryUrls": [{"url": u} for u in urls], "maxItemsPerStartUrl": 1,
                       "scrapeProductDetails": True, "maxProductVariantsAsSeparateResults": 0}).encode()
    req = urllib.request.Request(
        f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={token}&timeout=280",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=320) as r:
        return json.loads(r.read())


def money(v):
    if isinstance(v, dict):
        v = v.get("value")
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(v or ""))
    return float(m.group(1).replace(",", "")) if m else None


def parse(item):
    in_stock = item.get("inStock")
    price = money(item.get("price")) if in_stock else None
    ranks = [r for r in (item.get("bestsellerRanks") or []) if isinstance(r, dict) and r.get("rank")]
    bsr = int(ranks[0]["rank"]) if ranks else None          # first = root category (Beauty)
    bought = item.get("monthlyPurchaseVolume") or None
    seller = (item.get("seller") or {}).get("name") if isinstance(item.get("seller"), dict) else item.get("seller")
    return {"price": price, "bsr": bsr, "bought": bought, "seller": seller, "in_stock": in_stock,
            "asin": item.get("asin") or item.get("originalAsin"), "title": item.get("title")}


def to_update(pr, asin, today):
    if pr and pr["price"]:
        note = f"{today}: Apify AU · seller {pr['seller'] or '?'}" + (" · AMAZON SELLS" if (pr["seller"] or "").startswith("Amazon") else "")
        return {"ASIN": asin, "BSR": pr["bsr"] or "", "Offers": "", "Buy Box": f"A${pr['price']:.2f}",
                "Rank drops 30d": pr["bought"] or "", "Keepa checked": note}
    if pr:
        return {"ASIN": asin, "BSR": pr["bsr"] or "", "Buy Box": "", "Keepa checked": f"{today}: Apify AU · out of stock / no offer"}
    return {"Keepa checked": f"{today}: no listing on Amazon AU"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="all", choices=["due", "pending", "backlog", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--raw", action="store_true", help="print raw keys of the first Apify item")
    ap.add_argument("--print-asins", action="store_true", help="stop after finding ASINs (no Apify)")
    a = ap.parse_args()
    today = g.today()
    svc = g.svc()
    selling, matrix = load_selling(svc), load_catalog_gating(svc)
    d = g.read_all(("Decisions", "Catalog", "Products"))
    head, rows = d["Decisions"]["head"], d["Decisions"]["rows"]
    live = g.live_set(d["Catalog"]["rows"])
    prods = {p["EAN"]: p for p in d["Products"]["rows"]}
    cat_asin = {r["EAN"]: r["ASIN"] for r in d["Catalog"]["rows"] if r.get("EAN") and r.get("ASIN")}

    def recent(x):
        k = (x.get("Keepa checked") or "")[:10]
        try:
            return (dt.date.fromisoformat(today) - dt.date.fromisoformat(k)).days < RECHECK_DAYS
        except ValueError:
            return False

    def wanted(x):
        if x["Market"] != "AU" or x.get("Show") != "yes" or recent(x):
            return False
        b = prods.get(x["EAN"], {}).get("Brand", "")
        ok, why = brand_check(b, "AU", selling, matrix) if b else (True, "")
        if not ok and why != "brand not in matrix":
            return False
        due = x["Decision"] == "recheck" and x["Recheck on"] and x["Recheck on"] <= today
        pend = not x["Decision"]
        bl = g.is_backlog(x, live) and x.get("Listing status") not in ("Taken", "Listing created")
        return {"due": due, "pending": pend, "backlog": bl, "all": due or pend or bl}[a.scope]

    todo = [x for x in rows if wanted(x)]
    if a.limit:
        todo = todo[:a.limit]
    print(f"AU to check: {len(todo)}")
    if not todo:
        return

    # 1) ASIN
    for x in todo:
        p = prods.get(x["EAN"], {})
        x["_asin"] = x.get("ASIN") or cat_asin.get(x["EAN"]) or p.get("ASIN") or ""
    need = [x for x in todo if not x["_asin"]]
    if need:
        key = keepa_key()
        for dom in (1, 2, 6):
            left = [x for x in need if not x["_asin"]]
            for i in range(0, len(left), 100):
                r = keepa("product", {"domain": dom, "code": ",".join(x["EAN"] for x in left[i:i + 100]), "history": 0}, key)
                by_ean = {}
                for pr in r.get("products") or []:
                    for e in pr.get("eanList") or []:
                        by_ean.setdefault(e.zfill(13) if 8 < len(e) < 13 else e, pr["asin"])
                for x in left[i:i + 100]:
                    x["_asin"] = by_ean.get(x["EAN"], "")
    no_asin = [x for x in todo if not x["_asin"]]
    print(f"ASIN found: {len(todo) - len(no_asin)} · none: {len(no_asin)}")
    if a.print_asins:
        for x in todo:
            print("ASIN", x["EAN"], x["_asin"], prods.get(x["EAN"], {}).get("Product", "")[:50])
        return

    # 2) Apify amazon.com.au
    token = apify_token()
    if not token:
        print("AU skipped: no Apify token — add APIFY_TOKEN=apify_api_… to gr-pipeline/.env")
        return
    res = {}
    with_asin = [x for x in todo if x["_asin"]]
    for i in range(0, len(with_asin), BATCH):
        batch = with_asin[i:i + BATCH]
        items = apify_run([f"https://www.amazon.com.au/dp/{x['_asin']}" for x in batch], token)
        if a.raw and items:
            print("raw keys:", sorted(items[0].keys()))
            print({k: items[0].get(k) for k in ("asin", "price", "listPrice", "inStock", "seller", "bestsellerRanks",
                                                 "monthlyPurchaseVolume", "title")})
        for it in items:
            pr = parse(it)
            if pr["asin"]:
                res[pr["asin"]] = pr
        print(f"  apify {i + len(batch)}/{len(with_asin)} · items {len(items)}")

    updates = [(x, to_update(res.get(x["_asin"]) if x["_asin"] else None, x["_asin"], today)) for x in todo]
    print(f"with price: {sum(1 for _, u in updates if 'Buy Box' in u)} · no price: {sum(1 for _, u in updates if 'out of stock' in u.get('Keepa checked', ''))} · "
          f"no listing: {sum(1 for _, u in updates if 'no listing' in u.get('Keepa checked', ''))}")
    for x, u in updates[:10]:
        print(f"  {x['EAN']} {prods.get(x['EAN'], {}).get('Product', '')[:40]} → {u}")
    if a.dry_run:
        return
    head = g.ensure_cols("Decisions", head, ["BSR", "Offers", "Buy Box", "Rank drops 30d", "Keepa checked"])
    data = [{"range": f"'Decisions'!{g.col(head.index(k))}{x['_row']}", "values": [[v]]} for x, u in updates for k, v in u.items()]
    for i in range(0, len(data), 500):
        svc.spreadsheets().values().batchUpdate(spreadsheetId=g.SID, body={"valueInputOption": "RAW", "data": data[i:i + 500]}).execute()
    print(f"written: {len(updates)}")


if __name__ == "__main__":
    main()
