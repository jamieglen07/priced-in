# Priced-In: a "what's priced in" reverse DCF web app

## About the user
- The user is a **complete beginner at coding**.
- Explain every step simply, in plain English, as you go. Avoid jargon. When a technical term can't be avoided, define it in one short sentence.
- Before running a command or writing code, say what it does and why it's needed.
- Make small changes one at a time, so each step is easy to follow.

## What the app does
1. The user enters a stock ticker (e.g. `AAPL`).
2. The app gets the current share price and financial data for that company.
3. It runs a **reverse DCF** (discounted cash flow). A normal DCF starts with a growth guess and works out what the stock is worth. A reverse DCF goes the other way: it starts with today's share price and solves for the **free cash flow growth rate the market is implying**. That is, how fast the company must grow to justify the price.
4. **Later:** track how that implied growth rate changes around **earnings dates**, to see how the market's expectations move when results come out.

## Tech stack
- **Python** (3.15 on this machine)
- **yfinance**: downloads stock prices and financial statements from Yahoo Finance
- **pandas**: handles tables of data
- **scipy**: provides the equation solver used to find the implied growth rate
- **plotly**: makes interactive charts
- **streamlit**: turns a Python script into a web app in the browser

## Project setup
- Dependencies live in a virtual environment in the `.venv` folder.
- Activate it: `source .venv/bin/activate`
- Install packages: `pip install -r requirements.txt`
- Run the app: `streamlit run app.py` (opens at http://localhost:8501)

## Files
- `data.py`: fetches price, shares, free cash flow, debt, cash and earnings dates; converts everything to the trading currency (pence -> pounds, accounts -> trading currency at today's FX rate); computes enterprise value.
- `reverse_dcf.py`: 10-year DCF + terminal value; solves (scipy `brentq`) for the growth rate that makes DCF value = enterprise value; prints workings, sensitivity table and a plain-English summary.

## Known data quirks
- Yahoo's earnings dates for ULVR.L stop at 2017 (Apple's are up to date).
- Yahoo usually gives only 4 years of annual cash flow.

## Status
- Streamlit web app (`app.py`) working: ticker box, discount + terminal growth sliders, summary, key numbers, chart, sensitivity table. Next idea: track implied growth around earnings dates.
