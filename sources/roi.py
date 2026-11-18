#!/usr/bin/env python3
"""
ROI + Max Buy Price for decisions, using Vika's calculation engine
(tweety-beauty-ai/ub-mto-analyzer core.py — same code as her Products / MTO Analyzer; vendored in vendor/).

For every US / CA / UK decision that is pending or due for recheck (or --ean …) and has
  - a Buy Box from Keepa (column «Buy Box», filled by sources/recheck_keepa.py) and
  - a best price from a supplier we can buy from (Offers, «Можно закупать» = yes)
writes into Decisions: ROI · Min ROI · Max Buy Price · ROI calc.
Max Buy Price = highest purchase price (EUR excl VAT) that still meets the market minimum
(core.ROI_THRESHOLDS: US 17 · CA 20 · UK 15), solved from the same calc_* functions.

Not covered yet (asked Vika 2026-11-11): AU (no calc_au in core.py); per-EAN FBA fee and real
freight/customs (core.py uses its flat defaults here).

Usage: python3 sources/roi.py [--scope due|pending|both|all-yes] [--ean EAN ...] [--dry-run]
"""
import argparse
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
sys.path.insert(0, PIPE)
sys.path.insert(0, os.path.join(PIPE, "engine"))   # copy of Vika's ub-mto-analyzer/core.py
import gr_data as g      # noqa: E402
import core              # noqa: E402  (Vika's engine)

# AU is not in core.py (it works on Keepa data). Same formula as Vika's ub-cost-calculator calc_au,
# with its defaults (Aug 2026): shipping 10.40 € · labour 2.35 € · tariff 5% on goods+shipping · GST 10% stripped
# from the sell price (import GST is reclaimable, not in COGS) · referral 13% · FBA 7.30 AUD · DSF 3% on referral+FBA.
AU_P = {"au_ship": 10.40, "au_lab": 2.35, "au_fba": 7.30, "au_ref": 13.0, "au_gst": 10.0, "au_tar": 5.0}


def calc_au(p_eur, s_aud, P, is_dg=True):
    rate = P["eur_aud"]
    p = p_eur * rate
    ship = (P["au_ship"] + P.get("au_customs", 0.0)) * rate
    lab = P["au_lab"] * rate
    tariff = (p + ship) * P["au_tar"] / 100
    cogs = p + ship + lab + tariff
    s = s_aud / (1 + P["au_gst"] / 100)
    ref = s * P["au_ref"] / 100
    dsf = (ref + P["au_fba"]) * P.get("dsf", 0.0) / 100
    ppu = s - cogs - ref - P["au_fba"] - dsf
    return ppu / cogs if cogs > 0 else 0


def eur_aud():
    try:
        import json as _j, urllib.request as _u
        with _u.urlopen("https://api.frankfurter.dev/v1/latest?base=EUR&symbols=AUD", timeout=8) as r:
            return round(_j.loads(r.read())["rates"]["AUD"], 4)
    except Exception:
        return 1.6052                  # calculator fallback (ECB 9 Oct 2026)


CALC = {"US": core.calc_us, "CA": core.calc_ca, "UK": core.calc_uk, "AU": calc_au}
COLS = ["ROI", "Min ROI", "Max Buy Price", "ROI calc"]


def money(s):
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(s or ""))
    return float(m.group(1).replace(",", ".")) if m else None


def max_buy_price(calc, sell, P, thr):
    """Highest p (EUR) with calc(p, sell) ≥ thr. ROI falls as p grows → bisection."""
    if calc(0.01, sell, P) < thr:
        return None
    lo, hi = 0.01, 5000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if calc(mid, sell, P) >= thr else (lo, mid)
    return int(lo * 100) / 100          # floor to the cent so buying at it meets the target


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="both", choices=["due", "pending", "both", "all-yes", "backlog"])
    ap.add_argument("--ean", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    today = g.today()

    d = g.read_all(("Decisions", "Offers", "Catalog"))
    live = g.live_set(d["Catalog"]["rows"])
    head, rows = d["Decisions"]["head"], d["Decisions"]["rows"]
    best = {}
    for o in d["Offers"]["rows"]:
        if o["Can buy"] != "yes":
            continue
        p = money(o["Price, € excl VAT"])
        if p and (o["EAN"] not in best or p < best[o["EAN"]][0]):
            best[o["EAN"]] = (p, o["Supplier"])

    def wanted(x):
        if x["Market"] not in CALC or x.get("Show") != "yes":
            return False
        if a.ean:
            return x["EAN"] in a.ean
        due = x["Decision"] == "recheck" and x["Recheck on"] and x["Recheck on"] <= today
        pend = not x["Decision"]
        yes = x["Decision"] == "yes"
        return {"due": due, "pending": pend, "both": due or pend, "all-yes": yes,
                "backlog": g.is_backlog(x, live)}[a.scope]

    rates = core.fetch_live_rates() or {}
    P = {**core.DEFAULT_PARAMS, **AU_P, **{k: v for k, v in rates.items() if k != "date"}, "eur_aud": eur_aud()}
    stamp = f"{today} · core.py · ECB {rates.get('date', 'fallback')}"

    updates, n_ok, n_skip = [], 0, 0
    for x in filter(wanted, rows):
        sell, offer = money(x.get("Buy Box")), best.get(x["EAN"])
        thr = core.roi_bar(x["Market"]) / 100
        if not sell:
            n_skip += 1
            continue
        calc = CALC[x["Market"]]
        mbp = max_buy_price(calc, sell, P, thr)
        upd = {"Min ROI": f"{thr:.0%}", "Max Buy Price": f"€{mbp:.2f}" if mbp else "not reachable", "ROI calc": stamp,
               "ROI": f"{calc(offer[0], sell, P):.1%}" if offer else ""}
        updates.append((x, upd))
        n_ok += 1
    print(f"ROI computed: {n_ok} · skipped (no Buy Box): {n_skip} · rates {rates.get('date', 'fallback')}")
    for x, u in updates[:10]:
        print(f"  {x['Market']} {x['EAN']} BB {x.get('Buy Box')} best {best.get(x['EAN'])} → {u['ROI'] or '—'} / MBP {u['Max Buy Price']}")
    if a.dry_run or not updates:
        return
    head = g.ensure_cols("Decisions", head, COLS)
    data = [{"range": f"'Decisions'!{g.col(head.index(k))}{x['_row']}", "values": [[v]]} for x, u in updates for k, v in u.items()]
    for i in range(0, len(data), 500):
        g.svc().spreadsheets().values().batchUpdate(spreadsheetId=g.SID, body={"valueInputOption": "RAW", "data": data[i:i + 500]}).execute()
    print(f"written: {len(updates)}")


if __name__ == "__main__":
    main()
