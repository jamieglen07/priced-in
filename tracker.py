"""
tracker.py - the expectations tracker.

For every trading day over the last few years, it asks: "using that day's
share price, and only the accounts that had been published by then, what
FCF growth was the market pricing in?" Then it looks at how that changed
around each earnings announcement.

Run it on its own to see results for Apple:
    python tracker.py
"""

import json
import os
from pathlib import Path

import pandas as pd
import yfinance as yf
from scipy.optimize import brentq

from data import get_exchange_rate, to_main_currency

LOOKBACK_YEARS = 3
REPORT_LAG_DAYS = 60          # accounts assumed public ~60 days after year end
MAX_YEARS_IN_AVERAGE = 4      # same as the main page: up to a 4-year FCF average
SHOCK_WINDOW = 2              # trading days either side of the earnings date
YEARS = 10
LOWEST_GROWTH, HIGHEST_GROWTH = -0.50, 1.00

# Earnings dates you type in yourself are saved here.
EARNINGS_FILE = Path(os.environ.get(
    "PRICED_IN_EARNINGS_FILE", Path(__file__).parent / "earnings_dates.json"))


# ---------------------------------------------------------------------------
# Saved earnings dates
# ---------------------------------------------------------------------------
def load_saved_dates(ticker):
    """Earnings dates the user added for this ticker (a list of dates)."""
    if not EARNINGS_FILE.exists():
        return []
    saved = json.loads(EARNINGS_FILE.read_text())
    return sorted(pd.to_datetime(d).date() for d in saved.get(ticker, []))


def save_dates(ticker, dates):
    """Save the user's earnings dates for this ticker, replacing any old ones."""
    saved = json.loads(EARNINGS_FILE.read_text()) if EARNINGS_FILE.exists() else {}
    cleaned = sorted({pd.to_datetime(d).date().isoformat() for d in dates if pd.notna(d)})
    if cleaned:
        saved[ticker] = cleaned
    else:
        saved.pop(ticker, None)
    EARNINGS_FILE.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Downloading the history
# ---------------------------------------------------------------------------
def _row(table, *names):
    """First matching row of a financial table, as {year-end date: value}."""
    for name in names:
        if name in table.index:
            row = table.loc[name]
            row.index = pd.to_datetime(row.index).tz_localize(None).normalize()
            return row
    return pd.Series(dtype=float)


def load_history(ticker):
    """
    Download everything the tracker needs: daily prices, every annual report
    Yahoo has (with the date it would have become public), exchange rates
    and Yahoo's earnings dates.
    """
    stock = yf.Ticker(ticker)
    info = stock.info

    # Daily closing prices. auto_adjust=False keeps the real traded price
    # (Yahoo still adjusts it for share splits, which we match below).
    prices = stock.history(period=f"{LOOKBACK_YEARS}y", auto_adjust=False)["Close"]
    if prices.empty:
        raise ValueError(f"No price history for {ticker}.")
    prices.index = prices.index.tz_localize(None).normalize()
    prices, currency = to_main_currency(prices, info.get("currency"))
    reporting_currency = (info.get("financialCurrency") or currency).upper()

    # One row per annual report.
    cashflow, balance = stock.cashflow, stock.balance_sheet
    reports = pd.DataFrame({
        "fcf": _row(cashflow, "Operating Cash Flow") + _row(cashflow, "Capital Expenditure"),
        "debt": _row(balance, "Total Debt"),
        "cash": _row(balance, "Cash Cash Equivalents And Short Term Investments",
                     "Cash And Cash Equivalents"),
        "shares": _row(balance, "Ordinary Shares Number", "Share Issued"),
    }).sort_index()
    reports = reports.dropna(subset=["fcf"])
    reports[["debt", "cash"]] = reports[["debt", "cash"]].fillna(0.0)
    reports["public_date"] = reports.index + pd.Timedelta(days=REPORT_LAG_DAYS)

    # Yahoo's past prices are adjusted for later share splits (and share
    # consolidations), so adjust each report's share count the same way.
    splits = stock.splits
    if len(splits):
        splits.index = splits.index.tz_localize(None)
        for year_end in reports.index:
            later = splits[splits.index > year_end]
            reports.loc[year_end, "shares"] *= later.prod() if len(later) else 1.0

    # Exchange rate (accounts currency -> trading currency) for each day.
    if reporting_currency == currency:
        fx = pd.Series(1.0, index=prices.index)
    else:
        fx = None
        for pair, invert in [(f"{reporting_currency}{currency}=X", False),
                             (f"{currency}{reporting_currency}=X", True)]:
            hist = yf.Ticker(pair).history(period=f"{LOOKBACK_YEARS}y")["Close"]
            if not hist.empty:
                hist.index = hist.index.tz_localize(None).normalize()
                fx = 1 / hist if invert else hist
                break
        if fx is None:   # no history: fall back to today's rate
            fx = pd.Series(get_exchange_rate(reporting_currency, currency), index=prices.index)
        fx = fx.reindex(prices.index).ffill().bfill()

    # Yahoo's earnings dates (may be missing or out of date).
    yahoo_dates = []
    try:
        earnings = stock.get_earnings_dates(limit=40)
        if earnings is not None and not earnings.empty:
            yahoo_dates = sorted({d.date() for d in earnings.index})
    except Exception:
        pass

    return {
        "ticker": ticker,
        "currency": currency,
        "reporting_currency": reporting_currency,
        "prices": prices,
        "reports": reports,
        "fx": fx,
        "yahoo_dates": yahoo_dates,
    }


# ---------------------------------------------------------------------------
# The day-by-day calculation
# ---------------------------------------------------------------------------
def _value_per_unit_fcf(growth, discount_rate, terminal_growth):
    """DCF value of a company with £1 of starting FCF (same maths as reverse_dcf.py)."""
    total, cash = 0.0, 1.0
    for year in range(1, YEARS + 1):
        cash *= 1 + growth
        total += cash / (1 + discount_rate) ** year
    terminal = cash * (1 + terminal_growth) / (discount_rate - terminal_growth)
    return total + terminal / (1 + discount_rate) ** YEARS


def _implied_growth(start_fcf, enterprise_value, discount_rate, terminal_growth):
    """Like reverse_dcf.implied_growth, but quicker - it runs ~750 times."""
    if start_fcf <= 0 or enterprise_value <= 0:
        return None
    target = enterprise_value / start_fcf

    def gap(g):
        return _value_per_unit_fcf(g, discount_rate, terminal_growth) - target

    if gap(LOWEST_GROWTH) > 0 or gap(HIGHEST_GROWTH) < 0:
        return None
    return brentq(gap, LOWEST_GROWTH, HIGHEST_GROWTH)


def build_tracker(history, discount_rate, terminal_growth):
    """
    One row per trading day: the price, the accounts that were public that
    day, the enterprise value and the implied FCF growth.
    """
    reports = history["reports"]
    rows = []
    for day, price in history["prices"].items():
        # Only accounts that would have been public on this day.
        public = reports[reports["public_date"] <= day]
        if public.empty:
            continue
        recent = public.tail(MAX_YEARS_IN_AVERAGE)
        latest = public.iloc[-1]
        fx = history["fx"].get(day, 1.0)

        market_value = price * latest["shares"]
        debt, cash = latest["debt"] * fx, latest["cash"] * fx
        enterprise_value = market_value + debt - cash
        average_fcf = recent["fcf"].mean() * fx

        rows.append({
            "date": day,
            "price": price,
            "market_value": market_value,
            "enterprise_value": enterprise_value,
            "average_fcf": average_fcf,
            "years_in_average": len(recent),
            "latest_report": public.index[-1].date(),
            "implied_growth": _implied_growth(average_fcf, enterprise_value,
                                              discount_rate, terminal_growth),
        })
    return pd.DataFrame(rows).set_index("date")


def report_switch_dates(tracker):
    """Days when a newly published annual report started being used."""
    changed = tracker["latest_report"] != tracker["latest_report"].shift()
    return list(tracker.index[changed][1:])


# ---------------------------------------------------------------------------
# Expectations shocks
# ---------------------------------------------------------------------------
def expectation_shocks(tracker, earnings_dates, window=SHOCK_WINDOW):
    """
    For each earnings date, the change in implied growth over the 5 trading
    days around it (2 before, the day itself, 2 after), measured from the
    close just before that window to the close at its end.
    Biggest changes (up or down) first.
    """
    days = tracker.index
    switches = set(report_switch_dates(tracker))
    rows = []
    for date in sorted(set(earnings_dates)):
        position = days.searchsorted(pd.Timestamp(date))   # first trading day on/after
        start, end = position - window - 1, position + window
        if position >= len(days) or start < 0 or end >= len(days):
            continue   # window falls outside the tracked period
        before, after = tracker.iloc[start], tracker.iloc[end]
        if pd.isna(before["implied_growth"]) or pd.isna(after["implied_growth"]):
            continue
        window_days = days[start + 1:end + 1]
        rows.append({
            "Earnings date": date,
            "Implied growth before": before["implied_growth"],
            "Implied growth after": after["implied_growth"],
            "Change (points)": (after["implied_growth"] - before["implied_growth"]) * 100,
            "Share price change": after["price"] / before["price"] - 1,
            "New annual accounts in window": "Yes" if any(d in switches for d in window_days) else "No",
        })
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    order = table["Change (points)"].abs().sort_values(ascending=False).index
    return table.loc[order].reset_index(drop=True)


def dates_in_period(dates, tracker):
    """Only the dates that fall inside the tracked period (and not in the future)."""
    if tracker.empty:
        return []
    first, last = tracker.index[0].date(), tracker.index[-1].date()
    return sorted(d for d in set(dates) if first <= d <= last)


# This part only runs when you run "python tracker.py" directly.
if __name__ == "__main__":
    import sys
    from data import get_company_data
    from wacc import calculate_wacc, get_risk_free_rate

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    data = get_company_data(ticker)
    rf, src = get_risk_free_rate(data["currency"])
    wacc = calculate_wacc(data, rf, src)["wacc"]
    history = load_history(ticker)
    tracker = build_tracker(history, wacc, 0.025)

    print(f"{ticker}: WACC fixed at {wacc:.2%}, {len(tracker)} trading days "
          f"from {tracker.index[0].date()} to {tracker.index[-1].date()}")
    print("\nAnnual reports and when they'd have been public:")
    print(history["reports"].assign(fcf=lambda r: r.fcf / 1e9, debt=lambda r: r.debt / 1e9,
                                    cash=lambda r: r.cash / 1e9, shares=lambda r: r.shares / 1e9)
          .round({"fcf": 3, "debt": 3, "cash": 3, "shares": 3}).to_string())
    print("\nImplied growth, start / lowest / highest / latest:")
    g = tracker["implied_growth"]
    print(f"  {g.iloc[0]:.2%} / {g.min():.2%} ({g.idxmin().date()}) / "
          f"{g.max():.2%} ({g.idxmax().date()}) / {g.iloc[-1]:.2%}")
    print("\nNew annual accounts started being used on:",
          [d.date().isoformat() for d in report_switch_dates(tracker)])
    dates = dates_in_period(history["yahoo_dates"] + load_saved_dates(ticker), tracker)
    print(f"\nEarnings dates in period: {len(dates)}")
    shocks = expectation_shocks(tracker, dates)
    if not shocks.empty:
        print("\nExpectations shocks (biggest first):")
        print(shocks.to_string(formatters={
            "Implied growth before": "{:.2%}".format, "Implied growth after": "{:.2%}".format,
            "Change (points)": "{:+.2f}".format, "Share price change": "{:+.1%}".format}))
