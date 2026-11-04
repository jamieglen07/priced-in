"""
app.py - the "What's Priced In?" web app.

Start it with:
    streamlit run app.py
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from data import get_company_data
from reverse_dcf import (
    DEFAULT_TERMINAL_GROWTH,
    FLAT_DISCOUNT_RATE,
    YEARS,
    historical_growth,
    implied_growth,
    projection_table,
    sensitivity_table,
    short_name,
    verdict,
)
from business_terms import analyse, summary_text
from tracker import (
    LOOKBACK_YEARS,
    REPORT_LAG_DAYS,
    build_tracker,
    dates_in_period,
    expectation_shocks,
    load_history,
    load_saved_dates,
    report_switch_dates,
    save_dates,
)
from wacc import EQUITY_RISK_PREMIUM, calculate_wacc, get_risk_free_rate

BILLIONS = 1_000_000_000
BLUE, ORANGE = "#2a78d6", "#eb6834"
RED, NEUTRAL, INK = "#e34948", "#f0efec", "#0b0b0b"


# Remember downloaded data for an hour, so moving a slider doesn't
# re-download everything from Yahoo (which would be slow).
@st.cache_data(ttl=3600, show_spinner=False)
def load_data(ticker):
    return get_company_data(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def load_tracker(ticker, discount_rate, terminal_growth):
    history = load_history(ticker)
    return history, build_tracker(history, discount_rate, terminal_growth)


@st.cache_data(ttl=3600, show_spinner=False)
def load_risk_free(currency):
    return get_risk_free_rate(currency)


def money(amount, currency):
    """Format a number like '125.9bn GBP'."""
    return f"{amount / BILLIONS:,.1f}bn {currency}"


# ---------------------------------------------------------------------------
# 1. Page title and ticker box
# ---------------------------------------------------------------------------
st.set_page_config(page_title="What's Priced In?", layout="centered")
st.title("What's Priced In?")
st.caption("A reverse DCF: the free cash flow growth today's share price implies.")

ticker = st.text_input(
    "Stock ticker",
    placeholder="e.g. AAPL, or ULVR.L for UK shares",
).strip().upper()

st.sidebar.header("Assumptions")

# Nothing typed yet: stop here and wait.
if not ticker:
    st.sidebar.caption("Enter a ticker to see the assumptions.")
    st.info("Type a ticker above to get started.")
    st.stop()

# ---------------------------------------------------------------------------
# 2. Fetch the data (with clear messages if something goes wrong)
# ---------------------------------------------------------------------------
with st.spinner(f"Downloading data for {ticker}..."):
    try:
        data = load_data(ticker)
        risk_free_default, risk_free_source = load_risk_free(data["currency"])
    except ValueError as problem:
        st.error(str(problem))   # our own friendly messages from data.py
        st.stop()
    except Exception:
        st.error(f"Something went wrong getting data for '{ticker}'. "
                 "Yahoo Finance may be busy - try again in a minute.")
        st.stop()

cur = data["currency"]
start_fcf = data["average_fcf"]
enterprise_value = data["enterprise_value"]
fcf_series = data["fcf_table"]["Free cash flow"]
years_used = len(fcf_series)

# ---------------------------------------------------------------------------
# 3. Assumptions in the sidebar
# ---------------------------------------------------------------------------
terminal_growth = st.sidebar.slider(
    "Terminal growth (growth forever after year 10)",
    min_value=1.0, max_value=4.0, value=DEFAULT_TERMINAL_GROWTH * 100,
    step=0.5, format="%.1f%%",
) / 100

st.sidebar.subheader("WACC inputs")
equity_risk_premium = st.sidebar.number_input(
    "Equity risk premium (%)", min_value=0.0, max_value=15.0,
    value=EQUITY_RISK_PREMIUM * 100, step=0.25, format="%.2f",
    help="Extra yearly return investors want for owning shares instead of "
         "government bonds. 4-6% is the usual range.",
) / 100

# The key includes the currency, so switching from a UK to a US stock
# resets this box to the right country's bond yield.
risk_free = st.sidebar.number_input(
    f"Risk-free rate for {cur} (%)", min_value=0.0, max_value=20.0,
    value=round(risk_free_default * 100, 2), step=0.05, format="%.2f",
    key=f"risk_free_{cur}", help=f"Default: {risk_free_source}",
) / 100
if abs(risk_free - risk_free_default) > 1e-9:
    risk_free_source = "Edited by you in the sidebar"
st.sidebar.caption(f"Source: {risk_free_source}")

wacc = calculate_wacc(data, risk_free, risk_free_source, equity_risk_premium)

st.sidebar.subheader("Discount rate")
override = st.sidebar.toggle("Override the calculated WACC", key=f"override_{ticker}")
if override:
    discount_rate = st.sidebar.slider(
        "Your discount rate", min_value=3.0, max_value=16.0,
        value=min(16.0, max(3.0, round(wacc["wacc"] * 100, 2))),
        step=0.01, format="%.2f%%", key=f"wacc_slider_{ticker}",
    ) / 100
else:
    discount_rate = wacc["wacc"]
    st.sidebar.caption(f"Using the calculated WACC of {discount_rate:.2%}.")

# ---------------------------------------------------------------------------
# 4. Run the reverse DCF
# ---------------------------------------------------------------------------
st.header(f"{data['name']} ({ticker})")

# Negative cash flow: a reverse DCF can't give a sensible answer.
if start_fcf <= 0:
    st.warning(
        f"⚠️ **Free cash flow is negative.** Over the last {years_used} years "
        f"{data['name']} averaged {money(start_fcf, cur)} of free cash flow - it "
        "spends more cash than it brings in. A reverse DCF can't work out a "
        "growth rate, because growing a negative number only makes it more "
        "negative."
    )
    st.stop()

# The terminal value formula breaks if the discount rate isn't above terminal growth.
if discount_rate <= terminal_growth:
    st.error(f"The discount rate ({discount_rate:.2%}) must be higher than terminal "
             f"growth ({terminal_growth:.1%}). Lower terminal growth or raise the "
             "discount rate in the sidebar.")
    st.stop()

growth = implied_growth(start_fcf, enterprise_value, discount_rate, terminal_growth)
if growth is None:
    st.warning("No growth rate between -50% and +100% a year matches today's "
               "price, so the result would not be meaningful.")
    st.stop()

# ---------------------------------------------------------------------------
# 5. Plain-English summary
# ---------------------------------------------------------------------------
past = historical_growth(fcf_series)
outcome = verdict(growth, past)

summary = (f"To justify today's price, **{short_name(data['name'])}** must grow free "
           f"cash flow about **{growth:.1%} a year for {YEARS} years**. ")
if past is not None:
    summary += (f"From {fcf_series.index[0]} to {fcf_series.index[-1]} it grew "
                f"**{past:.1%} a year**. ")
summary += f"The market is **{outcome}**."

# Colour the box by the verdict (with the word itself, so it's never colour alone).
if outcome.startswith("OPTIMISTIC"):
    st.warning(summary, icon="📈")
elif outcome.startswith("PESSIMISTIC"):
    st.info(summary, icon="📉")
else:
    st.success(summary, icon="⚖️")

business = analyse(data, discount_rate, terminal_growth)
if business is not None:
    st.markdown("**In business terms:** " + summary_text(short_name(data["name"]), business))

flat_growth = implied_growth(start_fcf, enterprise_value, FLAT_DISCOUNT_RATE, terminal_growth)
rate_label = "your discount rate" if override else "the WACC"
if flat_growth is not None and abs(discount_rate - FLAT_DISCOUNT_RATE) > 1e-9:
    st.caption(f"Using {rate_label} of {discount_rate:.2%}. With a flat "
               f"{FLAT_DISCOUNT_RATE:.0%} discount rate it would be {flat_growth:.1%} "
               f"({(growth - flat_growth) * 100:+.1f} percentage points difference).")

# ---------------------------------------------------------------------------
# 6. Key numbers
# ---------------------------------------------------------------------------
st.subheader("Key numbers")
st.caption(f"In billions of {cur}. Balance sheet at {data['balance_sheet_date']}.")
a, b, c = st.columns(3)
a.metric("Market value", f"{data['market_value'] / BILLIONS:,.1f}")
b.metric("+ Total debt", f"{data['total_debt'] / BILLIONS:,.1f}")
c.metric("− Cash", f"{data['cash'] / BILLIONS:,.1f}")
d, e, f = st.columns(3)
d.metric("= Enterprise value", f"{enterprise_value / BILLIONS:,.1f}")
e.metric(f"Average FCF ({years_used} yrs)", f"{start_fcf / BILLIONS:,.2f}")
f.metric("Implied growth", f"{growth:.1%}")

if data["fx_rate"] != 1.0:
    st.caption(f"Accounts converted from {data['reporting_currency']} at "
               f"1 {data['reporting_currency']} = {data['fx_rate']:.4f} {cur}.")

# ---------------------------------------------------------------------------
# 7. WACC breakdown
# ---------------------------------------------------------------------------
st.subheader("Discount rate (WACC)")
st.caption("The return investors and lenders want, averaged by how much of each "
           "the company uses. Future cash is discounted at this rate. A higher "
           "discount rate makes future cash worth less today, so MORE growth is "
           "needed to justify the price; a lower rate means less growth is needed.")
g1, g2, g3 = st.columns(3)
g1.metric("Beta (adjusted)", f"{wacc['beta']:.2f}",
          help=f"Raw beta from Yahoo: {wacc['raw_beta']:.2f}. Adjusted = ⅔ × raw + ⅓ × 1.0, "
               "because betas tend to drift towards 1.0 over time.")
g2.metric("Credit rating (estimated)", wacc["rating"],
          help="Based on interest coverage (EBIT ÷ interest expense): how many times "
               "over profit could pay the interest bill.")
g3.metric("Credit spread", f"{wacc['spread']:.2%}")
h1, h2, h3 = st.columns(3)
h1.metric("Cost of equity", f"{wacc['cost_of_equity']:.2%}")
h2.metric("Cost of debt (after tax)", f"{wacc['after_tax_cost_of_debt']:.2%}")
h3.metric("WACC", f"{wacc['wacc']:.2%}")
if wacc["interest_paid_rate"] is not None and data["total_debt"] > 0:
    st.caption(f"Cost of debt before tax is {wacc['pre_tax_cost_of_debt']:.2%} "
               f"(today's borrowing rate: risk-free + {wacc['spread']:.2%} spread). "
               f"For comparison, the interest it actually paid works out at "
               f"{wacc['interest_paid_rate']:.2%} of its debt, which reflects older, "
               "lower rates.")

if override:
    st.info(f"You've overridden the WACC: using **{discount_rate:.2%}** instead of "
            f"the calculated {wacc['wacc']:.2%}.")
for warning in wacc["warnings"]:
    st.warning(warning, icon="⚠️")

st.dataframe(pd.DataFrame(wacc["table"]), hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# 8. In business terms: revenue growth and FCF margin
# ---------------------------------------------------------------------------
if business is not None:
    st.subheader("What would it take? Sales growth and margins")
    st.caption("Free cash flow = revenue × FCF margin (the share of each sale that ends "
               "up as free cash). The same cash flow growth can come from faster sales, "
               "higher margins, or a mix.")
    hg = business["hist_growth"]
    hist_from, hist_to = business["hist_years"]
    k1, k2, k3 = st.columns(3)
    k1.metric(f"Revenue ({business['revenue_year']})", money(business["revenue0"], cur))
    k2.metric("FCF margin today", f"{business['margin0']:.1%}",
              help=f"Average free cash flow ({years_used} years) ÷ latest revenue: the same "
                   "starting cash as the main calculation."
                   + (f" The latest single year was {business['latest_margin']:.1%}."
                      if business["latest_margin"] is not None else ""))
    k3.metric(f"Revenue growth {hist_from}-{hist_to}",
              f"{hg:.1%}" if hg is not None else "n/a")
    n1, n2 = st.columns(2)
    ng, nm = business["needed_growth"], business["needed_margin"]
    n1.metric("① Revenue growth needed at today's margin",
              f"{ng:.1%} a year" if ng is not None else "n/a")
    if nm is None:
        nm_text = "n/a"
    elif nm > 1:
        nm_text = "over 100% (impossible)"
    else:
        nm_text = f"{nm:.1%}"
    n2.metric("② Year-10 margin needed at historical sales growth", nm_text)
    for warning in business["warnings"]:
        st.warning(warning, icon="⚠️")

    # ③ The grid: which combinations justify today's price?
    grid = business["grid"]
    xs = [g * 100 for g in grid.columns]
    ys = [m * 100 for m in grid.index]
    worth = grid.values
    marks = [["✓" if v >= 1 else "" for v in row] for row in worth]
    heat = go.Figure()
    heat.add_heatmap(
        x=xs, y=ys, z=worth.clip(0, 2), zmin=0, zmax=2, zmid=1,
        colorscale=[[0, RED], [0.5, NEUTRAL], [1, BLUE]],
        text=marks, texttemplate="%{text}", textfont=dict(size=11),
        customdata=worth, xgap=2, ygap=2,
        hovertemplate=("Revenue growth %{x:.0f}% a year<br>Year-10 margin %{y:.1f}%<br>"
                       "Worth %{customdata:.0%} of today's price<extra></extra>"),
        colorbar=dict(title=dict(text="Worth vs<br>today's price", side="top"),
                      len=0.8, y=0.42, tickformat=".0%",
                      tickvals=[0, 0.5, 1, 1.5, 2],
                      ticktext=["0%", "50%", "100%", "150%", "200%+"]),
    )
    # Break-even line: the margin needed at each growth rate.
    top = ys[-1] + (ys[1] - ys[0]) / 2
    line = [(g * 100, m * 100) for g, m in zip(business["growths"], business["breakeven"])
            if m is not None and 0 <= m * 100 <= top]
    if line:
        heat.add_scatter(x=[p[0] for p in line], y=[p[1] for p in line], mode="lines",
                         line=dict(color=INK, width=2, dash="dot"),
                         name="Break-even (justifies today's price)",
                         hovertemplate="Break-even: %{y:.1f}% margin at %{x:.0f}% growth<extra></extra>")
    # The company's track record: historical growth at today's margin.
    if hg is not None:
        shown_x = min(max(hg * 100, xs[0]), xs[-1])
        heat.add_scatter(x=[shown_x], y=[business["margin0"] * 100], mode="markers",
                         marker=dict(symbol="diamond", size=14, color=ORANGE,
                                     line=dict(color="white", width=2)),
                         name="Track record (historical growth, today's margin)",
                         hovertemplate=(f"Track record: {hg:.1%} growth, "
                                        f"{business['margin0']:.1%} margin<extra></extra>"))
    heat.update_layout(
        xaxis=dict(title="Revenue growth a year, next 10 years (%)", dtick=1),
        yaxis=dict(title="FCF margin in year 10 (%)"),
        legend=dict(orientation="h", y=-0.25, x=0),
        margin=dict(t=20, b=40, l=10, r=10), height=460,
    )
    st.plotly_chart(heat, width="stretch")
    note = ("✓ and blue = this mix of sales growth and year-10 margin would justify "
            "today's price; red = it wouldn't. The margin moves steadily from today's "
            "level to the year-10 level.")
    if hg is not None and not xs[0] <= hg * 100 <= xs[-1]:
        note += (f" The track record ({hg:.1%} growth) is outside the grid, so "
                 "its marker is pinned to the edge.")
    if business["margin0"] * 100 > top:
        note += " Today's margin is above the top of the grid."
    st.caption(note)

# ---------------------------------------------------------------------------
# 9. Chart of projected cash flows
# ---------------------------------------------------------------------------
st.subheader("Projected free cash flow")
table = projection_table(start_fcf, growth, discount_rate, terminal_growth)
yearly = table.drop("Terminal")

chart = go.Figure()
chart.add_bar(
    x=yearly.index, y=yearly["Cash flow"] / BILLIONS,
    name="Cash flow in that year", marker_color=BLUE,
    hovertemplate="Year %{x}<br>Cash flow: %{y:,.2f}bn " + cur + "<extra></extra>",
)
chart.add_bar(
    x=yearly.index, y=yearly["Present value"] / BILLIONS,
    name="Worth today (discounted)", marker_color=ORANGE,
    hovertemplate="Year %{x}<br>Worth today: %{y:,.2f}bn " + cur + "<extra></extra>",
)
chart.update_traces(marker_cornerradius=4)
chart.update_layout(
    barmode="group", bargap=0.3, bargroupgap=0.08,
    xaxis=dict(title="Year", tickmode="linear", showgrid=False),
    yaxis=dict(title=f"Billions of {cur}", rangemode="tozero"),
    legend=dict(orientation="h", y=1.12, x=0),
    margin=dict(t=40, b=40, l=10, r=10), height=380,
)
st.plotly_chart(chart, width="stretch")

terminal_pv = table.loc["Terminal", "Present value"]
st.caption(
    f"Plus everything after year {YEARS} (the terminal value), worth "
    f"{money(terminal_pv, cur)} today, or {terminal_pv / enterprise_value:.0%} "
    "of the total."
)

with st.expander("Show the year-by-year numbers"):
    shown = table.copy()
    shown["Cash flow"] = shown["Cash flow"] / BILLIONS
    shown["Present value"] = shown["Present value"] / BILLIONS
    shown.index = shown.index.astype(str)
    st.dataframe(shown.style.format({
        "Cash flow": "{:,.2f}", "Discount factor": "{:.4f}", "Present value": "{:,.2f}",
    }))

# ---------------------------------------------------------------------------
# 10. Sensitivity table
# ---------------------------------------------------------------------------
st.subheader("How sensitive is the answer?")
st.caption("Implied growth if the discount rate or terminal growth were a little "
           "different. 'n/a' means the discount rate is too close to terminal growth.")
sens = sensitivity_table(
    start_fcf, enterprise_value,
    discount_rates=(discount_rate - 0.01, discount_rate, discount_rate + 0.01),
    terminal_growths=(terminal_growth - 0.005, terminal_growth, terminal_growth + 0.005),
)
st.dataframe(sens)

# ---------------------------------------------------------------------------
# 11. Data notes
# ---------------------------------------------------------------------------
if data["missing_fcf_years"]:
    st.caption(f"Note: Yahoo had no cash flow data for {data['missing_fcf_years']}.")

# ---------------------------------------------------------------------------
# 12. Expectations tracker
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Expectations tracker")
st.caption(
    f"For every trading day over the last {LOOKBACK_YEARS} years: the FCF growth priced in "
    "by that day's share price. The discount rate stays fixed at "
    f"{discount_rate:.2%} (today's {'override' if override else 'WACC'}), so the line only "
    "moves because of the share price and newly published results."
)
st.markdown(
    "**Look-ahead bias** means using information that wasn't public yet on a given "
    "day, like annual results before they were published, which makes the past look "
    f"better-informed than it was; to avoid it, each year's accounts are only used from "
    f"{REPORT_LAG_DAYS} days after the year ends."
)

with st.spinner("Recalculating implied growth for every trading day..."):
    try:
        history, track = load_tracker(ticker, discount_rate, terminal_growth)
    except Exception:
        history, track = None, pd.DataFrame()

if track.empty or track["implied_growth"].isna().all():
    st.info("Not enough price history or published accounts to build the tracker.")
    st.stop()

# --- Earnings dates: Yahoo's, plus any the user has added ---------------------
yahoo_dates = dates_in_period(history["yahoo_dates"], track)
saved_dates = load_saved_dates(ticker)
last_day = track.index[-1].date()
out_of_date = not yahoo_dates or (last_day - yahoo_dates[-1]).days > 200
if out_of_date:
    st.info(f"Yahoo's earnings dates for {ticker} are missing or out of date"
            + (f" (the latest is {yahoo_dates[-1]})" if yahoo_dates else "")
            + ". Add them below: they're on the company's investor relations website.")

with st.expander(f"Add or edit earnings dates for {ticker} ({len(saved_dates)} saved)",
                 expanded=out_of_date):
    st.caption("One date per row. Use + to add a row; select a row and press Delete "
               "to remove it. Click Save when you're done. Saved in earnings_dates.json.")
    edited = st.data_editor(
        pd.DataFrame({"Earnings date": pd.to_datetime(pd.Series(saved_dates, dtype="object"))}),
        num_rows="dynamic", hide_index=True, key=f"dates_{ticker}",
        column_config={"Earnings date": st.column_config.DateColumn(format="YYYY-MM-DD")},
    )
    if st.button("Save dates", key=f"save_dates_{ticker}"):
        save_dates(ticker, list(edited["Earnings date"].dropna()))
        st.success("Saved.")
        st.rerun()
    if yahoo_dates:
        st.caption("From Yahoo (always included): "
                   + ", ".join(d.isoformat() for d in yahoo_dates))

earnings_dates = dates_in_period(yahoo_dates + saved_dates, track)
switches = report_switch_dates(track)

# --- Charts: implied growth on top, share price below ---------------------------
fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                    row_heights=[0.6, 0.4],
                    subplot_titles=("Implied FCF growth (% a year, next 10 years)",
                                    f"Share price ({cur})"))
fig.add_scatter(x=track.index, y=track["implied_growth"] * 100, mode="lines",
                line=dict(color=BLUE, width=2), name="Implied growth",
                hovertemplate="%{x|%d %b %Y}: %{y:.2f}%<extra></extra>", row=1, col=1)
fig.add_scatter(x=track.index, y=track["price"], mode="lines",
                line=dict(color=ORANGE, width=2), name="Share price",
                hovertemplate="%{x|%d %b %Y}: %{y:,.2f} " + cur + "<extra></extra>",
                row=2, col=1)
for d in earnings_dates:
    fig.add_vline(x=pd.Timestamp(d), line=dict(color="#52514e", width=1, dash="dash"),
                  row="all", col=1)
for d in switches:
    fig.add_vline(x=d, line=dict(color="#1baf7a", width=1.5, dash="dot"), row="all", col=1)
# Legend entries for the vertical lines.
fig.add_scatter(x=[None], y=[None], mode="lines", name="Earnings date",
                line=dict(color="#52514e", width=1, dash="dash"), row=1, col=1)
fig.add_scatter(x=[None], y=[None], mode="lines", name="New annual accounts start being used",
                line=dict(color="#1baf7a", width=1.5, dash="dot"), row=1, col=1)
fig.update_layout(height=560, hovermode="x unified", margin=dict(t=40, b=20, l=10, r=10),
                  legend=dict(orientation="h", y=-0.08, x=0))
fig.update_yaxes(ticksuffix="%", row=1, col=1)
st.plotly_chart(fig, width="stretch")

g = track["implied_growth"]
st.caption(
    f"From {g.iloc[0]:.1%} on {track.index[0]:%d %b %Y} to {g.iloc[-1]:.1%} today "
    f"(low {g.min():.1%} on {g.idxmin():%d %b %Y}, high {g.max():.1%} on "
    f"{g.idxmax():%d %b %Y}). Today's value can differ slightly from the top of the page, "
    "because the tracker uses the share count from the latest annual report rather than "
    "Yahoo's live figure. Early on, the FCF average uses fewer years "
    f"({int(track['years_in_average'].iloc[0])} at the start)."
)

# --- Expectations shocks --------------------------------------------------------
st.markdown("**Expectations shocks**")
st.caption("The change in implied growth over the 5 trading days around each earnings "
           "date (2 days before to 2 days after), biggest first. A big rise means the "
           "market started expecting more growth; a big fall means less.")
shocks = expectation_shocks(track, earnings_dates)
if shocks.empty:
    st.info("No earnings dates in the tracked period yet, so there are no shocks to show. "
            "Add dates above.")
else:
    st.dataframe(
        shocks.style.format({
            "Earnings date": lambda d: d.isoformat(),
            "Implied growth before": "{:.2%}", "Implied growth after": "{:.2%}",
            "Change (points)": "{:+.2f}", "Share price change": "{:+.1%}",
        }),
        hide_index=True, width="stretch",
    )
