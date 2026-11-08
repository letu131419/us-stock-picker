#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓 Yahoo Finance 美股数据 → data/stocks.json（供前端美股选股助手读取）。

运行环境：GitHub Actions ubuntu-latest（境外 runner，可正常拿 Yahoo crumb 过反爬）。
数据：行情 + 财务指标 + 公司概况 + 近90根日K，一次性写入 data/stocks.json。
"""
import json
import os
import time

import requests

# 股票池：覆盖主要行业的知名美股（可自行增删）
SYMBOLS = [
    # 科技
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "NFLX", "AMD",
    "INTC", "CRM", "ORCL", "ADBE", "AVGO", "TSLA",
    # 消费
    "WMT", "PG", "KO", "MCD", "NKE", "SBUX", "HD", "COST",
    # 金融
    "JPM", "BAC", "V", "MA",
    # 医药
    "JNJ", "PFE", "UNH", "LLY", "MRK",
    # 工业/能源
    "CAT", "BA", "XOM", "CVX",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

KLINES = 90  # 每只股票保留最近 N 根日K


def make_session():
    """建立 session 并拿 crumb（Yahoo 反爬：先拿 cookie 再拿 crumb）。"""
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://fc.yahoo.com", timeout=15)
    except Exception:
        pass
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).text.strip()
    return s, crumb


def _raw(d, key):
    """quoteSummary 里字段是 {raw:..., fmt:...} 结构，取 raw 数值。"""
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    if isinstance(v, dict):
        return v.get("raw")
    return v


def pct100(x):
    """把 0~1 的小数比例转成百分比数值（0.276 -> 27.6）。"""
    if x is None:
        return None
    try:
        return round(float(x) * 100, 2)
    except (TypeError, ValueError):
        return None


def fetch(session, crumb, symbol):
    rec = {"symbol": symbol}

    # 1) chart：行情 + K线
    try:
        r = session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1y", "interval": "1d"},
            timeout=20,
        )
        c = r.json()["chart"]["result"][0]
        meta = c.get("meta", {})
        rec["name"] = meta.get("longName") or meta.get("shortName") or symbol
        rec["price"] = meta.get("regularMarketPrice")
        rec["chgPct"] = meta.get("regularMarketChangePercent")
        rec["high52"] = meta.get("fiftyTwoWeekHigh")
        rec["low52"] = meta.get("fiftyTwoWeekLow")
        q = c["indicators"]["quote"][0]
        ts = c.get("timestamp", [])
        n = len(q.get("close") or [])
        start = max(0, n - KLINES)
        rec["klines"] = [
            {
                "t": ts[i] if i < len(ts) else None,
                "o": q["open"][i] if q.get("open") else None,
                "h": q["high"][i] if q.get("high") else None,
                "l": q["low"][i] if q.get("low") else None,
                "c": q["close"][i] if q.get("close") else None,
                "v": q["volume"][i] if q.get("volume") else None,
            }
            for i in range(start, n)
        ]
    except Exception as e:
        rec["chart_err"] = str(e)[:120]

    # 2) quoteSummary：财务指标 + 概况
    try:
        r = session.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            params={
                "modules": "summaryDetail,defaultKeyStatistics,financialData,assetProfile",
                "crumb": crumb,
            },
            timeout=20,
        )
        res = r.json()["quoteSummary"]["result"][0]
        sd = res.get("summaryDetail") or {}
        ks = res.get("defaultKeyStatistics") or {}
        fd = res.get("financialData") or {}
        ap = res.get("assetProfile") or {}

        rec["pe"] = _raw(sd, "trailingPE")
        rec["forwardPE"] = _raw(sd, "forwardPE")
        rec["pb"] = _raw(ks, "priceToBook")
        rec["peg"] = _raw(ks, "pegRatio")
        rec["marketCap"] = _raw(sd, "marketCap")
        rec["beta"] = _raw(sd, "beta")
        # 比例字段：统一转成百分比数值
        rec["dividendYield"] = pct100(_raw(sd, "dividendYield"))
        rec["roe"] = pct100(_raw(fd, "returnOnEquity"))
        rec["roa"] = pct100(_raw(fd, "returnOnAssets"))
        rec["grossMargin"] = pct100(_raw(fd, "grossMargins"))
        rec["operMargin"] = pct100(_raw(fd, "operatingMargins"))
        rec["netMargin"] = pct100(_raw(fd, "profitMargins"))
        rec["revGrowth"] = pct100(_raw(fd, "revenueGrowth"))
        rec["epsGrowth"] = pct100(_raw(fd, "earningsGrowth"))
        rec["debtToEquity"] = _raw(fd, "debtToEquity")
        rec["currentRatio"] = _raw(fd, "currentRatio")
        rec["sector"] = ap.get("sector")
        rec["industry"] = ap.get("industry")
    except Exception as e:
        rec["qs_err"] = str(e)[:120]

    return rec


def main():
    session, crumb = make_session()
    if not crumb:
        print("ERROR: 拿不到 crumb")
        raise SystemExit(1)

    stocks = []
    for sym in SYMBOLS:
        try:
            stocks.append(fetch(session, crumb, sym))
        except Exception as e:
            stocks.append({"symbol": sym, "error": str(e)[:120]})
        time.sleep(0.4)  # 限速，避免触发风控

    os.makedirs("data", exist_ok=True)
    out = {"updatedAt": int(time.time()), "count": len(stocks), "stocks": stocks}
    with open("data/stocks.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    ok = sum(1 for s in stocks if "error" not in s and "chart_err" not in s)
    print(f"done: {len(stocks)} stocks, {ok} 完整, JSON {os.path.getsize('data/stocks.json')} bytes")


if __name__ == "__main__":
    main()
