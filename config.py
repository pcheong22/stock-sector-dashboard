"""
Configuration: stock universe, sector mapping, and benchmark ETFs.

This is intentionally a starter universe, not an attempt at full market
coverage. Expand SECTOR_TICKERS as needed -- the rest of the pipeline
doesn't care how many tickers are in here.
"""

# Benchmark for "relative return vs market"
MARKET_BENCHMARK = "SPY"

# Sector -> representative SPDR sector ETF (used for "relative return vs sector")
SECTOR_ETFS = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

# Starter universe: ticker -> sector.
# Picked as a liquid, recognizable cross-section of each sector so Phase 1
# has something real to look at. Add/remove tickers freely -- nothing else
# in the codebase hardcodes this list.
SECTOR_TICKERS = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "CSCO", "QCOM"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "AXP", "C", "SPGI"],
    "Health Care": ["UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "BKNG", "TJX", "CMG"],
    "Consumer Staples": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "CL", "MDLZ", "KMB"],
    "Energy": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "WMB"],
    "Industrials": ["CAT", "RTX", "HON", "UNP", "BA", "GE", "LMT", "DE", "UPS", "ETN"],
    "Materials": ["LIN", "SHW", "FCX", "NEM", "APD", "ECL", "NUE", "DOW", "PPG", "VMC"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "ED", "WEC"],
    "Real Estate": ["PLD", "AMT", "EQIX", "SPG", "PSA", "O", "WELL", "DLR", "AVB", "EQR"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "TMUS", "CMCSA", "VZ", "T", "EA", "WBD"],
}


def ticker_sector_map():
    """Flat dict: ticker -> sector."""
    mapping = {}
    for sector, tickers in SECTOR_TICKERS.items():
        for t in tickers:
            mapping[t] = sector
    return mapping


def all_tickers():
    return sorted(ticker_sector_map().keys())


def all_fetch_symbols():
    """Everything we need to pull from the data source: stocks + benchmark + sector ETFs."""
    syms = set(all_tickers())
    syms.add(MARKET_BENCHMARK)
    syms.update(SECTOR_ETFS.values())
    return sorted(syms)
