#!/bin/zsh
# Двойной клик → откроется GR Pipeline (Today) в браузере. Закрыть: Ctrl+C в этом окне.
cd "$(dirname "$0")"
python3 -m streamlit run app.py --server.headless false --browser.gatherUsageStats false
