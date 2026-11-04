# Priced-In

**What growth is the market pricing in?** Type in a stock ticker and this web app works backwards from today's share price to the free cash flow growth the market is expecting.

## What it does

Most valuation models start with a guess about growth and work out what a share is worth. This app does the opposite: it starts with the price people are paying and asks *"how fast must this company grow to be worth that?"*. This is called a **reverse DCF** (reverse discounted cash flow).

It then compares that answer with how fast the company has actually grown, and says whether the market looks **optimistic**, **pessimistic** or **in line**.

Example (November 2026, default settings):

> To justify today's price, Unilever must grow free cash flow about 6.5% a year for 10 years. From 2022 to 2025 it grew 6.6% a year. The market is in line.

## How it works

1. **Get the data.** It downloads the share price, number of shares, debt, cash and the last few years of cash flow from Yahoo Finance.
2. **Put everything in one currency.** For example, Unilever's price is in pence and its accounts are in euros, so both are converted into pounds.
3. **Work out the price of the whole business** (enterprise value): market value of the shares + debt − cash.
4. **Measure the cash the business makes.** Free cash flow = cash from operations − spending on things like factories. The app uses the average of the available years.
5. **Work out the discount rate (WACC).** This is the yearly return the company's shareholders and lenders want, averaged by how much of each it uses:
   - *Shareholders* (cost of equity, using CAPM) want the government bond yield + beta × an equity risk premium (5%). Beta measures how much the share moves compared with the market, so riskier shares get a higher rate. The app uses an **adjusted beta** (⅔ × raw beta + ⅓ × 1.0), because betas tend to drift towards 1.0 over time.
   - *Lenders* (cost of debt): what the company would pay to borrow **today**, which is the bond yield plus a credit spread. The spread comes from the company's **interest coverage** (profit before interest and tax ÷ interest expense): the more times over it can pay its interest, the safer it is and the lower the spread. The lookup table is [Damodaran's synthetic rating table](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html) (January 2026), running from 0.40% for AAA to 19% for D. The result is reduced for tax, because interest lowers the tax bill. The interest the company actually paid is shown for comparison.
   - If any input is missing or looks unrealistic (e.g. a beta above 3), a sensible default is used and the app shows a warning.
6. **Value future cash.** It grows that cash flow for 10 years, then assumes slow steady growth forever. Each future amount is "discounted" at the WACC, because £1 in ten years is worth less than £1 today. A **higher** WACC makes future cash worth even less, so **more** growth is needed to justify the price; a lower WACC means less growth is needed.
7. **Solve backwards.** It tries growth rates until the total value of future cash equals the enterprise value. That growth rate is what's "priced in".
8. **Translate into business terms.** Free cash flow = revenue × FCF margin (the share of each sale that becomes free cash). The app shows:
   - the **revenue growth needed** if the margin stays at today's level;
   - the **year-10 margin needed** if sales keep growing at their historical rate;
   - a **grid** of revenue growth (0–15% a year) against year-10 margin, showing which combinations justify today's price, with a break-even line and the company's track record marked.

   For example: *"To justify today's price, Unilever needs either 3.6% revenue growth a year at today's 14.0% FCF margin, or margins rising to 22.2% by year 10 with sales growing at the historical rate (−1.1% a year)."*

You can change the assumptions in the sidebar:
- **Terminal growth:** growth forever after year 10 (default 2.5%).
- **Equity risk premium** (default 5%) and **risk-free rate** (the 10-year government bond yield: live for US dollars, a typed-in default for other currencies).
- **Override the WACC** with your own discount rate.

The app shows a full WACC breakdown, with every input and where it came from.

A sensitivity table shows how much the answer moves when these change.

## How to run it

You need Python 3 installed.

```bash
# 1. Download the project
git clone https://github.com/jamieglen07/priced-in.git
cd priced-in

# 2. Create a private toolbox for the project and switch it on
python3 -m venv .venv
source .venv/bin/activate

# 3. Install the packages it needs
pip install -r requirements.txt

# 4. Start the app - it opens in your browser at http://localhost:8501
streamlit run app.py
```

You can also run the parts without the web page:
- `python data.py` prints the raw data for Unilever and Apple.
- `python wacc.py` prints the WACC breakdown for Unilever, Apple and Tesla.
- `python reverse_dcf.py` prints the full workings.
- `python business_terms.py` prints the revenue and margin view, including the grid.

## Files

| File | What it does |
|---|---|
| `app.py` | The web page |
| `data.py` | Downloads the data and converts currencies |
| `wacc.py` | Works out each company's discount rate (WACC). Editable assumptions are at the top |
| `reverse_dcf.py` | The DCF maths and the solver that finds the implied growth |
| `business_terms.py` | Turns implied growth into revenue growth and FCF margin, including the grid |
| `requirements.txt` | The list of Python packages needed |

## Current limitations

- **Short history.** Yahoo Finance usually gives only 4 years of annual cash flow, so one unusual year can change the result a lot.
- **Today's exchange rate for every year.** Older years would really have been earned at different rates.
- **No answer for companies with negative free cash flow.** Growing a negative number can't justify a positive price, so the app shows a warning instead.
- **Simple model.** One growth rate for 10 years, one discount rate for every year, and no adjustment for things like share buybacks, long-term investments or one-off events.
- **Risk-free rates outside the US are typed in by hand.** The UK gilt yield in `wacc.py` (5.3%, October 2026) needs updating as it changes. Other currencies use a rough 4.5% guess.
- **The synthetic credit rating only looks at interest coverage.** Companies with little debt (like Tesla) get the top AAA rating even if real rating agencies, which also look at other risks, rate them lower. The spread table should be updated when Damodaran publishes a new one (each January).
- **The implied growth is very sensitive to the discount rate.** For Unilever, moving from a flat 9% to its 7.7% WACC changes implied growth from about 6.5% to about 3.6%.
- **"Today's margin" uses average free cash flow ÷ latest revenue**, so it matches the starting point of the main calculation. The latest single year's margin can differ, and the app shows it alongside.
- **Historical revenue growth can be distorted** by companies buying or selling businesses. If revenue jumps or falls sharply in one year, the app warns you and measures growth only from that year onwards. For example, Unilever's figures appear to exclude ice cream from 2023.
- **Data quality depends on Yahoo Finance.** For example, Yahoo's earnings dates for Unilever stop in 2017.
- **The verdict is a rule of thumb.** "In line" means within 2 percentage points of past growth. Small changes to the sliders can flip it.
- **Planned, not built yet:** tracking how implied growth changes around earnings announcements.

This is a learning project, not investment advice.
