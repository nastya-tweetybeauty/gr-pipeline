# GR Pipeline on Streamlit Community Cloud

The same code runs locally (Nastya's OAuth token + `.env`) and on Streamlit Cloud (service account + secrets). See `gcp.py`.

## One-time setup

1. **GitHub**: private repo `tweety-beauty-ai/gr-pipeline` containing this folder (`.gitignore` keeps keys, backups and xlsx out; Vika's ROI engine is committed as `engine/core.py`, see `engine/SOURCE.txt`).
2. **Google service account** (console.cloud.google.com → project → IAM & Admin → Service accounts):
   - enable *Google Sheets API* in the project;
   - create a service account → Keys → Add key → JSON;
   - share the **GR Pipeline** sheet with its `client_email` as **Editor**, and the **Brand Approval Matrix** as **Viewer**.
3. **Streamlit Cloud** (share.streamlit.io, sign in with GitHub):
   - Create app → repo `tweety-beauty-ai/gr-pipeline`, branch `main`, main file `app.py`, Python 3.11;
   - Advanced settings → Secrets: paste `secrets.example.toml` filled in (Keepa key, Apify token, service-account JSON fields);
   - Sharing: public, like Vika's calculator (decision 2026-11-19) — anyone with the link can open the app and write decisions, so share the link only inside the team.

## Behaviour on the cloud

- Writes are done by the service account. The app is public (no sign-in), so the *By* column is always "Nastya".
- The daily Keepa source still runs on the first open of the day (and from the Apps Script morning trigger).
- `sources/ss_catalog.py` (Catalog tab from SellerSnap exports on Drive) stays local-only for now.

## Updating

Push to `main`; Streamlit Cloud redeploys automatically.
