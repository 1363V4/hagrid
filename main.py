import asyncio
import json
import random
import secrets
import string
from html import escape

import rd

TICK_SECONDS = 0.2
TOTAL_ROWS = 134
BARS = 27
ROW_H = 72
OVERSCAN = 6
DEFAULT_VH = 900
MAX_VH = 4000

RNG = random.Random(42)
VOL = {"Bond": 0.004, "ETF": 0.005, "Crypto": 0.03, "Stock": 0.012}
ICON = {"Bond": "#3b82c4", "ETF": "#6fcf3a", "Crypto": "#f7931a", "Stock": "#27a6e8"}


def _col(title, **overrides):
    return {
        "title": title,
        "width": "1fr",
        "align": "left",
        "sortable": True,
        "filterable": True,
        "hint": "contains… (comma = any of)",
        "width_sm": "1fr",
        "hide_sm": False,
        **overrides,
    }


COLUMNS = {
    "ticker": _col("Ticker", width="1.55fr", width_sm="1.7fr", hint="AIG, ADI, bond…"),
    "timeline": _col("Timeline", sortable=False, filterable=False),
    "instrument": _col("Instrument", align="right", hint="Stock, Bond…", hide_sm=True),
    "pnl": _col("P&L", align="right", hint="minimum value"),
    "total": _col(
        "Total Value", align="right", hint="minimum value", width_sm="1.15fr"
    ),
}


# ───────────── data ─────────────
FIRST_ROWS = [
    ("US10Y", "U.S. Treasury 10-Year Bond", "Bond", 100, 132.9551, 11.7924),
    ("CAD30Y", "Canada 30-Year Government Bond", "Bond", 96, 115.8984, 10.2764),
    ("MUB", "iShares National Muni Bond ETF", "ETF", 115, 21.1178, 4.8203),
    ("BTC-USD", "Bitcoin", "Crypto", 30000, 0.2286, 0.0722),
    ("T", "AT&T Inc.", "Stock", 20, 146.096, -9.978),
    ("FRN2027", "France Government Bond 2027", "Bond", 102, 152.4503, -25.9036),
    ("ADI", "Analog Devices, Inc.", "Stock", 160, 6.7679, 0.8236),
    ("AIG", "American International Group", "Stock", 52, 71.289, 0.0),
    ("DAL", "Delta Air Lines, Inc.", "Stock", 40, 27.4485, 7.25),
]

EXTRA = """AAPL|Apple Inc.|Stock
MSFT|Microsoft Corp.|Stock
NVDA|NVIDIA Corp.|Stock
AMZN|Amazon.com, Inc.|Stock
GOOGL|Alphabet Inc.|Stock
META|Meta Platforms, Inc.|Stock
TSLA|Tesla, Inc.|Stock
JPM|JPMorgan Chase & Co.|Stock
V|Visa Inc.|Stock
KO|The Coca-Cola Company|Stock
PEP|PepsiCo, Inc.|Stock
XOM|Exxon Mobil Corp.|Stock
CVX|Chevron Corp.|Stock
PFE|Pfizer Inc.|Stock
INTC|Intel Corp.|Stock
CSCO|Cisco Systems, Inc.|Stock
ORCL|Oracle Corp.|Stock
BA|The Boeing Company|Stock
UAL|United Airlines Holdings, Inc.|Stock
DE10Y|German Bund 10-Year|Bond
UK10Y|UK Gilt 10-Year|Bond
JP10Y|Japan Government Bond 10-Year|Bond
IT10Y|Italy BTP 10-Year|Bond
ES10Y|Spain Government Bond 10-Year|Bond
SPY|SPDR S&P 500 ETF|ETF
QQQ|Invesco QQQ Trust|ETF
AGG|iShares Core U.S. Aggregate Bond ETF|ETF
TLT|iShares 20+ Year Treasury Bond ETF|ETF
GLD|SPDR Gold Shares|ETF
ETH-USD|Ethereum|Crypto
SOL-USD|Solana|Crypto
XRP-USD|XRP|Crypto"""


def make_row(ticker, name, kind, qty, price, delta):
    v = VOL[kind]
    history = [price * (1 + 3 * v * RNG.gauss(0, 1)) for _ in range(BARS - 1)] + [price]
    return {
        "ticker": ticker,
        "name": name,
        "kind": kind,
        "qty": qty,
        "price": price,
        "cost": price - delta,
        "anchor": price,
        "history": history,
        "flash": {},
    }


def random_row(ticker, name, kind):
    price = round(RNG.uniform(8, 300), 4)
    delta = price * RNG.uniform(-0.15, 0.15)
    return make_row(ticker, name, kind, RNG.randint(10, 200), price, delta)


def build_rows():
    rows = [make_row(*r) for r in FIRST_ROWS]
    rows += [random_row(*line.split("|")) for line in EXTRA.splitlines()]
    seen = {r["ticker"] for r in rows}
    while len(rows) < TOTAL_ROWS:
        t = "".join(RNG.choices(string.ascii_uppercase, k=RNG.choice((3, 4))))
        if t in seen:
            continue
        seen.add(t)
        kind = RNG.choice(("Stock", "Stock", "Stock", "Bond", "ETF"))
        rows.append(random_row(t, f"{t.title()} Holdings Corp.", kind))
    return rows


def row_pnl(row):
    return row["price"] - row["cost"]


def row_total(row):
    return row["qty"] * row["price"]


def row_total_pnl(row):
    return row["qty"] * row_pnl(row)


# ───────────── state ─────────────
def new_state():
    return {
        "sort_column": None,
        "sort_desc": False,
        "filters": {},
        "column_order": list(COLUMNS),
        "hidden_columns": [],
        "rows": build_rows(),
        "tick_no": 0,
    }


state = new_state()


def check_column(name, need=None):
    if name not in COLUMNS or (need and not COLUMNS[name][need]):
        raise ValueError(f"column {name!r} does not support {need or 'this'}")


def visible_columns(state):
    return [n for n in state["column_order"] if n not in state["hidden_columns"]]


# ───────────── streams: one per browser tab ─────────────
subs = {}


def look(sub, top, vh):
    top = max(0.0, min(float(top), 1e7))
    vh = max(0, min(int(vh), MAX_VH))
    moved = (int(top // ROW_H), vh) != (int(sub["top"] // ROW_H), sub["vh"])
    sub["top"], sub["vh"] = top, vh
    return moved


def new_sub(top=0, vh=0):
    sub = {"q": asyncio.Queue(maxsize=1), "top": 0.0, "vh": 0}
    look(sub, top, vh)
    return sub


def wake(sub):
    try:
        sub["q"].put_nowait(True)
    except asyncio.QueueFull:
        pass


def publish():
    for sub in subs.values():
        wake(sub)


def changed(state):
    for r in state["rows"]:
        r["flash"] = {}
    publish()


# ───────────── things that change state ─────────────
def set_sort(state, column, descending=False):
    check_column(column, "sortable")
    state["sort_column"], state["sort_desc"] = column, descending
    changed(state)


def clear_sort(state):
    state["sort_column"], state["sort_desc"] = None, False
    changed(state)


def toggle_sort(state, column):
    check_column(column, "sortable")
    if state["sort_column"] != column:
        set_sort(state, column)
    elif not state["sort_desc"]:
        set_sort(state, column, True)
    else:
        clear_sort(state)


def set_filter(state, column, text):
    check_column(column, "filterable")
    text = text.strip()
    if text:
        state["filters"][column] = text
    else:
        state["filters"].pop(column, None)
    changed(state)


def hide_column(state, column):
    check_column(column)
    if column in state["hidden_columns"] or len(visible_columns(state)) == 1:
        return
    state["hidden_columns"].append(column)
    state["filters"].pop(column, None)
    if state["sort_column"] == column:
        state["sort_column"], state["sort_desc"] = None, False
    changed(state)


def show_all_columns(state):
    state["hidden_columns"].clear()
    changed(state)


def move_column(state, column, onto):
    check_column(column)
    check_column(onto)
    if column == onto:
        return
    order = state["column_order"]
    moved = [n for n in order if n != column]
    moved.insert(order.index(onto), column)
    state["column_order"] = moved
    changed(state)


def nudge_column(state, column, step):
    check_column(column)
    cols = visible_columns(state)
    if column not in cols:
        return
    j = cols.index(column) + step
    if 0 <= j < len(cols):
        move_column(state, column, cols[j])


def tick(state):
    state["tick_no"] += 1
    for r in state["rows"]:
        prev = r["price"]
        shown_price, shown_total = fmt(prev), fmt(row_total(r))
        pull = 0.08 * (r["anchor"] - prev)
        noise = prev * VOL[r["kind"]] * RNG.gauss(0, 1)
        r["price"] = max(0.01, prev + pull + noise)
        r["history"].append(r["price"])
        del r["history"][0]
        way = "up" if r["price"] >= prev else "down"
        r["flash"] = {"timeline": way}
        if fmt(r["price"]) != shown_price:
            r["flash"]["pnl"] = way
        if fmt(row_total(r)) != shown_total:
            r["flash"]["total"] = way
    publish()


async def run_ticks(state):
    while True:
        await asyncio.sleep(TICK_SECONDS)
        tick(state)


tick_task = None


def ensure_ticking(state):
    global tick_task
    if tick_task is None or tick_task.done():
        tick_task = asyncio.get_running_loop().create_task(run_ticks(state))


# ───────────── filter and sort ─────────────
def row_matches(row, filters):
    for column, query in filters.items():
        if column in ("ticker", "instrument"):
            hay = (
                f"{row['ticker']} {row['name']}" if column == "ticker" else row["kind"]
            )
            terms = [t.strip().lower() for t in query.split(",") if t.strip()]
            if terms and not any(t in hay.lower() for t in terms):
                return False
        else:
            try:
                low = float(query.replace(",", ""))
            except ValueError:
                continue
            value = row["price"] if column == "pnl" else row_total(row)
            if value < low:
                return False
    return True


SORT_KEYS = {
    "ticker": lambda r: r["ticker"],
    "instrument": lambda r: (r["kind"], r["ticker"]),
    "pnl": lambda r: r["price"],
    "total": row_total,
}


def visible_rows(state):
    rows = [r for r in state["rows"] if row_matches(r, state["filters"])]
    if state["sort_column"]:
        rows.sort(key=SORT_KEYS[state["sort_column"]], reverse=state["sort_desc"])
    return rows


# ───────────── html: small helpers ─────────────
def esc(s):
    return escape(str(s), quote=True)


def fmt(x):
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def money(delta, value):
    d = "" if abs(delta) < 0.005 else ("↑" if delta > 0 else "↓") + fmt(abs(delta))
    way = "up" if d.startswith("↑") else "down" if d else "flat"
    return {"d": d, "dir": way, "v": fmt(value)}


def flash_cls(r, name, parity):
    way = r["flash"].get(name)
    return f" flash-{way} f{parity}" if way else ""


def spark_bars(values, newest_cls=""):
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    bars = ""
    for i, v in enumerate(values):
        h = round(10 + 90 * (v - lo) / span)
        cls = (
            f' class="{newest_cls.strip()}"'
            if newest_cls and i == len(values) - 1
            else ""
        )
        bars += f'<i{cls} style="height:{h}%" data-v="{fmt(v)}"></i>'
    return f'<div class="spark">{bars}</div>'


ICON_FILTER = (
    '<svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round"><path d="M2.5 5h15M5 10h10M7.5 15h5"/></svg>'
)
ICON_MENU = (
    '<svg viewBox="0 0 20 20" width="20" height="20" fill="currentColor">'
    '<circle cx="10" cy="4" r="1.8"/><circle cx="10" cy="10" r="1.8"/><circle cx="10" cy="16" r="1.8"/></svg>'
)


# ───────────── html: datastar attributes ─────────────
CLOSE = "$_open = ''; "


def attr(name, expr):
    return f' {name}="{esc(expr)}"'


def cmd(url, **args):
    sets = "".join(f"${k} = '{v}'; " for k, v in args.items())
    return f"{sets}@post('{url}')"


def toggle(key):
    return f"$_open = $_open === '{key}' ? '' : '{key}'"


def pop(key, cls, body):
    pid = "pop-" + key.replace(":", "-")
    shown = attr("data-show", f"$_open === '{key}'")
    return (
        f'<div class="pop {cls}" id="{pid}" style="display:none" data-ignore-morph{shown}>'
        f"{body}</div>"
    )


def render_filter_pop(name, hint):
    key = f"filter:{name}"
    focus = attr(
        "data-effect", f"$_open === '{key}' && requestAnimationFrame(() => el.focus())"
    )
    enter = attr("data-on:keydown", "evt.key === 'Enter' && ($_open = '')")
    send = attr("data-on:input__debounce.250ms", cmd("/filter", col=name))
    clear = attr("data-on:click", f"$filters.{name} = ''; " + cmd("/filter", col=name))
    body = (
        f'<input type="text" placeholder="{esc(hint)}" autocomplete="off" data-bind="filters.{name}"'
        f"{send}{enter}{focus}>"
        f'<button type="button" class="clear"{clear}>Clear</button>'
    )
    return pop(key, "filter", body)


def render_menu_pop(name):
    c = COLUMNS[name]
    items = []
    if c["filterable"]:
        items.append(("Filter…", f"$_open = 'filter:{name}'", "sm-only"))
    if c["sortable"]:
        items += [
            ("Sort ascending", CLOSE + cmd("/sort", col=name, dir="asc"), ""),
            ("Sort descending", CLOSE + cmd("/sort", col=name, dir="desc"), ""),
            ("Clear sort", CLOSE + cmd("/sort", col=name, dir="none"), ""),
        ]
    items += [
        (
            "Move left",
            CLOSE + cmd("/nudge", col=name, dir="left"),
            "sep" if items else "",
        ),
        ("Move right", CLOSE + cmd("/nudge", col=name, dir="right"), ""),
    ]
    forget = f"$filters.{name} = ''; " if c["filterable"] else ""
    items.append(("Hide column", CLOSE + forget + cmd("/hide", col=name), "sep"))

    body = ""
    for label, expr, cls in items:
        klass = f' class="{cls}"' if cls else ""
        body += f'<button type="button"{klass}{attr("data-on:click", expr)}>{label}</button>'
    return pop(f"menu:{name}", "menu", body)


def drag_handle(name):
    start = (
        f"$_dragging = true; $col = '{name}'; evt.dataTransfer.effectAllowed = 'move'; "
        f"evt.dataTransfer.setData('text/plain', '{name}'); "
        "evt.dataTransfer.setDragImage(el.parentElement, 12, 12)"
    )
    return (
        ' draggable="true"'
        + attr("data-on:dragstart", start)
        + attr("data-on:dragend", "$_dragging = false; $_over = ''")
    )


def drop_zone(name):
    mine = f"$_dragging && $col !== '{name}'"
    over = attr("data-on:dragover__prevent", f"{mine} && ($_over = '{name}')")
    drop = attr(
        "data-on:drop__prevent",
        f"{mine} && ($onto = '{name}', $_over = '', $_dragging = false, @post('/move'))",
    )
    return over + drop


# ───────────── html: grid ─────────────
def render_th(state, name, hide=""):
    c = COLUMNS[name]
    mark = ""
    if state["sort_column"] == name:
        mark = f'<span class="sort">{"▼" if state["sort_desc"] else "▲"}</span>'
    sort_on = (
        attr("data-on:click", cmd("/sort", col=name, dir="toggle"))
        if c["sortable"]
        else ""
    )
    parts = [
        f'<span class="title"{drag_handle(name)}{sort_on}>{esc(c["title"])}{mark}</span>'
    ]
    if c["filterable"]:
        on = " on" if name in state["filters"] else ""
        open_filter = attr("data-on:click", toggle(f"filter:{name}"))
        parts.append(
            f'<button type="button" class="icon filter-btn{on}" title="Filter"{open_filter}>{ICON_FILTER}</button>'
            + render_filter_pop(name, c["hint"])
        )
    open_menu = attr("data-on:click", toggle(f"menu:{name}"))
    parts.append(
        f'<button type="button" class="icon menu-btn" title="Column menu"{open_menu}>{ICON_MENU}</button>'
        + render_menu_pop(name)
    )
    shown = attr("data-show", f"$_over === '{name}'")
    drop_mark = (
        f'<i class="drop-mark" id="drop-{name}" aria-hidden="true" style="display:none" '
        f"data-ignore-morph{shown}></i>"
    )
    away = attr(
        "data-on:click__outside", f"$_open.endsWith(':{name}') && ($_open = '')"
    )
    filtered = " filtered" if name in state["filters"] else ""
    return (
        f'<div class="th {c["align"]}{filtered}{hide}" id="th-{name}" role="columnheader" '
        f'data-column="{name}" data-sortable="{int(c["sortable"])}"{away}{drop_zone(name)}>'
        + "".join(parts)
        + drop_mark
        + "</div>"
    )


def render_cell(name, r, parity, hide=""):
    if name == "ticker":
        return (
            f'<div class="cell{hide}" data-col="ticker">'
            f'<span class="logo" style="background:{ICON[r["kind"]]}">{esc(r["ticker"][:2])}</span>'
            f'<b>{esc(r["ticker"])}</b><span class="name">{esc(r["name"])}</span></div>'
        )
    if name == "timeline":
        bars = spark_bars(r["history"], flash_cls(r, "timeline", parity))
        return f'<div class="cell tl{hide}" data-col="timeline">{bars}</div>'
    if name == "instrument":
        return f'<div class="cell right{hide}" data-col="instrument">{esc(r["kind"])}</div>'
    if name == "pnl":
        m = money(row_pnl(r), r["price"])
    else:
        m = money(row_total_pnl(r), row_total(r))
    return (
        f'<div class="cell right {m["dir"]}{flash_cls(r, name, parity)}{hide}" data-col="{name}">'
        f'<span class="delta">{m["d"]}</span><span class="pill">{m["v"]}</span></div>'
    )


def window(n, top, vh):
    first = max(0, int(top // ROW_H) - OVERSCAN)
    last = min(n, int((top + (vh or DEFAULT_VH)) // ROW_H) + 1 + OVERSCAN)
    return first, last


def phone_columns(cols):
    return [n for n in cols if not COLUMNS[n]["hide_sm"]] or cols


def render_row(r, slot, cols, parity, hide):
    t = esc(r["ticker"])
    cells = "".join(render_cell(n, r, parity, hide[n]) for n in cols)
    return (
        f'<div class="row" role="row" id="row-{t}" aria-rowindex="{slot + 1}" data-ticker="{t}" '
        f'style="transform:translateY({slot * ROW_H}px)">{cells}</div>'
    )


def render_grid(state, sub):
    cols, rows = visible_columns(state), visible_rows(state)
    slot = {r["ticker"]: i for i, r in enumerate(rows)}
    first, last = window(len(rows), sub["top"], sub["vh"])
    parity = state["tick_no"] % 2
    phone = phone_columns(cols)
    hide = {n: "" if n in phone else " sm-hide" for n in cols}
    head = "".join(render_th(state, n, hide[n]) for n in cols)
    body = "".join(
        render_row(r, slot[r["ticker"]], cols, parity, hide)
        for r in state["rows"]
        if first <= slot.get(r["ticker"], -1) < last
    )
    widths = " ".join(COLUMNS[n]["width"] for n in cols)
    widths_sm = " ".join(COLUMNS[n]["width_sm"] for n in phone)

    restore = ""
    hidden = len(state["hidden_columns"])
    if hidden:
        label = f"Show {hidden} hidden column{'s' if hidden > 1 else ''}"
        restore = f'<button type="button" class="link"{attr("data-on:click", cmd("/show-all"))}>{label}</button>'

    report = "$top = el.scrollTop; $vh = el.clientHeight; @post('/viewport')"
    scroller = (
        attr("data-on:scroll__throttle.100ms.trail", report)
        + attr("data-on:resize__window__debounce.150ms", report)
        + attr("data-init", report)
    )
    return (
        '<main id="grid">'
        f'<div class="inner" style="--cols:{widths};--cols-sm:{widths_sm};--row-h:{ROW_H}px">'
        f'<div class="hrow" role="row">{head}</div>'
        f'<div class="rows" id="rows"{scroller}>'
        f'<div class="sizer" style="height:{len(rows) * ROW_H}px">{body}</div></div>'
        f'<div class="foot"><span>Rows: {len(rows)}</span>{restore}'
        f"<span>Total Rows: {len(state['rows'])}</span></div></div>"
        "</main>"
    )


def render_index(state):
    seed = {
        "cid": secrets.token_urlsafe(9),
        "top": 0,
        "vh": 0,
        "col": "",
        "dir": "",
        "onto": "",
        "_open": "",
        "_over": "",
        "_dragging": False,
        "filters": {
            n: state["filters"].get(n, "")
            for n, c in COLUMNS.items()
            if c["filterable"]
        },
    }
    esc_key = attr("data-on:keydown__window", "evt.key === 'Escape' && ($_open = '')")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script type="module" src="/static/js/datastar-rocket.js"></script>
<title>Portfolio</title>
<link rel="stylesheet" href="/static/css/grid.css"></head>
<body data-signals="{esc(json.dumps(seed))}" data-init="@get('/stream')"{esc_key}>
<main id="grid"></main>
</body>
</html>
"""


# ───────────── routes ─────────────
@rd.get("/")
async def index(request):
    return rd.html(render_index(state))


@rd.get("/stream")
async def stream(request):
    ensure_ticking(state)
    s = request.signals
    cid = s["cid"]
    sub = new_sub(s.get("top", 0), s.get("vh", 0))
    subs[cid] = sub
    try:
        yield rd.patch(render_grid(state, sub))
        while True:
            await sub["q"].get()
            yield rd.patch(render_grid(state, sub))
    finally:
        if subs.get(cid) is sub:
            del subs[cid]


@rd.post("/sort")
async def cmd_sort(request):
    s = request.signals
    column, how = s["col"], s["dir"]
    if how == "toggle":
        toggle_sort(state, column)
    elif how in ("asc", "desc"):
        set_sort(state, column, how == "desc")
    elif how == "none" and state["sort_column"] == column:
        clear_sort(state)
    return rd.empty()


@rd.post("/filter")
async def cmd_filter(request):
    s = request.signals
    set_filter(state, s["col"], s["filters"].get(s["col"], ""))
    return rd.empty()


@rd.post("/hide")
async def cmd_hide(request):
    hide_column(state, request.signals["col"])
    return rd.empty()


@rd.post("/show-all")
async def cmd_show_all(request):
    show_all_columns(state)
    return rd.empty()


@rd.post("/move")
async def cmd_move(request):
    s = request.signals
    move_column(state, s["col"], s["onto"])
    return rd.empty()


@rd.post("/nudge")
async def cmd_nudge(request):
    s = request.signals
    nudge_column(state, s["col"], {"left": -1, "right": 1}[s["dir"]])
    return rd.empty()


@rd.post("/viewport")
async def cmd_viewport(request):
    s = request.signals
    sub = subs.get(s["cid"])
    if sub and look(sub, s["top"], s["vh"]):
        wake(sub)
    return rd.empty()


rd.run(reload=True)
