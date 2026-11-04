"""
wacc.py - works out a discount rate for each company: its WACC
(weighted average cost of capital).

The idea: a company is paid for by two groups of people.
  - Shareholders, who want a return for taking risk  -> cost of equity
  - Lenders, who charge interest                     -> cost of debt
WACC is the average of the two, weighted by how much of each the company uses.

Run it on its own to see the WACC for a few test companies:
    python wacc.py
"""

import yfinance as yf

# ===========================================================================
# EDITABLE ASSUMPTIONS - change these numbers if you disagree with them
# ===========================================================================

# Extra yearly return investors want for owning shares instead of safe
# government bonds. 4-6% is the usual range; 5% is a common middle choice.
EQUITY_RISK_PREMIUM = 0.05

# Risk-free rate for each currency: the 10-year government bond yield.
# USD is downloaded live (^TNX). Others are typed in by hand - update them
# from a site like tradingeconomics.com when they go out of date.
RISK_FREE_DEFAULTS = {
    "GBP": (0.053, "UK 10-year gilt, about 5.3% on 30 Oct 2026 (edit in wacc.py)"),
}
OTHER_CURRENCY_RISK_FREE = 0.045  # rough guess for any currency not listed

# Credit spreads: the extra interest a company pays above the risk-free rate,
# based on its interest coverage ratio (EBIT ÷ interest expense).
# Each row: (coverage at least this, rating, spread).
# Source: Damodaran's synthetic rating table for large non-financial
# companies, data as of January 2026:
# https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html
CREDIT_SPREAD_TABLE = [
    (8.50, "AAA", 0.0040),
    (6.50, "AA", 0.0055),
    (5.50, "A+", 0.0070),
    (4.25, "A", 0.0078),
    (3.00, "A-", 0.0089),
    (2.50, "BBB", 0.0111),
    (2.25, "BB+", 0.0138),
    (2.00, "BB", 0.0184),
    (1.75, "B+", 0.0275),
    (1.50, "B", 0.0321),
    (1.25, "B-", 0.0509),
    (0.80, "CCC", 0.0885),
    (0.65, "CC", 0.1261),
    (0.20, "C", 0.1600),
    (float("-inf"), "D", 0.1900),
]
DEFAULT_RATING = "BBB"   # used when coverage can't be worked out

# Fallbacks used when a company's own data is missing or looks wrong.
DEFAULT_BETA = 1.0         # 1.0 = exactly as risky as the stock market overall
MAX_BETA = 3.0
DEFAULT_TAX_RATE = 0.25    # a typical corporate tax rate
MAX_TAX_RATE = 0.40
WACC_WARNING_RANGE = (0.05, 0.15)   # outside this, show a "check this" note

# ===========================================================================


def get_risk_free_rate(currency):
    """Return (rate, where it came from) for a trading currency like "USD"."""
    if currency == "USD":
        # ^TNX is the US 10-year Treasury yield, quoted in percent (e.g. 4.89).
        quote = yf.Ticker("^TNX").fast_info.get("lastPrice")
        if quote:
            return quote / 100, "US 10-year Treasury yield, live from Yahoo (^TNX)"
    if currency in RISK_FREE_DEFAULTS:
        return RISK_FREE_DEFAULTS[currency]
    return (OTHER_CURRENCY_RISK_FREE,
            f"No {currency} bond yield available - rough default (edit in wacc.py)")


def adjust_beta(raw_beta):
    """
    Blume adjustment: pull the beta a third of the way towards 1.0.
    Over time, most companies' betas drift towards the market average,
    so this gives a better guess of future risk than the raw number.
    """
    return (2 / 3) * raw_beta + (1 / 3) * 1.0


def rating_from_coverage(coverage):
    """Look up (rating, spread) for an interest coverage ratio."""
    for minimum, rating, spread in CREDIT_SPREAD_TABLE:
        if coverage >= minimum:
            return rating, spread


def spread_for_rating(rating):
    return next(spread for _, r, spread in CREDIT_SPREAD_TABLE if r == rating)


def pct(x):
    return f"{x:.2%}"


def calculate_wacc(data, risk_free, risk_free_source,
                   equity_risk_premium=EQUITY_RISK_PREMIUM):
    """
    Work out the WACC for a company, using the dictionary from get_company_data().

    Returns a dictionary with:
      "wacc":     the final number, e.g. 0.068 for 6.8%
      "table":    every input and result, with where it came from
      "warnings": anything that was missing or looked wrong
    """
    warnings = []
    rows = []

    def add(item, value, source):
        rows.append({"Item": item, "Value": value, "Where it came from": source})

    # --- Cost of equity (CAPM) --------------------------------------------
    # The return shareholders want = safe rate + (how risky x extra for risk).
    raw_beta = data.get("beta")
    raw_source = "Yahoo Finance (how much the share moved vs. the market)"
    if raw_beta is None:
        warnings.append(f"Beta is missing, so a default of {DEFAULT_BETA} was used.")
        raw_beta, raw_source = DEFAULT_BETA, "Default - Yahoo had no beta"
    elif raw_beta <= 0:
        warnings.append(f"Beta of {raw_beta:.2f} is zero or negative, which isn't realistic "
                        f"for a normal company - used {DEFAULT_BETA} instead.")
        raw_beta, raw_source = DEFAULT_BETA, f"Default - Yahoo's beta ({data['beta']:.2f}) was ≤ 0"
    elif raw_beta > MAX_BETA:
        warnings.append(f"Beta of {raw_beta:.2f} is above {MAX_BETA}, which is extreme - "
                        f"used {DEFAULT_BETA} instead.")
        raw_beta, raw_source = DEFAULT_BETA, f"Default - Yahoo's beta ({data['beta']:.2f}) was > {MAX_BETA}"

    beta = adjust_beta(raw_beta)
    cost_of_equity = risk_free + beta * equity_risk_premium

    add("Risk-free rate", pct(risk_free), risk_free_source)
    add("Beta (raw)", f"{raw_beta:.2f}", raw_source)
    add("Beta (adjusted)", f"{beta:.2f}", "Blume adjustment: ⅔ × raw beta + ⅓ × 1.0")
    add("Equity risk premium", pct(equity_risk_premium), "Assumption (editable)")
    add("Cost of equity", pct(cost_of_equity), "Risk-free + adjusted beta × equity risk premium")

    # --- Cost of debt: what the company would pay to borrow TODAY ----------
    # Interest coverage = EBIT ÷ interest: how many times over the company's
    # profit could pay its interest bill. Higher = safer = cheaper borrowing.
    ebit, interest, debt = data["ebit_by_year"], data["interest_by_year"], data["debt_by_year"]
    has_debt = data["total_debt"] > 0

    coverage_years = [y for y in interest.index
                      if y in ebit.index and abs(interest[y]) > 0]
    coverage = None
    if coverage_years:
        year = coverage_years[0]
        coverage = ebit[year] / abs(interest[year])
        rating, spread = rating_from_coverage(coverage)
        coverage_text = f"{coverage:.1f}x"
        coverage_source = f"EBIT ÷ interest expense ({year} accounts)"
        rating_source = "Damodaran synthetic rating table (Jan 2026)"
        latest_year = max(ebit.index.max(), interest.index.max())
        if has_debt and year != latest_year:
            warnings.append(f"Latest interest expense is missing, so the coverage ratio "
                            f"uses {year} figures.")
    else:
        rating, spread = DEFAULT_RATING, spread_for_rating(DEFAULT_RATING)
        coverage_text = "n/a"
        coverage_source = "No EBIT or interest data"
        rating_source = f"Default - assumed {DEFAULT_RATING}"
        if has_debt:
            warnings.append(f"Couldn't work out interest coverage, so a {DEFAULT_RATING} "
                            f"rating ({pct(spread)} spread) was assumed.")

    if coverage is not None and coverage < 1.25 and has_debt:
        warnings.append(f"Interest coverage is only {coverage:.1f}x (profit barely covers, "
                        f"or doesn't cover, the interest bill), so the company is rated "
                        f"{rating} and borrowing is expensive.")

    pre_tax_cost_of_debt = risk_free + spread

    # Interest reduces a company's tax bill, so debt is cheaper after tax.
    tax, pretax = data["tax_by_year"], data["pretax_by_year"]
    tax_years = [y for y in tax.index if y in pretax.index and pretax[y] > 0]
    tax_rate, tax_source = None, None
    if tax_years:
        year = tax_years[0]
        tax_rate = tax[year] / pretax[year]
        tax_source = f"Tax ÷ profit before tax ({year} accounts)"
        if not 0 <= tax_rate <= MAX_TAX_RATE:
            warnings.append(f"Effective tax rate worked out at {pct(tax_rate)}, which "
                            f"looks unusual - used {DEFAULT_TAX_RATE:.0%} instead.")
            tax_rate = None
    if tax_rate is None:
        if not tax_years:
            warnings.append(f"No usable tax data (or a loss), so a {DEFAULT_TAX_RATE:.0%} "
                            "tax rate was used.")
        tax_rate, tax_source = DEFAULT_TAX_RATE, "Default"

    after_tax_cost_of_debt = pre_tax_cost_of_debt * (1 - tax_rate)

    add("Interest coverage", coverage_text, coverage_source)
    add("Implied credit rating", rating, rating_source)
    add("Credit spread", pct(spread), f"Spread for a {rating} rating")
    add("Cost of debt (before tax)", pct(pre_tax_cost_of_debt), "Risk-free + credit spread")
    add("Tax rate", pct(tax_rate), tax_source)
    add("Cost of debt (after tax)", pct(after_tax_cost_of_debt), "Cost of debt × (1 − tax rate)")

    # Comparison only: the old method (interest actually paid ÷ debt).
    # This reflects rates from when the money was borrowed, not today's.
    paid_years = [y for y in interest.index if y in debt.index and debt[y] > 0]
    interest_paid_rate = None
    if paid_years:
        year = paid_years[0]
        interest_paid_rate = abs(interest[year]) / debt[year]
        add("For comparison: interest paid ÷ debt", pct(interest_paid_rate),
            f"Old method, {year} accounts (not used in WACC)")

    if not has_debt:
        rows = [r for r in rows if not r["Item"].startswith(("Interest coverage", "Implied",
                                                             "Credit spread", "For comparison"))]
        for r in rows:
            if r["Item"].startswith("Cost of debt"):
                r["Where it came from"] += " - not used, the company has no debt"

    # --- Weights --------------------------------------------------------------
    equity, total_debt = data["market_value"], data["total_debt"]
    equity_weight = equity / (equity + total_debt)
    debt_weight = 1 - equity_weight
    cur = data["currency"]

    add("Equity (market value)", f"{equity / 1e9:,.1f}bn {cur}", "Share price × shares")
    add("Debt", f"{total_debt / 1e9:,.1f}bn {cur}", "Latest balance sheet")
    add("Equity weight", pct(equity_weight), "Equity ÷ (equity + debt)")
    add("Debt weight", pct(debt_weight), "Debt ÷ (equity + debt)")

    # --- WACC ---------------------------------------------------------------
    wacc = equity_weight * cost_of_equity + debt_weight * after_tax_cost_of_debt
    add("WACC", pct(wacc), "Equity weight × cost of equity + debt weight × after-tax cost of debt")

    low, high = WACC_WARNING_RANGE
    if not low <= wacc <= high:
        warnings.append(f"A WACC of {pct(wacc)} is unusual (most companies are "
                        f"{low:.0%}-{high:.0%}). Check the inputs above.")

    return {
        "wacc": wacc,
        "raw_beta": raw_beta,
        "beta": beta,
        "cost_of_equity": cost_of_equity,
        "coverage": coverage,
        "rating": rating,
        "spread": spread,
        "pre_tax_cost_of_debt": pre_tax_cost_of_debt,
        "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "interest_paid_rate": interest_paid_rate,
        "table": rows,
        "warnings": warnings,
    }


# This part only runs when you run "python wacc.py" directly.
if __name__ == "__main__":
    import pandas as pd
    from data import get_company_data

    for ticker in ["ULVR.L", "AAPL", "TSLA"]:
        data = get_company_data(ticker)
        rf, rf_source = get_risk_free_rate(data["currency"])
        result = calculate_wacc(data, rf, rf_source)
        print(f"\n=== {data['name']} ({ticker}) ===")
        print(pd.DataFrame(result["table"]).to_string(index=False))
        for w in result["warnings"]:
            print("  ⚠️ ", w)
