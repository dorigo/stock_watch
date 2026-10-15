#!/usr/bin/env python3
"""
「共振回調策略」日線版回測（只測做多）

規則（把文章的 15 分／5 分 K 進場，改用日 K 近似）：
  1. 週線多頭：上一個完整週的收盤 > 週 EMA9、EMA21，且週 EMA9 向上
  2. 曾大漲後橫盤：近 120 日內從最低點上漲 ≥ 25%，目前收盤不低於之後高點的 85%，且高點出現在 5 日以前
  3. 共振支撐：日線 EMA9、EMA21、AVWAP（錨定在該段漲勢的起漲低點）三者差距 ≤ 3%
  4. 跌破後拉回：當日最低價跌破支撐區下緣，收盤站回支撐區下緣之上（訊號 K 線）
  5. 進場：之後 3 個交易日內突破訊號 K 線高點（跳空則以開盤價成交）
  6. 停損：訊號 K 線低點；風險超過 8% 的不做
  7. 出場：漲到 3R 先賣 1/3；之後收盤跌破日線 EMA9 出清剩餘部位；最多持有 60 日

對照組：同一批股票隨機挑日子以開盤價進場，用相同的風險幅度與出場規則。
另外測三個變體，看哪個條件真的有貢獻。

輸出：data/backtest_resonance.json
"""
import datetime as dt
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch as F  # noqa: E402

YEARS = 3
COST = 0.45            # 來回交易成本 %（手續費折扣後＋證交稅）
MAX_RISK = 8.0         # 停損幅度上限 %
PARTIAL_R = 3.0
MAX_HOLD = 60
ENTRY_WINDOW = 3
ZONE_WIDTH = 3.0       # 共振區寬度上限 %
RALLY = 1.25
PULLBACK_FLOOR = 0.85
RANDOM_MULT = 10
BOOT = 2000
OUT = F.DATA / "backtest_resonance.json"

# 樣本：自選股＋台股大中型、成交活絡的股票（固定清單，部分可能已改名或下市，抓不到會自動略過）
UNIVERSE = """
2330 2317 2454 2308 2382 2881 2882 2891 2303 3711 2412 2886 2884 1216 2885 2357 3034 2892 2880 2345
3231 2379 2883 5880 2890 3008 2002 1303 1301 2207 6669 2395 4938 2327 3037 2301 2603 2609 2615 3045
4904 1101 2912 5871 6505 1326 2408 3661 3443 2376 2356 2449 3017 2383 6415 3653 2368 3533 8046 2059
1504 1513 1519 3481 2409 6770 2344 3260 5269 3529 6488 5347 8069 3105 6274 2474 3044 1590 9910 2105
6239 2324 2353 4958 2492 3702 2360 1795 4743 6446 3293 8299 5483 3324 2201
""".split()


def log(*a):
    F.log(*a)


def ema(vals, n):
    k, out, e = 2 / (n + 1), [], None
    for v in vals:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def load_prices(codes, start):
    data = {}
    for c in codes:
        rows = F.finmind("TaiwanStockPrice", c, start)
        bars = []
        for r in rows or []:
            o, h, l, cl, v = (F.num(r.get(k)) for k in ("open", "max", "min", "close", "Trading_Volume"))
            if cl and h and l and o and v:
                bars.append({"d": r["date"], "o": o, "h": h, "l": l, "c": cl, "v": v})
        if len(bars) >= 200:
            data[c] = bars
        else:
            log(f"    · {c} 資料不足，略過")
    return data


def indicators(bars):
    closes = [b["c"] for b in bars]
    e9, e21, e50 = ema(closes, 9), ema(closes, 21), ema(closes, 50)
    # 週線（依 ISO 週分組，週收盤 = 該週最後一天收盤）
    weeks, wk_of = [], []
    for b in bars:
        y, w, _ = dt.date.fromisoformat(b["d"]).isocalendar()
        key = (y, w)
        if not weeks or weeks[-1][0] != key:
            weeks.append([key, b["c"]])
        else:
            weeks[-1][1] = b["c"]
        wk_of.append(len(weeks) - 1)
    wc = [w[1] for w in weeks]
    we9, we21 = ema(wc, 9), ema(wc, 21)
    return e9, e21, e50, wk_of, wc, we9, we21


def weekly_ok(i, wk_of, wc, we9, we21):
    w = wk_of[i] - 1  # 只用已完成的上一週，避免偷看未來
    if w < 22:
        return False
    return wc[w] > we9[w] and wc[w] > we21[w] and we9[w] > we9[w - 1]


def find_setup(bars, i, e9, e21):
    """回傳 (anchor_idx, zone_low, zone_high) 或 None；只用到第 i 日（含）以前的資料"""
    if i < 130:
        return None
    lo_i = min(range(i - 120, i - 5), key=lambda k: bars[k]["l"])
    low = bars[lo_i]["l"]
    hi_i = max(range(lo_i, i), key=lambda k: bars[k]["h"])
    high = bars[hi_i]["h"]
    rally_ok = high >= low * RALLY and i - hi_i >= 5 and bars[i]["c"] >= high * PULLBACK_FLOOR
    pv = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in bars[lo_i:i + 1])
    vv = sum(b["v"] for b in bars[lo_i:i + 1])
    avwap = pv / vv
    lines = [e9[i], e21[i], avwap]
    zl, zh = min(lines), max(lines)
    return {"rally": rally_ok, "zone_ok": (zh - zl) / bars[i]["c"] * 100 <= ZONE_WIDTH,
            "zl": zl, "zh": zh, "avwap": avwap}


def simulate(bars, e9, entry_i, entry_px, stop):
    """回傳 (淨報酬%, R 倍數, 持有天數, 出場原因)"""
    risk = entry_px - stop
    if risk <= 0:
        return None
    target = entry_px + PARTIAL_R * risk
    remaining, realized, partial = 1.0, 0.0, False
    last = min(len(bars) - 1, entry_i + MAX_HOLD)
    reason = "到期"
    for k in range(entry_i, last + 1):
        b = bars[k]
        if b["l"] <= stop:  # 先檢查停損（保守）
            px = min(stop, b["o"]) if k > entry_i else stop
            realized += remaining * (px / entry_px - 1)
            remaining = 0
            reason = "停損" if not partial else "保本後回落"
            break
        if not partial and b["h"] >= target:
            realized += (1 / 3) * (target / entry_px - 1)
            remaining -= 1 / 3
            partial = True
            stop = max(stop, entry_px)  # 分批後停損移到成本
        if partial and b["c"] < e9[k]:
            realized += remaining * (b["c"] / entry_px - 1)
            remaining = 0
            reason = "跌破 EMA9"
            break
    if remaining > 0:
        realized += remaining * (bars[last]["c"] / entry_px - 1)
        k = last
        if last < entry_i + MAX_HOLD:
            reason = "持倉中"  # 資料到期前尚未出場，不列入績效
    gross = realized * 100
    net = gross - COST
    return {"net": round(net, 3), "r": round((realized * entry_px) / risk, 3), "days": k - entry_i + 1,
            "reason": reason, "partial": partial}


def run_strategy(data, use_week=True, use_rally=True, use_zone=True):
    trades = []
    for code, bars in data.items():
        e9, e21, e50, wk_of, wc, we9, we21 = indicators(bars)
        i = 130
        while i < len(bars) - 2:
            st = find_setup(bars, i, e9, e21)
            ok = st and (not use_week or weekly_ok(i, wk_of, wc, we9, we21)) \
                and (not use_rally or st["rally"]) and (not use_zone or st["zone_ok"])
            b = bars[i]
            if ok and b["l"] < st["zl"] <= b["c"]:
                trig, stop = b["h"], b["l"]
                risk_pct = (trig - stop) / trig * 100
                done = False
                if 0 < risk_pct <= MAX_RISK:
                    for j in range(i + 1, min(len(bars), i + 1 + ENTRY_WINDOW)):
                        if bars[j]["h"] > trig:
                            px = max(trig, bars[j]["o"])
                            if px - stop > 0 and (px - stop) / px * 100 <= MAX_RISK:
                                res = simulate(bars, e9, j, px, stop)
                                if res:
                                    trades.append({"code": code, "signal": b["d"], "entry": bars[j]["d"],
                                                   "px": round(px, 2), "stop": round(stop, 2),
                                                   "risk": round((px - stop) / px * 100, 2), **res})
                                    i = j + res["days"]  # 持倉期間不重複進場
                                    done = True
                            break
                if done:
                    continue
            i += 1
    return trades


def run_random(data, risks, n, seed=11):
    rnd = random.Random(seed)
    codes = list(data)
    out = []
    while len(out) < n and codes:
        c = rnd.choice(codes)
        bars = data[c]
        e9 = ema([b["c"] for b in bars], 9)
        j = rnd.randrange(130, len(bars) - 2)
        px = bars[j]["o"]
        r = rnd.choice(risks) if risks else 3.0
        res = simulate(bars, e9, j, px, px * (1 - r / 100))
        if res:
            out.append(res)
    return out


def stats(trades, base=None):
    trades = [t for t in trades if t["reason"] != "持倉中"]
    if not trades:
        return {"n": 0}
    nets = [t["net"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    mean = statistics.fmean(nets)
    out = {
        "n": len(nets), "mean": round(mean, 2), "median": round(statistics.median(nets), 2),
        "win": round(len(wins) / len(nets) * 100, 1),
        "avgWin": round(statistics.fmean(wins), 2) if wins else None,
        "avgLoss": round(statistics.fmean(losses), 2) if losses else None,
        "pf": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "avgR": round(statistics.fmean(t["r"] for t in trades), 2),
        "days": round(statistics.fmean(t["days"] for t in trades), 1),
        "partialRate": round(sum(t["partial"] for t in trades) / len(trades) * 100, 1),
        "total": round(sum(nets), 1),
    }
    if base is not None and len(nets) >= 5:
        rnd = random.Random(3)
        bm = statistics.fmean(base)
        boots = sorted(statistics.fmean(rnd.choices(nets, k=len(nets))) - bm for _ in range(BOOT))
        out["excess"] = round(mean - bm, 2)
        out["ci"] = [round(boots[int(BOOT * 0.025)], 2), round(boots[int(BOOT * 0.975)], 2)]
    return out


def main():
    codes = list(dict.fromkeys(F.read_watchlist() + UNIVERSE))
    start = (F.now_tw().date() - dt.timedelta(days=int(365 * YEARS) + 200)).isoformat()
    log(f"共振回調回測：{len(codes)} 檔，約 {YEARS} 年（含指標暖機期）")
    data = load_prices(codes, start)
    if not data:
        log("✗ 沒有資料")
        sys.exit(1)
    info = F.stock_info(list(data))
    log(f"  取得 {len(data)} 檔")

    main_trades = run_strategy(data)
    risks = [t["risk"] for t in main_trades] or [3.0]
    rand = run_random(data, risks, max(300, len(main_trades) * RANDOM_MULT))
    base = [t["net"] for t in rand if t["reason"] != "持倉中"]
    variants = [
        ("full", "完整策略", "週線多頭＋大漲後橫盤＋共振支撐＋跌破拉回", main_trades),
        ("no_week", "拿掉週線濾網", "其他條件相同，不看週線趨勢", run_strategy(data, use_week=False)),
        ("no_rally", "拿掉「大漲後橫盤」", "其他條件相同，不要求先前大漲", run_strategy(data, use_rally=False)),
        ("no_zone", "拿掉「共振區」", "其他條件相同，不要求 EMA 與 AVWAP 靠攏（只要跌破 EMA9/21/AVWAP 最低者後拉回）",
         run_strategy(data, use_zone=False)),
    ]
    watch = set(F.read_watchlist())
    by_year = {}
    for t in main_trades:
        by_year.setdefault(t["entry"][:4], []).append(t)
    for t in main_trades:
        t["name"] = (info.get(t["code"]) or [t["code"]])[0]
    result = {
        "generated": F.now_tw().isoformat(timespec="minutes"),
        "start": min(b[130]["d"] for b in data.values()),
        "end": max(b[-1]["d"] for b in data.values()),
        "stocks": len(data), "cost": COST, "params": {
            "maxRisk": MAX_RISK, "partialR": PARTIAL_R, "maxHold": MAX_HOLD, "zone": ZONE_WIDTH,
            "rally": RALLY, "pullbackFloor": PULLBACK_FLOOR, "entryWindow": ENTRY_WINDOW},
        "random": stats(rand),
        "variants": [{"key": k, "name": n, "desc": d, "stats": stats(tr, base)} for k, n, d, tr in variants],
        "byYear": {y: stats(v) for y, v in sorted(by_year.items())},
        "watchlist": stats([t for t in main_trades if t["code"] in watch], base),
        "exitReasons": {r: sum(1 for t in main_trades if t["reason"] == r) for r in ("停損", "保本後回落", "跌破 EMA9", "到期", "持倉中")},
        "trades": sorted(main_trades, key=lambda t: t["entry"], reverse=True)[:80],
    }
    F.save_json(OUT, result, compact=False)
    log(f"✓ 完成：{result['start']} ～ {result['end']}，{len(data)} 檔")
    for v in result["variants"]:
        s = v["stats"]
        log(f"  {v['name']}：n={s.get('n')} 平均={s.get('mean')}% 勝率={s.get('win')}% 超額={s.get('excess')} CI={s.get('ci')}")
    log(f"  隨機進場：{result['random']}")


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
        F.DATA.mkdir(parents=True, exist_ok=True)
        (F.DATA / "backtest_resonance.log").write_text(
            "\n".join(x for x in F.DEBUG if "[GET]" not in x and "len=" not in x), encoding="utf-8")
    sys.exit(code)
