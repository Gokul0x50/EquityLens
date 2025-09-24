from flask import Flask, render_template, request, send_file
import yfinance as yf
import pandas as pd
import io
import requests
import feedparser

app = Flask(__name__)


def format_inr(value):
    """Format large numbers into readable INR string (Crores)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        # convert to crores (1 Crore = 1e7)
        return f"₹{value/1e7:,.2f} Cr"
    except Exception:
        try:
            return f"₹{float(value):,.2f}"
        except Exception:
            return str(value)


def format_price(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        return f"₹{value:,.2f}"
    except Exception:
        return str(value)


def get_google_news(ticker):
    """Fetch latest news from Google News RSS for the ticker (limited)."""
    try:
        url = f"https://news.google.com/rss/search?q={ticker}+stock+india"
        r = requests.get(url, timeout=6)
        feed = feedparser.parse(r.text)
        items = []
        for entry in feed.entries[:10]:
            items.append({
                "title": entry.title,
                "link": entry.link,
                "published": entry.get("published", "")
            })
        return items
    except Exception:
        return []


def get_technical_indicators(ticker):
    """
    Compute simplified technical indicators from 6 months daily history.
    Returns dict of indicator_name -> readable value (value + signal).
    """
    try:
        stock = yf.Ticker(f"{ticker}.NS")
        hist = stock.history(period="6mo", interval="1d")
        if hist.empty or len(hist) < 20:
            return {}

        # RSI (14)
        delta = hist["Close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / (avg_loss.replace(0, 1e-9))
        rsi = 100 - (100 / (1 + rs))
        last_rsi = rsi.dropna().iloc[-1] if not rsi.dropna().empty else None

        # MACD
        ema12 = hist["Close"].ewm(span=12, adjust=False).mean()
        ema26 = hist["Close"].ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        last_macd = macd.iloc[-1] if not macd.empty else None
        last_signal = signal.iloc[-1] if not signal.empty else None

        # Moving averages
        ma20 = hist["Close"].rolling(window=20).mean().iloc[-1] if len(hist) >= 20 else None
        ma50 = hist["Close"].rolling(window=50).mean().iloc[-1] if len(hist) >= 50 else None
        last_price = hist["Close"].iloc[-1]

        # Volume trend
        vol_mean = hist["Volume"].rolling(window=20).mean().iloc[-1] if len(hist) >= 20 else None
        last_vol = hist["Volume"].iloc[-1]
        vol_pct = None
        if vol_mean and vol_mean > 0:
            vol_pct = (last_vol - vol_mean) / vol_mean * 100

        indicators = {}

        if last_rsi is not None:
            sig = "Bullish" if last_rsi < 30 else "Bearish" if last_rsi > 70 else "Neutral"
            indicators["RSI"] = f"{round(float(last_rsi),2)} ({sig})"

        if last_macd is not None and last_signal is not None:
            sig = "Bullish" if last_macd > last_signal else "Bearish"
            indicators["MACD"] = f"{round(float(last_macd),2)} / {round(float(last_signal),2)} ({sig})"

        if ma20 is not None and ma50 is not None:
            sig = "Bullish" if ma20 > ma50 else "Bearish"
            indicators["MA20 vs MA50"] = f"{round(float(ma20),2)} / {round(float(ma50),2)} ({sig})"

        if ma50 is not None:
            sig = "Bullish" if last_price > ma50 else "Bearish"
            indicators["Price vs MA50"] = f"{round(float(last_price),2)} vs {round(float(ma50),2)} ({sig})"

        if vol_pct is not None:
            sig = "Bullish" if vol_pct > 10 else "Neutral" if abs(vol_pct) <= 10 else "Bearish"
            indicators["Volume Trend"] = f"{round(float(vol_pct),2)}% ({sig})"

        return indicators
    except Exception:
        return {}


# Static sector averages (placeholder)
SECTOR_AVERAGES = {
    "Energy": {"PE": 18, "PB": 2.1, "DividendYield": "2.3%"},
    "Technology": {"PE": 25, "PB": 5.2, "DividendYield": "1.1%"},
    "Financial Services": {"PE": 20, "PB": 2.5, "DividendYield": "1.8%"},
    "Healthcare": {"PE": 22, "PB": 3.0, "DividendYield": "1.5%"},
    "Industrials": {"PE": 19, "PB": 2.2, "DividendYield": "1.4%"},
}


@app.route("/", methods=["GET", "POST"])
def home():
    # initialize outputs so template never gets undefined variables
    stock_info = None
    financials = pd.DataFrame()
    key_metrics = {}
    ratios = {}
    ticker = None
    news = []
    technicals = {}
    sector_data = None

    if request.method == "POST":
        ticker = request.form.get("ticker", "").strip().upper()
        if not ticker:
            return render_template("index.html")

        yf_t = yf.Ticker(f"{ticker}.NS")

        try:
            info = yf_t.info or {}
        except Exception:
            info = {}

        # quarterly financials
        try:
            financials = yf_t.quarterly_financials.T.reset_index()
        except Exception:
            financials = pd.DataFrame()

        # key financials (formatted)
        revenue = info.get("totalRevenue")
        net_income = info.get("netIncomeToCommon") or info.get("netIncome")
        ebitda = info.get("ebitda")
        eps = info.get("trailingEps")
        operating_income = info.get("operatingIncome")

        key_metrics = {
            "Revenue": format_inr(revenue),
            "Net Income": format_inr(net_income),
            "EBITDA": format_inr(ebitda),
            "EPS": format_price(eps) if eps is not None else "-",
            "Operating Income": format_inr(operating_income),
        }

        # ratios
        ratios = {
            "P/E Ratio": round(info.get("trailingPE", 0), 2) if info.get("trailingPE") else "N/A",
            "P/B Ratio": round(info.get("priceToBook", 0), 2) if info.get("priceToBook") else "N/A",
            "ROE": f"{round(info.get('returnOnEquity', 0) * 100, 2)}%" if info.get("returnOnEquity") else "N/A",
            "ROA": f"{round(info.get('returnOnAssets', 0) * 100, 2)}%" if info.get("returnOnAssets") else "N/A",
            "Debt to Equity": round(info.get("debtToEquity", 0), 2) if info.get("debtToEquity") else "N/A",
        }

        # clean BSE code (remove suffix)
        raw_sym = info.get("symbol", ticker)
        try:
            if isinstance(raw_sym, str) and "." in raw_sym:
                bse_code = raw_sym.split(".")[0].strip()
            else:
                bse_code = str(raw_sym).strip()
        except Exception:
            bse_code = ticker

        stock_info = {
            "longName": info.get("longName", ticker),
            "sector": info.get("sector", "-"),
            "industry": info.get("industry", "-"),
            "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice") or None,
            "currentPriceFormatted": format_price(info.get("currentPrice") or info.get("regularMarketPrice")),
            "marketCap": info.get("marketCap", None),
            "marketCapFormatted": format_inr(info.get("marketCap")),
            "peRatio": info.get("trailingPE", "-"),
            "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh", "-"),
            "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow", "-"),
            "beta": info.get("beta", "-"),
            "website": info.get("website", "-"),
            "bseCode": bse_code,
            "nseCode": ticker,
            "summary": info.get("longBusinessSummary", "No summary available."),
        }

        # news
        news = get_google_news(ticker)

        # technicals
        technicals = get_technical_indicators(ticker)

        # sector snapshot
        sector = stock_info.get("sector")
        if sector and sector in SECTOR_AVERAGES:
            avg = SECTOR_AVERAGES[sector]
            sector_data = {
                "sector": sector,
                "industry": stock_info.get("industry", "-"),
                "stockPE": stock_info.get("peRatio", "N/A"),
                "sectorPE": avg.get("PE", "N/A"),
                "sectorPB": avg.get("PB", "N/A"),
                "sectorDivYield": avg.get("DividendYield", "N/A")
            }

    return render_template(
        "index.html",
        stock_info=stock_info,
        ticker=ticker,
        key_metrics=key_metrics,
        ratios=ratios,
        financials=financials,
        news=news,
        technicals=technicals,
        sector_data=sector_data
    )


@app.route("/download/<ticker>")
def download(ticker):
    yf_t = yf.Ticker(f"{ticker}.NS")
    try:
        financials = yf_t.quarterly_financials.T.reset_index()
    except Exception:
        financials = pd.DataFrame()

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine="xlsxwriter")
    financials.to_excel(writer, index=False, sheet_name="Quarterly Financials")
    writer.close()
    output.seek(0)
    return send_file(output, download_name=f"{ticker}_financials.xlsx", as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
