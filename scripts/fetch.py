#!/usr/bin/env python3
"""
自選股籌碼追蹤 — 資料抓取與計分

只用 Python 標準函式庫，不需要 pip install。
流程：
  1. 讀 watchlist.txt 的股票代號
  2. 由 FinMind 取得最近 N 個交易日的收盤、三大法人、融資（證交所會擋國外 IP）
  3. 抓集保戶股權分散表（千張大戶，每週）
  4. 依規則計分，輸出 data/latest.json、data/report/YYYY-MM-DD.json、data/index.json

用法：
  python scripts/fetch.py           正常抓資料
  python scripts/fetch.py --demo    產生示範資料（不連網，用來先看版面）
"""
import csv
import datetime as dt
import io
import json
import os
import random
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORT = DATA / "report"
TDCC_DIR = DATA / "tdcc"
TZ = dt.timezone(dt.timedelta(hours=8))

NEED_DAYS = 22          # 抓取的交易日數（回算歷史分數需要較多天）
LOOKBACK_DAYS = 200     # 往回抓幾個日曆天的資料（K 線與 60 日均線需要）
CHART_DAYS = 60         # K 線圖顯示的交易日數
FUND_YEARS = 5          # 估值歷史位置的比較期間（年）
FUND_VERSION = 2        # 基本面快取格式版本（新增欄位時加 1，會強制重抓）
TREND_DAYS = 10         # 回算近幾日的分數（分數走勢）
HIST_DAYS = 10          # 明細與法人小圖顯示幾日
KEEP_REPORTS = 120      # 歷史清單保留幾天
MIN_GAP = 1.2           # 同一網站兩次請求最少間隔秒數
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# 計分權重（總分 100）
W_INST_TODAY = 25   # 法人當日買超 ÷ 5 日均量
W_INST_5D = 25      # 法人 5 日累計買超
W_STREAK = 10       # 外資、投信連續買超天數
W_BIG = 25          # 千張大戶持股比例週增減
W_MARGIN = 15       # 融資餘額減少

# 滿分門檻
FULL_INST_TODAY = 0.20   # 當日法人買超達 5 日均量 20%
FULL_INST_5D = 0.50      # 5 日累計達 5 日均量 50%
FULL_STREAK = 5          # 連買 5 天
FULL_BIG = 0.50          # 千張大戶一週增加 0.5 個百分點
FULL_MARGIN = 0.03       # 融資餘額單日減少 3%


DEBUG = []


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    DEBUG.append(msg)


def now_tw():
    return dt.datetime.now(TZ)


# ───────────────────────── HTTP ─────────────────────────
_last_hit = {}


def http_get(url, as_json=True, tries=3):
    host = urllib.parse.urlparse(url).netloc
    for i in range(tries):
        wait = MIN_GAP - (time.time() - _last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json,text/csv,text/plain,*/*",
                "Accept-Language": "zh-TW,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
            text = decode(raw)
            DEBUG.append(f"    [GET] {url}\n          len={len(raw)} head={text[:600]!r}")
            if as_json:
                if text.lstrip().startswith("<"):
                    raise ValueError("回傳 HTML 而非 JSON（可能被擋或網址已變更）")
                return json.loads(text)
            return text
        except Exception as e:  # noqa: BLE001
            log(f"    ! 第 {i + 1}/{tries} 次失敗：{e}")
            time.sleep(5 * (i + 1))
    log(f"    ✗ 放棄：{url}")
    return None


def decode(raw):
    for enc in ("utf-8-sig", "cp950", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ───────────────────────── 解析工具 ─────────────────────────
def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"<[^>]+>|\s|,|\+", "", str(v))
    if s in ("", "-", "--", "---", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(\.\d+)?", s)
        return float(m.group()) if m else None


def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def save_json(p, obj, compact=True):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) if compact \
        else json.dumps(obj, ensure_ascii=False, indent=1)
    p.write_text(text, encoding="utf-8")


# ───────────────────────── FinMind ─────────────────────────
# 證交所會擋國外 IP（GitHub Actions 在美國），因此股價、法人、融資改由 FinMind 取得。
# 不需要註冊；若在 GitHub Secrets 設定 FINMIND_TOKEN，可提高請求上限。
FINMIND = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "").strip()


def finmind(dataset, data_id=None, start=None):
    q = {"dataset": dataset}
    if data_id:
        q["data_id"] = data_id
    if start:
        q["start_date"] = start
    if FINMIND_TOKEN:
        q["token"] = FINMIND_TOKEN
    j = http_get(FINMIND + "?" + urllib.parse.urlencode(q))
    if not j or j.get("status") != 200:
        if j:
            log(f"    ! FinMind {dataset} {data_id or ''}：{j.get('msg')}")
        return None
    return j.get("data") or []


def stock_info(codes):
    """代號 → [名稱, 市場]。全市場清單存在 data/all_stocks.json（每 7 天更新），也供網頁搜尋新增自選股。"""
    path = DATA / "all_stocks.json"
    cache = load_json(path, {})
    stocks = cache.get("stocks", {})
    age_ok = cache.get("updated", "") >= (now_tw().date() - dt.timedelta(days=7)).isoformat()
    if stocks and age_ok and all(c in stocks for c in codes):
        return stocks
    log("• 更新全市場股票清單")
    rows = finmind("TaiwanStockInfo")
    if rows:
        fresh = {}
        for r in rows:
            sid, mk = r.get("stock_id", ""), {"twse": "上市", "tpex": "上櫃"}.get(r.get("type"))
            if mk and re.fullmatch(r"[0-9]{4,6}[A-Z]?", sid):
                fresh[sid] = [r.get("stock_name") or sid, mk]
        if fresh:
            stocks = dict(sorted(fresh.items()))
            save_json(path, {"updated": now_tw().date().isoformat(), "stocks": stocks})
    return stocks


def fetch_stock(code, start):
    """回傳 {日期: 當日資料}"""
    days = {}
    rows = finmind("TaiwanStockPrice", code, start)
    if rows is None:
        return None
    for r in rows:
        if not r.get("Trading_Volume") and not r.get("close"):
            continue  # 停牌
        days[r["date"]] = {
            "open": num(r.get("open")), "high": num(r.get("max")), "low": num(r.get("min")),
            "close": num(r.get("close")), "chg": num(r.get("spread")) or 0.0,
            "vol": num(r.get("Trading_Volume")), "amt": num(r.get("Trading_money")),
        }
    rows = finmind("TaiwanStockInstitutionalInvestorsBuySell", code, start) or []
    inst = {}
    for r in rows:
        d = inst.setdefault(r["date"], {"foreign": 0.0, "trust": 0.0, "dealer": 0.0, "inst": 0.0})
        net = (num(r.get("buy")) or 0) - (num(r.get("sell")) or 0)
        name = r.get("name", "")
        if name == "Foreign_Investor":
            d["foreign"] += net
        elif name == "Investment_Trust":
            d["trust"] += net
        elif name.startswith("Dealer"):
            d["dealer"] += net
        d["inst"] += net
    for date, v in inst.items():
        if date in days:
            days[date].update(v)
    rows = finmind("TaiwanStockMarginPurchaseShortSale", code, start) or []
    for r in rows:
        if r["date"] in days:
            days[r["date"]].update(margin=num(r.get("MarginPurchaseTodayBalance")),
                                   margin_prev=num(r.get("MarginPurchaseYesterdayBalance")))
    return days


def build_days(codes):
    start = (now_tw().date() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    info = stock_info(codes)
    per = {}
    log("• 股價 / 三大法人 / 融資（FinMind）")
    for c in codes:
        log(f"  ↳ {c}")
        got = fetch_stock(c, start)
        if got:
            per[c] = got
        else:
            log(f"    ! {c} 沒有資料（代號錯誤或已下市）")
    global RAW, INFO
    RAW, INFO = per, info
    all_dates = sorted({d for v in per.values() for d in v}, reverse=True)[:NEED_DAYS]
    days = []
    for d in all_dates:
        stocks = {}
        for c, v in per.items():
            if d in v:
                name, market = info.get(c, [c, "—"])
                stocks[c] = {"name": name or c, "market": market or "—", **v[d]}
        days.append({"date": d, "stocks": stocks})
    if days:
        latest = days[0]["stocks"].values()
        days[0]["has_inst"] = all("inst" in s for s in latest)
        days[0]["has_margin"] = all("margin" in s for s in latest)
    return days  # 新 → 舊


# ───────────────────────── K 線 ─────────────────────────
RAW, INFO = {}, {}


def build_charts():
    """近 CHART_DAYS 日：[日期, 開, 高, 低, 收, 量(張), 法人(張), MA5, MA20, MA60]"""
    out = {}
    for code, days in RAW.items():
        ds = sorted(days)
        closes = [days[d]["close"] for d in ds]

        def ma(i, n):
            return round(sum(closes[i - n + 1:i + 1]) / n, 2) if i >= n - 1 and all(closes[i - n + 1:i + 1]) else None
        rows = []
        for i, d in enumerate(ds):
            if i < len(ds) - CHART_DAYS:
                continue
            x = days[d]
            r2 = lambda v: None if v is None else round(v, 2)  # noqa: E731
            rows.append([d, r2(x.get("open")), r2(x.get("high")), r2(x.get("low")), r2(x.get("close")), lots(x.get("vol")),
                         lots(x.get("inst")) if "inst" in x else None, ma(i, 5), ma(i, 20), ma(i, 60)])
        out[code] = rows
    return out


# ───────────────────────── 均線交叉（週線／月線 vs 季線）─────────────────────────
CROSS_WINDOW = 10   # 只回報近 N 個交易日內發生的交叉


def ma_cross(code):
    days = RAW.get(code) or {}
    ds = sorted(days)
    closes = [days[d].get("close") for d in ds]
    if len(closes) < 62 or not all(closes[-62:]):
        return None

    def ma(n):
        return [sum(closes[i - n + 1:i + 1]) / n if i >= n - 1 and all(closes[i - n + 1:i + 1]) else None
                for i in range(len(closes))]
    m5, m20, m60 = ma(5), ma(20), ma(60)

    def last_cross(fast, up):
        for i in range(len(ds) - 1, max(len(ds) - 1 - CROSS_WINDOW, 60), -1):
            a0, b0, a1, b1 = fast[i - 1], m60[i - 1], fast[i], m60[i]
            if None in (a0, b0, a1, b1):
                continue
            if (up and a0 <= b0 and a1 > b1) or (not up and a0 >= b0 and a1 < b1):
                return {"date": ds[i], "ago": len(ds) - 1 - i}
        return None
    last = len(ds) - 1
    r = lambda v: None if v is None else round(v, 2)  # noqa: E731
    return {
        "ma5": r(m5[last]), "ma20": r(m20[last]), "ma60": r(m60[last]),
        "above5": m5[last] > m60[last], "above20": m20[last] > m60[last],
        "vs60": round((closes[last] / m60[last] - 1) * 100, 2),
        "up5": last_cross(m5, True), "up20": last_cross(m20, True),
        "dn5": last_cross(m5, False), "dn20": last_cross(m20, False),
    }


# ───────────────────────── 基本面（估值、月營收）─────────────────────────
def pct_rank(hist, v):
    hist = [h for h in hist if h is not None]
    if v is None or len(hist) < 60:
        return None
    return round(sum(h < v for h in hist) / len(hist) * 100, 1)


def fetch_valuation(code):
    start = (now_tw().date() - dt.timedelta(days=365 * FUND_YEARS + 10)).isoformat()
    rows = finmind("TaiwanStockPER", code, start)
    if not rows:
        return None
    pe = [num(r.get("PER")) for r in rows]
    pe = [x if x and x > 0 else None for x in pe]
    pb = [num(r.get("PBR")) for r in rows]
    pb = [x if x and x > 0 else None for x in pb]
    dy = [num(r.get("dividend_yield")) for r in rows]
    last = rows[-1]
    cur_pe, cur_pb, cur_dy = pe[-1], pb[-1], dy[-1]

    def rng(h):
        h = [x for x in h if x is not None]
        return [round(min(h), 2), round(statistics.median(h), 2), round(max(h), 2)] if len(h) >= 60 else None
    return {
        "date": last.get("date"), "years": FUND_YEARS,
        "per": cur_pe, "perPct": pct_rank(pe, cur_pe), "perRange": rng(pe),
        "pbr": cur_pb, "pbrPct": pct_rank(pb, cur_pb), "pbrRange": rng(pb),
        "yield": cur_dy, "yieldPct": pct_rank(dy, cur_dy), "yieldRange": rng(dy),
    }


def fetch_revenue(code):
    start = (now_tw().date().replace(day=1) - dt.timedelta(days=800)).isoformat()
    rows = finmind("TaiwanStockMonthRevenue", code, start)
    if not rows:
        return None
    rev = {}
    for r in rows:
        y, m, v = r.get("revenue_year"), r.get("revenue_month"), num(r.get("revenue"))
        if y and m and v:
            rev[(int(y), int(m))] = v
    if not rev:
        return None
    keys = sorted(rev)
    out = []
    for (y, m) in keys[-13:]:
        prev_m = (y, m - 1) if m > 1 else (y - 1, 12)
        v = rev[(y, m)]
        yoy = rev.get((y - 1, m))
        mom = rev.get(prev_m)
        out.append({"ym": f"{y}-{m:02d}", "rev": round(v / 1e8, 2),
                    "yoy": round((v / yoy - 1) * 100, 1) if yoy else None,
                    "mom": round((v / mom - 1) * 100, 1) if mom else None})
    ly, lm = keys[-1]
    cur = sum(rev.get((ly, k), 0) for k in range(1, lm + 1))
    prev = sum(rev.get((ly - 1, k), 0) for k in range(1, lm + 1))
    return {"months": out, "latest": out[-1]["ym"], "ytdYoy": round((cur / prev - 1) * 100, 1) if prev and cur else None}


def _date(v):
    v = str(v or "").strip()[:10]
    return v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and not v.startswith("1900") else None


def fetch_dividends(code):
    """股利政策：即將除權息、最近幾次配息、近一年現金股利"""
    start = (now_tw().date() - dt.timedelta(days=365 * 3)).isoformat()
    rows = finmind("TaiwanStockDividend", code, start)
    if rows is None:
        return None
    if rows and code == read_watchlist()[0]:
        log(f"    · 股利欄位：{sorted(rows[0].keys())}")
    today = now_tw().date().isoformat()
    events = []
    for r in rows:
        cash = (num(r.get("CashEarningsDistribution")) or 0) + (num(r.get("CashStatutorySurplus")) or 0)
        stock = (num(r.get("StockEarningsDistribution")) or 0) + (num(r.get("StockStatutorySurplus")) or 0)
        ex_cash, ex_stock = _date(r.get("CashExDividendTradingDate")), _date(r.get("StockExDividendTradingDate"))
        pay = _date(r.get("CashDividendPaymentDate"))
        period = str(r.get("year") or "").strip()
        ann = _date(r.get("AnnouncementDate")) or _date(r.get("date"))
        if cash > 0:
            events.append({"type": "息", "ex": ex_cash, "cash": round(cash, 4), "pay": pay, "period": period, "ann": ann})
        if stock > 0:
            events.append({"type": "權", "ex": ex_stock, "stock": round(stock, 4), "period": period, "ann": ann})
    # 已公布但還沒定除息日的，也列為即將（ex 為 None）
    upcoming = sorted([e for e in events if e["ex"] is None or e["ex"] >= today],
                      key=lambda e: e["ex"] or "9999")
    past = sorted([e for e in events if e["ex"] and e["ex"] < today], key=lambda e: e["ex"], reverse=True)
    one_year = (now_tw().date() - dt.timedelta(days=365)).isoformat()
    ttm = round(sum(e.get("cash", 0) for e in past if e["ex"] >= one_year), 4)
    # 未公布除息日、且同一期別已在過去事件中的，代表是舊資料，排除
    done = {(e["period"], e["type"]) for e in past}
    upcoming = [e for e in upcoming if not (e["ex"] is None and (e["period"], e["type"]) in done)]
    return {"upcoming": upcoming[:4], "past": past[:8], "ttmCash": ttm}


def update_fundamentals(codes):
    """一天只抓一次，快取在 data/fundamentals.json"""
    path = DATA / "fundamentals.json"
    cache = load_json(path, {})
    today = now_tw().date().isoformat()
    stocks = cache.get("stocks", {}) if cache.get("date") == today and cache.get("v") == FUND_VERSION else {}
    todo = [c for c in codes if c not in stocks]
    if todo:
        log(f"• 基本面（估值、月營收）：{len(todo)} 檔")
        for c in todo:
            log(f"  ↳ {c}")
            stocks[c] = {"val": fetch_valuation(c), "rev": fetch_revenue(c), "div": fetch_dividends(c)}
    else:
        log("• 基本面：今日已更新，沿用快取")
    stocks = {c: stocks[c] for c in codes if c in stocks}
    save_json(path, {"date": today, "v": FUND_VERSION, "stocks": stocks})
    return stocks


# ───────────────────────── 集保千張大戶 ─────────────────────────
def update_tdcc():
    log("• 集保戶股權分散表")
    text = http_get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", as_json=False)
    if not text:
        log("  ✗ 無法取得（千張大戶欄位將沿用舊資料）")
        return
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    big, date = {}, None
    for r in reader:
        if len(r) < 6:
            continue
        try:
            level = int(r[2])
        except ValueError:
            continue
        date = date or r[0].strip()
        if level == 15:  # 持股 1,000,001 股以上 = 千張大戶
            big[r[1].strip()] = [num(r[5]), int(num(r[3]) or 0)]
    if not date or not big:
        log(f"  ✗ 格式無法解析，表頭：{header}")
        return
    path = TDCC_DIR / f"{date}.json"
    if path.exists():
        log(f"  · 已是最新（{date}）")
    else:
        save_json(path, {"date": date, "big": big})
        log(f"  ✓ 新增 {date}，共 {len(big)} 檔")


def tdcc_all():
    return [x for x in (load_json(f) for f in sorted(TDCC_DIR.glob("*.json"), reverse=True)) if x]


def tdcc_pair(all_weeks, day):
    """day（YYYY-MM-DD）當時已有的最新兩週集保資料"""
    key = day.replace("-", "")
    return [w for w in all_weeks if not str(w["date"]).isdigit() or w["date"] <= key][:2]


# ───────────────────────── 計分 ─────────────────────────
def clamp01(x):
    return max(0.0, min(1.0, x))


def lots(shares):
    return None if shares is None else round(shares / 1000)


def streak(series, key):
    n = 0
    for s in series:
        if s and (s.get(key) or 0) > 0:
            n += 1
        else:
            break
    return n


def score_stock(code, days, tdcc):
    series = [dd["stocks"].get(code) for dd in days]
    t = series[0]
    if not t:
        return None
    last5 = [s for s in series[:5] if s]
    vols = [s["vol"] for s in last5 if s.get("vol")]
    avg5 = sum(vols) / len(vols) if vols else None
    inst = t.get("inst")
    sum5 = sum((s.get("inst") or 0) for s in last5)
    f_streak, t_streak = streak(series, "foreign"), streak(series, "trust")

    prev4 = [s["vol"] for s in series[1:5] if s and s.get("vol")]
    vol_ratio = (t["vol"] / (sum(prev4) / len(prev4))) if prev4 and t.get("vol") else None

    big_pct = big_delta = big_people = None
    if tdcc and tdcc[0] and code in tdcc[0]["big"]:
        big_pct, big_people = tdcc[0]["big"][code]
        if len(tdcc) > 1 and tdcc[1] and code in tdcc[1]["big"] and big_pct is not None:
            prev = tdcc[1]["big"][code][0]
            if prev is not None:
                big_delta = round(big_pct - prev, 2)

    m, mp = t.get("margin"), t.get("margin_prev")
    m_chg = (m - mp) if (m is not None and mp is not None) else None
    m_ratio = ((mp - m) / mp) if (m_chg is not None and mp) else None

    p1 = clamp01((inst / avg5) / FULL_INST_TODAY) * W_INST_TODAY if inst and avg5 and inst > 0 else 0
    p2 = clamp01((sum5 / avg5) / FULL_INST_5D) * W_INST_5D if avg5 and sum5 > 0 else 0
    p3 = min(max(f_streak, t_streak), FULL_STREAK) / FULL_STREAK * W_STREAK
    p4 = clamp01(big_delta / FULL_BIG) * W_BIG if big_delta and big_delta > 0 else 0
    p5 = clamp01(m_ratio / FULL_MARGIN) * W_MARGIN if m_ratio and m_ratio > 0 else 0
    score = round(p1 + p2 + p3 + p4 + p5, 1)

    tags = []
    if inst is not None:
        tags.append("法人買超" if inst > 0 else ("法人賣超" if inst < 0 else ""))
    if f_streak >= 2:
        tags.append(f"外資連買{f_streak}天")
    if t_streak >= 2:
        tags.append(f"投信連買{t_streak}天")
    if big_delta and big_delta > 0:
        tags.append("大戶增持")
    elif big_delta and big_delta < 0:
        tags.append("大戶減持")
    if m_chg is not None and m_chg < 0:
        tags.append("融資減少")
    elif m_chg is not None and mp and m_chg / mp > FULL_MARGIN:
        tags.append("融資大增")
    if vol_ratio and vol_ratio >= 2:
        tags.append("量增")
    close, chg = t.get("close"), t.get("chg") or 0
    chg_pct = round(chg / (close - chg) * 100, 2) if close and close - chg else 0.0
    if chg_pct >= 5 and vol_ratio and vol_ratio >= 2 and (inst or 0) <= 0 and t.get("high") and close >= t["high"] * 0.99:
        tags.append("隔日沖賣壓")

    hist = []
    for dd in reversed(days[:HIST_DAYS]):
        s = dd["stocks"].get(code)
        if s:
            hist.append({"d": dd["date"], "c": s.get("close"), "p": s.get("chg"),
                         "v": lots(s.get("vol")), "f": lots(s.get("foreign")), "t": lots(s.get("trust")),
                         "dl": lots(s.get("dealer")), "i": lots(s.get("inst")), "m": s.get("margin")})

    return {
        "code": code, "name": t.get("name"), "market": t.get("market"),
        "close": close, "chg": round(chg, 2), "chgPct": chg_pct,
        "vol": lots(t.get("vol")), "amt": round((t.get("amt") or 0) / 1e8, 2),
        "foreign": lots(t.get("foreign")), "trust": lots(t.get("trust")), "dealer": lots(t.get("dealer")),
        "inst": lots(inst), "sum5": lots(sum5), "avg5": lots(avg5), "volRatio": round(vol_ratio, 2) if vol_ratio else None,
        "fStreak": f_streak, "tStreak": t_streak,
        "bigPct": big_pct, "bigDelta": big_delta, "bigPeople": big_people,
        "margin": m, "marginChg": m_chg,
        "score": score,
        "parts": {"instToday": round(p1, 1), "inst5d": round(p2, 1), "streak": round(p3, 1),
                  "big": round(p4, 1), "margin": round(p5, 1)},
        "tags": [x for x in tags if x],
        "hist": hist,
        "partial": "inst" not in t or "margin" not in t,
    }


def score_full(code, days, weeks):
    """今日分數＋回算近 TREND_DAYS 日的分數走勢"""
    rec = score_stock(code, days, tdcc_pair(weeks, days[0]["date"]))
    if not rec:
        return None
    trend = []
    for i in range(TREND_DAYS):
        sub = days[i:]
        if len(sub) < 5:
            break
        if code not in sub[0]["stocks"]:
            continue
        past = rec if i == 0 else score_stock(code, sub, tdcc_pair(weeks, sub[0]["date"]))
        trend.append({"d": sub[0]["date"], "s": past["score"]})
    trend.reverse()  # 舊 → 新
    sc = [x["s"] for x in trend]
    rec["trend"] = trend
    rec["scoreChg1"] = round(sc[-1] - sc[-2], 1) if len(sc) >= 2 else None
    rec["scoreChg5"] = round(sc[-1] - sc[-6], 1) if len(sc) >= 6 else None
    up = 0
    for a, b in zip(reversed(sc[:-1]), reversed(sc[1:])):
        if b > a:
            up += 1
        else:
            break
    rec["scoreUp"] = up
    if up >= 3:
        rec["tags"].append(f"分數連升{up}天")
    if rec["scoreChg5"] is not None and rec["scoreChg5"] >= 20:
        rec["tags"].append("分數大增")
    elif rec["scoreChg5"] is not None and rec["scoreChg5"] <= -20:
        rec["tags"].append("分數大減")
    by_date = {x["d"]: x["s"] for x in trend}
    for h in rec["hist"]:
        h["s"] = by_date.get(h["d"])
    return rec


# ───────────────────────── 主流程 ─────────────────────────
def read_watchlist():
    codes = []
    for line in (ROOT / "watchlist.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([0-9]{4,6}[A-Z]?)\b", line)
        if m and m.group(1) not in codes:
            codes.append(m.group(1))
    return codes


def write_outputs(report):
    REPORT.mkdir(parents=True, exist_ok=True)
    save_json(REPORT / f"{report['date']}.json", report)
    save_json(DATA / "latest.json", report)
    files = sorted(REPORT.glob("*.json"), reverse=True)
    for old in files[KEEP_REPORTS:]:
        old.unlink()
    save_json(DATA / "index.json", {"dates": [f.stem for f in files[:KEEP_REPORTS]]}, compact=False)


def main():
    codes = read_watchlist()
    if not codes:
        log("watchlist.txt 沒有任何股票代號")
        sys.exit(1)
    log(f"自選股 {len(codes)} 檔：{', '.join(codes)}")

    if "--demo" in sys.argv:
        return demo(codes)

    days = build_days(codes)
    if not days:
        log("✗ 找不到任何交易日資料，請查看上方錯誤訊息")
        sys.exit(1)
    update_tdcc()
    weeks = tdcc_all()
    tdcc = tdcc_pair(weeks, days[0]["date"])

    latest = days[0]
    stocks = [s for s in (score_full(c, days, weeks) for c in codes) if s]
    stocks.sort(key=lambda s: s["score"], reverse=True)
    for i, s in enumerate(stocks, 1):
        s["rank"] = i
    report = {
        "date": latest["date"],
        "updated": now_tw().isoformat(timespec="minutes"),
        "complete": bool(latest.get("has_inst") and latest.get("has_margin")),
        "tdccDate": tdcc[0]["date"] if tdcc and tdcc[0] else None,
        "tdccPrevDate": tdcc[1]["date"] if len(tdcc) > 1 and tdcc[1] else None,
        "notFound": [c for c in codes if c not in latest["stocks"]],
        "stocks": stocks,
    }
    fund = update_fundamentals(codes)
    for s in stocks:
        v = (fund.get(s["code"]) or {}).get("val") or {}
        r = (fund.get(s["code"]) or {}).get("rev") or {}
        s["per"], s["perPct"], s["yield"], s["yieldPct"] = v.get("per"), v.get("perPct"), v.get("yield"), v.get("yieldPct")
        m = (r.get("months") or [None])[-1] or {}
        s["revYoy"], s["revYm"] = m.get("yoy"), m.get("ym")
        d = (fund.get(s["code"]) or {}).get("div") or {}
        nxt = next((e for e in d.get("upcoming", []) if e.get("ex")), None)
        s["nextEx"] = {"date": nxt["ex"], "type": nxt["type"], "cash": nxt.get("cash"), "stock": nxt.get("stock")} if nxt else None
    for s in stocks:
        s["ma"] = ma_cross(s["code"])
        mc = s["ma"] or {}
        if mc.get("up20") and mc["up20"]["ago"] <= 5:
            s["tags"].insert(0, "月線突破季線")
        elif mc.get("up5") and mc["up5"]["ago"] <= 5:
            s["tags"].insert(0, "週線突破季線")
        if mc.get("dn20") and mc["dn20"]["ago"] <= 5:
            s["tags"].insert(0, "月線跌破季線")
    save_json(DATA / "charts.json", {"date": report["date"], "stocks": build_charts()})
    write_outputs(report)
    log(f"✓ 完成：{report['date']}，{len(stocks)} 檔" + ("" if report["complete"] else "（部分資料尚未公布，稍後會再更新）"))
    if report["notFound"]:
        log(f"  ! 查無代號：{', '.join(report['notFound'])}")


# ───────────────────────── 示範資料 ─────────────────────────
DEMO_NAMES = {"2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2603": "長榮", "3231": "緯創",
              "6488": "環球晶", "8069": "元太", "00878": "國泰永續高股息"}


def demo(codes):
    rnd = random.Random(42)
    today = now_tw().date()
    dates, d = [], today
    while len(dates) < NEED_DAYS:
        if d.weekday() < 5:
            dates.append(d)
        d -= dt.timedelta(days=1)
    days = []
    base = {c: rnd.uniform(25, 1100) for c in codes}
    for di in reversed(dates):
        st = {}
        for c in codes:
            prev = base[c]
            close = round(prev * (1 + rnd.gauss(0.002, 0.025)), 2)
            vol = rnd.uniform(3e6, 6e7)
            f, t, dl = rnd.gauss(0.02, 0.1) * vol, rnd.gauss(0.01, 0.04) * vol, rnd.gauss(0, 0.03) * vol
            mprev = rnd.uniform(2000, 30000)
            st[c] = {"name": DEMO_NAMES.get(c, f"股票{c}"), "market": "上櫃" if c in ("6488", "8069") else "上市",
                     "open": prev, "high": max(prev, close) * 1.01, "low": min(prev, close) * 0.99,
                     "close": close, "chg": round(close - prev, 2), "vol": vol, "amt": vol * close,
                     "foreign": f, "trust": t, "dealer": dl, "inst": f + t + dl,
                     "margin": round(mprev * (1 + rnd.gauss(0, 0.02))), "margin_prev": round(mprev)}
            base[c] = close
        days.insert(0, {"date": di.isoformat(), "stocks": st})
    tdcc = [{"date": "demo", "big": {c: [rnd.uniform(40, 85), rnd.randint(200, 3000)] for c in codes}}]
    tdcc.append({"date": "demo", "big": {c: [v[0] - rnd.gauss(0.1, 0.4), v[1]] for c, v in tdcc[0]["big"].items()}})
    stocks = [score_full(c, days, tdcc) for c in codes]
    stocks.sort(key=lambda s: s["score"], reverse=True)
    for i, s in enumerate(stocks, 1):
        s["rank"] = i
    report = {"date": days[0]["date"], "updated": now_tw().isoformat(timespec="minutes"), "complete": True,
              "demo": True, "tdccDate": None, "tdccPrevDate": None, "notFound": [], "stocks": stocks}
    write_outputs(report)
    log("✓ 已產生示範資料 data/latest.json")


if __name__ == "__main__":
    import traceback
    code = 0
    try:
        main()
    except SystemExit as e:
        code = e.code or 0
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
        code = 1
    finally:
        if "--demo" not in sys.argv:
            DATA.mkdir(parents=True, exist_ok=True)
            (DATA / "debug.log").write_text(f"執行時間 {now_tw().isoformat(timespec='seconds')}\n" + "\n".join(DEBUG), encoding="utf-8")
    sys.exit(code)
