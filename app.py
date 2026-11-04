"""
app.py - the "What's Priced In?" web app.

Start it with:
    streamlit run app.py
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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
from wacc import EQUITY_RISK_PREMIUM, calculate_wacc, get_risk_free_rate

BILLIONS = 1_000_000_000
BLUE, ORANGE = "#2a78d6", "#eb6834"


# Remember downloaded data for an hour, so moving a slider doesn't
# re-download everything from Yahoo (which would be slow).
@st.cache_data(ttl=3600, show_spinner=False)
def load_data(ticker):
    return get_company_data(ticker)


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
           "the company uses. Future cash is discounted at this rate.")
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
# 8. Chart of projected cash flows
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
# 9. Sensitivity table
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
# 10. Data notes
# ---------------------------------------------------------------------------
if data["missing_fcf_years"]:
    st.caption(f"Note: Yahoo had no cash flow data for {data['missing_fcf_years']}.")
