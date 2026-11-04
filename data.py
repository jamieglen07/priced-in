"""
data.py - fetches the raw numbers the reverse DCF needs, using yfinance,
and puts them all into ONE currency so they can be compared.

Run it on its own to see the data for a couple of test companies:
    python data.py
"""

import pandas as pd
import yfinance as yf


# Some exchanges quote prices in a "small" unit, like pence instead of pounds.
# Each entry says: small unit -> (main currency, how many small units make one).
MINOR_CURRENCIES = {
    "GBp": ("GBP", 100),  # UK pence -> pounds
    "GBX": ("GBP", 100),  # another code Yahoo uses for pence
    "ZAc": ("ZAR", 100),  # South African cents -> rand
    "ZAC": ("ZAR", 100),
    "ILA": ("ILS", 100),  # Israeli agorot -> shekels
}


def to_main_currency(price, currency):
    """Turn e.g. 4970.5 GBp into 49.705 GBP. Main currencies pass through unchanged."""
    if currency in MINOR_CURRENCIES:
        main_currency, divisor = MINOR_CURRENCIES[currency]
        return price / divisor, main_currency
    return price, currency.upper()


def get_exchange_rate(from_currency, to_currency):
    """
    How many units of `to_currency` you get for 1 unit of `from_currency`.
    Uses Yahoo's currency tickers, e.g. "EURGBP=X" = pounds per euro.
    """
    if from_currency == to_currency:
        return 1.0

    rate = yf.Ticker(f"{from_currency}{to_currency}=X").fast_info.get("lastPrice")
    if rate:
        return rate

    # Fallback: if Yahoo only has the pair the other way round, flip it.
    reverse = yf.Ticker(f"{to_currency}{from_currency}=X").fast_info.get("lastPrice")
    if reverse:
        return 1 / reverse

    raise ValueError(f"Couldn't find an exchange rate from {from_currency} to {to_currency}")


def latest_value(table, row_names):
    """
    Get the most recent number from a financial table, trying each row name
    in turn (Yahoo labels differ between companies). Blank counts as 0.
    """
    for name in row_names:
        if name in table.index:
            value = table.loc[name].iloc[0]
            if pd.notna(value):
                return value
    return 0.0


def row_by_year(table, row_name):
    """
    One row of a financial table as {year: value}, newest year first,
    with blank years removed. Empty if Yahoo doesn't have that row.
    """
    if table is None or table.empty or row_name not in table.index:
        return pd.Series(dtype=float)
    row = table.loc[row_name].dropna()
    row.index = row.index.year
    return row.sort_index(ascending=False)


def get_company_data(ticker):
    """
    Download the key numbers for one stock and return them in a dictionary
    (a labelled collection of values, like a mini form with named boxes).
    Every money amount is converted into the stock's trading currency.
    """
    stock = yf.Ticker(ticker)
    try:
        info = stock.info  # Yahoo's summary page for the company
    except Exception:
        info = {}

    # If Yahoo has no price, the ticker almost certainly doesn't exist.
    if not (info.get("currentPrice") or info.get("regularMarketPrice")) or not info.get("currency"):
        raise ValueError(f"Couldn't find a stock called '{ticker}'. Check the spelling - "
                         "UK shares need '.L' on the end, e.g. ULVR.L.")

    # --- 1. Currencies -----------------------------------------------------
    quote_currency = info.get("currency")  # what the price is quoted in, e.g. GBp
    reporting_currency = (info.get("financialCurrency") or quote_currency).upper()

    # --- 2. Share price, shares and market value --------------------------
    raw_price = info.get("currentPrice") or info.get("regularMarketPrice")
    price, currency = to_main_currency(raw_price, quote_currency)
    shares = info.get("sharesOutstanding")
    market_value = price * shares

    # --- 3. Free cash flow for each year ----------------------------------
    # The cash flow statement is a table: one row per item, one column per year.
    cashflow = stock.cashflow
    needed = ["Operating Cash Flow", "Capital Expenditure"]
    if cashflow is None or cashflow.empty or not all(r in cashflow.index for r in needed):
        raise ValueError(f"Yahoo has no cash flow data for '{ticker}'. It may be a fund "
                         "or index rather than a company.")
    operating_cf = cashflow.loc["Operating Cash Flow"]
    capex = cashflow.loc["Capital Expenditure"]

    # Yahoo stores capital expenditure as a NEGATIVE number (cash going out),
    # so "operating cash flow minus capex" means adding it here.
    free_cf = operating_cf + capex

    fcf_table = pd.DataFrame({
        "Operating cash flow": operating_cf,
        "Capital expenditure": capex,
        "Free cash flow": free_cf,
    })
    fcf_table.index = fcf_table.index.year  # show just the year, e.g. 2025
    fcf_table = fcf_table.sort_index()      # oldest year first

    # Note any years Yahoo left blank, then drop them.
    missing_years = fcf_table[fcf_table.isna().any(axis=1)].index.tolist()
    fcf_table = fcf_table.dropna()

    # Convert the accounts into the trading currency (e.g. euros -> pounds).
    # Note: this uses TODAY's rate for every year, which keeps things simple.
    fx_rate = get_exchange_rate(reporting_currency, currency)
    fcf_table = fcf_table * fx_rate

    # --- 4. Summary numbers -----------------------------------------------
    latest_fcf = fcf_table["Free cash flow"].iloc[-1]
    average_fcf = fcf_table["Free cash flow"].mean()

    # --- 5. Debt, cash and enterprise value -------------------------------
    # The balance sheet is a snapshot of what the company owns and owes.
    # We use the most recent one (the first column).
    balance_sheet = stock.balance_sheet
    balance_sheet_date = balance_sheet.columns[0].date()
    total_debt = latest_value(balance_sheet, ["Total Debt"]) * fx_rate
    cash = latest_value(balance_sheet, [
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Cash Equivalents",
    ]) * fx_rate

    # Enterprise value = the price of buying the WHOLE business:
    # the shares, plus taking on its debts, minus the cash you'd get with it.
    enterprise_value = market_value + total_debt - cash

    # --- 6. Inputs for the discount rate (WACC) ---------------------------
    # These stay in the accounts' own currency: they're only used to work out
    # percentages (like interest / debt), where the currency cancels out.
    income = stock.income_stmt
    interest_by_year = row_by_year(income, "Interest Expense")
    tax_by_year = row_by_year(income, "Tax Provision")
    pretax_by_year = row_by_year(income, "Pretax Income")
    debt_by_year = row_by_year(balance_sheet, "Total Debt")

    # --- 7. Past earnings announcement dates ------------------------------
    earnings = stock.get_earnings_dates(limit=60)
    past_earnings = []
    if earnings is not None and not earnings.empty:
        now = pd.Timestamp.now(tz=earnings.index.tz)
        past = earnings[earnings.index < now]
        past_earnings = sorted(past.index.date, reverse=True)  # newest first

    return {
        "ticker": ticker,
        "name": info.get("longName", ticker),
        "currency": currency,                    # everything below is in this
        "quote_currency": quote_currency,
        "reporting_currency": reporting_currency,
        "fx_rate": fx_rate,
        "price": price,
        "shares_outstanding": shares,
        "market_value": market_value,
        "balance_sheet_date": balance_sheet_date,
        "total_debt": total_debt,
        "cash": cash,
        "enterprise_value": enterprise_value,
        "beta": info.get("beta"),
        "interest_by_year": interest_by_year,
        "debt_by_year": debt_by_year,
        "tax_by_year": tax_by_year,
        "pretax_by_year": pretax_by_year,
        "fcf_table": fcf_table,
        "missing_fcf_years": missing_years,
        "latest_fcf": latest_fcf,
        "average_fcf": average_fcf,
        "latest_fcf_yield": latest_fcf / market_value,
        "average_fcf_yield": average_fcf / market_value,
        "past_earnings_dates": past_earnings,
    }


def print_company_data(data):
    """Print the results in a tidy, readable layout."""
    billions = 1_000_000_000
    cur = data["currency"]

    print("=" * 60)
    print(f"  {data['name']} ({data['ticker']})")
    print("=" * 60)

    print("\nCurrencies:")
    print(f"  Price quoted in:      {data['quote_currency']}")
    print(f"  Accounts reported in: {data['reporting_currency']}")
    if data["fx_rate"] == 1.0:
        print(f"  No conversion needed - everything is in {cur}")
    else:
        print(f"  Converted at:         1 {data['reporting_currency']} = {data['fx_rate']:.4f} {cur}")

    print(f"\nShare price:         {data['price']:,.2f} {cur}")
    print(f"Shares outstanding:  {data['shares_outstanding'] / billions:,.3f} billion")
    print(f"Market value:        {data['market_value'] / billions:,.1f} billion {cur}")
    print(f"\nBalance sheet at {data['balance_sheet_date']} (billions of {cur}):")
    print(f"  Market value          {data['market_value'] / billions:>10,.1f}")
    print(f"  + Total debt          {data['total_debt'] / billions:>10,.1f}")
    print(f"  - Cash & short-term   {data['cash'] / billions:>10,.1f}")
    print(f"  = Enterprise value    {data['enterprise_value'] / billions:>10,.1f}")

    print(f"\nFree cash flow by year (billions of {cur}):")
    print((data["fcf_table"] / billions).round(2).to_string())
    if data["missing_fcf_years"]:
        print(f"  (No data from Yahoo for: {data['missing_fcf_years']})")

    years = len(data["fcf_table"])
    print(f"\nLatest free cash flow:        {data['latest_fcf'] / billions:,.2f} billion {cur}")
    print(f"{f"Average over {years} years:":<30}{data['average_fcf'] / billions:,.2f} billion {cur}")
    print(f"FCF yield (latest year):      {data['latest_fcf_yield']:.2%}")
    print(f"{f"FCF yield ({years}-year average):":<30}{data['average_fcf_yield']:.2%}")

    dates = data["past_earnings_dates"]
    newest = dates[0] if dates else "none"
    print(f"\nPast earnings dates: {len(dates)} found, newest is {newest}")
    print()


# This part only runs when you run "python data.py" directly.
if __name__ == "__main__":
    for ticker in ["ULVR.L", "AAPL"]:
        print_company_data(get_company_data(ticker))
