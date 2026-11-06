"""Portfolio grid: state -> HTML, streamed to Datastar.  No JS, no JSON.  Styling lives in grid.css.

        state = new_state()
        asyncio.create_task(run_ticks(state))     # moving values every TICK_SECONDS

        q = hub.subscribe()                       # one queue per /stream connection
        await q.get()                             # wakes on every state change -> render_grid(state)

Every function that changes state (tick, set_sort, set_filter, ...) calls hub.publish().
Cells whose *shown* value changed on the last tick carry flash-up / flash-down classes.
"""

import asyncio
import random
import string
from html import escape

import rd

TICK_SECONDS = 0.2
TOTAL_ROWS = 134
BARS, SPARK_W, SPARK_H = 27, 274, 62  # timeline sparkline geometry
PITCH, BAR_W = SPARK_W / BARS, 7

RNG = random.Random(42)
VOL = {
    "Bond": 0.004,
    "ETF": 0.005,
    "Crypto": 0.03,
    "Stock": 0.012,
}  # per-tick volatility
ICON = {"Bond": "#3b82c4", "ETF": "#6fcf3a", "Crypto": "#f7931a", "Stock": "#27a6e8"}


def _col(
    title,
    width="1fr",
    align="left",
    sortable=True,
    filterable=True,
    hint="contains… (comma = any of)",
):
    return {
        "title": title,
        "width": width,
        "align": align,
        "sortable": sortable,
        "filterable": filterable,
        "hint": hint,
    }


COLUMNS = {  # insertion order = display order
    "ticker": _col("Ticker", "1.55fr", hint="AIG, ADI, bond…"),
    "timeline": _col("Timeline", sortable=False, filterable=False),
    "instrument": _col("Instrument", align="right", hint="Stock, Bond…"),
    "pnl": _col("P&L", align="right", hint="minimum value"),
    "total": _col("Total Value", align="right", hint="minimum value"),
}


# ───────────────────────────── data ─────────────────────────────
def make_row(ticker, name, kind, qty, price, delta):
    """delta = unit P&L at creation (price - cost basis)."""
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


def row_pnl(row):
    return row["price"] - row["cost"]


def row_total(row):
    return row["qty"] * row["price"]


def row_total_pnl(row):
    return row["qty"] * row_pnl(row)


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


def build_rows():
    def rand(ticker, name, kind):
        price = round(RNG.uniform(8, 300), 4)
        return make_row(
            ticker,
            name,
            kind,
            RNG.randint(10, 200),
            price,
            price * RNG.uniform(-0.15, 0.15),
        )

    # First nine rows reproduce the screenshot: (qty, price, unit P&L).
    rows = [
        make_row("US10Y", "U.S. Treasury 10-Year Bond", "Bond", 100, 132.9551, 11.7924),
        make_row(
            "CAD30Y", "Canada 30-Year Government Bond", "Bond", 96, 115.8984, 10.2764
        ),
        make_row("MUB", "iShares National Muni Bond ETF", "ETF", 115, 21.1178, 4.8203),
        make_row("BTC-USD", "Bitcoin", "Crypto", 30000, 0.2286, 0.0722),
        make_row("T", "AT&T Inc.", "Stock", 20, 146.096, -9.978),
        make_row(
            "FRN2027", "France Government Bond 2027", "Bond", 102, 152.4503, -25.9036
        ),
        make_row("ADI", "Analog Devices, Inc.", "Stock", 160, 6.7679, 0.8236),
        make_row("AIG", "American International Group", "Stock", 52, 71.289, 0.0),
        make_row("DAL", "Delta Air Lines, Inc.", "Stock", 40, 27.4485, 7.25),
    ]
    rows += [rand(*line.split("|")) for line in EXTRA.splitlines()]
    seen = {r["ticker"] for r in rows}
    while len(rows) < TOTAL_ROWS:  # pad with synthetic names to exercise scrolling
        t = "".join(RNG.choices(string.ascii_uppercase, k=RNG.choice((3, 4))))
        if t not in seen:
            seen.add(t)
            rows.append(
                rand(
                    t,
                    f"{t.title()} Holdings Corp.",
                    RNG.choice(("Stock", "Stock", "Stock", "Bond", "ETF")),
                )
            )
    return rows


# ───────────────────────────── state ─────────────────────────────
def new_state():
    """Everything: the user's choices (first four keys) and the moving market data (rows)."""
    return {
        "sort_column": None,  # column name or None
        "sort_desc": False,
        "filters": {},  # column name -> filter text
        "hidden_columns": [],
        "rows": build_rows(),
        "tick_no": 0,  # parity drives the flash animation restart
    }


def _column(name, need=None):
    if name not in COLUMNS or (need and not COLUMNS[name][need]):
        raise ValueError(f"column {name!r} does not support {need or 'this'}")
    return COLUMNS[name]


# ───────────────────────────── change notification (asyncio pub/sub) ─────────────────────────────
class Hub:
    """One queue per stream. A queue holds at most one wake-up: if a render is already pending it will
    see the latest state anyway, so slow clients coalesce updates instead of building a backlog."""

    def __init__(self):
        self.queues = set()

    def subscribe(self):
        q = asyncio.Queue(maxsize=1)
        self.queues.add(q)
        return q

    def unsubscribe(self, q):
        self.queues.discard(q)

    def publish(self):  # call from the event loop
        for q in self.queues:
            try:
                q.put_nowait(True)
            except asyncio.QueueFull:
                pass


hub = Hub()


def _changed(state):
    """A user choice changed: drop stale flashes (they belong to the previous tick) and wake every stream."""
    for r in state["rows"]:
        r["flash"] = {}
    hub.publish()


def visible_columns(state):
    return [name for name in COLUMNS if name not in state["hidden_columns"]]


# ───────────────────────────── functions that change state ─────────────────────────────
def set_sort(state, column, descending=False):
    _column(column, "sortable")
    state["sort_column"], state["sort_desc"] = column, bool(descending)
    _changed(state)


def clear_sort(state):
    state["sort_column"], state["sort_desc"] = None, False
    _changed(state)


def toggle_sort(state, column):
    """Header click: none -> ascending -> descending -> none."""
    _column(column, "sortable")
    if state["sort_column"] != column:
        set_sort(state, column)
    elif not state["sort_desc"]:
        set_sort(state, column, True)
    else:
        clear_sort(state)


def set_filter(state, column, text):
    """ticker/instrument: comma-separated substrings (any match). pnl/total: minimum value. '' clears."""
    _column(column, "filterable")
    text = text.strip()
    if text:
        state["filters"][column] = text
    else:
        state["filters"].pop(column, None)
    _changed(state)


def hide_column(state, column):
    _column(column)
    if column in state["hidden_columns"] or len(visible_columns(state)) == 1:
        return
    state["hidden_columns"].append(column)
    state["filters"].pop(column, None)
    if state["sort_column"] == column:
        state["sort_column"], state["sort_desc"] = None, False
    _changed(state)


def show_all_columns(state):
    state["hidden_columns"].clear()
    _changed(state)


def tick(state):
    """Advance every moving value one step, record which *displayed* values changed, wake the streams."""
    state["tick_no"] += 1
    for r in state["rows"]:
        prev, shown = r["price"], (fmt(r["price"]), fmt(row_total(r)))
        r["price"] = max(
            0.01,
            prev
            + 0.08 * (r["anchor"] - prev)
            + prev * VOL[r["kind"]] * RNG.gauss(0, 1),
        )
        r["history"].append(r["price"])
        del r["history"][0]
        way = "up" if r["price"] >= prev else "down"
        r["flash"] = {"timeline": way}  # the newest bar is always new
        if fmt(r["price"]) != shown[0]:
            r["flash"]["pnl"] = way
        if fmt(row_total(r)) != shown[1]:
            r["flash"]["total"] = way
    hub.publish()


async def run_ticks(state):
    """Start once with the app: asyncio.create_task(run_ticks(state))."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        tick(state)


_tick_task = None


def ensure_ticking(state):
    """Start the tick loop once. Needs a running event loop, so call it from async code
    (a startup hook, or at the top of the /stream handler) - never at import time."""
    global _tick_task
    if _tick_task is None or _tick_task.done():
        _tick_task = asyncio.get_running_loop().create_task(
            run_ticks(state)
        )  # keep a reference: tasks are weakly held


# ───────────────────────────── filtering & sorting ─────────────────────────────
def _matches(row, filters):
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
            if (row["price"] if column == "pnl" else row_total(row)) < low:
                return False
    return True


SORT_KEYS = {
    "ticker": lambda r: r["ticker"],
    "instrument": lambda r: (r["kind"], r["ticker"]),
    "pnl": lambda r: r["price"],
    "total": row_total,
}


def visible_rows(state):
    rows = [r for r in state["rows"] if _matches(r, state["filters"])]
    if state["sort_column"]:
        rows.sort(key=SORT_KEYS[state["sort_column"]], reverse=state["sort_desc"])
    return rows


# ───────────────────────────── state -> HTML ─────────────────────────────
def esc(s):
    return escape(str(s), quote=True)


def fmt(x):
    """1,234.5 / 132.96 / 0.23 – thousands separators, up to 2 decimals, no trailing zeros."""
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def money(delta, value):
    d = "" if abs(delta) < 0.005 else ("↑" if delta > 0 else "↓") + fmt(abs(delta))
    return {
        "d": d,
        "dir": "up" if d.startswith("↑") else "down" if d else "flat",
        "v": fmt(value),
    }


def flash_cls(r, name, parity):
    """' flash-up f0' when this column's shown value changed on the last tick, else ''.
    f0/f1 alternate each tick so the CSS animation name changes and restarts on consecutive changes."""
    way = r["flash"].get(name)
    return f" flash-{way} f{parity}" if way else ""


def spark_svg(values, newest_cls=""):
    """Bars min-max scaled (smallest stays visible); each bar's <title> is its hover tooltip."""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    rects = ""
    for i, v in enumerate(values):
        h = round(SPARK_H * (0.1 + 0.9 * (v - lo) / span), 1)
        cls = (
            f' class="{newest_cls.strip()}"'
            if newest_cls and i == len(values) - 1
            else ""
        )
        rects += (
            f'<rect{cls} x="{i * PITCH:.1f}" y="{round(SPARK_H - h, 1)}" width="{BAR_W}" height="{h}">'
            f"<title>{fmt(v)}</title></rect>"
        )
    return f'<svg class="spark" viewBox="0 0 {SPARK_W} {SPARK_H}" width="{SPARK_W}" height="{SPARK_H}">{rects}</svg>'


ICON_FILTER = (
    '<svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round"><path d="M2.5 5h15M5 10h10M7.5 15h5"/></svg>'
)
ICON_MENU = (
    '<svg viewBox="0 0 20 20" width="20" height="20" fill="currentColor">'
    '<circle cx="10" cy="4" r="1.8"/><circle cx="10" cy="10" r="1.8"/><circle cx="10" cy="16" r="1.8"/></svg>'
)


def render_th(state, name):
    c = COLUMNS[name]
    mark = ""
    if state["sort_column"] == name:
        mark = f'<span class="sort">{"▼" if state["sort_desc"] else "▲"}</span>'
    filt = ""
    if c["filterable"]:
        on = " on" if name in state["filters"] else ""
        filt = (
            f'<button type="button" class="icon{on}" data-action="filter" title="Filter" '
            f'data-hint="{esc(c["hint"])}" data-value="{esc(state["filters"].get(name, ""))}">{ICON_FILTER}</button>'
        )
    menu = f'<button type="button" class="icon" data-action="menu" title="Column menu">{ICON_MENU}</button>'
    return (
        f'<div class="th {c["align"]}" role="columnheader" data-column="{name}" data-sortable="{int(c["sortable"])}">'
        f'<span class="title" data-action="sort">{esc(c["title"])}{mark}</span>{filt}{menu}</div>'
    )


def render_cell(name, r, parity):
    if name == "ticker":
        return (
            f'<div class="cell" data-col="ticker"><span class="logo" style="background:{ICON[r["kind"]]}">'
            f'{esc(r["ticker"][:2])}</span><b>{esc(r["ticker"])}</b><span class="name">{esc(r["name"])}</span></div>'
        )
    if name == "timeline":
        svg = spark_svg(r["history"], flash_cls(r, "timeline", parity))
        return f'<div class="cell tl" data-col="timeline">{svg}</div>'
    if name == "instrument":
        return f'<div class="cell right" data-col="instrument">{esc(r["kind"])}</div>'
    m = (
        money(row_pnl(r), r["price"])
        if name == "pnl"
        else money(row_total_pnl(r), row_total(r))
    )
    return (
        f'<div class="cell right {m["dir"]}{flash_cls(r, name, parity)}" data-col="{name}">'
        f'<span class="delta">{m["d"]}</span><span class="pill">{m["v"]}</span></div>'
    )


def render_grid(state):
    cols, rows = visible_columns(state), visible_rows(state)
    parity = state["tick_no"] % 2
    head = "".join(render_th(state, n) for n in cols)
    body = "".join(
        f'<div class="row" role="row" id="row-{esc(r["ticker"])}" data-ticker="{esc(r["ticker"])}">'
        + "".join(render_cell(n, r, parity) for n in cols)
        + "</div>"
        for r in rows
    )
    widths = " ".join(COLUMNS[n]["width"] for n in cols)
    return (
        '<main id="grid">'
        f'<div class="inner" style="--cols:{widths}">'
        f'<div class="hrow" role="row">{head}</div><div class="rows">{body}</div>'
        f'<div class="foot"><span>Rows: {len(rows)}</span><span>Total Rows: {len(state["rows"])}</span></div></div>'
        "</main>"
    )


INDEX = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script type="module" src="/static/js/datastar-rocket.js"></script>
<title>Portfolio</title><link rel="stylesheet" href="/static/css/grid.css"></head>
<body><main id="grid" data-init="@get('/stream')"></main></body>
</html>
"""


state = new_state()


@rd.get("/")
async def index(request):
    return rd.html(INDEX)


@rd.get("/stream")
async def stream(request):
    ensure_ticking(state)  # i have to add a startup lifecycle to rd
    q = hub.subscribe()  # subscribe before the first paint so nothing is missed
    try:
        yield rd.patch(render_grid(state))
        while True:
            await q.get()
            yield rd.patch(render_grid(state))
    finally:
        hub.unsubscribe(q)


rd.run(reload=True)
