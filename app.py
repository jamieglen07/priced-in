"""
app.py - the "What's Priced In?" web app.

Start it with:
    streamlit run app.py
"""

import plotly.graph_objects as go
import streamlit as st

from data import get_company_data
from reverse_dcf import (
    YEARS,
    historical_growth,
    implied_growth,
    projection_table,
    sensitivity_table,
    verdict,
)

BILLIONS = 1_000_000_000
BLUE, ORANGE = "#2a78d6", "#eb6834"


# Remember each company's data for an hour, so moving a slider doesn't
# re-download everything from Yahoo (which would be slow).
@st.cache_data(ttl=3600, show_spinner=False)
def load_data(ticker):
    return get_company_data(ticker)


def money(amount, currency):
    """Format a number like '125.9bn GBP'."""
    return f"{amount / BILLIONS:,.1f}bn {currency}"


# ---------------------------------------------------------------------------
# 1. Page title and inputs
# ---------------------------------------------------------------------------
st.set_page_config(page_title="What's Priced In?", layout="centered")
st.title("What's Priced In?")
st.caption("A reverse DCF: the free cash flow growth today's share price implies.")

ticker = st.text_input(
    "Stock ticker",
    placeholder="e.g. AAPL, or ULVR.L for UK shares",
).strip().upper()

col1, col2 = st.columns(2)
discount_rate = col1.slider(
    "Discount rate (the yearly return investors want)",
    min_value=6.0, max_value=12.0, value=9.0, step=0.5, format="%.1f%%",
) / 100
terminal_growth = col2.slider(
    "Terminal growth (growth forever after year 10)",
    min_value=1.0, max_value=4.0, value=2.5, step=0.5, format="%.1f%%",
) / 100

# Nothing typed yet: stop here and wait.
if not ticker:
    st.info("Type a ticker above to get started.")
    st.stop()

# ---------------------------------------------------------------------------
# 2. Fetch the data (with clear messages if something goes wrong)
# ---------------------------------------------------------------------------
with st.spinner(f"Downloading data for {ticker}..."):
    try:
        data = load_data(ticker)
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

growth = implied_growth(start_fcf, enterprise_value, discount_rate, terminal_growth)
if growth is None:
    st.warning("No growth rate between -50% and +100% a year matches today's "
               "price, so the result would not be meaningful.")
    st.stop()

# ---------------------------------------------------------------------------
# 3. Plain-English summary
# ---------------------------------------------------------------------------
past = historical_growth(fcf_series)
short_name = data["name"].replace(" PLC", "").replace(" Inc.", "")
outcome = verdict(growth, past)

summary = (f"To justify today's price, **{short_name}** must grow free cash flow "
           f"about **{growth:.1%} a year for {YEARS} years**. ")
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

# ---------------------------------------------------------------------------
# 4. Key numbers
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
# 5. Chart of projected cash flows
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
# 6. Sensitivity table
# ---------------------------------------------------------------------------
st.subheader("How sensitive is the answer?")
st.caption("Implied growth if you change the assumptions a little around your "
           "slider settings.")
sens = sensitivity_table(
    start_fcf, enterprise_value,
    discount_rates=(discount_rate - 0.01, discount_rate, discount_rate + 0.01),
    terminal_growths=(terminal_growth - 0.005, terminal_growth, terminal_growth + 0.005),
)
st.dataframe(sens)

# ---------------------------------------------------------------------------
# 7. Data warnings
# ---------------------------------------------------------------------------
if data["missing_fcf_years"]:
    st.caption(f"Note: Yahoo had no cash flow data for {data['missing_fcf_years']}.")
