"""
Credentials and Google Sheets access that work in both places the GR Pipeline runs:

* Nastya's Mac: Google = service account ~/.config/gr-pipeline-sa.json if present (same identity as the cloud),
  else her OAuth token (~/.config/sheets-write-credentials.json); keys = environment or the .env files next to the code.
* Streamlit Cloud: Google = service account from secrets ([gcp_service_account] table, passed on to
  the source scripts as env GCP_SA_JSON by app.py); keys = secrets (root-level secrets are env vars).

Also holds the Brand Approval Matrix reader so the pipeline no longer imports ../sheet_export.py.
"""
import json
import os
import unicodedata
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
WS = os.path.dirname(RESEARCH)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LOCAL_TOKEN = Path.home() / ".config" / "sheets-write-credentials.json"

BRAND_MATRIX_ID = "1aJbNQ71fUffSAR54kf6eokShWdkY2FrSk2x7DCdwtHE"
CATALOG_TAB = "catalog authorization approval"
CATALOG_MARKETS = ["AMZ US", "AMZ CA", "AMZ UK", "AMZ AU"]  # column order in that tab

ENV_FILES = [os.path.join(HERE, ".env"), os.path.join(RESEARCH, ".env")]


def secret(name, *aliases):
    """Env var (Streamlit Cloud exposes root-level secrets as env vars) → .env files → None."""
    for n in (name, *aliases):
        if os.environ.get(n):
            return os.environ[n].strip().strip('"').strip("'")
    for f in ENV_FILES:
        if os.path.exists(f):
            for line in open(f):
                for n in (name, *aliases):
                    if line.startswith(n + "="):
                        v = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                        if v:
                            return v
    return None


LOCAL_SA = Path.home() / ".config" / "gr-pipeline-sa.json"   # Vika's SA for GR Pipeline (2026-11-19), never in git


def _service_account_info():
    raw = os.environ.get("GCP_SA_JSON")
    if raw:
        return json.loads(raw)
    if LOCAL_SA.exists():                 # on the Mac: same identity as the cloud app
        return json.loads(LOCAL_SA.read_text())
    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:
        pass
    return None


def credentials():
    info = _service_account_info()
    if info:
        from google.oauth2.service_account import Credentials as SA
        return SA.from_service_account_info(info, scopes=SCOPES)
    from google.oauth2.credentials import Credentials
    if not LOCAL_TOKEN.exists():
        raise RuntimeError("No Google credentials. On Streamlit Cloud add the [gcp_service_account] table to the app's "
                           "Secrets (Manage app → Settings → Secrets) and reboot; on a Mac put the key in "
                           "~/.config/gr-pipeline-sa.json.")
    tok = json.loads(LOCAL_TOKEN.read_text())
    return Credentials(token=tok.get("access_token"), refresh_token=tok.get("refresh_token"),
                       token_uri=tok.get("token_uri"), client_id=tok.get("client_id"),
                       client_secret=tok.get("client_secret"), scopes=tok.get("scopes"))


def sheets_client():
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=credentials(), cache_discovery=False)


def _norm_brand(brand):
    """Accent/case-insensitive brand key (same as sheet_export._norm_brand)."""
    s = unicodedata.normalize("NFKD", (brand or "")).encode("ascii", "ignore").decode("ascii")
    return s.strip().lower()


def load_catalog_gating(svc=None):
    """{brand_lower: {"AMZ US": status, ...}} from the matrix's catalog authorization tab
    (same as sheet_export.load_catalog_gating). Absent brand = unknown, not gated."""
    svc = svc or sheets_client()
    res = svc.spreadsheets().values().get(
        spreadsheetId=BRAND_MATRIX_ID, range=f"'{CATALOG_TAB}'!A2:E1000").execute()
    gating = {}
    for row in res.get("values", []):
        if not row or not row[0].strip():
            continue
        gating[_norm_brand(row[0])] = {
            m: (row[i + 1].strip().lower() if len(row) > i + 1 else "") for i, m in enumerate(CATALOG_MARKETS)}
    return gating
