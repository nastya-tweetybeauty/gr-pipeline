import datetime as dt
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import FormulaRule
from openpyxl.utils import get_column_letter

OUT = "/private/tmp/claude-502/-Users-a4721621-Desktop-claude-workspace/63b1b94a-28ed-4812-bb6d-50e7be262718/scratchpad/gr/GR_Pipeline_mockup.xlsx"
F = "Arial"
HDR_FILL = PatternFill("solid", fgColor="1F3A5F")
HDR_FONT = Font(name=F, bold=True, color="FFFFFF", size=10)
INPUT_FILL = PatternFill("solid", fgColor="FFF4C2")   # Настя заполняет
SCRIPT_FONT = Font(name=F, color="0000FF", size=10)    # заполняет скрипт
FORMULA_FONT = Font(name=F, color="000000", size=10)
LINK_FONT = Font(name=F, color="008000", size=10)      # тянется с другой вкладки
BASE = Font(name=F, size=10)
TITLE = Font(name=F, bold=True, size=14, color="1F3A5F")
SUB = Font(name=F, italic=True, size=9, color="666666")
BANNER = PatternFill("solid", fgColor="FDE2E1")
thin = Side(style="thin", color="D9D9D9")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
N = 500  # rows covered by range formulas

TODAY = dt.date(2026, 11, 6)
d = lambda days: TODAY - dt.timedelta(days=days)

wb = Workbook()


def sheet(name, title, subtitle, headers, widths, kinds):
    """kinds: per column 'in' (Настя), 'sc' (скрипт), 'fx' (формула), 'ln' (с другой вкладки)."""
    ws = wb.create_sheet(name)
    ws["A1"] = title; ws["A1"].font = TITLE
    ws["A2"] = subtitle; ws["A2"].font = SUB
    for i, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(row=3, column=i, value=h)
        c.font = HDR_FONT; c.fill = HDR_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[3].height = 32
    ws.freeze_panes = "A4"
    ws._kinds = kinds
    return ws


def put(ws, row, values):
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=v)
        k = ws._kinds[i - 1]
        c.font = {"in": BASE, "sc": SCRIPT_FONT, "fx": FORMULA_FONT, "ln": LINK_FONT}[k]
        if k == "in":
            c.fill = INPUT_FILL
        c.border = BORDER
        if isinstance(v, dt.date):
            c.number_format = "yyyy-mm-dd"


def style_empty_inputs(ws, first, last):
    for r in range(first, last + 1):
        for i, k in enumerate(ws._kinds, 1):
            c = ws.cell(row=r, column=i)
            if k == "in":
                c.fill = INPUT_FILL
            c.border = BORDER


def dv(ws, formula, rng):
    v = DataValidation(type="list", formula1=formula, allow_blank=True)
    ws.add_data_validation(v); v.add(rng)


# ───────────────────────── README ─────────────────────────
ws = wb.active; ws.title = "README"
ws.column_dimensions["A"].width = 24; ws.column_dimensions["B"].width = 110
lines = [
    ("GR Pipeline — макет новой структуры", None, TITLE),
    ("Все данные в файле — ПРИМЕРЫ (реальные EAN из текущей GR-таблицы, но цены других поставщиков, Buy Box и ROI выдуманы для демонстрации).", None, Font(name=F, bold=True, color="C00000", size=10)),
    ("", None, BASE),
    ("Идея", "Таблица — не реестр того, что уже решено, а воронка, которая каждый день сама отдаёт работу. Скрипты собирают сигналы и считают цифры; Настя только принимает решения.", BASE),
    ("", None, BASE),
    ("ВКЛАДКИ", None, Font(name=F, bold=True, size=11)),
    ("Today", "Ежедневный экран (15–20 мин): что нового во входящих, что ждёт решения, что пора перепроверить.", BASE),
    ("Inbox", "Лента сигналов «появился новый товар». Строку = один сигнал. Пишут скрипты (поставщики, Keepa, конкуренты, анонсы). Настя ставит Triage.", BASE),
    ("Products", "Каталог: 1 строка = 1 EAN. Постоянные факты о товаре + на какой стадии воронки он сейчас.", BASE),
    ("Offers", "Цены поставщиков: 1 строка = EAN × поставщик × дата. Сколько угодно поставщиков на товар, история цен. Запрещённые поставщики (PC) видны, но не участвуют в «лучшей цене».", BASE),
    ("Decisions", "Решения: 1 строка = EAN × маркет. Цифры (Buy Box, продажи, Max Buy Price из калькулятора Вики), подсказка-вердикт формулой, решение Насти, дата следующей перепроверки.", BASE),
    ("Market view", "Привычный широкий вид «EAN × маркеты» — собирается формулами из Decisions, руками не редактируется. Для команды, которая привыкла к старому формату.", BASE),
    ("Brands", "Бренды: gated по маркетам (из Brand Approval Matrix Чада), отслеживаем ли новые ASIN бренда.", BASE),
    ("Sources", "Источники сигналов и их эффективность: сколько сигналов, сколько дошло до approved. Раз в месяц — выкидываем шумные.", BASE),
    ("Settings", "Минимальный ROI по маркетам (как в ub-cost-calculator), список поставщиков с флагом «можно закупать».", BASE),
    ("", None, BASE),
    ("ЦВЕТА", None, Font(name=F, bold=True, size=11)),
    ("Жёлтая заливка", "заполняет Настя (решения, triage, комментарии)", BASE),
    ("Синий текст", "заполняет скрипт (Keepa, поставщики, калькулятор Вики)", Font(name=F, color="0000FF", size=10)),
    ("Чёрный текст", "формула внутри вкладки — не трогать", BASE),
    ("Зелёный текст", "формула, тянет данные с другой вкладки — не трогать", Font(name=F, color="008000", size=10)),
    ("", None, BASE),
    ("СТАТУСЫ ВОРОНКИ", None, Font(name=F, bold=True, size=11)),
    ("Inbox → Triage", "research (берём в работу → строка в Products) · skip (не наше) · duplicate (уже есть)", BASE),
    ("Decisions → Decision", "yes · no · recheck. Для no/recheck формула ставит дату перепроверки (recheck +7 дн, no +30 дн) — строка вернётся в Today сама.", BASE),
    ("Products → Stage", "new → в работе → approved (≥1 маркет yes) → listing", BASE),
    ("", None, BASE),
    ("ROI", "Max Buy Price и ROI — из ub-cost-calculator (Вика, github.com/tweety-beauty-ai/ub-cost-calculator): та же формула COGS/fees, реальные freight/customs по EAN и FBA fee из SellerSnap/Keepa. Для Walmart расчёта в калькуляторе нет — пока считаем отдельно.", BASE),
    ("Перенос старых данных", "5 191 строку из текущей GR-таблицы разложит скрипт: Products (EAN/бренд/тип), Offers (Buy Price + Supplier), Decisions (7 маркетов × Do we proceed / Research comment).", BASE),
]
for r, (a, b, font) in enumerate(lines, 1):
    ws.cell(row=r, column=1, value=a).font = font if b is None else Font(name=F, bold=True, size=10)
    if b:
        c = ws.cell(row=r, column=2, value=b); c.font = font; c.alignment = Alignment(wrap_text=True, vertical="top")
ws["A1"].font = TITLE
for r, fill in ((18, INPUT_FILL),):
    ws.cell(row=r, column=1).fill = fill

# ───────────────────────── Settings ─────────────────────────
st = sheet("Settings", "Settings", "ROI-минимумы = ROI_MINIMUMS из ub-cost-calculator (Вика, 2 Nov 2026). Поставщики — из колонки Supplier текущей GR-таблицы.",
           ["Market", "Min ROI", "Источник", "", "Supplier", "Можно закупать?", "Комментарий"],
           [10, 10, 40, 3, 20, 16, 40], ["in", "in", "in", "in", "in", "in", "in"])
roi = [("US", 0.17, "ub-cost-calculator"), ("CA", 0.20, "ub-cost-calculator"), ("UK", 0.15, "ub-cost-calculator"),
       ("AU", 0.17, "ub-cost-calculator (отдельного минимума пока нет)"), ("WM", None, "нет в калькуляторе — уточнить")]
sup = [("aromas", "yes", ""), ("arenal", "yes", ""), ("douglas", "yes", ""), ("sabina", "yes", ""),
       ("paco perfumerias", "yes", ""), ("nova engel", "yes", ""), ("perfumes club", "no", "не закупаем — только как сигнал о новинках")]
for i in range(max(len(roi), len(sup))):
    row = 4 + i
    a = roi[i] if i < len(roi) else ("", None, "")
    s = sup[i] if i < len(sup) else ("", "", "")
    put(st, row, [a[0], a[1], a[2], None, s[0], s[1], s[2]])
    st.cell(row=row, column=2).number_format = "0%"
    st.cell(row=row, column=4).fill = PatternFill(); st.cell(row=row, column=4).border = Border()
dv(st, '"yes,no"', "F4:F30")

# ───────────────────────── Brands ─────────────────────────
br = sheet("Brands", "Brands", "Gated-колонки обновляет скрипт из Brand Approval Matrix (Чад). «Следить» = скрипт ищет новые ASIN бренда в Keepa каждый день.",
           ["Brand", "US gated", "CA gated", "UK gated", "AU gated", "WM gated", "Следить за новинками", "Комментарий"],
           [22, 10, 10, 10, 10, 10, 14, 40], ["in", "sc", "sc", "sc", "sc", "sc", "in", "in"])
brands = [("Chloe", "no", "no", "no", "no", "no", "yes", ""),
          ("Guerlain", "no", "no", "no", "no", "no", "yes", ""),
          ("Carolina Herrera", "no", "no", "no", "no", "no", "yes", ""),
          ("Yves Saint Laurent", "yes", "no", "no", "no", "no", "yes", "US gated — снимется → все строки US вернутся в Today"),
          ("Jean Paul Gaultier", "yes", "no", "no", "no", "no", "no", "GS1-проблема с EAN")]
for i, b in enumerate(brands):
    put(br, 4 + i, list(b))
dv(br, '"yes,no"', "B4:G300")

# ───────────────────────── Sources ─────────────────────────
so = sheet("Sources", "Sources", "Каждый источник пишет в Inbox. Signals / In work / Approved считаются формулами. Раз в месяц: источник с нулевым Approved — отключаем.",
           ["Source", "Тип", "Как работает", "Частота", "Скрипт", "Последний запуск", "Signals (все)", "Взято в работу", "Approved (≥1 маркет)", "Hit rate"],
           [26, 14, 60, 10, 26, 14, 10, 10, 12, 10],
           ["in", "in", "in", "in", "in", "sc", "fx", "ln", "ln", "fx"])
sources = [
    ("Поставщик: новинки", "supply", "Новинки на сайтах поставщиков (Aromas, Sabina, Nova Engel…; PC — только как сигнал)", "ежедневно", "novedades_scraper.py", d(0)),
    ("Keepa: новый ASIN", "demand", "ASIN в Fragrance, созданные за 7–30 дней, с monthlySold > 0 (US/CA/UK)", "ежедневно", "новый", d(0)),
    ("Keepa: бренд", "demand", "Новые ASIN брендов с флагом «Следить» во вкладке Brands", "ежедневно", "новый", d(0)),
    ("Конкурент: витрина", "demand", "ASIN, которые добавили продавцы, торгующие теми же товарами, что и мы (Keepa /seller storefront)", "ежедневно", "новый", d(0)),
    ("Анонс бренда", "launch", "Анонсы запусков (Fragrantica, пресс-релизы) — товара ещё нет на Amazon, можно зайти первыми", "еженедельно", "новый", d(3)),
    ("Переоткрыто", "reopen", "no listing → листинг появился; gated → бренд одобрен; low roi → цена поставщика упала / BB вырос", "ежедневно", "формулы + скрипт", d(0)),
]
for i, s in enumerate(sources):
    r = 4 + i
    put(so, r, list(s) + [
        f'=COUNTIF(Inbox!$B$4:$B${N},A{r})',
        f'=COUNTIF(Products!$H$4:$H${N},A{r})',
        f'=COUNTIFS(Products!$H$4:$H${N},A{r},Products!$K$4:$K${N},">0")',
        f'=IF(G{r}=0,"",I{r}/G{r})'])
    so.cell(row=r, column=10).number_format = "0%"

# ───────────────────────── Inbox ─────────────────────────
ib = sheet("Inbox", "Inbox — входящие сигналы", "Пишут скрипты (синий). «Уже в Products?» и «Gated?» — формулы-автофильтр. Настя ставит Triage: research → строка уходит в Products.",
           ["Дата", "Source", "Деталь источника", "EAN", "ASIN", "Brand", "Product", "Маркет сигнала", "Сигнал", "Уже в Products?", "Gated где-то?", "Triage", "Комментарий"],
           [11, 20, 30, 15, 13, 18, 46, 9, 34, 11, 10, 11, 30],
           ["sc", "sc", "sc", "sc", "sc", "sc", "sc", "sc", "sc", "fx", "ln", "in", "in"])
inbox = [
    (d(0), "Keepa: новый ASIN", "Fragrance US, создан 2026-10-28", "3616307650960", "B0HJXL2RCD", "Chloe", "Chloé Le Nectar EDP 100ml Refillable", "US", "~300 sold/mo, 3 оффера, BB $142", "research", ""),
    (d(0), "Конкурент: витрина", "продавец X добавил ASIN", "3346470624535", "B0HHJ6WTXC", "Guerlain", "Guerlain L'Heure Bleue EDP 100ml", "US", "~100 sold/mo, 5 офферов", "research", ""),
    (d(0), "Поставщик: новинки", "aromas.es / estuches", "8411061142417", "B0GXR7JT7Q", "Carolina Herrera", "Carolina Herrera La Bomba Gift Set 80ml EDP + 100ml Lotion + 10ml", "—", "новинка у поставщика, €98", "research", ""),
    (d(1), "Анонс бренда", "fragrantica.com", "3614274521368", "B0GK9KXK3T", "Yves Saint Laurent", "YSL Libre Berry Crush EDP 30ml", "—", "запуск ноябрь 2026", "research", ""),
    (d(1), "Поставщик: новинки", "perfumesclub.com / novedades", "3614275003092", "", "Yves Saint Laurent", "YSL Y Gift Set 100ml EDP + 50ml Gel + 10ml", "—", "новинка у поставщика (PC — только сигнал)", "research", "ASIN не найден"),
    (d(0), "Переоткрыто", "UK: no listing → листинг появился", "8410225598053", "B0HHQQSNDK", "Jean Paul Gaultier", "JPG La Favorite EDP 100ml", "UK", "листинг создан 2026-11-03", "research", ""),
    (d(0), "Keepa: бренд", "бренд Chloe, новый ASIN", "3616307650953", "B0HJXPBF3R", "Chloe", "Chloé Le Nectar EDP 50ml Refillable", "US", "~120 sold/mo", "", ""),
    (d(0), "Keepa: новый ASIN", "Fragrance UK, создан 2026-11-02", "", "B0HQXXXXXX", "Unknown brand", "Dupe 'inspired by' perfume 100ml", "UK", "~50 sold/mo", "", ""),
]
for i, x in enumerate(inbox):
    r = 4 + i
    put(ib, r, list(x[:9]) + [
        f'=IF(D{r}="","нет EAN",IF(COUNTIF(Products!$A$4:$A${N},D{r})>0,"да","нет"))',
        f'=IF(COUNTIF(Brands!$A$4:$A$300,F{r})=0,"бренд ?",IF(COUNTIF(INDEX(Brands!$B$4:$F$300,MATCH(F{r},Brands!$A$4:$A$300,0),0),"yes")>0,"да","нет"))',
        x[9], x[10]])
style_empty_inputs(ib, 4 + len(inbox), 60)
dv(ib, '"research,skip,duplicate"', f"L4:L{N}")

# ───────────────────────── Products ─────────────────────────
pr = sheet("Products", "Products — каталог (1 строка = 1 EAN)", "Факты о товаре. Лучшая цена и стадия — формулы из Offers и Decisions.",
           ["EAN", "Brand", "Product", "Тип", "Объём, ml", "Origin", "Добавлен", "Первый источник", "ASIN (US)", "Лучшая цена, € (разреш.)", "Маркетов yes", "Маркетов ждут решения", "Stage"],
           [15, 18, 46, 12, 9, 15, 11, 20, 13, 12, 9, 10, 12],
           ["sc", "sc", "sc", "in", "sc", "in", "sc", "sc", "sc", "ln", "ln", "ln", "fx"])
products = [
    ("3616307650960", "Chloe", "Chloé Le Nectar EDP 100ml Refillable", "EDP", 100, "new release", d(0), "Keepa: новый ASIN", "B0HJXL2RCD"),
    ("3346470624535", "Guerlain", "Guerlain L'Heure Bleue EDP 100ml", "EDP", 100, "new release", d(0), "Конкурент: витрина", "B0HHJ6WTXC"),
    ("8411061142417", "Carolina Herrera", "Carolina Herrera La Bomba Gift Set 80ml EDP + 100ml Lotion + 10ml", "gift set", 80, "gift set", d(0), "Поставщик: новинки", "B0GXR7JT7Q"),
    ("3614274521368", "Yves Saint Laurent", "YSL Libre Berry Crush EDP 30ml", "EDP", 30, "new release", d(1), "Анонс бренда", "B0GK9KXK3T"),
    ("3614275003092", "Yves Saint Laurent", "YSL Y Gift Set 100ml EDP + 50ml Gel + 10ml", "gift set", 100, "gift set", d(1), "Поставщик: новинки", ""),
    ("8410225598053", "Jean Paul Gaultier", "JPG La Favorite EDP 100ml", "EDP", 100, "existed on market", d(40), "Переоткрыто", "B0HHQQSNDK"),
]
for i, p in enumerate(products):
    r = 4 + i
    put(pr, r, list(p) + [
        f'=IF(COUNTIFS(Offers!$A$4:$A${N},A{r},Offers!$H$4:$H${N},"yes")=0,"нет поставщика",_xlfn.MINIFS(Offers!$C$4:$C${N},Offers!$A$4:$A${N},A{r},Offers!$H$4:$H${N},"yes"))',
        f'=COUNTIFS(Decisions!$B$4:$B${N},A{r},Decisions!$R$4:$R${N},"yes")',
        f'=COUNTIFS(Decisions!$B$4:$B${N},A{r},Decisions!$R$4:$R${N},"")',
        f'=IF(K{r}>0,"approved",IF(L{r}>0,"в работе","закрыт"))'])
    pr.cell(row=r, column=10).number_format = '#,##0.00'
style_empty_inputs(pr, 4 + len(products), 60)

# ───────────────────────── Offers ─────────────────────────
of = sheet("Offers", "Offers — цены поставщиков (1 строка = EAN × поставщик × дата)", "Пишут скрипты поставщиков. «Можно закупать» — из Settings. «Лучшая?» отмечает минимальную разрешённую цену по EAN.",
           ["EAN", "Supplier", "Цена, € excl VAT", "Дата цены", "В наличии", "Ссылка", "Product (из Products)", "Можно закупать", "Лучшая?", "ключ"],
           [15, 18, 12, 11, 9, 34, 40, 11, 9, 22],
           ["sc", "sc", "sc", "sc", "sc", "sc", "ln", "ln", "fx", "fx"])
offers = [
    ("3616307650960", "perfumes club", 80.00, d(1), "yes", "perfumesclub.com/…"),
    ("3616307650960", "aromas", 82.50, d(0), "yes", "aromas.es/…"),
    ("3616307650960", "sabina", 84.10, d(0), "yes", "sabina portal"),
    ("3346470624535", "perfumes club", 126.00, d(2), "yes", "perfumesclub.com/…"),
    ("3346470624535", "douglas", 131.00, d(0), "yes", "douglas.es/…"),
    ("8411061142417", "perfumes club", 100.00, d(3), "yes", "perfumesclub.com/…"),
    ("8411061142417", "aromas", 98.00, d(0), "yes", "aromas.es/…"),
    ("3614274521368", "perfumes club", 57.95, d(5), "yes", "perfumesclub.com/…"),
    ("3614274521368", "arenal", 59.90, d(0), "no", "perfumeriasarenal.com/…"),
    ("3614275003092", "perfumes club", 95.00, d(1), "yes", "perfumesclub.com/…"),
    ("8410225598053", "perfumes club", 90.00, d(10), "yes", "perfumesclub.com/…"),
    ("8410225598053", "paco perfumerias", 92.00, d(0), "yes", "pacoperfumerias.com/…"),
]
for i, o in enumerate(offers):
    r = 4 + i
    put(of, r, list(o) + [
        f'=IFERROR(INDEX(Products!$C$4:$C${N},MATCH(A{r},Products!$A$4:$A${N},0)),"⚠ нет в Products")',
        f'=IFERROR(INDEX(Settings!$F$4:$F$30,MATCH(B{r},Settings!$E$4:$E$30,0)),"новый поставщик?")',
        f'=IF(H{r}<>"yes","",IF(C{r}=_xlfn.MINIFS($C$4:$C${N},$A$4:$A${N},A{r},$H$4:$H${N},"yes"),"✓",""))',
        f'=IF(H{r}="yes",A{r}&"|"&TEXT(C{r},"0.00"),"")'])
    of.cell(row=r, column=3).number_format = '#,##0.00'
style_empty_inputs(of, 4 + len(offers), 60)

# ───────────────────────── Decisions ─────────────────────────
de = sheet("Decisions", "Decisions — решения (1 строка = EAN × маркет)",
           "Синие колонки заполнят скрипты (Keepa + ub-cost-calculator). «Подсказка» — формула. Решение (жёлтое) — Настя. Дата перепроверки ставится сама.",
           ["ключ", "EAN", "Market", "Product", "Brand", "Gated?", "ASIN", "Buy Box (лок. валюта)", "Продаж/мес", "Офферов",
            "Лучшая цена, €", "Поставщик", "Max Buy Price, € (калькулятор Вики)", "Min ROI", "Запас, € (MBP − цена)", "ROI при лучшей цене",
            "Подсказка", "Decision", "Reason", "Решено", "Перепроверить"],
           [20, 15, 8, 36, 16, 8, 13, 11, 9, 8, 10, 14, 13, 8, 10, 10, 26, 10, 26, 11, 12],
           ["fx", "sc", "sc", "ln", "ln", "ln", "sc", "sc", "sc", "sc", "ln", "ln", "sc", "ln", "fx", "sc", "fx", "in", "in", "in", "fx"])
mkcol = {"US": "B", "CA": "C", "UK": "D", "AU": "E", "WM": "F"}
dec = [
    # EAN, mkt, ASIN, BB, sold, offers, MBP, ROI, decision, reason, decided
    ("3616307650960", "US", "B0HJXL2RCD", 142.00, 300, 3, 91.40, 0.26, "", "", None),
    ("3616307650960", "CA", "", None, None, None, None, None, "", "", None),
    ("3616307650960", "UK", "B0HJXL2RCD", 109.00, 80, 6, 79.10, 0.12, "", "", None),
    ("3346470624535", "US", "B0HHJ6WTXC", 189.00, 100, 5, 128.30, 0.14, "", "", None),
    ("8411061142417", "AU", "B0GXR7JT7Q", 239.00, 60, 2, 104.20, 0.22, "yes", "Approved", d(0)),
    ("8411061142417", "US", "B0GXR7JT7Q", 128.00, 150, 9, 82.00, 0.05, "no", "Declined: low roi", d(0)),
    ("3614274521368", "US", "B0GK9KXK3T", None, None, None, None, None, "", "", None),
    ("3614274521368", "UK", "B0GK9KXK3T", 72.00, 200, 4, 51.80, 0.08, "", "", None),
    ("3614275003092", "US", "", None, None, None, None, None, "", "", None),
    ("8410225598053", "UK", "B0HHQQSNDK", 118.00, 90, 3, 86.50, 0.11, "recheck", "мало офферов, BB нестабилен", d(8)),
]
for i, x in enumerate(dec):
    r = 4 + i
    ean, mkt, asin, bb, sold, offs, mbp, roi_v, decision, reason, decided = x
    put(de, r, [
        f'=B{r}&"|"&C{r}', ean, mkt,
        f'=IFERROR(INDEX(Products!$C$4:$C${N},MATCH(B{r},Products!$A$4:$A${N},0)),"")',
        f'=IFERROR(INDEX(Products!$B$4:$B${N},MATCH(B{r},Products!$A$4:$A${N},0)),"")',
        f'=IFERROR(INDEX(Brands!$B$4:$F$300,MATCH(E{r},Brands!$A$4:$A$300,0),MATCH(C{r}&" gated",Brands!$B$3:$F$3,0)),"?")',
        asin, bb, sold, offs,
        f'=IFERROR(INDEX(Products!$J$4:$J${N},MATCH(B{r},Products!$A$4:$A${N},0)),"")',
        f'=IF(ISNUMBER(K{r}),IFERROR(INDEX(Offers!$B$4:$B${N},MATCH(B{r}&"|"&TEXT(K{r},"0.00"),Offers!$J$4:$J${N},0)),""),"")',
        mbp,
        f'=IFERROR(INDEX(Settings!$B$4:$B$10,MATCH(C{r},Settings!$A$4:$A$10,0)),"")',
        f'=IF(AND(ISNUMBER(M{r}),ISNUMBER(K{r})),M{r}-K{r},"")',
        roi_v,
        (f'=IF(F{r}="yes","Declined: gated",IF(G{r}="","нет листинга → recheck",IF(NOT(ISNUMBER(K{r})),"нет разрешённого поставщика",'
         f'IF(NOT(ISNUMBER(M{r})),"ждёт расчёта ROI",IF(O{r}>=0,"✅ Approve?","Declined: low roi")))))'),
        decision, reason, decided,
        f'=IF(T{r}="","",IF(R{r}="recheck",T{r}+7,IF(R{r}="no",T{r}+30,"")))',
    ])
    for col, fmt in (("H", '#,##0.00'), ("K", '#,##0.00'), ("M", '#,##0.00'), ("O", '#,##0.00;[Red]-#,##0.00'),
                     ("N", "0%"), ("P", "0%"), ("U", "yyyy-mm-dd"), ("T", "yyyy-mm-dd")):
        de[f"{col}{r}"].number_format = fmt
style_empty_inputs(de, 4 + len(dec), 80)
dv(de, '"yes,no,recheck"', f"R4:R{N}")
dv(de, '"US,CA,UK,AU,WM,WM CA,eBay"', f"C4:C{N}")
dv(de, '"Approved,Approved: by request,Declined: gated,Declined: low roi,Declined: low sales,Declined: no listing,Declined: tester,Declined: old version,Declined: wrong EAN,Declined: discontinued,Declined: incl. NDG,Declined: gift set"', f"S4:S{N}")
green = PatternFill("solid", fgColor="D9F2D9"); red = PatternFill("solid", fgColor="F8D7D7")
de.conditional_formatting.add(f"Q4:Q{N}", FormulaRule(formula=['LEFT($Q4,1)="✅"'], fill=green))
de.conditional_formatting.add(f"Q4:Q{N}", FormulaRule(formula=['LEFT($Q4,8)="Declined"'], fill=red))
de.conditional_formatting.add(f"U4:U{N}", FormulaRule(formula=[f'AND(ISNUMBER($U4),$U4<=TODAY())'], fill=PatternFill("solid", fgColor="FFE699")))
de["M3"].comment = Comment("max_buy_price() из ub-cost-calculator: макс. цена закупки (€ excl VAT), при которой ROI = минимум маркета, при текущем Buy Box. Считает скрипт батчем по всем строкам.", "Claude")
de["Q3"].comment = Comment("Подсказка, не решение. Порядок: gated → нет листинга → нет разрешённого поставщика → нет расчёта → запас ≥ 0 ?", "Claude")

# ───────────────────────── Market view ─────────────────────────
mv = sheet("Market view", "Market view — старый широкий формат (только чтение)", "Собирается из Decisions. Пусто = решения по маркету нет.",
           ["EAN", "Brand", "Product", "US", "US reason", "CA", "CA reason", "UK", "UK reason", "AU", "AU reason", "WM", "WM reason"],
           [15, 18, 46, 8, 22, 8, 22, 8, 22, 8, 22, 8, 22], ["ln"] * 13)
for i in range(len(products)):
    r = 4 + i
    row = [f"=Products!A{r}", f"=Products!B{r}", f"=Products!C{r}"]
    for m in ("US", "CA", "UK", "AU", "WM"):
        row += [f'=IFERROR(INDEX(Decisions!$R$4:$R${N},MATCH($A{r}&"|{m}",Decisions!$A$4:$A${N},0))&"","")',
                f'=IFERROR(INDEX(Decisions!$S$4:$S${N},MATCH($A{r}&"|{m}",Decisions!$A$4:$A${N},0))&"","")']
    put(mv, r, row)

# ───────────────────────── Today ─────────────────────────
td = wb.create_sheet("Today", 1)
td["A1"] = "Today — ежедневный экран"; td["A1"].font = TITLE
td["A2"] = "Цифры — живые формулы. Списки ниже в макете статичные; в Google Sheets их заменят формулы FILTER/QUERY (текст формулы — в серой строке над каждым списком)."; td["A2"].font = SUB
for col, w in zip("ABCDEFGH", (34, 14, 15, 9, 44, 24, 14, 14)):
    td.column_dimensions[col].width = w
kpi = [
    ("Inbox: не разобрано", f'=COUNTIFS(Inbox!$B$4:$B${N},"<>",Inbox!$L$4:$L${N},"")'),
    ("Decisions: ждут решения", f'=COUNTIFS(Decisions!$B$4:$B${N},"<>",Decisions!$R$4:$R${N},"")'),
    ("…из них подсказка «✅ Approve?»", f'=COUNTIFS(Decisions!$R$4:$R${N},"",Decisions!$Q$4:$Q${N},"✅*")'),
    ("Пора перепроверить", f'=COUNTIFS(Decisions!$U$4:$U${N},"<="&TODAY())'),
    ("Approved за 7 дней", f'=COUNTIFS(Decisions!$R$4:$R${N},"yes",Decisions!$T$4:$T${N},">="&(TODAY()-7))'),
    ("Новых сигналов за 7 дней", f'=COUNTIFS(Inbox!$A$4:$A${N},">="&(TODAY()-7))'),
]
for i, (k, f) in enumerate(kpi):
    r = 4 + i
    td.cell(row=r, column=1, value=k).font = Font(name=F, bold=True, size=10)
    c = td.cell(row=r, column=2, value=f); c.font = Font(name=F, bold=True, size=12, color="1F3A5F")
    c.alignment = Alignment(horizontal="center"); c.border = BORDER


def block(start, title, gs_formula, headers, rows):
    td.cell(row=start, column=1, value=title).font = Font(name=F, bold=True, size=11, color="1F3A5F")
    g = td.cell(row=start + 1, column=1, value="Google Sheets: " + gs_formula)
    g.font = Font(name="Courier New", size=8, color="666666")
    for i, h in enumerate(headers, 1):
        c = td.cell(row=start + 2, column=i, value=h); c.font = HDR_FONT; c.fill = HDR_FILL; c.border = BORDER
    for j, rw in enumerate(rows):
        for i, v in enumerate(rw, 1):
            c = td.cell(row=start + 3 + j, column=i, value=v); c.font = BASE; c.border = BORDER
            if isinstance(v, dt.date):
                c.number_format = "yyyy-mm-dd"
    return start + 4 + len(rows)


r = 11
r = block(r, "1. Новое во входящих — поставить Triage",
          '=FILTER({Inbox!B4:B,Inbox!A4:A,Inbox!D4:D,Inbox!H4:H,Inbox!G4:G,Inbox!I4:I}, Inbox!L4:L="", Inbox!B4:B<>"")',
          ["Source", "Дата", "EAN", "Маркет", "Product", "Сигнал"],
          [(x[1], x[0], x[3], x[7], x[6], x[8]) for x in inbox if not x[9]])
r = block(r, "2. Ждут решения — сначала «✅ Approve?»",
          '=SORT(FILTER({Decisions!Q4:Q,Decisions!B4:C,Decisions!D4:D,Decisions!O4:O,Decisions!L4:L}, Decisions!R4:R="", Decisions!B4:B<>""), 1, FALSE)',
          ["Подсказка", "EAN", "Market", "Product", "Запас, €", "Поставщик"],
          [("✅ Approve?", "3616307650960", "US", "Chloé Le Nectar EDP 100ml Refillable", 8.90, "aromas"),
           ("Declined: low roi", "3616307650960", "UK", "Chloé Le Nectar EDP 100ml Refillable", -3.40, "aromas"),
           ("Declined: low roi", "3346470624535", "US", "Guerlain L'Heure Bleue EDP 100ml", -2.70, "douglas"),
           ("Declined: low roi", "3614274521368", "UK", "YSL Libre Berry Crush EDP 30ml", -8.10, "arenal"),
           ("Declined: gated", "3614274521368", "US", "YSL Libre Berry Crush EDP 30ml", "", ""),
           ("нет листинга → recheck", "3616307650960", "CA", "Chloé Le Nectar EDP 100ml Refillable", "", ""),
           ("Declined: gated", "3614275003092", "US", "YSL Y Gift Set 100ml EDP + 50ml Gel + 10ml", "", "")])
r = block(r, "3. Пора перепроверить",
          '=FILTER({Decisions!U4:U,Decisions!B4:C,Decisions!D4:D,Decisions!R4:S}, Decisions!U4:U<=TODAY(), Decisions!U4:U<>"")',
          ["Перепроверить", "EAN", "Market", "Product", "Было", "Причина"],
          [(d(1), "8410225598053", "UK", "JPG La Favorite EDP 100ml", "recheck", "мало офферов, BB нестабилен")])

wb.move_sheet("Today", offset=-(wb.sheetnames.index("Today") - 1))
order = ["README", "Today", "Inbox", "Products", "Offers", "Decisions", "Market view", "Brands", "Sources", "Settings"]
wb._sheets = [wb[n] for n in order]
wb["Today"].sheet_properties.tabColor = "1F3A5F"
wb["Inbox"].sheet_properties.tabColor = "4F81BD"
wb["Decisions"].sheet_properties.tabColor = "4F81BD"
for n in ("Brands", "Sources", "Settings"):
    wb[n].sheet_properties.tabColor = "A6A6A6"
wb.save(OUT)
print("saved", OUT)
