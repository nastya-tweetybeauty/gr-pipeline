#!/usr/bin/env python3
"""
Источник «Keepa: новый ASIN» → вкладка Inbox в GR Pipeline.

Ищет в Keepa листинги в Fragrance (US / CA / UK), которые появились за последние N дней
и уже продаются (BSR ≤ порога), и отбирает то, что нам интересно:

  1. «Переоткрыто» — EAN уже есть в Products, а по этому маркету раньше стояло
     No listing / Foreign-market ASIN → листинг появился, товар можно вернуть в работу.
  2. «Keepa: новый ASIN» — ни ASIN, ни EAN ещё нет в Products.

В обоих случаях бренд должен быть approved в Brand Approval Matrix (Чад) на этом маркете —
approved = «ok» ИЛИ пустая клетка; gated, «ba - need approve to sell», «gs 1 issue» и бренды не из матрицы отсеиваются (решение Насти 2026-11-06).
Категория — только Fragrance (categories_include = узел Fragrance маркета, включая подкатегории).

Всё остальное (чужие бренды, gated, уже в каталоге, уже во Входящих) отсеивается — в конце
печатается, сколько и почему.

Почему BSR, а не monthlySold: Keepa отдаёт «bought in past month» только от 50 шт., у свежих
листингов его почти никогда нет (проверено 2026-11-06: за 30 дней US 20 / CA 1 / UK 3 ASIN
против 313 / 145 / 320 по BSR ≤ 150k).

Keepa не покрывает amazon.com.au — AU здесь нет.

Usage:
    python3 sources/keepa_new_asins.py                 # последние 2 дня (ежедневный запуск)
    python3 sources/keepa_new_asins.py --days 30       # первый прогон / догнать пропуски
    python3 sources/keepa_new_asins.py --dry-run       # ничего не писать, только показать
"""
import argparse
import collections
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
RESEARCH = os.path.dirname(PIPE)
sys.path.insert(0, PIPE)
from gcp import sheets_client as _sheets_client, _norm_brand, load_catalog_gating, secret  # noqa: E402

SOURCE = "Keepa: new ASIN"
REOPEN = "Reopened"
MARKETS = [  # Fragrance node per marketplace (как в keepa_fragrance_subcats.py)
    {"label": "US", "domain": 1, "cat": "11056591", "cur": "$"},
    {"label": "CA", "domain": 6, "cat": "6344431011", "cur": "C$"},
    {"label": "UK", "domain": 2, "cat": "118457031", "cur": "£"},
]
REOPEN_REASONS = {"No listing", "Foreign-market ASIN"}
INBOX_HEADER = ["Date", "Source", "Source detail", "EAN", "ASIN", "Brand", "Product", "Signal market",
                "Signal", "Auto check", "Triage", "Comment"]
MAX_PER_MARKET = 1000
# Решения Насти 2026-11-06:
EXCLUDED_BRANDS = {"victoria's secret"}   # в основном body mist, продаёт сам бренд
MIN_VOLUME_ML = 20                        # миниатюры/сэмплы отсекаем (вместе с мини-сетами)
OZ_ML = 29.5735


def max_volume_ml(title):
    """Наибольший объём из названия, мл (для набора «80ml EDP + 10ml» = 80). None — объёма в названии нет."""
    t = title.lower().replace(",", ".")
    vols = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)\s*ml\b", t)]
    vols += [float(v) * OZ_ML for v in re.findall(r"(\d+(?:\.\d+)?)\s*(?:fl\.?\s*oz|ounces?|oz)\b", t)]
    return max(vols) if vols else None


# ───────────── каталог (вкладка Catalog = живые листинги SellerSnap, см. sources/ss_catalog.py) ─────────────
LIVE_STATES = {"Active", "Out of Stock"}
STOP = set("""eau de du des la le les el parfum perfume toilette cologne spray vapo vaporisateur vaporizador natural
edp edt edc for women woman womens men man mens him her his pour homme femme unisex by the and with fl oz ounce
ounces ml new size""".split())


def _norm_txt(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def line_tokens(title, brand):
    bw = set(_norm_txt(brand).split())
    return {w for w in _norm_txt(title).split()
            if len(w) > 1 and not any(ch.isdigit() for ch in w) and w not in STOP and w not in bw}


class Catalog:
    """Что у нас реально продаётся: EAN/ASIN по маркетам + «тот же товар под другим EAN» + бренд, закрытый на маркете."""

    def __init__(self, catalog_rows, prods):
        self.live = collections.defaultdict(set)          # EAN → {маркеты, где листинг живой}
        self.live_asin = collections.defaultdict(set)     # ASIN → {маркеты}
        self.items = {}                                   # EAN → {brand, tokens, size}
        brand_mk = collections.defaultdict(collections.Counter)
        for r in catalog_rows:
            ean, mk, st = r.get("EAN", ""), r.get("Market", ""), r.get("State", "")
            if r.get("Brand"):
                brand_mk[(_norm_brand(r["Brand"]), mk)][st] += 1
            if st in LIVE_STATES:
                if ean:
                    self.live[ean].add(mk)
                if r.get("ASIN"):
                    self.live_asin[r["ASIN"]].add(mk)
            if ean:
                self._add(ean, r.get("Brand", ""), r.get("Title", ""))
        for p in prods:
            if p.get("EAN") in self.items or p.get("EAN") in self.live:
                self._add(p["EAN"], p.get("Brand", ""), p.get("Product", ""))
        # бренд «не продаём на маркете»: нет ни одного Active, и больше половины листингов Deactivated
        self.blocked = {k for k, c in brand_mk.items()
                        if c.get("Active", 0) == 0 and c.get("Deactivated", 0) * 2 > sum(c.values())}

    def _add(self, ean, brand, title):
        it = self.items.setdefault(ean, {"brand": _norm_brand(brand), "tokens": set(), "size": None})
        if brand and not it["brand"]:
            it["brand"] = _norm_brand(brand)
        it["tokens"] |= line_tokens(title, brand)
        v = max_volume_ml(title)
        if v and not it["size"]:
            it["size"] = v

    def same_product(self, brand, title, exclude=()):
        """EAN товара из каталога, который совпадает по бренду, линейке и объёму (±12%)."""
        b, toks, size = _norm_brand(brand), line_tokens(title, brand), max_volume_ml(title)
        if not toks:
            return None
        for ean, it in self.items.items():
            if ean in exclude or it["brand"] != b or not it["tokens"]:
                continue
            if size and it["size"] and abs(size - it["size"]) / max(size, it["size"]) > 0.12:
                continue
            # одно название целиком входит в другое; «лишнее» слово с обеих сторон (Donna vs Uomo) = другой товар
            if toks <= it["tokens"] or it["tokens"] <= toks:
                return ean
        return None


# ───────────── Brand Approval Matrix (Чад): две вкладки ─────────────
SELLING_TAB = "brand selling approval"       # главная: можем ли продавать («Hard Gated» = нет)


def load_selling(svc):
    from gcp import BRAND_MATRIX_ID
    rows = svc.spreadsheets().values().get(spreadsheetId=BRAND_MATRIX_ID, range=f"'{SELLING_TAB}'!A2:E").execute().get("values", [])
    out = {}
    for r in rows:
        if r and r[0].strip():
            r = r + [""] * 5
            out[_norm_brand(r[0])] = {m: r[1 + i].strip().lower() for i, m in enumerate(["US", "CA", "UK", "AU"])}
    return out


def selling_blocked(status):
    """«hard gated», «hard gated for fragrances/ ungated for …» → нельзя; «ok», «ok/ partially gated», пусто, заметки → можно."""
    s = (status or "").strip().lower()
    return "gated" in s and not s.startswith("ok") and not s.startswith("ungated")


def brand_check(brand, mk, selling, matrix):
    """(можно?, причина отказа). Решение Насти 2026-11-11: selling-вкладка главная; catalog-вкладка — ok или пусто."""
    b = _norm_brand(brand)
    sell = selling.get(b)
    cat = matrix.get(b)
    if sell is None and cat is None:
        return False, "brand not in matrix"
    if sell is not None and selling_blocked(sell.get(mk)):
        return False, f"brand hard gated (selling) in {mk}"
    st = (cat or {}).get(f"AMZ {mk}", "")
    if st not in ("ok", ""):
        return False, f"brand not approved for catalog in {mk} ({st})"
    return True, ""


def is_mini(title):
    v = max_volume_ml(title)
    if v is not None:
        return v < MIN_VOLUME_ML
    return bool(re.search(r"\b(mini|miniature|sample|vial|travel size)\b", title.lower()))


# ───────────── Keepa ─────────────
def keepa_key():
    k = secret("KEEPA_API_KEY")
    if k:
        return k
    raise RuntimeError("KEEPA_API_KEY not found (env or product-research/.env)")


def keepa(path, params, key):
    params = dict(params, key=key)
    url = f"https://api.keepa.com/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                raw = gzip.decompress(raw) if r.info().get("Content-Encoding") == "gzip" else raw
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("  Keepa 429 — waiting 60 s for tokens to refill…")
                time.sleep(60)
                continue
            raise
        data = json.loads(raw)
        if data.get("error"):
            raise RuntimeError(f"Keepa {path}: {data['error']}")
        return data
    raise RuntimeError(f"Keepa {path}: timed out waiting for tokens")


def keepa_minutes(days_ago):
    return int(time.time() / 60) - 21564000 - days_ago * 1440


def from_keepa_minutes(m):
    return dt.datetime.utcfromtimestamp((m + 21564000) * 60).date() if m and m > 0 else None


def new_asins(m, days, max_bsr, key):
    asins, page = [], 0
    while len(asins) < MAX_PER_MARKET:
        sel = {"categories_include": [m["cat"]], "trackingSince_gte": keepa_minutes(days),
               "current_SALES_gte": 1, "current_SALES_lte": max_bsr,
               "sort": [["current_SALES", "asc"]], "perPage": 100, "page": page}
        d = keepa("query", {"domain": m["domain"], "selection": json.dumps(sel)}, key)
        batch = d.get("asinList") or []
        asins += batch
        if len(batch) < 100 or len(asins) >= (d.get("totalResults") or 0):
            break
        page += 1
    return asins


def products(m, asins, key):
    out = []
    for i in range(0, len(asins), 100):
        d = keepa("product", {"domain": m["domain"], "asin": ",".join(asins[i:i + 100]), "stats": 30}, key)
        out += d.get("products") or []
    return out


def norm_ean(e):
    d = "".join(ch for ch in str(e or "") if ch.isdigit())
    return d.zfill(13) if 8 < len(d) < 13 else d


def price(cur, idx):
    v = cur[idx] if len(cur) > idx else -1
    return v / 100 if v and v > 0 else None


# ───────────── Google Sheet ─────────────
def read_tab(svc, sid, tab):
    rows = svc.spreadsheets().values().get(spreadsheetId=sid, range=f"'{tab}'").execute().get("values", [])
    if not rows:
        return [], []
    head = rows[0]
    return head, [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in rows[1:]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30,
                    help="listings created in the last N days (always 30: sales often start a few days later; repeats are cut against Inbox)")
    ap.add_argument("--max-bsr", type=int, default=150000)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ignore-inbox", action="store_true", help="for testing filters: ignore what is already in Inbox")
    a = ap.parse_args()

    cfg = json.load(open(os.path.join(PIPE, "config.json")))
    sid = cfg["spreadsheet_id"]
    svc = _sheets_client()
    key = keepa_key()

    _, prods = read_tab(svc, sid, "Products")
    _, decs = read_tab(svc, sid, "Decisions")
    matrix = load_catalog_gating(svc)        # catalog authorization approval: {brand: {"AMZ US": "ok"|"gated"|"", ...}}
    selling = load_selling(svc)              # brand selling approval: {brand: {"US": "ok"|"hard gated"|…}}
    inbox_head, inbox = read_tab(svc, sid, "Inbox")
    _, cat_rows = read_tab(svc, sid, "Catalog")
    cat = Catalog(cat_rows, prods)
    print(f"Catalog: {len(cat_rows)} live/deactivated listings; brands closed on a market: "
          + (", ".join(f"{b} {m}" for b, m in sorted(cat.blocked)) or "—"))

    known_asins = {p["ASIN"] for p in prods if p["ASIN"]} | {p["ASIN 2"] for p in prods if p["ASIN 2"]}
    known_asins |= {d["ASIN"] for d in decs if d["ASIN"]}
    known_eans = {p["EAN"] for p in prods}
    reopenable = {(d["EAN"], d["Market"]) for d in decs if d["Reason"] in REOPEN_REASONS and d["Decision"] != "yes"}
    in_inbox = {(r.get("ASIN"), r.get("Signal market")) for r in inbox}

    today = dt.date.today().isoformat()
    new_rows, skipped = [], collections.Counter()
    for m in MARKETS:
        asins = new_asins(m, a.days, a.max_bsr, key)
        print(f"[{m['label']}] listings from the last {a.days} days with BSR ≤ {a.max_bsr:,}: {len(asins)}")
        for p in products(m, asins, key):
            asin, mk = p["asin"], m["label"]
            eans = [norm_ean(e) for e in (p.get("eanList") or []) if norm_ean(e)]
            ean = next((e for e in eans if e in known_eans or e in cat.live), eans[0] if eans else "")
            brand = (p.get("brand") or "").strip()
            cur = (p.get("stats") or {}).get("current") or []
            bsr = cur[3] if len(cur) > 3 and cur[3] and cur[3] > 0 else None
            offers = cur[11] if len(cur) > 11 and cur[11] and cur[11] > 0 else None
            bb = price(cur, 18) or price(cur, 1)
            since = from_keepa_minutes(p.get("trackingSince"))

            if (asin, mk) in in_inbox and not a.ignore_inbox:
                skipped["already in Inbox"] += 1
                continue
            if _norm_brand(brand) in EXCLUDED_BRANDS:
                skipped[f"excluded brand ({brand})"] += 1
                continue
            if is_mini(p.get("title") or ""):
                skipped[f"mini < {MIN_VOLUME_ML} ml"] += 1
                continue
            # Brand Approval Matrix: selling (hard gated = нельзя) + catalog (ok или пусто)
            ok, why = brand_check(brand, mk, selling, matrix)
            if not ok:
                skipped[why] += 1
                continue
            # Реальный каталог (SellerSnap): бренд закрыт на маркете / уже продаём / тот же товар под другим EAN
            if (_norm_brand(brand), mk) in cat.blocked:
                skipped[f"brand not sold in {mk} (catalog: Deactivated only)"] += 1
                continue
            if mk in cat.live_asin.get(asin, ()) or any(mk in cat.live.get(e, ()) for e in eans):
                skipped["already selling on this market (same EAN/ASIN)"] += 1
                continue
            same = cat.same_product(brand, p.get("title") or "", exclude=eans)
            if same and mk in cat.live.get(same, ()):
                skipped["same product already selling on market (other EAN)"] += 1
                continue
            same_note = (f"same product as EAN {same}, live in {'/'.join(sorted(cat.live[same]))}"
                         if same and cat.live.get(same) else "")
            if ean and (ean, mk) in reopenable:
                src, detail = REOPEN, f"{mk}: was No listing / other-market ASIN → listing appeared"
            elif asin in known_asins or (ean and ean in known_eans):
                skipped["already in GR catalog"] += 1
                continue
            else:
                src, detail = SOURCE, f"Fragrance {mk}, in Keepa since {since or '?'}"

            sig = [f"BSR {bsr:,}".replace(",", " ") if bsr else "BSR —"]
            drops = (p.get("stats") or {}).get("salesRankDrops30")
            if drops is not None and drops >= 0:
                sig.append(f"{drops} rank drops/30d")
            if p.get("monthlySold"):
                sig.append(f"~{p['monthlySold']} sold/mo")
            if offers:
                sig.append(f"{offers} offer" + ("" if offers == 1 else "s"))
            if bb:
                sig.append(f"BB {m['cur']}{bb:.2f}")
            checks = [same_note] if same_note else []
            if not ean:
                checks.append("no EAN in Keepa")
            if len(eans) > 1:
                checks.append(f"{len(eans)} EAN")
            new_rows.append([today, src, detail, ean, asin, brand, (p.get("title") or "")[:200], mk,
                             " · ".join(sig), "; ".join(checks), "", ""])
            in_inbox.add((asin, mk))

    print(f"\nAdded to Inbox: {len(new_rows)} "
          f"(new ASIN: {sum(r[1] == SOURCE for r in new_rows)}, reopened: {sum(r[1] == REOPEN for r in new_rows)})")
    for k, v in skipped.most_common():
        print(f"  skipped — {k}: {v}")
    for r in new_rows[:25]:
        print(f"  {r[7]} | {r[1]} | {r[5]} | {r[6][:60]} | {r[8]}")

    if a.dry_run or not new_rows:
        return
    if inbox_head != INBOX_HEADER:
        svc.spreadsheets().values().update(spreadsheetId=sid, range="'Inbox'!A1", valueInputOption="RAW",
                                           body={"values": [INBOX_HEADER]}).execute()
    svc.spreadsheets().values().append(spreadsheetId=sid, range="'Inbox'!A1", valueInputOption="RAW",
                                       insertDataOption="INSERT_ROWS", body={"values": new_rows}).execute()
    # отметка «последний запуск» во вкладке Sources
    _, srcs = read_tab(svc, sid, "Sources")
    for i, s in enumerate(srcs):
        if s.get("Source") == SOURCE:
            svc.spreadsheets().values().update(
                spreadsheetId=sid, range=f"'Sources'!E{i + 2}:F{i + 2}", valueInputOption="RAW",
                body={"values": [["sources/keepa_new_asins.py", f"{today} · +{len(new_rows)}"]]}).execute()
    print("written to Inbox")


if __name__ == "__main__":
    main()
