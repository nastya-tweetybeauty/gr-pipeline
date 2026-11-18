#!/usr/bin/env python3
"""
Keepa figures for decisions that need a look: due rechecks + pending (no decision yet), US / CA / UK.
Finds the listing by ASIN (or by EAN when there is no ASIN) and writes into Decisions:
  ASIN (if found) · BSR · Offers · Buy Box · Rank drops 30d · Keepa checked
Keepa has no amazon.com.au → AU rows are skipped.

Usage: python3 sources/recheck_keepa.py [--scope due|pending|both] [--dry-run]
"""
import argparse
import collections
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import gr_data as g                              # noqa: E402
from keepa_new_asins import keepa, keepa_key     # noqa: E402

DOMAIN = {"US": 1, "UK": 2, "CA": 6}
CUR = {"US": "$", "UK": "£", "CA": "C$"}
NEW_COLS = ["BSR", "Offers", "Buy Box", "Rank drops 30d", "Keepa checked"]


def metrics(p, mk):
    cur = (p.get("stats") or {}).get("current") or []
    v = lambda i: cur[i] if len(cur) > i and cur[i] is not None and cur[i] > 0 else None
    bb = v(18) or v(1)
    drops = (p.get("stats") or {}).get("salesRankDrops30")
    return {"BSR": v(3) or "", "Offers": v(11) or "", "Buy Box": f"{CUR[mk]}{bb / 100:.2f}" if bb else "",
            "Rank drops 30d": drops if drops is not None and drops >= 0 else ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="both", choices=["due", "pending", "both", "backlog"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ean", nargs="*", help="only these EANs (any decision state)")
    a = ap.parse_args()
    today = g.today()
    svc = g.svc()
    dd = g.read_all(("Decisions", "Catalog"))
    d = dd["Decisions"]
    head, rows = d["head"], d["rows"]
    live = g.live_set(dd["Catalog"]["rows"])

    def wanted(x):
        if x["Show"] != "yes" or x["Market"] not in DOMAIN:
            return False
        if a.ean:
            return x["EAN"] in a.ean
        if a.scope == "backlog":
            return g.is_backlog(x, live)
        due = x["Decision"] == "recheck" and x["Recheck on"] and x["Recheck on"] <= today
        pend = not x["Decision"]
        return (a.scope in ("due", "both") and due) or (a.scope in ("pending", "both") and pend)

    todo = [x for x in rows if wanted(x)]
    print(f"to check: {len(todo)} ({dict(collections.Counter(x['Market'] for x in todo))})")
    key = keepa_key()
    found, updates = collections.Counter(), []
    for mk, dom in DOMAIN.items():
        mrows = [x for x in todo if x["Market"] == mk]
        if not mrows:
            continue
        by_asin = [x for x in mrows if x["ASIN"]]
        by_ean = [x for x in mrows if not x["ASIN"]]
        prods = {}
        for i in range(0, len(by_asin), 100):
            r = keepa("product", {"domain": dom, "asin": ",".join(x["ASIN"] for x in by_asin[i:i + 100]), "stats": 30}, key)
            for p in r.get("products") or []:
                prods[("asin", p["asin"])] = p
        for i in range(0, len(by_ean), 100):
            r = keepa("product", {"domain": dom, "code": ",".join(x["EAN"] for x in by_ean[i:i + 100]), "stats": 30}, key)
            for p in r.get("products") or []:
                for e in p.get("eanList") or []:
                    e = e.zfill(13) if 8 < len(e) < 13 else e
                    best = prods.get(("ean", e))
                    bsr = lambda q: ((q.get("stats") or {}).get("current") or [0] * 4)[3] or 10 ** 9
                    if not best or 0 < bsr(p) < bsr(best):
                        prods[("ean", e)] = p
        for x in mrows:
            p = prods.get(("asin", x["ASIN"])) if x["ASIN"] else prods.get(("ean", x["EAN"]))
            if p and p.get("title"):
                upd = metrics(p, mk)
                upd["ASIN"] = x["ASIN"] or p["asin"]
                upd["Keepa checked"] = today
                found["listing found"] += 1
            else:
                upd = {"Keepa checked": f"{today}: no listing on Amazon {mk}"}
                found["no listing"] += 1
            updates.append((x, upd))
    print(dict(found))
    for x, u in updates[:12]:
        print(f"  {x['Market']} {x['EAN']} → {u}")
    if a.dry_run:
        return

    # add the new columns to Decisions if they are not there yet
    missing = [c for c in NEW_COLS if c not in head]
    if missing:
        meta = svc.spreadsheets().get(spreadsheetId=g.SID, fields="sheets.properties").execute()
        sh = next(s["properties"] for s in meta["sheets"] if s["properties"]["title"] == "Decisions")
        need = len(head) + len(missing) - sh["gridProperties"]["columnCount"]
        if need > 0:
            svc.spreadsheets().batchUpdate(spreadsheetId=g.SID, body={"requests": [{"appendDimension": {
                "sheetId": sh["sheetId"], "dimension": "COLUMNS", "length": need}}]}).execute()
        start = len(head)
        svc.spreadsheets().values().update(spreadsheetId=g.SID, range=f"'Decisions'!{g.col(start)}1",
                                           valueInputOption="RAW", body={"values": [missing]}).execute()
        head = head + missing
    data = [{"range": f"'Decisions'!{g.col(head.index(k))}{x['_row']}", "values": [[v]]} for x, u in updates for k, v in u.items()]
    for i in range(0, len(data), 500):
        svc.spreadsheets().values().batchUpdate(spreadsheetId=g.SID, body={"valueInputOption": "RAW", "data": data[i:i + 500]}).execute()
    print(f"written: {len(updates)} decisions")


if __name__ == "__main__":
    main()
