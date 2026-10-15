#!/usr/bin/env python3
"""測試各資料來源在 GitHub Actions 上能否取得資料，結果寫入 data/probe.log"""
import time
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
URLS = [
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN",
    "https://openapi.twse.com.tw/v1/fund/T86",
    "https://www.twse.com.tw/rwd/zh/fund/T86?date=20261014&selectType=ALLBUT0999&response=json",
    "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=20261014&type=ALLBUT0099",
    "https://www.twse.com.tw/fund/T86?response=json&date=20261014&selectType=ALLBUT0999",
    "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=2026/10/14&type=EW&response=json",
    "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=2026/10/14&response=json",
    "https://www.tpex.org.tw/www/zh-tw/margin/balance?date=2026/10/14&response=json",
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
    "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5",
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id=2330&start_date=2026-10-01",
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id=2330&start_date=2026-10-08",
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMarginPurchaseShortSale&data_id=2330&start_date=2026-10-08",
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id=2330&start_date=2026-09-20",
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id=6488&start_date=2026-10-08",
    "https://ipinfo.io/json",
]
out = []
for u in URLS:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp950", errors="replace")
        out.append(f"### {u}\nstatus=ok len={len(raw)}\nhead={text[:700]!r}\n")
    except Exception as e:  # noqa: BLE001
        out.append(f"### {u}\nERROR {e}\n")
    time.sleep(3)
Path("data").mkdir(exist_ok=True)
Path("data/probe.log").write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
