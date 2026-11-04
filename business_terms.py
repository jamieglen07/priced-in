"""
business_terms.py - turns "the market implies X% free cash flow growth" into
the two things a business actually controls: how fast SALES grow, and how much
of each pound of sales ends up as free cash (the FCF MARGIN).

    free cash flow = revenue × FCF margin

So the same cash flow growth can come from faster sales, fatter margins, or a
bit of both. This file works out which combinations justify today's price.

Run it on its own to see results for a few test companies:
    python business_terms.py
"""

import numpy as np
import pandas as pd
from scipy.optimize import brentq

YEARS = 10

# Revenue growth range for the grid: 0% to 15% a year. If the company's
# history is negative, the grid starts lower so its track record fits.
GRID_TOP_GROWTH = 15
GRID_LOWEST_START = -10

# A year-on-year revenue change bigger than this suggests the figures aren't
# like-for-like (e.g. the company bought or sold a business).
BIG_REVENUE_FALL = -0.10
BIG_REVENUE_JUMP = 0.30


def value_from_revenue(revenue0, margin0, revenue_growth, margin10,
                       discount_rate, terminal_growth, years=YEARS):
    """
    Value today of a company whose revenue grows at `revenue_growth` a year,
    while its FCF margin moves in a straight line from `margin0` (today) to
    `margin10` (year 10). After year 10 cash grows at `terminal_growth` forever.
    """
    total = 0.0
    fcf = 0.0
    for year in range(1, years + 1):
        revenue = revenue0 * (1 + revenue_growth) ** year
        margin = margin0 + (margin10 - margin0) * year / years
        fcf = revenue * margin
        total += fcf / (1 + discount_rate) ** year
    terminal_value = fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    return total + terminal_value / (1 + discount_rate) ** years


def required_revenue_growth(revenue0, margin0, target_value, discount_rate, terminal_growth):
    """Revenue growth needed if the margin stays at today's level (None if no sensible answer)."""
    if revenue0 <= 0 or margin0 <= 0:
        return None

    def gap(g):
        return value_from_revenue(revenue0, margin0, g, margin0,
                                  discount_rate, terminal_growth) - target_value

    low, high = -0.50, 1.00
    if gap(low) > 0 or gap(high) < 0:
        return None
    return brentq(gap, low, high)


def required_margin(revenue0, margin0, revenue_growth, target_value,
                    discount_rate, terminal_growth):
    """
    Year-10 FCF margin needed if revenue grows at `revenue_growth`.
    The value rises in a straight line as the year-10 margin rises, so two
    trial values are enough to find the exact answer.
    """
    if revenue0 <= 0:
        return None
    v0 = value_from_revenue(revenue0, margin0, revenue_growth, 0.0, discount_rate, terminal_growth)
    v1 = value_from_revenue(revenue0, margin0, revenue_growth, 1.0, discount_rate, terminal_growth)
    if v1 == v0:
        return None
    return (target_value - v0) / (v1 - v0)


def margin_steps(margin0, breakeven_at_top_growth=None):
    """
    Pick a sensible range of year-10 margins for the grid: up to about 3x
    today's margin, and high enough to reach the break-even point at the
    fastest growth rate shown (but never above 100%).
    """
    top = max(0.20, np.ceil(max(margin0, 0) * 3 / 0.05) * 0.05)
    if breakeven_at_top_growth is not None:
        top = max(top, np.ceil(breakeven_at_top_growth * 1.15 / 0.05) * 0.05)
    top = min(1.0, top)
    step = next(s for s in (0.01, 0.02, 0.025, 0.05, 0.10) if top / s <= 15)
    count = int(round(top / step))
    return [round(step * i, 4) for i in range(1, count + 1)]


def compound_growth(series):
    """Average yearly growth from the first to the last value (None if not measurable)."""
    series = series.dropna()
    if len(series) < 2 or series.iloc[0] <= 0 or series.iloc[-1] <= 0:
        return None
    return (series.iloc[-1] / series.iloc[0]) ** (1 / (len(series) - 1)) - 1


def analyse(data, discount_rate, terminal_growth):
    """
    Work out the business-terms view for one company.
    Returns a dictionary of results, or None if there's no revenue data.
    """
    revenue = data["revenue_by_year"]
    if revenue.empty or revenue.iloc[-1] <= 0:
        return None

    target = data["enterprise_value"]
    revenue0 = revenue.iloc[-1]
    latest_year = revenue.index[-1]

    # Today's margin: the same starting cash as the main calculation (the
    # average FCF), divided by the latest revenue. That keeps both views
    # consistent: at a steady margin, revenue growth = implied FCF growth.
    margin0 = data["average_fcf"] / revenue0
    fcf = data["fcf_table"]["Free cash flow"]
    latest_margin = fcf.iloc[-1] / revenue0 if fcf.index[-1] == latest_year else None

    # Look for sudden jumps or falls that suggest the years aren't like-for-like.
    warnings = []
    history = revenue
    changes = revenue.pct_change().dropna()
    breaks = [year for year, change in changes.items()
              if change < BIG_REVENUE_FALL or change > BIG_REVENUE_JUMP]
    if breaks:
        year = breaks[-1]
        change = changes[year]
        direction = "fell" if change < 0 else "jumped"
        after_break = revenue[revenue.index >= year]
        note = (f"Revenue {direction} {abs(change):.0%} from {year - 1} to {year}. That "
                "often means a business was sold or bought, or the figures were restated.")
        if len(after_break) >= 2:
            history = after_break
            note += (f" To compare like with like, historical growth is measured from "
                     f"{year} onwards only.")
        else:
            note += " Historical revenue growth may not reflect the underlying business."
        warnings.append(note)

    hist_growth = compound_growth(history)

    needed_growth = required_revenue_growth(revenue0, margin0, target,
                                            discount_rate, terminal_growth)
    needed_margin = (required_margin(revenue0, margin0, hist_growth, target,
                                     discount_rate, terminal_growth)
                     if hist_growth is not None else None)

    # The grid: value of each combination as a % of today's enterprise value.
    start = 0 if hist_growth is None or hist_growth >= 0 else \
        max(GRID_LOWEST_START, int(np.floor(hist_growth * 100)) - 1)
    growths = [g / 100 for g in range(start, GRID_TOP_GROWTH + 1)]

    top_growth_breakeven = required_margin(revenue0, margin0, growths[-1],
                                           target, discount_rate, terminal_growth)
    margins = margin_steps(margin0, top_growth_breakeven)
    grid = pd.DataFrame(
        [[value_from_revenue(revenue0, margin0, g, m, discount_rate, terminal_growth) / target
          for g in growths] for m in margins],
        index=margins, columns=growths,
    )
    # The break-even line: the margin needed at each revenue growth rate.
    breakeven = [required_margin(revenue0, margin0, g, target, discount_rate, terminal_growth)
                 for g in growths]

    return {
        "revenue0": revenue0,
        "revenue_year": latest_year,
        "revenue_by_year": revenue,
        "margin0": margin0,
        "latest_margin": latest_margin,
        "hist_growth": hist_growth,
        "hist_years": (history.index[0], latest_year),
        "needed_growth": needed_growth,
        "needed_margin": needed_margin,
        "grid": grid,
        "growths": growths,
        "breakeven": breakeven,
        "warnings": warnings,
    }


def summary_text(name, result):
    """The plain-English sentence."""
    m0, ng = result["margin0"], result["needed_growth"]
    nm, hg = result["needed_margin"], result["hist_growth"]

    # Part 1: sales growth needed if the margin stays where it is.
    if ng is None:
        growth_part = None
    elif ng >= 0:
        growth_part = f"**{ng:.1%} revenue growth** a year at today's **{m0:.1%}** FCF margin"
    else:
        growth_part = (f"sales that **shrink up to {abs(ng):.1%} a year** at today's "
                       f"**{m0:.1%}** FCF margin")

    # Part 2: year-10 margin needed if sales grow at the historical rate.
    margin_part, impossible = None, False
    if nm is not None and hg is not None:
        rate = f"the historical rate ({hg:.1%} a year)"
        if nm > 1:
            impossible = True
        elif nm >= m0:
            margin_part = f"margins rising to **{nm:.1%}** by year 10 with sales growing at {rate}"
        elif nm >= 0:
            margin_part = f"margins falling as low as **{nm:.1%}** by year 10 with sales growing at {rate}"
        else:
            margin_part = f"sales growing at {rate}, even with almost no cash margin"

    # "needs" when the bar is high; "could manage with" when it's low.
    verb = "needs" if ng is None or ng >= 0 else "could manage with"
    if growth_part and margin_part:
        return f"To justify today's price, {name} {verb} either {growth_part}, or {margin_part}."
    if growth_part and impossible:
        return (f"To justify today's price, {name} {verb} {growth_part}. If sales only grow at "
                f"the historical rate ({hg:.1%} a year), no margin would be enough: even "
                "turning 100% of sales into free cash would fall short.")
    if growth_part:
        return f"To justify today's price, {name} {verb} {growth_part}."
    if margin_part:
        return f"To justify today's price, {name} needs {margin_part}."
    return f"There isn't enough data to put {name}'s price into revenue and margin terms."


# This part only runs when you run "python business_terms.py" directly.
if __name__ == "__main__":
    from data import get_company_data
    from reverse_dcf import DEFAULT_TERMINAL_GROWTH, implied_growth, short_name
    from wacc import calculate_wacc, get_risk_free_rate

    for ticker in ["ULVR.L", "AAPL", "TSLA"]:
        data = get_company_data(ticker)
        rf, src = get_risk_free_rate(data["currency"])
        r = calculate_wacc(data, rf, src)["wacc"]
        result = analyse(data, r, DEFAULT_TERMINAL_GROWTH)
        g = implied_growth(data["average_fcf"], data["enterprise_value"], r, DEFAULT_TERMINAL_GROWTH)
        cur = data["currency"]
        print(f"\n=== {data['name']} ({ticker}) - WACC {r:.2%} ===")
        print(f"Implied FCF growth:          {g:.2%}")
        print(f"Latest revenue ({result['revenue_year']}):       {result['revenue0'] / 1e9:,.1f}bn {cur}")
        print(f"FCF margin used (avg FCF ÷ latest revenue): {result['margin0']:.1%}")
        if result["latest_margin"] is not None:
            print(f"Latest-year FCF margin:      {result['latest_margin']:.1%}")
        a, b = result["hist_years"]
        print(f"Historical revenue growth {a}-{b}: {result['hist_growth']:.1%}")
        print(f"(1) Revenue growth needed at today's margin: {result['needed_growth']:.2%}")
        print(f"(2) Year-10 margin needed at historical growth: {result['needed_margin']:.1%}")
        print(summary_text(short_name(data["name"]), result).replace("**", ""))
        for w in result["warnings"]:
            print("  ⚠️ ", w)
        print("Grid (✓ = justifies today's price), rows = year-10 margin, cols = revenue growth:")
        ticks = (result["grid"] >= 1).replace({True: "✓", False: "·"})
        ticks.index = [f"{m:.0%}" for m in ticks.index]
        ticks.columns = [f"{g:.0%}" for g in ticks.columns]
        print(ticks.iloc[::-1].to_string())
