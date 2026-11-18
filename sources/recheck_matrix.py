#!/usr/bin/env python3
"""
Закрывает recheck-решения, которые больше не нужно перепроверять вручную:
  - бренд hard gated (brand selling approval) или не approved для каталога на маркете → no / Gated
    (вернётся сам, когда бренд одобрят: «Что вернёт в работу» = бренд одобрен в матрице);
  - товар уже живой на маркете по вкладке Catalog (SellerSnap) → yes / Already in catalog.
Старое решение/причина сохраняются в «Комментарий».

Usage: python3 sources/recheck_matrix.py [--apply]     (без --apply только считает)
"""
import argparse, collections, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import gr_data as g                                                   # noqa: E402
from keepa_new_asins import load_selling, brand_check, LIVE_STATES    # noqa: E402
from gcp import load_catalog_gating                               # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
svc = g.svc()
selling, matrix = load_selling(svc), load_catalog_gating(svc)
d = g.read_all(("Decisions", "Products", "Catalog"))
D, head = d["Decisions"]["rows"], d["Decisions"]["head"]
brand_by_ean = {p["EAN"]: p["Brand"] for p in d["Products"]["rows"]}
live = {(r["EAN"], r["Market"]) for r in d["Catalog"]["rows"] if r["State"] in LIVE_STATES and r["EAN"]}
today = g.today()

recheck = [x for x in D if x["Show"] == "yes" and x["Decision"] == "recheck" and x["Market"] in g.MARKETS]
plan, stats = [], collections.Counter()
for x in recheck:
    due = bool(x["Recheck on"]) and x["Recheck on"] <= today
    brand = brand_by_ean.get(x["EAN"], "")
    ok, why = brand_check(brand, x["Market"], selling, matrix) if brand else (True, "")
    old = f"{today}: auto-closed from recheck (was {x['Reason']}"
    if (x["EAN"], x["Market"]) in live:
        plan.append((x, {"Decision": "yes", "Reason": "Already in catalog", "Reopen trigger": "—", "Recheck on": "",
                         "Comment": f"{old}); live on {x['Market']} in SellerSnap"}))
        stats[("already live", due)] += 1
    elif x["Reason"] == "JPG: catalog only":          # JPG (GS1): можем только добавлять в каталог — решает Настя
        stats[("JPG catalog only — left for Nastya", due)] += 1
    elif brand and not ok and why != "brand not in matrix":
        plan.append((x, {"Decision": "no", "Reason": "Gated", "Reopen trigger": "brand approved in matrix", "Recheck on": "",
                         "Comment": f"{old}); {brand}: {why}"}))
        stats[("gated: " + why, due)] += 1
    else:
        stats[("stays", due)] += 1

print(f"recheck US/CA/UK/AU: {len(recheck)} (due now: {sum(1 for x in recheck if x['Recheck on'] and x['Recheck on'] <= today)})")
for (k, due), v in sorted(stats.items(), key=lambda kv: (-kv[1])):
    print(f"  {'[due]' if due else '[later]'} {k}: {v}")
by_brand = collections.Counter((brand_by_ean.get(x['EAN'], '?'), x['Market']) for x, u in plan if u["Decision"] == "no")
print("  gated by brand:", ", ".join(f"{b} {m} ×{n}" for (b, m), n in by_brand.most_common(12)))
if a.apply:
    for x, upd in plan:
        upd.update({"Decided": today, "By": "auto (matrix)"})
    data = [{"range": f"'Decisions'!{g.col(head.index(k))}{x['_row']}", "values": [[v]]} for x, upd in plan for k, v in upd.items()]
    for i in range(0, len(data), 500):
        svc.spreadsheets().values().batchUpdate(spreadsheetId=g.SID, body={"valueInputOption": "RAW", "data": data[i:i + 500]}).execute()
    print(f"written: {len(plan)} decisions")
