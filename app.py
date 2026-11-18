"""
GR Pipeline — экран Today.

Запуск (из папки gr-pipeline):
    python3 -m streamlit run app.py

При первом открытии за день приложение само запускает источник «Keepa: новый ASIN»
за все дни с прошлого запуска (автозапуска в фоне нет — решение Насти: без Full Disk Access).
"""
import datetime as dt
import os
import json
import subprocess
import sys

import streamlit as st

import gr_data as g  # noqa: E402


st.set_page_config(page_title="GR Pipeline", page_icon="🌙", layout="wide", initial_sidebar_state="expanded")

# Streamlit Cloud: the service account lives in secrets; hand it to the source scripts,
# which run as subprocesses and cannot read st.secrets (see gcp.py). Locally there are no secrets.
try:
    if "gcp_service_account" in st.secrets and not os.environ.get("GCP_SA_JSON"):
        os.environ["GCP_SA_JSON"] = json.dumps(dict(st.secrets["gcp_service_account"]))
except Exception:
    pass

# Who is deciding: on Streamlit Cloud the signed-in viewer's email, locally Nastya.
try:
    _email = (st.experimental_user.get("email") or "") if hasattr(st, "experimental_user") else ""
except Exception:
    _email = ""
if _email and "@" in _email and not _email.startswith("test@"):
    g.USER = _email.split("@")[0].capitalize()

HERE = os.path.dirname(os.path.abspath(__file__))
KEEPA_SOURCE = "Keepa: new ASIN"
REOPEN_SOURCE = "Reopened"
REASONS = g.reason_triggers()
REASON_LIST = ["Approved", "Approved: by request"] + sorted(r for r in REASONS if not r.startswith("Approved"))
CUR = {"US": "$", "CA": "C$", "UK": "£", "AU": "A$"}


# ───────────────────────── данные ─────────────────────────
@st.cache_data(ttl=600, show_spinner="Loading sheet…")
def load():
    return g.read_all()


def refresh():
    load.clear()
    st.rerun()


def run_script(name, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "sources", name), *map(str, args)],
                       capture_output=True, text=True, cwd=HERE, timeout=900)
    lines = [l for l in (r.stdout + r.stderr).splitlines() if "Warning" not in l and "warnings.warn" not in l]
    return r.returncode == 0, f"── {name}\n" + "\n".join(lines[-25:])


def run_keepa(days):
    """New listings → Keepa figures for pending/due decisions → ROI (Vika's core.py)."""
    steps = [run_script("keepa_new_asins.py", "--days", days), run_script("recheck_keepa.py"),
             run_script("au_prices.py", "--scope", "all"), run_script("roi.py")]
    return all(ok for ok, _ in steps), "\n\n".join(log for _, log in steps)


def enrich_ean(ean):
    run_script("recheck_keepa.py", "--ean", ean)
    run_script("au_prices.py", "--scope", "pending")
    run_script("roi.py", "--ean", ean)


def keepa_last_run(data):
    for s in data["Sources"]["rows"]:
        if s.get("Source") == KEEPA_SOURCE and s.get("Last run"):
            try:
                return dt.date.fromisoformat(s["Last run"][:10])
            except ValueError:
                return None
    return None


data = load()

# Автообновление: раз в день при открытии — за все дни с прошлого запуска
last = keepa_last_run(data)
if "auto_checked" not in st.session_state:
    st.session_state.auto_checked = True
    if last != dt.date.today():
        days = 30   # всегда 30 дней: листинг часто начинает продаваться не сразу; повторы режутся по Inbox
        with st.spinner(f"Checking Keepa for new listings (last {days} days, first run today)…"):
            ok, log = run_keepa(days)
        st.session_state.keepa_log = (ok, log)
        load.clear()
        data = load()

P, O, D, B, I, S = (data[t] for t in ("Products", "Offers", "Decisions", "Brands", "Inbox", "Settings"))
prod_by_ean = {p["EAN"]: p for p in P["rows"]}
offers_by_ean = {}
for o in O["rows"]:
    offers_by_ean.setdefault(o["EAN"], []).append(o)
brand_by_name = {b["Brand"].strip().lower(): b for b in B["rows"]}
dec_by_key = {(d["EAN"], d["Market"]): d for d in D["rows"]}
allowed_sup = {s["Supplier"]: s["Can buy?"] for s in S["rows"] if s.get("Supplier")}


def best_offer(ean):
    ok = []
    for o in offers_by_ean.get(ean, []):
        try:
            price = float(str(o["Price, € excl VAT"]).replace(",", "."))
        except ValueError:
            continue
        if o["Can buy"] == "yes":
            ok.append((price, o["Supplier"]))
    return min(ok) if ok else None


def brand_status(brand, mk):
    b = brand_by_name.get((brand or "").strip().lower())
    return (b or {}).get(mk, "") if b else "not in matrix"


def suggest(ean, mk, d, brand):
    st_ = brand_status(brand, mk)
    if ("gated" in st_ and not st_.startswith(("ok", "ungated"))) or st_ in ("ba - need approve to sell", "gs 1 issue"):
        return ":red[●]", f"brand: {st_}", "Gated"
    if "no listing" in (d.get("Keepa checked") or ""):
        return ":orange[●]", "no listing on Amazon (Keepa " + d["Keepa checked"][:10] + ")", "No listing"
    if not d.get("ASIN"):
        return ":orange[●]", "no listing", "No listing"
    if d.get("ROI") and d.get("Min ROI"):
        roi, thr = float(d["ROI"].rstrip("%")), float(d["Min ROI"].rstrip("%"))
        if roi >= thr:
            return ":gray[○]", f"Approve? ROI {d['ROI']} ≥ min {d['Min ROI']}", "Approved"
        return ":red[●]", f"low ROI: {d['ROI']} < min {d['Min ROI']}", "Low ROI"
    if not best_offer(ean):
        hint = f" · max buy {d['Max Buy Price']}" if d.get("Max Buy Price", "").startswith("€") else ""
        return ":orange[●]", "no approved supplier" + hint, "Few suppliers"
    if not d.get("Buy Box"):
        return ":gray[○]", "no Buy Box from Keepa yet — ROI not calculated", "Approved"
    return ":gray[○]", "ROI not calculated yet (AU / no data)", "Approved"


# ───────────────────────── действия ─────────────────────────
def triage(row, decision):
    g.update_cells("Inbox", I["head"], row["_row"], {"Triage": decision})
    if decision != "research":
        return
    ean, mk, asin = row["EAN"], row["Signal market"], row["ASIN"]
    if row["Source"] == REOPEN_SOURCE and (ean, mk) in dec_by_key:
        d = dec_by_key[(ean, mk)]
        g.update_cells("Decisions", D["head"], d["_row"], {
            "ASIN": asin, "Decision": "", "Reason": "", "Reopen trigger": "", "Recheck on": "",
            "Comment": f"{g.today()}: reopened — {row['Source detail']}; was {d['Decision']} / {d['Reason']}"})
        return
    if ean and ean not in prod_by_ean:
        g.append_rows("Products", P["head"], [{
            "EAN": ean, "ASIN": asin, "Brand": row["Brand"], "Product": row["Product"],
            "Added": g.today(), "First source": row["Source"]}])
    new = []
    for m in g.MARKETS:
        if (ean, m) in dec_by_key:
            continue
        new.append({"EAN": ean, "Market": m, "ASIN": asin if m == mk else "", "Show": "yes",
                    "Comment": f"{g.today()}: from Inbox ({row['Source']}, signal {mk})"})
    if new:
        g.append_rows("Decisions", D["head"], new)


def decide(d, decision, reason):
    upd = {"Decision": decision, "Reason": reason, "Decided": g.today(), "By": g.USER,
           "Reopen trigger": REASONS.get(reason, "—") if decision != "yes" else "—",
           "Recheck on": (dt.date.today() + dt.timedelta(days=7)).isoformat() if decision == "recheck" else ""}
    g.update_cells("Decisions", D["head"], d["_row"], upd)


def add_offer(ean, supplier, price):
    g.append_rows("Offers", O["head"], [{
        "EAN": ean, "Supplier": supplier, "Price, € excl VAT": price, "Price date": g.today(),
        "Can buy": allowed_sup.get(supplier, "yes"), "Source": "manual (app)"}])


# ───────────────────────── экран ─────────────────────────
pending_inbox = [r for r in I["rows"] if r.get("ASIN") and not r.get("Triage")]
shown = [d for d in D["rows"] if d.get("Show") == "yes"]
pending = [d for d in shown if not d.get("Decision")]
due = [d for d in shown if d.get("Decision") == "recheck" and d.get("Recheck on") and d["Recheck on"] <= g.today()]

# После «yes»: статус листинга. Live определяется сам — EAN живой на маркете в каталоге SellerSnap (вкладка Catalog).
LIST_STATUSES = ["Approved", "Taken", "Listing created"]
live_now = {(r["EAN"], r["Market"]) for r in data.get("Catalog", {"rows": []})["rows"] if r.get("State") in ("Active", "Out of Stock")}
to_list = [d for d in shown if d.get("Decision") == "yes" and (d["EAN"], d["Market"]) not in live_now
           and (d.get("Decided") not in ("", "before migration") or d.get("Listing status") in ("Taken", "Listing created"))]
def brand_closed(ean, mk):
    st_ = brand_status(prod_by_ean.get(ean, {}).get("Brand", ""), mk)
    return ("gated" in st_ and not st_.startswith(("ok", "ungated"))) or st_ in ("ba - need approve to sell", "gs 1 issue")


backlog_all = [d for d in shown if g.is_backlog(d, live_now) and d.get("Listing status") not in ("Taken", "Listing created")]
backlog = [d for d in backlog_all if not brand_closed(d["EAN"], d["Market"])]
backlog_gated = len(backlog_all) - len(backlog)


def _num(v):
    try:
        return float(str(v).replace(" ", "").rstrip("%"))
    except ValueError:
        return None


def backlog_key(d):
    drops, bsr = _num(d.get("Rank drops 30d")) or 0, _num(d.get("BSR")) or 10 ** 9
    return (-drops, bsr)


def drop_backlog(d):
    roi, thr = _num(d.get("ROI")), _num(d.get("Min ROI"))
    if "no listing" in (d.get("Keepa checked") or ""):
        reason = "No listing"
    elif roi is not None and thr is not None and roi < thr:
        reason = "Low ROI"
    else:
        reason = "Low sales"
    g.update_cells("Decisions", D["head"], d["_row"], {
        "Decision": "no", "Reason": reason, "Reopen trigger": REASONS.get(reason, "—"), "Decided": g.today(),
        "By": g.USER, "Comment": f"{g.today()}: dropped from approved backlog (was yes / {d.get('Reason')})"})


def set_listing_status(d, status):
    head = g.ensure_cols("Decisions", D["head"], ["Listing status", "Listing status date"])
    g.update_cells("Decisions", head, d["_row"], {"Listing status": status, "Listing status date": g.today()})

# ───────────────────────── оформление: ночное небо, золото, рамка рукописи ─────────────────────────
STYLE = """
<style>
/* Dusk: тихое сумеречное небо, туман, тонкий луч света; один красный акцент — зачёркивание */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root{
  --ink:#07090D; --dusk:#0F1A2A; --dusk-hi:#1B2D45; --mist:#D5DADB; --mist-2:#B3BBC1; --text:#F1EFEA;
  --peach:#E9C9A8; --peach-lo:rgba(233,201,168,.16); --red:#C2463C;
  --serif:'IBM Plex Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif; --sans:'IBM Plex Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif;
  --mono:'IBM Plex Mono', ui-monospace, Menlo, monospace;
}
.stApp{
  /* горизонт со светом и отражённый полумесяц (static/eclipse.jpg) + лёгкое затемнение */
  background:
    linear-gradient(180deg, rgba(5,7,11,.30) 0%, rgba(5,7,11,.10) 45%, rgba(5,7,11,.30) 75%, rgba(5,7,11,.60) 100%),
    url('/app/static/eclipse.jpg') center 42% / cover no-repeat,
    linear-gradient(180deg, var(--dusk-hi), var(--ink));
  background-attachment: fixed; color: var(--text);
}
html, body, .stApp, p, li, label, input, textarea, select, button, .stCaption { font-family: var(--sans) !important; }
.stMarkdown p, .stMarkdown li { font-size: .98rem; line-height: 1.55; letter-spacing: .005em; }
[data-testid="stCaptionContainer"], .stCaption { color: var(--mist-2) !important; }
code { font-family: var(--mono) !important; background: rgba(201,207,203,.07) !important; color: var(--mist) !important;
  font-size: .82em !important; border-radius: 2px; }
a, a:visited { color: var(--peach) !important; text-decoration: none; border-bottom: 1px solid rgba(233,201,168,.3); }
a:hover { border-bottom-color: var(--peach); }
del, s { text-decoration-color: var(--red) !important; text-decoration-thickness: 1.5px; color: var(--mist-2); }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 3.2rem; max-width: 1180px; }

/* заголовки — курсивная антиква с точкой */
h1, h2, h3 { font-family: var(--sans) !important; font-weight: 600 !important; color: var(--text) !important; letter-spacing: -.01em; }
h4 { font-family: var(--sans) !important; font-weight: 500 !important; letter-spacing: .18em; font-size: .82rem !important;
  text-transform: uppercase; color: var(--mist) !important; }
.gr-title { font-family: var(--sans); font-weight: 600; font-size: 2.3rem; line-height: 1.1; color: var(--text);
  letter-spacing: -.015em; margin: .2rem 0 .7rem; text-wrap: balance; text-shadow: 0 2px 18px rgba(0,0,0,.45); }
.gr-title .dot { display: none; }
.gr-sub { color: var(--mist); max-width: 66ch; font-size: .92rem; line-height: 1.6; margin-bottom: 1.6rem; text-shadow: 0 1px 10px rgba(0,0,0,.5);
  padding-left: .9rem; border-left: 1px solid rgba(233,201,168,.35); }
.gr-sub b { color: var(--mist); font-weight: 500; }
.dot-red { color: var(--red); } .dot-peach { color: var(--peach); } .dot-mist { color: var(--mist-2); }

/* карточки — туман: полупрозрачно, тонкая тёплая граница */
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {
  background: rgba(10,14,22,.80);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(233,201,168,.16) !important; border-radius: 2px !important;
  box-shadow: 0 18px 40px rgba(0,0,0,.28);
  padding: 1.15rem 1.3rem 1.3rem !important; margin-bottom: .9rem;
}
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"]:hover { border-color: rgba(233,201,168,.28) !important; }

/* кнопки */
.stButton > button, [data-testid="stPopover"] button {
  font-family: var(--sans) !important; font-weight: 500; letter-spacing: .16em; text-transform: uppercase; font-size: .7rem !important;
  border-radius: 1px !important; border: 1px solid rgba(201,207,203,.28) !important; background: transparent !important;
  color: var(--mist) !important; white-space: nowrap;
}
.stButton > button p, [data-testid="stPopover"] button p { white-space: nowrap; font-size: .7rem !important; }
.stButton > button:hover { border-color: var(--peach) !important; color: var(--peach) !important; }
.stButton > button[kind="primary"], [data-testid="stBaseButton-primary"] {
  background: var(--peach) !important; color: var(--ink) !important; border-color: var(--peach) !important;
}
.stButton > button[kind="primary"]:hover { background: #F3DCC2 !important; color: var(--ink) !important; }
.stButton > button:disabled { opacity: .3; }
[data-baseweb="select"] > div { background: rgba(7,9,13,.45) !important; border-color: rgba(201,207,203,.18) !important; border-radius: 1px !important; }

/* метрика — крупные курсивные цифры */
[data-testid="stMetricValue"] { font-family: var(--sans) !important; font-weight: 600; font-size: 2rem !important; color: var(--text) !important; }
[data-testid="stMetricLabel"] p { letter-spacing: .18em; text-transform: uppercase; font-size: .66rem !important; color: var(--mist-2) !important; }

/* боковая панель — почти чёрная, по краю тонкий луч света */
[data-testid="stSidebar"] { background: rgba(7,9,13,.93) !important; backdrop-filter: blur(12px); border-right: none; }
[data-testid="stSidebar"]::after {
  content: ""; position: absolute; top: 0; right: 0; width: 1px; height: 100%;
  background: linear-gradient(180deg, transparent 0%, rgba(233,201,168,.15) 20%, rgba(243,220,194,.85) 52%, rgba(233,201,168,.15) 82%, transparent 100%);
  box-shadow: 0 0 18px 2px rgba(233,201,168,.18);
}
.gr-brand { font-family: var(--sans); font-weight: 600; font-size: 1rem; letter-spacing: .24em; text-transform: uppercase; color: var(--text); line-height: 1; }
.gr-brand .mark { color: var(--peach); letter-spacing: .2em; font-weight: 400; margin-right: .5rem; }
.gr-date { margin: .9rem 0 1.4rem; color: var(--mist); font-size: .85rem; }
.gr-date b { font-size: 1.15rem; font-weight: 600; color: var(--text); }
.gr-date span { color: var(--mist-2); font-size: .75rem; margin-left: .35rem; }
[data-testid="stSidebar"] [role="radiogroup"] { gap: .1rem; }
[data-testid="stSidebar"] [role="radiogroup"] label { padding: .55rem .2rem .55rem .8rem; border-left: 1px solid rgba(201,207,203,.12); width: 100%; }
[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child { display: none; }   /* без кружков радио */
[data-testid="stSidebar"] [role="radiogroup"] label p { font-size: .9rem !important; color: var(--mist-2) !important; letter-spacing: .02em; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover p { color: var(--mist) !important; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { border-left: 1px solid var(--peach); box-shadow: -6px 0 14px -8px rgba(233,201,168,.7); }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p { color: var(--text) !important; }
[data-testid="stSidebar"] hr { border-color: rgba(201,207,203,.1); }
/* карточка товара: бренд → название → метки → ячейки «подпись / значение» */
.gr-top { display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; margin-bottom: .25rem; }
.gr-brandline { font-size: .7rem; letter-spacing: .2em; text-transform: uppercase; color: var(--mist-2); font-weight: 500; }
.gr-pill { font-size: .68rem; font-weight: 600; letter-spacing: .12em; color: var(--ink); background: var(--peach);
  padding: .12rem .45rem; border-radius: 1px; }
.gr-src { font-size: .68rem; letter-spacing: .06em; color: var(--mist-2); border: 1px solid rgba(201,207,203,.22);
  padding: .08rem .4rem; border-radius: 1px; }
.gr-pname { font-size: 1.12rem; font-weight: 600; color: var(--text); line-height: 1.35; margin: .1rem 0 .75rem; }
.gr-stats { display: flex; flex-wrap: wrap; align-items: stretch; gap: .4rem 0;
  background: rgba(201,207,203,.035); border: 1px solid rgba(201,207,203,.08); border-radius: 1px;
  padding: .7rem .9rem .75rem; margin: .1rem 0 .35rem; }
.gr-stat { display: flex; flex-direction: column; gap: .15rem; padding: 0 .85rem 0 0; margin-right: .85rem;
  border-right: 1px solid rgba(201,207,203,.10); min-width: 0; }
.gr-stat:last-of-type { border-right: none; }
.gr-stat span { font-size: .62rem; letter-spacing: .18em; text-transform: uppercase; color: var(--mist-2); }
.gr-stat b { font-size: 1.02rem; font-weight: 500; color: var(--text); font-variant-numeric: tabular-nums; white-space: nowrap; }
.gr-stat b.money { color: var(--peach); }
.gr-stat b.bad { color: #E07A6E; }
.gr-stat b.good { color: #9FD3A8; }
.gr-stat b.mono { font-family: var(--mono); font-size: .86rem; font-weight: 400; color: var(--mist); padding-top: .12rem; }
.gr-stat b.none { color: rgba(179,187,193,.5); }
a.gr-keepa { align-self: center; margin-left: auto; font-size: .66rem; letter-spacing: .16em; text-transform: uppercase;
  border: 1px solid rgba(233,201,168,.45) !important; padding: .35rem .6rem; color: var(--peach) !important;
  text-decoration: none !important; }
a.gr-keepa:hover { background: rgba(233,201,168,.08); }
.gr-mini { padding: .45rem .55rem .5rem; gap: .35rem 0; }
.gr-mini .gr-stat { padding-right: .5rem; margin-right: .5rem; }
.gr-mini .gr-stat b { font-size: .88rem; }
.gr-mini .gr-stat span { font-size: .56rem; letter-spacing: .14em; }
.gr-note { margin-top: .7rem; font-size: .85rem; color: var(--peach); }
.gr-note::before { content: "—  "; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""
st.markdown(STYLE, unsafe_allow_html=True)


SOURCE_LABEL = {"Keepa: new ASIN": "Keepa · new ASIN", "Reopened": "Reopened", "Supplier: new releases": "Supplier · new",
                "Keepa: brand": "Keepa · brand watch", "Competitor: storefront": "Competitor storefront",
                "Brand announcement": "Brand launch", "Old GR table": "Old GR table"}


import html as _html
import re as _re


def _stat(label, value, cls=""):
    v = _html.escape(str(value)) if value not in (None, "") else "—"
    c = cls if value not in (None, "") else "none"
    return f'<div class="gr-stat"><span>{label}</span><b class="{c}">{v}</b></div>'


def parse_signal(sig):
    m = lambda rx: (_re.search(rx, sig or "") or [None, None])[1]
    return {"bsr": m(r"BSR ([\d ]+\d)"), "sold": m(r"~(\d+) sold/mo"), "offers": m(r"(\d+) offers?"),
            "drops": m(r"(\d+) rank drops/30d"),
            "bb": m(r"BB ([^\s·]+)")}


def inbox_card_html(r):
    mk = r["Signal market"]
    sig = parse_signal(r.get("Signal", ""))
    link = (f"https://keepa.com/#!product/{g.KEEPA_DOMAIN[mk]}-{r['ASIN']}" if mk in g.KEEPA_DOMAIN
            else f"https://www.amazon.{g.AMAZON_TLD.get(mk, 'com')}/dp/{r['ASIN']}")
    src = SOURCE_LABEL.get(r["Source"], r["Source"])
    note = f'<div class="gr-note">{_html.escape(r["Auto check"])}</div>' if r.get("Auto check") else ""
    return (f'<div class="gr-top"><span class="gr-brandline">{_html.escape(r.get("Brand", ""))}</span>'
            f'<span class="gr-pill">{_html.escape(mk)}</span><span class="gr-src">{_html.escape(src)}</span>'
            f'<a class="gr-keepa" href="{link}" target="_blank">{"Keepa" if mk in g.KEEPA_DOMAIN else "Amazon"} ↗</a></div>'
            f'<div class="gr-pname">{_html.escape(r["Product"])}</div>'
            f'<div class="gr-stats">{_stat("BSR", sig["bsr"])}{_stat("Offers", sig["offers"])}'
            f'{_stat("Buy Box", sig["bb"], "money")}{_stat("Rank drops 30d", sig["drops"])}'
            + (_stat("Sold / mo", sig["sold"]) if sig["sold"] else "")
            + f'{_stat("EAN", r.get("EAN"), "mono")}{_stat("ASIN", r.get("ASIN"), "mono")}</div>'
            + note)


def page_title(text, sub=""):
    st.markdown(f'<div class="gr-title">{text}<span class="dot">.</span></div>'
                + (f'<div class="gr-sub">{sub}</div>' if sub else ""), unsafe_allow_html=True)


def product_card(ean, decs, key_prefix):
    p = prod_by_ean.get(ean, {})
    brand = p.get("Brand", "")
    with st.container(border=True):
        src = p.get('First source', '—')
        st.markdown(f'<div class="gr-top"><span class="gr-brandline">{_html.escape(brand)}</span>'
                    f'<span class="gr-src">{_html.escape(SOURCE_LABEL.get(src, src))}</span></div>'
                    f'<div class="gr-pname">{_html.escape(p.get("Product", ean))}</div>'
                    f'<div class="gr-stats" style="margin-bottom:.4rem">{_stat("EAN", ean, "mono")}'
                    f'{_stat("Added", p.get("Added"))}</div>', unsafe_allow_html=True)
        left, right = st.columns([1.3, 4])
        with left:
            st.caption("SUPPLIERS")
            offs = offers_by_ean.get(ean, [])
            best = best_offer(ean)
            for o in offs:
                txt = f"{o['Supplier']} · €{o['Price, € excl VAT']}"
                if o["Can buy"] == "no":
                    st.markdown(f"~~{txt}~~")
                elif best and o["Supplier"] == best[1]:
                    st.markdown(f":orange[**{txt}**]")
                else:
                    st.markdown(txt)
            if not best:
                st.markdown(":red[—] no supplier we can buy from")
            with st.popover("➕ Supplier price"):
                sups = [s for s, a in allowed_sup.items() if a == "yes"]
                sup = st.selectbox("Supplier", sups, key=f"{key_prefix}_sup")
                pr = st.number_input("Price, € excl VAT", min_value=0.0, step=0.5, key=f"{key_prefix}_pr")
                if st.button("Save", key=f"{key_prefix}_save") and pr > 0:
                    add_offer(ean, sup, pr)
                    run_script("roi.py", "--ean", ean)
                    refresh()
        with right:
            cols = st.columns(len(decs))
            for c, d in zip(cols, decs):
                mk = d["Market"]
                icon, why, default_reason = suggest(ean, mk, d, brand)
                with c:
                    st.markdown(f"#### {mk} {icon}")
                    if d.get("ASIN"):
                        if mk in g.KEEPA_DOMAIN:
                            st.markdown(f"[Keepa ↗](https://keepa.com/#!product/{g.KEEPA_DOMAIN[mk]}-{d['ASIN']}) `{d['ASIN']}`")
                        else:
                            st.markdown(f"[Amazon AU ↗](https://www.amazon.com.au/dp/{d['ASIN']})"
                                        + ("" if "Apify AU" in (d.get("Keepa checked") or "") else "  \n:gray[Keepa does not cover AU]"))
                    if "AMAZON SELLS" in (d.get("Keepa checked") or ""):
                        st.markdown(":orange[—] Amazon sells this listing")
                    if d.get("Keepa checked") and "no listing" not in d["Keepa checked"]:
                        st.markdown('<div class="gr-stats gr-mini">'
                                    + _stat("BSR", f'{int(d["BSR"]):,}'.replace(",", " ") if str(d.get("BSR", "")).isdigit() else d.get("BSR"))
                                    + _stat("Offers", d.get("Offers")) + _stat("Buy Box", d.get("Buy Box"), "money")
                                    + _stat("Drops 30d", d.get("Rank drops 30d")) + "</div>", unsafe_allow_html=True)
                    if d.get("Max Buy Price"):
                        bo = best_offer(ean)
                        mbp = float(d["Max Buy Price"].lstrip("€")) if d["Max Buy Price"].startswith("€") else None
                        room = f"{'+' if mbp - bo[0] >= 0 else '−'}€{abs(mbp - bo[0]):.2f}" if (mbp is not None and bo) else ""
                        good = (d.get("ROI") and d.get("Min ROI")
                                and float(d["ROI"].rstrip("%")) >= float(d["Min ROI"].rstrip("%")))
                        st.markdown('<div class="gr-stats gr-mini">' + _stat("ROI", d.get("ROI"), "good" if good else "bad")
                                    + _stat("Min", d.get("Min ROI")) + _stat("Max buy", d.get("Max Buy Price"))
                                    + _stat("Room", room, "bad" if room.startswith("−") else "good") + "</div>",
                                    unsafe_allow_html=True)
                    if d.get("Decision"):
                        st.markdown(f'<div class="gr-top"><span class="gr-src">current: {_html.escape(d["Decision"])}'
                                    f' · {_html.escape(d.get("Reason", ""))}</span></div>', unsafe_allow_html=True)
                    st.caption(why)
                    if d.get("Comment"):
                        cm = d["Comment"]
                        for ru, en in SOURCE_LABEL.items():
                            cm = cm.replace(ru, en)
                        st.caption(cm[:160])
                    reason = st.selectbox("Reason", REASON_LIST, index=REASON_LIST.index(default_reason),
                                          key=f"{key_prefix}_{mk}_{d['_row']}_reason", label_visibility="collapsed")
                    val = st.radio("Decision", ["yes", "no", "recheck"], index=None, horizontal=True,
                                   key=f"{key_prefix}_{mk}_{d['_row']}_dec", label_visibility="collapsed")
                    if st.button("Save decision", key=f"{key_prefix}_{mk}_{d['_row']}_save", disabled=val is None,
                                 use_container_width=True):
                        r = reason if val != "yes" or reason.startswith("Approved") else "Approved"
                        decide(d, val, r)
                        refresh()


def by_ean(rows):
    out = {}
    for d in rows:
        out.setdefault(d["EAN"], []).append(d)
    for v in out.values():
        v.sort(key=lambda x: g.MARKETS.index(x["Market"]) if x["Market"] in g.MARKETS else 9)
    return out


groups, dgroups, lgroups = by_ean(pending), by_ean(due), by_ean(to_list)
decided_today = sum(1 for d in shown if d.get("Decided") == g.today() and d.get("By") == g.USER)

# ───────────────────────── боковая панель: навигация ─────────────────────────
def search_box(key):
    return st.text_input("Search", key=f"q_{key}", placeholder="Search by EAN, ASIN, brand or product name",
                         label_visibility="collapsed").strip().lower()


def match_product(q, ean, extra=()):
    if not q:
        return True
    p = prod_by_ean.get(ean, {})
    hay = " ".join([ean, p.get("ASIN", ""), p.get("ASIN 2", ""), p.get("Brand", ""), p.get("Product", ""), *extra]).lower()
    return all(w in hay for w in q.split())


def match_group(q, ean, decs):
    return match_product(q, ean, [d.get("ASIN", "") for d in decs])


PAGES = {
    "inbox": ("Inbox", len(pending_inbox)),
    "pending": ("Pending decisions", len(groups)),
    "recheck": ("Due for recheck", len(dgroups)),
    "tolist": ("To list", len(lgroups)),
    "backlog": ("Approved backlog", sum(1 for d in backlog if (_num(d.get("Rank drops 30d")) or 0) > 0)),
    "search": ("Search", ""),
}
with st.sidebar:
    t = dt.date.today()
    st.markdown(f'<div class="gr-brand"><span class="mark">( )</span>GR pipeline</div>'
                f'<div class="gr-date"><b>{t:%d}</b> · {t:%m} <span>({t:%A}, ’{t:%y})</span></div>', unsafe_allow_html=True)
    page = st.radio("Section", list(PAGES), key="page", label_visibility="collapsed",
                    format_func=lambda k: f"{PAGES[k][0]}  ·  {PAGES[k][1]}" if PAGES[k][1] != "" else PAGES[k][0])
    st.divider()
    st.metric("Decided today", decided_today)
    if st.button("Refresh now", use_container_width=True):
        with st.spinner("Checking Keepa (listings from the last 30 days)…"):
            st.session_state.keepa_log = run_keepa(30)
        refresh()
    st.caption(f"Keepa last run: {keepa_last_run(data) or '—'}")
    st.markdown(f"[Open the Google Sheet ↗]({g.SHEET_URL})")
    if "keepa_log" in st.session_state:
        ok, log = st.session_state.keepa_log
        with st.expander("Keepa run log" + ("" if ok else " — ⚠️ error"), expanded=not ok):
            st.code(log or "(empty)")

# ───────────────────────── разделы ─────────────────────────
if page == "inbox":
    page_title("Inbox", "New signals from Keepa and suppliers. Research adds the product to the catalog "
                        "and creates decisions for US / CA / UK / AU.")
    if not pending_inbox:
        st.info("Inbox is clear.")
    q = search_box("inbox")
    for r in [r for r in pending_inbox if match_product(q, r["EAN"], [r.get("ASIN", ""), r.get("Brand", ""), r.get("Product", "")])]:
        with st.container(border=True):
            c = st.columns([7.2, 1.35, 1], vertical_alignment="center")
            c[0].markdown(inbox_card_html(r), unsafe_allow_html=True)
            if c[1].button("Research", key=f"in_r_{r['_row']}", type="primary", use_container_width=True):
                triage(r, "research")
                with st.spinner("Pulling Keepa figures and ROI…"):
                    enrich_ean(r["EAN"])
                refresh()
            if c[2].button("Skip", key=f"in_s_{r['_row']}", use_container_width=True):
                triage(r, "skip")
                refresh()

elif page == "pending":
    page_title("Pending decisions", '<span class="dot-red">●</span> brand gated &nbsp; <span class="dot-peach">●</span> no listing or no supplier '
                                    '&nbsp; <span class="dot-mist">○</span> ready to decide. The reason is pre-filled from the hint; '
                                    'recheck brings the market back in 7 days.')
    if not groups:
        st.info("No pending decisions.")
    q = search_box("pending")
    for ean, decs in [(e, v) for e, v in groups.items() if match_group(q, e, v)]:
        product_card(ean, decs, f"p_{ean}")

elif page == "tolist":
    page_title("To list", "Approved markets that are not live yet. Take one, create the listing, mark it created — "
                          "it disappears by itself once the EAN is live in SellerSnap.")
    if not lgroups:
        st.info("Nothing to list — every approved market is live.")
    q = search_box("tolist")
    for ean, decs in [(e, v) for e, v in lgroups.items() if match_group(q, e, v)]:
        p = prod_by_ean.get(ean, {})
        with st.container(border=True):
            st.markdown(f'<div class="gr-top"><span class="gr-brandline">{_html.escape(p.get("Brand", ""))}</span></div>'
                        f'<div class="gr-pname">{_html.escape(p.get("Product", ean))}</div>'
                        f'<div class="gr-stats" style="margin-bottom:.5rem">{_stat("EAN", ean, "mono")}'
                        + (_stat("Best price", f"€{best_offer(ean)[0]:.2f} · {best_offer(ean)[1]}", "money") if best_offer(ean) else _stat("Best price", ""))
                        + "</div>", unsafe_allow_html=True)
            cols = st.columns(len(decs))
            for c, d in zip(cols, decs):
                mk, status = d["Market"], d.get("Listing status") or "Approved"
                with c:
                    st.markdown(f'<div class="gr-top"><span class="gr-pill">{mk}</span>'
                                f'<span class="gr-src">{_html.escape(status)}'
                                + (f' · {_html.escape(d.get("Listing status date", ""))}' if d.get("Listing status date") else "")
                                + '</span></div>', unsafe_allow_html=True)
                    if d.get("ASIN") and mk in g.KEEPA_DOMAIN:
                        st.markdown(f"[Keepa ↗](https://keepa.com/#!product/{g.KEEPA_DOMAIN[mk]}-{d['ASIN']}) `{d['ASIN']}`")
                    nxt = {"Approved": "Taken", "Taken": "Listing created"}.get(status)
                    if nxt and st.button(f"Mark {nxt.lower()}", key=f"ls_{d['_row']}", use_container_width=True,
                                         type="primary" if nxt == "Taken" else "secondary"):
                        set_listing_status(d, nxt)
                        refresh()
                    if status == "Listing created":
                        st.caption("Waiting for SellerSnap to show it live.")

elif page == "backlog":
    page_title("Approved backlog", "Approved before the migration but not live in SellerSnap. Sorted by demand "
                                   "(rank drops in 30 days, then BSR). Take moves it to To list; Drop closes it with a reason.")
    f1, f2, f3 = st.columns([1.2, 1.7, 3])
    mk_f = f1.selectbox("Market", ["All"] + g.MARKETS, key="bl_mk")
    only_selling = f2.toggle("Only selling (drops ≥ 1)", value=True, key="bl_sell")
    only_roi = f2.toggle("Only ROI ≥ min", value=False, key="bl_roi")
    rows = [d for d in backlog if (mk_f == "All" or d["Market"] == mk_f)
            and (not only_selling or (_num(d.get("Rank drops 30d")) or 0) > 0)
            and (not only_roi or (_num(d.get("ROI")) is not None and _num(d.get("Min ROI")) is not None
                                  and _num(d["ROI"]) >= _num(d["Min ROI"])))]
    q = search_box("backlog")
    rows = [d for d in rows if match_product(q, d["EAN"], [d.get("ASIN", "")])]
    rows.sort(key=backlog_key)
    f3.markdown(f'<div class="gr-sub" style="margin:0">{len(rows)} of {len(backlog)} · hidden: {backlog_gated} with a brand '
                f'now gated in the matrix · AU has no Keepa data yet</div>',
                unsafe_allow_html=True)
    limit = st.session_state.get("bl_limit", 30)
    for d in rows[:limit]:
        p, mk, ean = prod_by_ean.get(d["EAN"], {}), d["Market"], d["EAN"]
        bo = best_offer(ean)
        link = (f"https://keepa.com/#!product/{g.KEEPA_DOMAIN[mk]}-{d['ASIN']}" if d.get("ASIN") and mk in g.KEEPA_DOMAIN
                else (f"https://www.amazon.{g.AMAZON_TLD[mk]}/dp/{d['ASIN']}" if d.get("ASIN") else ""))
        roi, thr = _num(d.get("ROI")), _num(d.get("Min ROI"))
        with st.container(border=True):
            c = st.columns([7.2, 1.35, 1], vertical_alignment="center")
            c[0].markdown(
                f'<div class="gr-top"><span class="gr-brandline">{_html.escape(p.get("Brand", ""))}</span>'
                f'<span class="gr-pill">{mk}</span>'
                + (f'<a class="gr-keepa" href="{link}" target="_blank">{"Keepa" if mk in g.KEEPA_DOMAIN else "Amazon"} ↗</a>' if link else "")
                + f'</div><div class="gr-pname">{_html.escape(p.get("Product", ean))}</div>'
                f'<div class="gr-stats">{_stat("BSR", d.get("BSR"))}{_stat("Offers", d.get("Offers"))}'
                f'{_stat("Buy Box", d.get("Buy Box"), "money")}{_stat("Drops 30d", d.get("Rank drops 30d"))}'
                + _stat("ROI", d.get("ROI"), "good" if (roi is not None and thr is not None and roi >= thr) else "bad")
                + _stat("Max buy", d.get("Max Buy Price"))
                + _stat("Best supplier", f"€{bo[0]:.2f} · {bo[1]}" if bo else "", "money")
                + "</div>"
                + ('<div class="gr-note">no listing on Amazon now</div>' if "no listing" in (d.get("Keepa checked") or "") else "")
                + ('<div class="gr-note">Amazon sells this listing</div>' if "AMAZON SELLS" in (d.get("Keepa checked") or "") else ""),
                unsafe_allow_html=True)
            if c[1].button("Take", key=f"bl_t_{d['_row']}", type="primary", use_container_width=True):
                set_listing_status(d, "Taken")
                refresh()
            if c[2].button("Drop", key=f"bl_d_{d['_row']}", use_container_width=True):
                drop_backlog(d)
                refresh()
    if len(rows) > limit and st.button(f"Show 30 more ({len(rows) - limit} left)"):
        st.session_state.bl_limit = limit + 30
        st.rerun()

elif page == "search":
    page_title("Search", "Any product by EAN, ASIN, brand or name — whatever state its decisions are in.")
    q = search_box("global")
    if len(q) < 3:
        st.info("Type at least 3 characters.")
    else:
        all_by_ean = by_ean([d for d in shown if d["Market"] in g.MARKETS])
        hits = [e for e in set(list(all_by_ean) + list(prod_by_ean)) if match_group(q, e, all_by_ean.get(e, []))]
        st.caption(f"{len(hits)} found" + (" · showing the first 20" if len(hits) > 20 else ""))
        for ean in sorted(hits)[:20]:
            decs = all_by_ean.get(ean, [])
            if decs:
                product_card(ean, decs, f"s_{ean}")
            else:
                p = prod_by_ean.get(ean, {})
                with st.container(border=True):
                    st.markdown(f'<div class="gr-pname">{_html.escape(p.get("Product", ean))}</div>'
                                f'<div class="gr-sub">EAN {ean} · no market decisions yet</div>', unsafe_allow_html=True)

else:
    page_title("Due for recheck", "Decisions whose recheck date has come. Old rechecks from the migration arrive 30 a day.")
    if not dgroups:
        st.info("Nothing to recheck today.")
    q = search_box("recheck")
    dshow = [(e, v) for e, v in dgroups.items() if match_group(q, e, v)]
    for ean, decs in dshow[:40]:
        product_card(ean, decs, f"r_{ean}")
    if len(dshow) > 40:
        st.caption(f"…and {len(dshow) - 40} more. Showing the first 40.")
