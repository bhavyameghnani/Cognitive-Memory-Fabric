"""Instrument reference data and the single shared synthetic market path.

Every price in the operational layer comes from here. One shared path of monthly asset-class returns
(Dec-2025 -> Jun-2026) is applied to every client, mirroring the conversation generator, which tracked every
client against one shared fictional market path. Instrument = beta x class return + small seeded noise.
All issuers/fund houses are fictional.
"""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .config import BASE_DATE, MONTH_ENDS, SEED

# --- shared market path: monthly returns for Jan..Jun 2026 (index 0 = January) -------------------------
# Fitted so flow-adjusted portfolio totals track the totals quoted in conversations.csv (see audit).
EQUITY = [0.008, -0.052, -0.008, 0.055, 0.030, 0.012]
BOND = [0.003, 0.004, -0.003, 0.006, 0.004, 0.003]


def _model(name: str) -> list[float]:
    e, b = EQUITY, BOND
    f = {
        "us": lambda i: e[i],
        "world": lambda i: 0.97 * e[i],
        "intl": lambda i: 1.0 * e[i] - 0.001,
        "em": lambda i: 1.25 * e[i],
        "small": lambda i: 1.35 * e[i],
        "divq": lambda i: 0.75 * e[i] + 0.001,
        "reit": lambda i: 0.95 * e[i] + 0.3 * b[i],
        "infra": lambda i: 0.70 * e[i],
        "tech": lambda i: 1.40 * e[i],
        "plan529": lambda i: 0.45 * e[i] + 0.4 * b[i],
        "muni": lambda i: 0.85 * b[i],
        "tint": lambda i: b[i],
        "corp": lambda i: 0.9 * b[i] + 0.08 * e[i],
        "tshort": lambda i: 0.35 * b[i] + 0.002,
        "tips": lambda i: 0.9 * b[i] + 0.001,
        "ladder": lambda i: 0.10 * b[i] + 0.0030,
        "stable": lambda i: 0.0025,
        "cash": lambda i: 0.0,
        "gold": lambda i: [0.030, 0.018, -0.012, 0.010, 0.008, -0.004][i],
        "flat": lambda i: 0.0,
        "dcomp": lambda i: 0.0025,
    }[name]
    return [f(i) for i in range(6)]


# explicit idiosyncratic paths (single-name equities and crypto), taken from what the conversations say:
# Vantora: "dropped a few percent" (Jan 20), "down almost thirty percent since January" (Mar 19), "up eight percent
# from the low" (Jun 9). Northgate: vest on Feb 27; no price statements, so a plausible path.
CUSTOM = {
    "vantora": [-0.080, -0.130, -0.120, 0.030, 0.040, 0.040],
    "northgate": [0.020, 0.060, -0.090, 0.040, 0.030, 0.020],
    "empco": [0.015, 0.010, -0.080, 0.050, 0.030, 0.010],
    "crypto": [0.060, -0.200, -0.150, 0.120, 0.040, 0.020],
}

NOISE_SD = {"Equity Index Fund": 0.002, "Equity Mutual Fund": 0.003, "Equity ETF": 0.003,
            "Debt Mutual Fund": 0.0008, "Government Bond": 0.0006}

# key: (name, instrument_type, asset_class, sector, issuer, liquidity_category, model, beta_scale, nav_jan31)
# beta_scale multiplies the model return (1.0 = class return).
INSTRUMENTS: dict[str, tuple] = {
    # equity funds / ETFs
    "geq":   ("Global Equity Index Fund", "Equity Index Fund", "Equity", "Diversified", "Aldgate Index Funds", "liquid_daily", "world", 1.0, 148.20),
    "usli":  ("U.S. Large Cap Index Fund", "Equity Index Fund", "Equity", "Diversified", "Aldgate Index Funds", "liquid_daily", "us", 1.0, 212.40),
    "tmi":   ("U.S. Total Market Index Fund", "Equity Index Fund", "Equity", "Diversified", "Meridian Asset Management", "liquid_daily", "us", 1.0, 98.75),
    "intl":  ("International Developed Markets Index Fund", "Equity Index Fund", "Equity", "Diversified", "Aldgate Index Funds", "liquid_daily", "intl", 1.0, 31.60),
    "em":    ("Emerging Markets Index Fund", "Equity Index Fund", "Equity", "Diversified", "Aldgate Index Funds", "liquid_daily", "em", 1.0, 24.80),
    "smg":   ("U.S. Small-Cap Growth Fund", "Equity Mutual Fund", "Equity", "Diversified", "Harbourview Funds", "liquid_daily", "small", 1.0, 41.30),
    "divq":  ("Dividend-Focused Equity Fund", "Equity Mutual Fund", "Equity", "Diversified", "Harbourview Funds", "liquid_daily", "divq", 1.0, 56.90),
    "reit":  ("Real Estate Securities Fund", "Equity Mutual Fund", "Equity", "Real Estate", "Brightwater Funds", "semi_liquid", "reit", 1.0, 33.10),
    "infra": ("Global Infrastructure Fund", "Equity Mutual Fund", "Equity", "Utilities & Infrastructure", "Harbourview Funds", "liquid_daily", "infra", 1.0, 18.40),
    "tech":  ("Technology Sector ETF", "Equity ETF", "Equity", "Technology", "Aldgate Index Funds", "liquid_daily", "tech", 1.0, 87.50),
    "p529":  ("Age-Based 529 Portfolio (2027)", "College Savings Portfolio", "Multi-Asset", "Diversified", "Evergreen 529 Plan", "liquid_daily", "plan529", 1.0, 14.20),
    # single names (fictional)
    "northgate": ("Northgate Freight Corp. common stock", "Individual Equity", "Equity", "Industrials", "Northgate Freight Corp.", "liquid_daily", "northgate", 1.0, 62.00),
    "vantora":   ("Vantora Systems Inc. common stock", "Individual Equity", "Equity", "Technology", "Vantora Systems Inc.", "liquid_daily", "vantora", 1.0, 48.00),
    "empco":     ("Employer common stock (vested RSUs)", "Individual Equity", "Equity", "Technology", "Employer of CLIENT-003 (name not stated in conversations)", "liquid_daily", "empco", 1.0, 74.00),
    # debt funds / bonds
    "muni":  ("Municipal Bond Fund", "Debt Mutual Fund", "Fixed Income", "Municipal", "Meridian Asset Management", "liquid_daily", "muni", 1.0, 10.85),
    "inttr": ("Intermediate Treasury Fund", "Debt Mutual Fund", "Fixed Income", "Government", "Aldgate Index Funds", "liquid_daily", "tint", 1.0, 11.40),
    "corp":  ("Investment-Grade Corporate Bond Fund", "Debt Mutual Fund", "Fixed Income", "Corporate", "Meridian Asset Management", "liquid_daily", "corp", 1.0, 9.95),
    "shtr":  ("Short-Term Treasury Fund", "Debt Mutual Fund", "Fixed Income", "Government", "Aldgate Index Funds", "liquid_daily", "tshort", 1.0, 10.20),
    "intbd": ("Intermediate Bond Fund", "Debt Mutual Fund", "Fixed Income", "Diversified Bonds", "Meridian Asset Management", "liquid_daily", "tint", 1.0, 10.60),
    "bndix": ("Total Bond Index Fund", "Debt Mutual Fund", "Fixed Income", "Diversified Bonds", "Aldgate Index Funds", "liquid_daily", "tint", 1.0, 10.10),
    "shbd":  ("Short-Term Bond Fund", "Debt Mutual Fund", "Fixed Income", "Diversified Bonds", "Meridian Asset Management", "liquid_daily", "tshort", 1.0, 10.05),
    "stable": ("Stable Value Fund", "Stable Value Fund", "Fixed Income", "Stable Value", "Cedar Hollow Insurance Co.", "liquid_daily", "stable", 1.0, 1.00),
    "tips":  ("Treasury Inflation-Protected Securities (TIPS)", "Government Bond", "Fixed Income", "Government", "U.S. Treasury", "liquid_daily", "tips", 1.0, 101.20),
    "tb6":   ("6-Month Treasury Bill", "Government Bond", "Fixed Income", "Government", "U.S. Treasury", "liquid_daily", "ladder", 1.0, 98.60),
    "tn1":   ("1-Year Treasury Note", "Government Bond", "Fixed Income", "Government", "U.S. Treasury", "liquid_daily", "ladder", 1.0, 99.10),
    "tn2":   ("2-Year Treasury Note", "Government Bond", "Fixed Income", "Government", "U.S. Treasury", "liquid_daily", "ladder", 1.0, 99.40),
    "cd2":   ("2-Year Certificate of Deposit", "Fixed Deposit", "Fixed Income", "Bank Deposit", "Cedar Hollow Bank", "semi_liquid", "ladder", 1.0, 1.00),
    # cash
    "mm":    ("Money Market Fund", "Money Market Fund", "Cash & Equivalents", "Cash", "Aldgate Index Funds", "cash_equivalent", "cash", 1.0, 1.00),
    "hysa":  ("High-Yield Savings Account", "Cash", "Cash & Equivalents", "Cash", "Cedar Hollow Bank", "cash_equivalent", "cash", 1.0, 1.00),
    "cash":  ("Cash Account", "Cash", "Cash & Equivalents", "Cash", "Cedar Hollow Bank", "cash_equivalent", "cash", 1.0, 1.00),
    # other
    "gold":  ("Gold Bullion ETF", "Gold ETF", "Commodities", "Gold", "Sterling Bullion Trust", "liquid_daily", "gold", 1.0, 241.00),
    "crypto": ("Digital Asset (spot crypto holding)", "Crypto Asset", "Alternatives", "Digital Assets", "n/a (decentralised asset)", "liquid_daily", "crypto", 1.0, 2925.00),
    "dcomp": ("Northgate Deferred Compensation Plan balance", "Deferred Compensation", "Other Illiquid", "Deferred Compensation", "Northgate Freight Corp.", "illiquid", "dcomp", 1.0, 1.00),
    "founders": ("Moreau Labs founder shares (private)", "Private Company Shares", "Other Illiquid", "Technology", "Moreau Labs", "illiquid", "flat", 1.0, 1.90),
    "holloway": ("Holloway Packaging (private company interest)", "Private Company Shares", "Other Illiquid", "Industrials", "Holloway Packaging", "illiquid", "flat", 1.0, 21.00),
}

LIQUIDITY_CATEGORIES = {
    "cash_equivalent": ("Cash and cash equivalents available same day (cash, savings, money-market).", 1),
    "liquid_daily": ("Daily-dealing funds, ETFs, listed shares and Treasuries; settles in about T+1 to T+3.", 2),
    "semi_liquid": ("Tradable but with gates, redemption limits or early-withdrawal penalties (real estate fund, CDs).", 3),
    "illiquid": ("Not freely saleable: private company shares, employer deferred compensation.", 4),
}

ASSET_CLASSES = {
    "Equity": "Listed equities and equity funds (including listed real-estate equity funds).",
    "Fixed Income": "Bonds, bond funds, Treasuries, stable value and bank term deposits.",
    "Cash & Equivalents": "Cash, savings and money-market funds.",
    "Multi-Asset": "Blended portfolios such as a 529 age-based option.",
    "Commodities": "Gold and other commodity ETFs.",
    "Alternatives": "Crypto and other alternatives.",
    "Other Illiquid": "Private company shares and deferred compensation (never part of the investable total).",
}


def q4(x: float | Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def q2(x: float | Decimal) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _noise_sd(itype: str) -> float:
    return NOISE_SD.get(itype, 0.0)


_SERIES: dict[str, list[Decimal]] = {}


def series(ikey: str) -> list[Decimal]:
    """Prices at [2025-12-31, 2026-01-31, ..., 2026-06-30] (7 points, 4 dp)."""
    if ikey in _SERIES:
        return _SERIES[ikey]
    name, itype, aclass, sector, issuer, liq, model, beta, nav = INSTRUMENTS[ikey]
    rets = list(CUSTOM[ikey]) if ikey in CUSTOM else [beta * r for r in _model(model)]
    sd = _noise_sd(itype)
    if sd and ikey not in CUSTOM:
        rng = random.Random(f"{SEED}-{ikey}")
        rets = [r + rng.gauss(0.0, sd) for r in rets]
    p = [0.0] * 7
    p[1] = nav
    p[0] = nav / (1.0 + rets[0])
    for m in range(1, 6):
        p[m + 1] = p[m] * (1.0 + rets[m])
    fixed = model in ("cash", "flat")
    out = [q4(nav if fixed else v) for v in p]
    _SERIES[ikey] = out
    return out


def price_on(ikey: str, d: date) -> Decimal:
    """Linear interpolation (by day) between month-end prices. Exact at month-ends."""
    pts = [BASE_DATE] + MONTH_ENDS
    s = series(ikey)
    if d <= pts[0]:
        return s[0]
    for i in range(1, len(pts)):
        if d <= pts[i]:
            span = (pts[i] - pts[i - 1]).days
            w = (d - pts[i - 1]).days / span
            return q4(float(s[i - 1]) + w * float(s[i] - s[i - 1]))
    return s[-1]
