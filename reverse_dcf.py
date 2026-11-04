"""
reverse_dcf.py - works out what free cash flow growth today's share price implies.

How it works, in two steps:
  1. dcf_value(): given a growth rate, add up what all future cash is worth today.
  2. implied_growth(): try growth rates until that value equals the market value.

Run it on its own to see results for a couple of test companies:
    python reverse_dcf.py
"""

import pandas as pd
from scipy.optimize import brentq

from data import get_company_data
from wacc import calculate_wacc, get_risk_free_rate

# The discount rate now comes from each company's WACC (see wacc.py).
# The old flat 9% is kept only to compare against.
FLAT_DISCOUNT_RATE = 0.09
DEFAULT_TERMINAL_GROWTH = 0.025  # 2.5%: growth forever after year 10
YEARS = 10                       # length of the "fast growth" period

# The range of yearly growth rates the solver searches: -50% to +100%.
LOWEST_GROWTH = -0.50
HIGHEST_GROWTH = 1.00


def projection_table(start_fcf, growth, discount_rate, terminal_growth, years=YEARS):
    """
    Build the year-by-year working, so the maths can be checked by hand.

    For each year:
      cash flow      = last year's cash flow x (1 + growth)
      discount factor = 1 / (1 + discount rate) ^ year
                        (how much £1 received in that year is worth today)
      present value  = cash flow x discount factor
    """
    rows = []
    fcf = start_fcf
    for year in range(1, years + 1):
        fcf = fcf * (1 + growth)
        factor = 1 / (1 + discount_rate) ** year
        rows.append({
            "Year": year,
            "Cash flow": fcf,
            "Discount factor": factor,
            "Present value": fcf * factor,
        })

    # Terminal value: everything after year 10, assuming slow steady growth
    # forever. The "growing perpetuity" formula adds it all up in one go:
    #   value at year 10 = next year's cash / (discount rate - terminal growth)
    terminal_cash = fcf * (1 + terminal_growth)
    terminal_value = terminal_cash / (discount_rate - terminal_growth)
    factor = 1 / (1 + discount_rate) ** years
    rows.append({
        "Year": "Terminal",
        "Cash flow": terminal_value,
        "Discount factor": factor,
        "Present value": terminal_value * factor,
    })

    return pd.DataFrame(rows).set_index("Year")


def dcf_value(start_fcf, growth, discount_rate, terminal_growth, years=YEARS):
    """Total value today = the sum of every present value in the table."""
    if discount_rate <= terminal_growth:
        # The formula breaks: it would say the company is worth infinite money.
        raise ValueError("The discount rate must be higher than the terminal growth rate.")
    table = projection_table(start_fcf, growth, discount_rate, terminal_growth, years)
    return table["Present value"].sum()


def implied_growth(start_fcf, market_value,
                   discount_rate=FLAT_DISCOUNT_RATE,
                   terminal_growth=DEFAULT_TERMINAL_GROWTH):
    """
    Find the 10-year growth rate at which the DCF value equals the market value.

    Returns the growth rate (e.g. 0.034 for 3.4%), or None if there isn't a
    sensible answer.
    """
    if start_fcf <= 0:
        # With negative cash flow, growing it makes the company look WORSE,
        # so "what growth justifies the price?" has no meaningful answer.
        return None

    # The solver needs a function that is zero at the right answer:
    # "DCF value minus market value".
    def gap(growth):
        return dcf_value(start_fcf, growth, discount_rate, terminal_growth) - market_value

    # brentq needs one end of the range to be too low and the other too high.
    if gap(LOWEST_GROWTH) > 0 or gap(HIGHEST_GROWTH) < 0:
        return None

    return brentq(gap, LOWEST_GROWTH, HIGHEST_GROWTH)


def sensitivity_table(start_fcf, market_value,
                      discount_rates=(0.08, 0.09, 0.10),
                      terminal_growths=(0.02, 0.025, 0.03)):
    """Implied growth for each mix of discount rate (rows) and terminal growth (columns)."""
    table = pd.DataFrame(
        index=[f"{d:.2%} discount" for d in discount_rates],
        columns=[f"{t:.1%} terminal" for t in terminal_growths],
    )
    for d in discount_rates:
        for t in terminal_growths:
            # The maths only works if the discount rate is above terminal growth.
            g = implied_growth(start_fcf, market_value, d, t) if d > t else None
            table.loc[f"{d:.2%} discount", f"{t:.1%} terminal"] = (
                f"{g:.2%}" if g is not None else "n/a"
            )
    return table


def short_name(full_name):
    """'Tesla, Inc.' -> 'Tesla', 'Unilever PLC' -> 'Unilever'."""
    for ending in [", Inc.", " Inc.", " Inc", " PLC", " plc", " Corporation", " Corp.", " Ltd"]:
        if full_name.endswith(ending):
            return full_name[: -len(ending)]
    return full_name


def historical_growth(fcf_series):
    """
    The average yearly growth rate from the first year to the last
    (the "compound annual growth rate"). Returns None if either end is
    negative or zero, because growth from or to a loss can't be measured.
    """
    first, last = fcf_series.iloc[0], fcf_series.iloc[-1]
    periods = len(fcf_series) - 1  # 4 years of data = 3 years of growth
    if periods < 1 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / periods) - 1


# How far apart the two growth rates can be (in percentage points)
# and still count as "in line". A rule of thumb, not a law.
IN_LINE_MARGIN = 0.02


def verdict(implied, past):
    """Compare what the price needs with what the company has actually done."""
    if past is None:
        return "can't be compared with the past (history includes a loss)"
    if implied > past + IN_LINE_MARGIN:
        return "OPTIMISTIC: it expects faster growth than the company has delivered"
    if implied < past - IN_LINE_MARGIN:
        return "PESSIMISTIC: it expects slower growth than the company has delivered"
    return "IN LINE: it expects roughly the growth the company has delivered"


def run_reverse_dcf(ticker, discount_rate=None,
                    terminal_growth=DEFAULT_TERMINAL_GROWTH):
    """
    Fetch the data for a ticker, run the reverse DCF and print everything.
    If no discount rate is given, the company's own WACC is used.
    """
    data = get_company_data(ticker)
    cur = data["currency"]

    if discount_rate is None:
        risk_free, risk_free_source = get_risk_free_rate(cur)
        wacc = calculate_wacc(data, risk_free, risk_free_source)
        discount_rate = wacc["wacc"]
        print("=" * 64)
        print(f"  WACC: {data['name']} ({ticker})")
        print("=" * 64)
        print(pd.DataFrame(wacc["table"]).to_string(index=False))
        for warning in wacc["warnings"]:
            print("  ⚠️ ", warning)
        print()
    billions = 1_000_000_000
    start_fcf = data["average_fcf"]
    market_value = data["market_value"]
    enterprise_value = data["enterprise_value"]
    fcf_series = data["fcf_table"]["Free cash flow"]
    years_used = len(fcf_series)
    first_year, last_year = fcf_series.index[0], fcf_series.index[-1]

    print("=" * 64)
    print(f"  Reverse DCF: {data['name']} ({ticker})")
    print("=" * 64)
    print(f"Balance sheet at {data['balance_sheet_date']} (billions of {cur}):")
    print(f"  Market value          {market_value / billions:>10,.1f}")
    print(f"  + Total debt          {data['total_debt'] / billions:>10,.1f}")
    print(f"  - Cash & short-term   {data['cash'] / billions:>10,.1f}")
    print(f"  = Enterprise value    {enterprise_value / billions:>10,.1f}")
    print(f"\nStarting FCF:         {start_fcf / billions:,.2f} billion {cur} "
          f"({years_used}-year average)")
    print(f"Discount rate:        {discount_rate:.1%}")
    print(f"Terminal growth:      {terminal_growth:.1%}")

    if start_fcf <= 0:
        print("\n⚠️  WARNING: average free cash flow is negative or zero.")
        print("   The company is spending more cash than it brings in, so a")
        print("   reverse DCF can't give a meaningful growth rate. Growing a")
        print("   negative number just makes it more negative.")
        print()
        return None

    growth = implied_growth(start_fcf, enterprise_value, discount_rate, terminal_growth)
    if growth is None:
        print(f"\n⚠️  No growth rate between {LOWEST_GROWTH:.0%} and {HIGHEST_GROWTH:.0%} "
              "matches the enterprise value.")
        print()
        return None

    # The old (not like-for-like) answer, kept only to show the difference.
    old_growth = implied_growth(start_fcf, market_value, discount_rate, terminal_growth)

    print(f"\n➡️  Implied FCF growth: {growth:.2%} a year for the next {YEARS} years")
    if old_growth is not None:
        change = growth - old_growth
        print(f"   (vs {old_growth:.2%} using market value only: "
              f"{change * 100:+.2f} percentage points)")
    flat_growth = implied_growth(start_fcf, enterprise_value, FLAT_DISCOUNT_RATE, terminal_growth)
    if flat_growth is not None and discount_rate != FLAT_DISCOUNT_RATE:
        print(f"   (vs {flat_growth:.2%} with a flat {FLAT_DISCOUNT_RATE:.0%} discount rate: "
              f"{(growth - flat_growth) * 100:+.2f} percentage points)")

    # Show the working at the implied growth rate.
    table = projection_table(start_fcf, growth, discount_rate, terminal_growth)
    shown = table.copy()
    shown["Cash flow"] = shown["Cash flow"] / billions
    shown["Present value"] = shown["Present value"] / billions
    print(f"\nYear-by-year working (cash amounts in billions of {cur}):")
    print(shown.to_string(formatters={
        "Cash flow": "{:,.2f}".format,
        "Discount factor": "{:.4f}".format,
        "Present value": "{:,.2f}".format,
    }))

    total = table["Present value"].sum()
    terminal_share = table.loc["Terminal", "Present value"] / total
    print(f"{'Total':>8}{total / billions:>40,.2f}   <- matches enterprise value")
    print(f"\nShare of value from the terminal (after year {YEARS}): {terminal_share:.0%}")

    print("\nSensitivity: implied growth under different assumptions")
    print(sensitivity_table(
        start_fcf, enterprise_value,
        discount_rates=(discount_rate - 0.01, discount_rate, discount_rate + 0.01),
        terminal_growths=(terminal_growth - 0.005, terminal_growth, terminal_growth + 0.005),
    ).to_string())

    # Plain-English summary.
    past = historical_growth(fcf_series)
    print("\nSummary:")
    print(f"  To justify today's price, {short_name(data['name'])} must grow free cash flow")
    print(f"  about {growth:.1%} a year for {YEARS} years.")
    if past is not None:
        print(f"  From {first_year} to {last_year} it grew {past:.1%} a year.")
    print(f"  The market is {verdict(growth, past)}.")
    print()
    return growth


# This part only runs when you run "python reverse_dcf.py" directly.
if __name__ == "__main__":
    for ticker in ["ULVR.L", "AAPL", "TSLA"]:
        run_reverse_dcf(ticker)
