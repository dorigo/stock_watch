import json, os, urllib.request, urllib.parse, datetime as dt
out = []
def get(url, hdr=None):
    try:
        req = urllib.request.Request(url, headers=hdr or {"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return "ERR", repr(e)
tok = os.environ.get("FINMIND_TOKEN", "")
def fm(ds, did="", start="2026-10-08"):
    q = {"dataset": ds, "start_date": start}
    if did: q["data_id"] = did
    if tok: q["token"] = tok
    return get("https://api.finmindtrade.com/api/v4/data?" + urllib.parse.urlencode(q))
s, b = fm("TaiwanStockInfo", start="")
try:
    rows = json.loads(b)["data"]
    idx = [r for r in rows if not r["stock_id"][:1].isdigit() or "指數" in (r.get("industry_category") or "") or "Index" in (r.get("industry_category") or "")]
    out.append(f"INFO non-numeric/index rows: {len(idx)}")
    for r in idx[:200]: out.append("  " + json.dumps(r, ensure_ascii=False))
    cats = sorted({r.get("industry_category") for r in rows if r.get("industry_category")})
    out.append("CATS " + ", ".join(cats))
except Exception as e:
    out.append(f"INFO fail {s} {b[:300]} {e}")
for ds, did in [("TaiwanStockPrice","TAIEX"),("TaiwanStockPrice","TPEx"),("USStockPrice","^GSPC"),("USStockPrice","^DJI"),("USStockPrice","^IXIC"),("USStockPrice","^SOX"),("USStockPrice","SPY"),("TaiwanStockTotalReturnIndex","TAIEX"),("TaiwanVariousIndicators5Seconds","")]:
    s, b = fm(ds, did, "2026-10-13")
    out.append(f"FM {ds} {did}: {s} {b[:500]}")
for sym in ["^TWII","^TWOII","^DJI","^GSPC","^IXIC","^SOX","^VIX","TWD=X"]:
    s, b = get("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym) + "?range=5d&interval=1d")
    out.append(f"YH {sym}: {s} {b[:400]}")
for u in ["https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX",
          "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=IND&date=20261015",
          "https://www.tpex.org.tw/openapi/v1/tpex_index",
          "https://stooq.com/q/d/l/?s=^spx&i=d"]:
    s, b = get(u)
    out.append(f"URL {u}: {s} {b[:1500]}")
open("data/probe.log", "w").write("\n".join(out))
