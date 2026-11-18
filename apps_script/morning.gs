/**
 * GR Pipeline — morning run (Google Apps Script, bound to the «GR Pipeline» sheet).
 *
 * Every morning, on Google's servers (no Mac, no Full Disk Access, no service account):
 *   0. Catalog tab ← latest seller-snap-reports daily exports on the Research shared drive
 *      (ss_<MKT>_last30days_*.csv = every live listing of all our accounts per market).
 *   1. Keepa: Fragrance listings (US / CA / UK) created in the last 30 days that now sell → Inbox (new ones only)
 *      (same rules as sources/keepa_new_asins.py, incl. catalog checks).
 *   2. Digest to Slack DM (or email if no Slack token): new items, inbox to triage,
 *      rechecks due today, pending decisions + link.
 *
 * Setup (once):
 *   Project Settings → Script properties:
 *     KEEPA_API_KEY   — required
 *     SLACK_BOT_TOKEN — optional (same bot as deviation_notifier); without it the digest goes by email
 *   Then run installDailyTrigger() once and allow access.
 *   Test any time: run morningRun().
 *
 * Filters (Nastya, 2026-11-06 / 11-11): Fragrance node only · Brand Approval Matrix: «brand selling approval»
 * must not be hard gated for that market AND «catalog authorization approval» must be «ok» or empty; brand in
 * neither tab → skip · Victoria's Secret excluded · minis < 20 ml cut.
 * Also refreshes the Brands tab (combined status per market) so the app's hints match.
 * Catalog checks (2026-11-11): brand with no Active and >50% Deactivated listings on a market is closed there
 * (Valentino US, Sisley US) · same EAN/ASIN already live on the market → skip · same product under another EAN
 * (brand + line words + size ±12%) live on the market → skip, live elsewhere → shown with a note.
 */

const CFG = {
  MARKETS: [
    { label: 'US', domain: 1, cat: '11056591', cur: '$' },
    { label: 'CA', domain: 6, cat: '6344431011', cur: 'C$' },
    { label: 'UK', domain: 2, cat: '118457031', cur: '£' },
  ],
  // Always look back 30 days: a listing often starts selling (BSR ≤ MAX_BSR) days after it appears,
  // so a «since last run» window misses it. Already-seen ASINs are skipped via the Inbox.
  LOOKBACK_DAYS: 30,
  MAX_BSR: 150000,
  MIN_VOLUME_ML: 20,
  EXCLUDED_BRANDS: ["victoria's secret"],
  BRAND_MATRIX_ID: '1aJbNQ71fUffSAR54kf6eokShWdkY2FrSk2x7DCdwtHE',
  MATRIX_TAB: 'catalog authorization approval',
  SELLING_TAB: 'brand selling approval',              // main one: «Hard Gated» = we cannot sell
  MATRIX_MARKETS: ['US', 'CA', 'UK', 'AU'],          // column order B..E in the matrix
  TZ: 'Europe/Moscow',
  HOUR: 8,
  SLACK_USER: 'U0795R217CJ',                          // Nastya's DM
  // seller-snap-reports → Research shared drive → Daily Repricing/AMZ <MKT>/Last 30 Days
  SS_FOLDERS: {
    US: '16Pzb-LZxKgbqdnbU3n4OVs5VptgYajPH',
    CA: '13I0jZHX3DeKDEvGLJJncwpOLQ-dpnLyE',
    UK: '1bZcnmEte5W1c0vRd0QfLmd3Djytn-2FL',
    AU: '16BVy43GRwPATm9ZR3uuIX0d9yXo8kIwi',
  },
};
const CATALOG_HEAD = ['Market', 'SKU', 'EAN', 'ASIN', 'Brand', 'Title', 'State', 'Qty', 'Fulfillment', 'Updated'];
const LIVE_STATES = ['Active', 'Out of Stock'];
const STOP = new Set(('eau de du des la le les el parfum perfume toilette cologne spray vapo vaporisateur vaporizador natural ' +
  'edp edt edc for women woman womens men man mens him her his pour homme femme unisex by the and with fl oz ounce ' +
  'ounces ml new size').split(' '));
const SOURCE = 'Keepa: new ASIN';
const REOPEN = 'Reopened';
const REOPEN_REASONS = ['No listing', 'Foreign-market ASIN'];
const INBOX_HEADER = ['Date', 'Source', 'Source detail', 'EAN', 'ASIN', 'Brand', 'Product', 'Signal market',
  'Signal', 'Auto check', 'Triage', 'Comment'];
const OZ_ML = 29.5735;


// ───────────────────────── entry points ─────────────────────────
function morningRun() {
  let res, cat;
  try {
    cat = refreshCatalog_();
  } catch (e) {
    cat = { error: String(e && e.message || e) };      // keep going with yesterday's Catalog tab
  }
  try {
    refreshBrands_();
  } catch (e) {
    Logger.log('Brands refresh failed: ' + e);
  }
  try {
    res = runKeepa_();
  } catch (e) {
    res = { error: String(e && e.stack || e), added: [], skipped: {} };
  }
  res.catalog = cat;
  sendDigest_(res);
}


// ───────────────────────── catalog from seller-snap-reports ─────────────────────────
function refreshCatalog_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const today = todayStr_();
  const out = [], perMarket = {};
  Object.keys(CFG.SS_FOLDERS).forEach(mk => {
    const it = DriveApp.getFolderById(CFG.SS_FOLDERS[mk]).getFiles();
    let best = null;
    while (it.hasNext()) {
      const f = it.next();
      if (f.getName().indexOf('ss_' + mk + '_last30days_') !== 0) continue;
      if (!best || f.getDateCreated() > best.getDateCreated()) best = f;
    }
    if (!best) throw new Error('no ss_' + mk + '_last30days file');
    const csv = Utilities.parseCsv(best.getBlob().getDataAsString('UTF-8').replace(/^\uFEFF/, ''));
    const h = csv[0], ix = n => h.indexOf(n);
    const iSku = ix('sku'), iAsin = ix('asin'), iTitle = ix('title'), iState = ix('listing_state'),
      iBrand = ix('brand'), iQty = ix('quantity'), iFul = ix('fulfillment');
    let n = 0;
    csv.slice(1).forEach(r => {
      if (!r[iSku]) return;
      const sku = String(r[iSku]).trim();
      const m = sku.match(/(\d{8,14})$/);
      out.push([mk, sku, m ? normEan_(m[1]) : '', r[iAsin] || '', r[iBrand] || '', String(r[iTitle] || '').slice(0, 200),
        r[iState] || '', r[iQty] || '', iFul >= 0 ? r[iFul] : '', today]);
      n++;
    });
    perMarket[mk] = n + ' (' + best.getName().replace('.csv', '').split('_').slice(-1)[0] + ')';
  });
  let sh = ss.getSheetByName('Catalog');
  if (!sh) sh = ss.insertSheet('Catalog');
  sh.clearContents();
  const rng = sh.getRange(1, 1, out.length + 1, CATALOG_HEAD.length);
  rng.setNumberFormat('@');
  rng.setValues([CATALOG_HEAD].concat(out));
  return { rows: out.length, perMarket: perMarket };
}


// ───────────────────────── catalog index (same logic as Catalog in keepa_new_asins.py) ─────────────────────────
function buildCatalog_(catRows, prods) {
  const live = {}, liveAsin = {}, items = {}, brandMk = {};
  const add = (ean, brand, title) => {
    const it = items[ean] || (items[ean] = { brand: normBrand_(brand), tokens: new Set(), size: null });
    if (brand && !it.brand) it.brand = normBrand_(brand);
    lineTokens_(title, brand).forEach(t => it.tokens.add(t));
    const v = maxVolumeMl_(title);
    if (v && !it.size) it.size = v;
  };
  catRows.forEach(r => {
    const ean = r.EAN, mk = r.Market, st = r.State;
    if (r.Brand) {
      const k = normBrand_(r.Brand) + '|' + mk;
      const c = brandMk[k] || (brandMk[k] = { total: 0 });
      c[st] = (c[st] || 0) + 1; c.total++;
    }
    if (LIVE_STATES.indexOf(st) >= 0) {
      if (ean) (live[ean] || (live[ean] = new Set())).add(mk);
      if (r.ASIN) (liveAsin[r.ASIN] || (liveAsin[r.ASIN] = new Set())).add(mk);
    }
    if (ean) add(ean, r.Brand, r.Title);
  });
  prods.forEach(p => { if (items[p.EAN] || live[p.EAN]) add(p.EAN, p.Brand, p.Product); });
  const blocked = new Set(Object.keys(brandMk).filter(k => !brandMk[k].Active && (brandMk[k].Deactivated || 0) * 2 > brandMk[k].total));
  return {
    live: live, liveAsin: liveAsin, blocked: blocked,
    sameProduct: (brand, title, exclude) => {
      const b = normBrand_(brand), toks = lineTokens_(title, brand), size = maxVolumeMl_(title);
      if (!toks.size) return null;
      for (const ean in items) {
        const it = items[ean];
        if (exclude.indexOf(ean) >= 0 || it.brand !== b || !it.tokens.size) continue;
        if (size && it.size && Math.abs(size - it.size) / Math.max(size, it.size) > 0.12) continue;
        const sub = (a, c) => [...a].every(x => c.has(x));
        if (sub(toks, it.tokens) || sub(it.tokens, toks)) return ean;   // Donna vs Uomo → no match
      }
      return null;
    },
  };
}

function lineTokens_(title, brand) {
  const norm = s => String(s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const bw = new Set(norm(brand).split(' '));
  return new Set(norm(title).split(' ').filter(w => w.length > 1 && !/\d/.test(w) && !STOP.has(w) && !bw.has(w)));
}

function installDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'morningRun')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('morningRun').timeBased().everyDays(1).atHour(CFG.HOUR).inTimezone(CFG.TZ).create();
  Logger.log('Daily trigger installed: morningRun at ~' + CFG.HOUR + ':00 ' + CFG.TZ);
}


// ───────────────────────── Keepa source ─────────────────────────
function runKeepa_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const today = todayStr_();
  const prods = readTab_(ss, 'Products');
  const decs = readTab_(ss, 'Decisions');
  const inbox = readTab_(ss, 'Inbox');
  const sources = readTab_(ss, 'Sources');
  const matrix = loadMatrix_();
  const cat = buildCatalog_(readTab_(ss, 'Catalog').rows, prods.rows);

  const srcRow = sources.rows.find(r => r.Source === SOURCE);
  const days = CFG.LOOKBACK_DAYS;

  const knownAsins = new Set();
  prods.rows.forEach(p => { if (p.ASIN) knownAsins.add(String(p.ASIN)); if (p['ASIN 2']) knownAsins.add(String(p['ASIN 2'])); });
  decs.rows.forEach(d => { if (d.ASIN) knownAsins.add(String(d.ASIN)); });
  const knownEans = new Set(prods.rows.map(p => String(p.EAN)));
  const reopenable = new Set(decs.rows
    .filter(d => REOPEN_REASONS.indexOf(d.Reason) >= 0 && d.Decision !== 'yes')
    .map(d => String(d.EAN) + '|' + d.Market));
  const inInbox = new Set(inbox.rows.map(r => r.ASIN + '|' + r['Signal market']));

  const key = PropertiesService.getScriptProperties().getProperty('KEEPA_API_KEY');
  if (!key) throw new Error('Script property KEEPA_API_KEY is not set');

  const added = [], skipped = {}, perMarket = {};
  const skip = why => { skipped[why] = (skipped[why] || 0) + 1; };

  CFG.MARKETS.forEach(m => {
    const asins = newAsins_(m, days, key);
    perMarket[m.label] = asins.length;
    products_(m, asins, key).forEach(p => {
      const asin = p.asin, mk = m.label;
      const eans = (p.eanList || []).map(normEan_).filter(Boolean);
      const ean = eans.find(e => knownEans.has(e) || cat.live[e]) || eans[0] || '';
      const brand = String(p.brand || '').trim();
      const title = String(p.title || '');
      const cur = (p.stats && p.stats.current) || [];
      const bsr = cur[3] > 0 ? cur[3] : null;
      const offers = cur[11] > 0 ? cur[11] : null;
      const bb = cur[18] > 0 ? cur[18] / 100 : (cur[1] > 0 ? cur[1] / 100 : null);

      if (inInbox.has(asin + '|' + mk)) return skip('already in Inbox');
      if (CFG.EXCLUDED_BRANDS.indexOf(normBrand_(brand)) >= 0) return skip('excluded brand (' + brand + ')');
      if (isMini_(title)) return skip('mini < ' + CFG.MIN_VOLUME_ML + ' ml');
      const bc = brandCheck_(brand, mk, matrix);
      if (!bc.ok) return skip(bc.why);

      // real catalog: brand closed on market / already selling / same product under another EAN
      if (cat.blocked.has(normBrand_(brand) + '|' + mk)) return skip('brand not sold in ' + mk + ' (catalog: Deactivated only)');
      if ((cat.liveAsin[asin] && cat.liveAsin[asin].has(mk)) || eans.some(e => cat.live[e] && cat.live[e].has(mk))) {
        return skip('already selling on this market (same EAN/ASIN)');
      }
      const same = cat.sameProduct(brand, title, eans);
      if (same && cat.live[same] && cat.live[same].has(mk)) return skip('same product already selling on market (other EAN)');
      const sameNote = same && cat.live[same] ? 'same product as EAN ' + same + ', live in ' + [...cat.live[same]].sort().join('/') : '';

      let src, detail;
      if (ean && reopenable.has(ean + '|' + mk)) {
        src = REOPEN; detail = mk + ': was No listing / other-market ASIN → listing appeared';
      } else if (knownAsins.has(asin) || (ean && knownEans.has(ean))) {
        return skip('already in catalog');
      } else {
        const since = fromKeepaMinutes_(p.trackingSince);
        src = SOURCE; detail = 'Fragrance ' + mk + ', in Keepa since ' + (since || '?');
      }
      const sig = [bsr ? 'BSR ' + bsr.toLocaleString('en-US').replace(/,/g, ' ') : 'BSR —'];
      const drops = p.stats ? p.stats.salesRankDrops30 : null;
      if (drops !== null && drops !== undefined && drops >= 0) sig.push(drops + ' rank drops/30d');
      if (p.monthlySold) sig.push('~' + p.monthlySold + ' sold/mo');
      if (offers) sig.push(offers + (offers === 1 ? ' offer' : ' offers'));
      if (bb) sig.push('BB ' + m.cur + bb.toFixed(2));
      const checks = sameNote ? [sameNote] : [];
      if (!ean) checks.push('no EAN in Keepa');
      if (eans.length > 1) checks.push(eans.length + ' EAN');
      added.push([today, src, detail, ean, asin, brand, title.slice(0, 200), mk, sig.join(' · '), checks.join('; '), '', '']);
      inInbox.add(asin + '|' + mk);
    });
  });

  // write Inbox (as plain text so EANs/dates are not reformatted)
  const sh = ss.getSheetByName('Inbox');
  if (inbox.head.join('|') !== INBOX_HEADER.join('|')) {
    sh.getRange(1, 1, 1, INBOX_HEADER.length).setValues([INBOX_HEADER]);
  }
  if (added.length) {
    const rng = sh.getRange(sh.getLastRow() + 1, 1, added.length, INBOX_HEADER.length);
    rng.setNumberFormat('@');
    rng.setValues(added);
  }
  if (srcRow) {
    const sSh = ss.getSheetByName('Sources');
    const c = sources.head.indexOf('Script') + 1, l = sources.head.indexOf('Last run') + 1;
    if (c > 0) sSh.getRange(srcRow._row, c).setValue('apps_script/morning.gs');
    if (l > 0) sSh.getRange(srcRow._row, l).setNumberFormat('@').setValue(today + ' · +' + added.length);
  }
  return { added: added, skipped: skipped, days: days, perMarket: perMarket };
}

function newAsins_(m, days, key) {
  const all = [];
  for (let page = 0; page < 10; page++) {
    const sel = {
      categories_include: [m.cat], trackingSince_gte: keepaMinutes_(days),
      current_SALES_gte: 1, current_SALES_lte: CFG.MAX_BSR,
      sort: [['current_SALES', 'asc']], perPage: 100, page: page,
    };
    const d = keepa_('query', { domain: m.domain, selection: JSON.stringify(sel) }, key);
    const batch = d.asinList || [];
    batch.forEach(a => all.push(a));
    if (batch.length < 100 || all.length >= (d.totalResults || 0)) break;
  }
  return all;
}

function products_(m, asins, key) {
  let out = [];
  for (let i = 0; i < asins.length; i += 100) {
    const d = keepa_('product', { domain: m.domain, asin: asins.slice(i, i + 100).join(','), stats: 30 }, key);
    out = out.concat(d.products || []);
  }
  return out;
}

function keepa_(path, params, key) {
  params.key = key;
  const qs = Object.keys(params).map(k => k + '=' + encodeURIComponent(params[k])).join('&');
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = UrlFetchApp.fetch('https://api.keepa.com/' + path + '?' + qs, { muteHttpExceptions: true });
    if (r.getResponseCode() === 429) { Utilities.sleep(60000); continue; }
    const d = JSON.parse(r.getContentText());
    if (d.error) throw new Error('Keepa ' + path + ': ' + JSON.stringify(d.error));
    return d;
  }
  throw new Error('Keepa ' + path + ': out of tokens (429)');
}


// ───────────────────────── digest ─────────────────────────
function sendDigest_(res) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const url = ss.getUrl();
  const today = todayStr_();
  const inbox = readTab_(ss, 'Inbox');
  const decs = readTab_(ss, 'Decisions');
  const toTriage = inbox.rows.filter(r => r.ASIN && !r.Triage).length;
  const shown = decs.rows.filter(d => d['Show'] === 'yes');
  const pending = shown.filter(d => !d.Decision).length;
  const due = shown.filter(d => d.Decision === 'recheck' && dateStr_(d['Recheck on']) && dateStr_(d['Recheck on']) <= today).length;

  const lines = [];
  lines.push('*GR Pipeline · ' + today + '*');
  if (res.catalog && res.catalog.error) lines.push('⚠️ Catalog refresh failed (using yesterday\'s): ' + res.catalog.error);
  if (res.error) {
    lines.push('⚠️ Keepa run failed: ' + res.error.split('\n')[0]);
  } else if (res.note) {
    lines.push('Keepa: ' + res.note);
  } else {
    const pm = Object.keys(res.perMarket || {}).map(k => k + ' ' + res.perMarket[k]).join(', ');
    lines.push('🆕 New in Inbox: *' + res.added.length + '* (Keepa checked last ' + res.days + ' days; new listings ' + pm + ')');
    res.added.slice(0, 10).forEach(a => lines.push('   • ' + a[7] + ' · ' + a[5] + ' — ' + a[6].slice(0, 70) + ' · ' + a[8]));
    if (res.added.length > 10) lines.push('   …and ' + (res.added.length - 10) + ' more');
  }
  lines.push('📥 Inbox to triage: *' + toTriage + '*');
  lines.push('🔁 Rechecks due: *' + due + '*');
  lines.push('⏳ Market decisions pending: *' + pending + '*');
  lines.push('<' + url + '|Open GR Pipeline sheet> · open the app: GR Today.command');
  const text = lines.join('\n');

  const token = PropertiesService.getScriptProperties().getProperty('SLACK_BOT_TOKEN');
  if (token) {
    const r = UrlFetchApp.fetch('https://slack.com/api/chat.postMessage', {
      method: 'post', contentType: 'application/json; charset=utf-8', muteHttpExceptions: true,
      headers: { Authorization: 'Bearer ' + token },
      payload: JSON.stringify({ channel: CFG.SLACK_USER, text: text, unfurl_links: false }),
    });
    const body = JSON.parse(r.getContentText());
    if (body.ok) return;
    Logger.log('Slack failed: ' + r.getContentText() + ' — falling back to email');
  }
  MailApp.sendEmail(Session.getEffectiveUser().getEmail(), 'GR Pipeline · ' + today,
    text.replace(/\*/g, '').replace(/<([^|>]+)\|([^>]+)>/g, '$2: $1'));
}


// ───────────────────────── helpers ─────────────────────────
function readTab_(ss, name) {
  const sh = ss.getSheetByName(name);
  const v = sh ? sh.getDataRange().getDisplayValues() : [[]];
  const head = v[0] || [];
  const rows = v.slice(1).map((r, i) => {
    const o = { _row: i + 2 };
    head.forEach((h, j) => { o[h] = r[j]; });
    return o;
  });
  return { head: head, rows: rows };
}

function loadMatrix_() {
  const book = SpreadsheetApp.openById(CFG.BRAND_MATRIX_ID);
  const read = tab => {
    const sh = book.getSheetByName(tab);
    const v = sh.getRange(2, 1, Math.max(sh.getLastRow() - 1, 1), 5).getDisplayValues();
    const out = {};
    v.forEach(r => {
      if (!String(r[0]).trim()) return;
      const st = {};
      CFG.MATRIX_MARKETS.forEach((mk, i) => { st[mk] = String(r[i + 1] || '').trim().toLowerCase(); });
      out[normBrand_(r[0])] = st;
    });
    return out;
  };
  return { catalog: read(CFG.MATRIX_TAB), selling: read(CFG.SELLING_TAB) };
}

function sellingBlocked_(s) {
  s = String(s || '').trim().toLowerCase();
  return s.indexOf('gated') >= 0 && s.indexOf('ok') !== 0 && s.indexOf('ungated') !== 0;
}

function brandCheck_(brand, mk, matrix) {
  const b = normBrand_(brand), sell = matrix.selling[b], cat = matrix.catalog[b];
  if (!sell && !cat) return { ok: false, why: 'brand not in matrix' };
  if (sell && sellingBlocked_(sell[mk])) return { ok: false, why: 'brand hard gated (selling) in ' + mk };
  const st = cat ? cat[mk] : '';
  if (st !== 'ok' && st !== '') return { ok: false, why: 'brand not approved for catalog in ' + mk + ' (' + st + ')' };
  return { ok: true };
}

// Brands tab = combined matrix status per market (same as sources/brands_refresh.py); keeps «Следить» / «Комментарий»
function refreshBrands_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const matrix = loadMatrix_();
  const old = {};
  readTab_(ss, 'Brands').rows.forEach(r => { old[normBrand_(r.Brand)] = r; });
  const counts = {};
  readTab_(ss, 'Products').rows.forEach(p => { if (p.Brand) counts[p.Brand] = (counts[p.Brand] || 0) + 1; });
  readTab_(ss, 'Catalog').rows.forEach(r => { if (r.Brand && !(r.Brand in counts)) counts[r.Brand] = 0; });
  const head = ['Brand', 'US', 'CA', 'UK', 'AU', 'Products in catalog', 'Watch new releases', 'Comment'];
  const rows = Object.keys(counts).sort((a, b) => counts[b] - counts[a]).map(b => {
    const k = normBrand_(b), sell = matrix.selling[k], cat = matrix.catalog[k], o = old[k] || {};
    const st = CFG.MATRIX_MARKETS.map(mk => {
      if (!sell && !cat) return 'not in matrix';
      const s = sell ? sell[mk] : '', c = cat ? cat[mk] : '';
      if (sellingBlocked_(s)) return s;
      return c || s || '—';
    });
    return [b].concat(st, [counts[b], o['Watch new releases'] || '', o['Comment'] || '']);
  });
  const sh = ss.getSheetByName('Brands');
  sh.clearContents();
  const rng = sh.getRange(1, 1, rows.length + 1, head.length);
  rng.setNumberFormat('@');
  rng.setValues([head].concat(rows));
}

function normBrand_(b) {
  return String(b || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^\x00-\x7F]/g, '').trim().toLowerCase();
}

function normEan_(e) {
  const d = String(e || '').replace(/\D/g, '');
  return d.length > 8 && d.length < 13 ? ('0000000000000' + d).slice(-13) : d;
}

function maxVolumeMl_(title) {
  const t = String(title).toLowerCase().replace(/,/g, '.');
  const vols = [];
  let m;
  const ml = /(\d+(?:\.\d+)?)\s*ml\b/g;
  while ((m = ml.exec(t))) vols.push(parseFloat(m[1]));
  const oz = /(\d+(?:\.\d+)?)\s*(?:fl\.?\s*oz|ounces?|oz)\b/g;
  while ((m = oz.exec(t))) vols.push(parseFloat(m[1]) * OZ_ML);
  return vols.length ? Math.max.apply(null, vols) : null;
}

function isMini_(title) {
  const v = maxVolumeMl_(title);
  if (v !== null) return v < CFG.MIN_VOLUME_ML;
  return /\b(mini|miniature|sample|vial|travel size)\b/.test(String(title).toLowerCase());
}

function keepaMinutes_(daysAgo) {
  return Math.floor(Date.now() / 60000) - 21564000 - daysAgo * 1440;
}

function fromKeepaMinutes_(m) {
  if (!m || m <= 0) return '';
  return Utilities.formatDate(new Date((m + 21564000) * 60000), 'UTC', 'yyyy-MM-dd');
}

function todayStr_() {
  return Utilities.formatDate(new Date(), CFG.TZ, 'yyyy-MM-dd');
}

function dateStr_(v) {
  const s = String(v || '').trim();
  return /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : '';
}
