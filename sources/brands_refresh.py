#!/usr/bin/env python3
"""
Вкладка Brands ← Brand Approval Matrix (Чад), обе вкладки:
  «brand selling approval» (главная: hard gated = продавать нельзя) +
  «catalog authorization approval» (ok / пусто = можно добавлять в каталог).

В колонках US/CA/UK/AU итоговый статус: hard-gated-статус selling, если он есть, иначе catalog-статус,
иначе selling-статус («ok»), иначе «—». «нет в матрице» — бренда нет ни в одной вкладке.
Колонки «Следить за новинками» и «Комментарий» сохраняются. Тот же расчёт делает Apps Script (refreshBrands_).

Usage: python3 sources/brands_refresh.py
"""
import collections
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import gr_data as g                                          # noqa: E402
from keepa_new_asins import load_selling, selling_blocked    # noqa: E402
from gcp import load_catalog_gating, _norm_brand            # noqa: E402

MK = ["US", "CA", "UK", "AU"]
HEAD = ["Brand", "US", "CA", "UK", "AU", "Products in catalog", "Watch new releases", "Comment"]


def status(b, mk, selling, catalog):
    s = (selling.get(b) or {}).get(mk, "")
    c = (catalog.get(b) or {}).get(f"AMZ {mk}", "")
    if b not in selling and b not in catalog:
        return "not in matrix"
    if selling_blocked(s):
        return s
    return c or s or "—"


def main():
    svc = g.svc()
    selling, catalog = load_selling(svc), load_catalog_gating(svc)
    d = g.read_all(("Products", "Catalog", "Brands"))
    old = {_norm_brand(r["Brand"]): r for r in d["Brands"]["rows"]}
    counts = collections.Counter(p["Brand"] for p in d["Products"]["rows"] if p["Brand"])
    for r in d["Catalog"]["rows"]:
        if r.get("Brand") and r["Brand"] not in counts:
            counts[r["Brand"]] += 0
    rows = [HEAD]
    for b, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        k = _norm_brand(b)
        o = old.get(k, {})
        rows.append([b] + [status(k, m, selling, catalog) for m in MK]
                    + [n, o.get("Watch new releases", ""), o.get("Comment", "")])
    svc.spreadsheets().values().clear(spreadsheetId=g.SID, range="'Brands'").execute()
    svc.spreadsheets().values().update(spreadsheetId=g.SID, range="'Brands'!A1", valueInputOption="RAW",
                                       body={"values": rows}).execute()
    hg = [(r[0], m) for r in rows[1:] for i, m in enumerate(MK) if "gated" in str(r[1 + i]) and not str(r[1 + i]).startswith("ok")]
    print(f"Brands: {len(rows) - 1}; hard gated / gated: {len(hg)}")
    print("  e.g.:", ", ".join(f"{b} {m}" for b, m in hg[:15]))


if __name__ == "__main__":
    main()
