#!/usr/bin/env python3
"""
籌碼分數回測

用與網站相同的計分規則，回算自選股過去約 2 年每天的分數，
統計各種「買進訊號」與「賣出警示」出現後 5 日、20 日的報酬，並和「任意一天買進」比較。

注意：
  - 千張大戶的歷史週資料無法免費取得，因此回測分數不含大戶項目（滿分 75 而非 100）。
  - 訊號在第 t 日收盤後才知道，因此以第 t+1 日開盤價進場、持有 N 個交易日後以收盤價計算報酬。
  - 同一檔股票同一種訊號觸發後 5 個交易日內不重複計算，避免連續幾天的訊號被重複計入。
  - 結果只代表過去，樣本有限，不保證未來。

輸出：data/backtest.json
"""
import datetime as dt
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch as F  # noqa: E402

YEARS = 2
HORIZONS = [5, 20]
COOLDOWN = 5
WINDOW = 30            # 計算單日分數時往前取的交易日數（足夠算 5 日均量與連買）
BOOT = 2000            # bootstrap 重抽次數
OUT = F.DATA / "backtest.json"

SIGNALS = [
    # key, 類型, 名稱, 說明
    ("chg5_up20", "buy", "分數 5 日上升 20 以上", "籌碼在短時間內明顯轉好（網站的「分數大增」標籤）"),
    ("up3", "buy", "分數連升 3 天以上", "買盤持續聚集（網站的「分數連升」標籤）"),
    ("score50", "buy", "分數達 50 以上", "多項籌碼訊號同時偏多"),
    ("chg5_dn20", "sell", "分數 5 日下降 20 以上", "籌碼在短時間內明顯轉壞（網站的「分數大減」標籤）"),
    ("inst_sell3_margin_up", "sell", "法人連賣 3 天且融資增加", "「法人在撤、散戶在接」的組合"),
    ("score_low", "sell", "分數低於 5（且前 5 日曾達 30）", "原本有買盤，突然完全消失"),
]


def log(*a):
    F.log(*a)


def build_history(codes):
    start = (F.now_tw().date() - dt.timedelta(days=int(365 * YEARS) + 45)).isoformat()
    info = F.stock_info(codes)
    per = {}
    for c in codes:
        log(f"  ↳ {c}")
        got = F.fetch_stock(c, start)
        if got:
            per[c] = got
        else:
            log(f"    ! {c} 沒有資料")
    all_dates = sorted({d for v in per.values() for d in v}, reverse=True)
    days = []
    for d in all_dates:
        stocks = {}
        for c, v in per.items():
            if d in v:
                name, market = info.get(c, [c, "—"])
                stocks[c] = {"name": name, "market": market, **v[d]}
        days.append({"date": d, "stocks": stocks})
    return days  # 新 → 舊


def score_series(code, days):
    """回傳 舊→新 的 [(date, score, rec)]"""
    out = []
    for i in range(len(days) - 6, -1, -1):  # 由舊到新
        if code not in days[i]["stocks"]:
            continue
        rec = F.score_stock(code, days[i:i + WINDOW], [])
        if rec:
            out.append((days[i]["date"], rec["score"], days[i]["stocks"][code]))
    return out


def detect(series):
    """series 舊→新；回傳 {signal_key: [index,...]}"""
    hits = {k: [] for k, *_ in SIGNALS}
    scores = [s for _, s, _ in series]
    for i in range(len(series)):
        sc = scores[i]
        if i >= 5 and sc - scores[i - 5] >= 20:
            hits["chg5_up20"].append(i)
        if i >= 3 and scores[i] > scores[i - 1] > scores[i - 2] > scores[i - 3]:
            hits["up3"].append(i)
        if sc >= 50:
            hits["score50"].append(i)
        if i >= 5 and sc - scores[i - 5] <= -20:
            hits["chg5_dn20"].append(i)
        if i >= 3:
            raw = [series[j][2] for j in range(i - 2, i + 1)]
            m_now, m_before = series[i][2].get("margin"), series[i - 3][2].get("margin")
            if all((r.get("inst") or 0) < 0 for r in raw) and m_now is not None and m_before is not None and m_now > m_before:
                hits["inst_sell3_margin_up"].append(i)
        if i >= 5 and sc < 5 and max(scores[i - 5:i]) >= 30:
            hits["score_low"].append(i)
    # 冷卻期：同一訊號 COOLDOWN 日內只算一次
    for k, idx in hits.items():
        kept, last = [], -10 ** 9
        for i in idx:
            if i - last > COOLDOWN:
                kept.append(i)
                last = i
        hits[k] = kept
    return hits


def fwd_return(series, i, n):
    """第 i 日訊號 → 第 i+1 日開盤進場，第 i+n 日收盤出場"""
    if i + n >= len(series):
        return None
    entry = series[i + 1][2].get("open") or series[i + 1][2].get("close")
    exitp = series[i + n][2].get("close")
    if not entry or not exitp:
        return None
    return (exitp / entry - 1) * 100


def summarize(rets, base_mean):
    if not rets:
        return {"n": 0}
    mean = statistics.fmean(rets)
    rnd = random.Random(7)
    boots = sorted(statistics.fmean(rnd.choices(rets, k=len(rets))) for _ in range(BOOT)) if len(rets) >= 5 else []
    lo, hi = (boots[int(BOOT * 0.025)], boots[int(BOOT * 0.975)]) if boots else (None, None)
    return {
        "n": len(rets),
        "mean": round(mean, 2),
        "median": round(statistics.median(rets), 2),
        "win": round(sum(r > 0 for r in rets) / len(rets) * 100, 1),
        "excess": round(mean - base_mean, 2),
        "ci": [round(lo - base_mean, 2), round(hi - base_mean, 2)] if lo is not None else None,
    }


def main():
    codes = F.read_watchlist()
    log(f"回測自選股 {len(codes)} 檔，期間約 {YEARS} 年")
    days = build_history(codes)
    if len(days) < 60:
        log("✗ 歷史資料不足")
        sys.exit(1)

    base = {h: [] for h in HORIZONS}
    sig_rets = {k: {h: [] for h in HORIZONS} for k, *_ in SIGNALS}
    per_stock = []
    events = []
    for c in codes:
        series = score_series(c, days)
        if len(series) < 40:
            continue
        name = series[-1][2].get("name", c)
        for i in range(len(series)):
            for h in HORIZONS:
                r = fwd_return(series, i, h)
                if r is not None:
                    base[h].append(r)
        hits = detect(series)
        row = {"code": c, "name": name, "days": len(series), "signals": {}}
        for k, idx in hits.items():
            rs = {h: [r for r in (fwd_return(series, i, h) for i in idx) if r is not None] for h in HORIZONS}
            for h in HORIZONS:
                sig_rets[k][h].extend(rs[h])
            row["signals"][k] = {"n": len(idx), "mean20": round(statistics.fmean(rs[20]), 2) if rs[20] else None}
            for i in idx[-3:]:
                events.append({"code": c, "name": name, "signal": k, "date": series[i][0], "score": series[i][1],
                               "r5": round(fwd_return(series, i, 5), 2) if fwd_return(series, i, 5) is not None else None,
                               "r20": round(fwd_return(series, i, 20), 2) if fwd_return(series, i, 20) is not None else None})
        per_stock.append(row)

    base_mean = {h: statistics.fmean(base[h]) for h in HORIZONS if base[h]}
    result = {
        "generated": F.now_tw().isoformat(timespec="minutes"),
        "start": days[-1]["date"], "end": days[0]["date"],
        "stocks": len(per_stock), "horizons": HORIZONS, "cooldown": COOLDOWN,
        "baseline": {str(h): {"n": len(base[h]), "mean": round(base_mean[h], 2),
                              "median": round(statistics.median(base[h]), 2),
                              "win": round(sum(r > 0 for r in base[h]) / len(base[h]) * 100, 1)} for h in HORIZONS},
        "signals": [{"key": k, "type": t, "name": n, "desc": d,
                     "stats": {str(h): summarize(sig_rets[k][h], base_mean[h]) for h in HORIZONS}}
                    for k, t, n, d in SIGNALS],
        "perStock": per_stock,
        "recent": sorted(events, key=lambda e: e["date"], reverse=True)[:40],
    }
    F.save_json(OUT, result, compact=False)
    log(f"✓ 回測完成：{result['start']} ～ {result['end']}，{len(per_stock)} 檔")
    for s in result["signals"]:
        st = s["stats"]["20"]
        log(f"  {s['name']}：n={st.get('n')} 20日平均={st.get('mean')}% 超額={st.get('excess')}% 勝率={st.get('win')}%")


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
        (F.DATA / "backtest.log").write_text("\n".join(F.DEBUG), encoding="utf-8")
    sys.exit(code)
