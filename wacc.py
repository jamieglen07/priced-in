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

# Fallbacks used when a company's own data is missing or looks wrong.
DEFAULT_BETA = 1.0         # 1.0 = exactly as risky as the stock market overall
MAX_BETA = 3.0
DEFAULT_TAX_RATE = 0.25    # a typical corporate tax rate
MAX_TAX_RATE = 0.40
DEBT_SPREAD = 0.015        # companies usually borrow at ~1.5% above the risk-free rate
COST_OF_DEBT_RANGE = (0.01, 0.15)   # outside 1-15% we assume the data is wrong
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
    beta = data.get("beta")
    beta_source = "Yahoo Finance (how much the share moves vs. the market)"
    if beta is None:
        warnings.append(f"Beta is missing, so a default of {DEFAULT_BETA} was used.")
        beta, beta_source = DEFAULT_BETA, "Default - Yahoo had no beta"
    elif beta <= 0:
        warnings.append(f"Beta of {beta:.2f} is zero or negative, which isn't realistic "
                        f"for a normal company - used {DEFAULT_BETA} instead.")
        beta, beta_source = DEFAULT_BETA, f"Default - Yahoo's beta ({data['beta']:.2f}) was ≤ 0"
    elif beta > MAX_BETA:
        warnings.append(f"Beta of {beta:.2f} is above {MAX_BETA}, which is extreme - "
                        f"used {DEFAULT_BETA} instead.")
        beta, beta_source = DEFAULT_BETA, f"Default - Yahoo's beta ({data['beta']:.2f}) was > {MAX_BETA}"

    cost_of_equity = risk_free + beta * equity_risk_premium

    add("Risk-free rate", pct(risk_free), risk_free_source)
    add("Beta", f"{beta:.2f}", beta_source)
    add("Equity risk premium", pct(equity_risk_premium), "Assumption (editable)")
    add("Cost of equity", pct(cost_of_equity), "Risk-free + beta × equity risk premium")

    # --- Cost of debt -------------------------------------------------------
    # Interest paid ÷ amount borrowed, using the newest year that has both.
    interest, debt = data["interest_by_year"], data["debt_by_year"]
    years_with_both = [y for y in interest.index if y in debt.index and debt[y] > 0]

    pre_tax_cost_of_debt = None
    if years_with_both:
        year = years_with_both[0]
        pre_tax_cost_of_debt = abs(interest[year]) / debt[year]
        debt_source = f"Interest expense ÷ total debt ({year} accounts)"
        low, high = COST_OF_DEBT_RANGE
        if not low <= pre_tax_cost_of_debt <= high:
            warnings.append(f"Cost of debt worked out at {pct(pre_tax_cost_of_debt)}, "
                            f"outside the realistic {low:.0%}-{high:.0%} range - used "
                            f"risk-free + {DEBT_SPREAD:.1%} instead.")
            pre_tax_cost_of_debt = None
        elif year != max(interest.index.max(), debt.index.max()):
            warnings.append(f"Latest interest expense is missing, so the cost of debt "
                            f"uses {year} figures.")

    if pre_tax_cost_of_debt is None:
        pre_tax_cost_of_debt = risk_free + DEBT_SPREAD
        debt_source = f"Default - risk-free + {DEBT_SPREAD:.1%}"
        if data["total_debt"] <= 0:
            debt_source = "Not used - the company has no debt"
        elif not years_with_both:
            warnings.append("No interest expense data, so the cost of debt was "
                            f"estimated as risk-free + {DEBT_SPREAD:.1%}.")
    elif pre_tax_cost_of_debt < risk_free:
        warnings.append(f"Cost of debt ({pct(pre_tax_cost_of_debt)}) is below today's "
                        f"risk-free rate ({pct(risk_free)}). That usually means the debt "
                        "was borrowed when interest rates were lower - new borrowing "
                        "would cost more.")

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

    add("Cost of debt (before tax)", pct(pre_tax_cost_of_debt), debt_source)
    add("Tax rate", pct(tax_rate), tax_source)
    add("Cost of debt (after tax)", pct(after_tax_cost_of_debt), "Cost of debt × (1 − tax rate)")

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
        "cost_of_equity": cost_of_equity,
        "after_tax_cost_of_debt": after_tax_cost_of_debt,
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
