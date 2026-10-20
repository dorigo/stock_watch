"""盤中到價通知

開盤時間（台北 9:00～13:30）每分鐘查一次證交所即時報價，
持股的股價「漲到目標價」時寄 Email 通知。每天每檔只寄一次（改了目標價會重新通知）。
（跌破停損價不寄信：停損會在晚上的 Email 日報和盤中即時看板提醒。）

需要的 GitHub Secrets（和 Email 日報共用）：
  MAIL_USER / MAIL_PASS / MAIL_TO（選填）/ MAIL_SERVER（選填）/ MAIL_PORT（選填）
  HOLDINGS   網站「我的持股」自動同步的持股（含目標價 target）

這個 repo 是公開的，執行紀錄任何人都看得到：所以紀錄裡只寫「幾檔、寄了幾封」，不寫股票代號與價格；
避免重複通知的狀態檔 data/alert_state.json 也只存雜湊值。
"""
import datetime as dt
import hashlib
import html
import json
import os
import smtplib
import ssl
import subprocess
import sys
import time
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

TZ = dt.timezone(dt.timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
STATE = "data/alert_state.json"
STATUS = "data/alert_status.json"   # 公開的執行狀態（只有數量與時間，沒有代號與價格），網頁顯示用
OPEN, CLOSE = 9 * 60, 13 * 60 + 31          # 只在 9:00～13:31 檢查（8:30～9:00 試撮價格不算）
INTERVAL = 60


def log(*a):
    print(*a, flush=True)


def now():
    return dt.datetime.now(TZ)


def mins(t):
    return t.hour * 60 + t.minute


def num(v):
    try:
        x = float(str(v).replace(",", ""))
        return x if x > 0 else None
    except Exception:
        return None


def fnum(v, d=2):
    return "—" if v is None else f"{v:,.{d}f}"


def load(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def site_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "dorigo/stock_watch")
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def load_holdings():
    try:
        obj = json.loads(os.environ.get("HOLDINGS", "").strip() or "{}") or {}
    except Exception:
        log("! HOLDINGS 不是正確的 JSON")
        return {}
    out = {}
    for c, h in obj.items():
        if not isinstance(h, dict):
            continue
        target, stop = num(h.get("target")), None   # 只通知目標價
        if target:
            out[str(c)] = {"name": h.get("name") or "", "cost": num(h.get("cost")), "shares": num(h.get("shares")) or 0,
                           "target": target, "stop": stop}
    return out


def market_of():
    m = {}
    for s in (load("data/latest.json", {}) or {}).get("stocks", []):
        m[s["code"]] = (s.get("name"), s.get("market"))
    for c, a in ((load("data/all_stocks.json", {}) or {}).get("stocks") or {}).items():
        if c not in m and isinstance(a, list) and a:
            m[c] = (a[0], a[1] if len(a) > 1 else None)
    return m


def fetch(codes, mk):
    out = {}
    for i in range(0, len(codes), 50):
        part = codes[i:i + 50]
        q = "|".join(f"{'otc' if (mk.get(c) or (None, None))[1] == '上櫃' else 'tse'}_{c}.tw" for c in part)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={q}&json=1&delay=0&_={int(time.time() * 1000)}"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://mis.twse.com.tw/stock/index.jsp"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        for x in j.get("msgArray") or []:
            out[x.get("c")] = {"d": str(x.get("d") or ""), "t": x.get("t") or "", "z": num(x.get("z")), "y": num(x.get("y")),
                               "h": num(x.get("h")), "l": num(x.get("l")), "n": x.get("n") or "",
                               "b": num(str(x.get("b") or "").split("_")[0]), "a": num(str(x.get("a") or "").split("_")[0])}
    return out


# ── 避免重複通知（只存雜湊，看不出是哪一檔） ──
def key(day, code, kind, price):
    salt = os.environ.get("MAIL_PASS", "") + os.environ.get("MAIL_USER", "")
    return hashlib.sha256(f"{salt}|{day}|{code}|{kind}|{price}".encode()).hexdigest()[:20]


def load_state(day):
    st = load(STATE, {}) or {}
    return set(st.get("sent") or []) if st.get("date") == day else set()


def save_state(day, sent, status=None):
    os.makedirs("data", exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"date": day, "sent": sorted(sent)}, f)
    if status is not None:
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for _ in range(3):
        cmds = [["git", "add", STATE, STATUS] if os.path.exists(STATUS) else ["git", "add", STATE], ["git", "commit", "-q", "-m", "到價通知狀態"], ["git", "pull", "-q", "--rebase"], ["git", "push", "-q"]]
        ok = True
        for c in cmds:
            r = subprocess.run(c, capture_output=True, text=True)
            if r.returncode != 0 and not (c[1] == "commit" and "nothing" in (r.stdout + r.stderr)):
                ok = False
                break
        if ok:
            return
        subprocess.run(["git", "rebase", "--abort"], capture_output=True)
        time.sleep(5)
    log("! 狀態檔提交失敗（不影響通知，只是重新啟動時可能重複寄一次）")


# ── 寄信 ──
def send(hits, url):
    user, pw = os.environ.get("MAIL_USER", "").strip(), os.environ.get("MAIL_PASS", "").replace(" ", "").strip()
    to = [x.strip() for x in (os.environ.get("MAIL_TO") or user).split(",") if x.strip()]
    server, port = os.environ.get("MAIL_SERVER") or "smtp.gmail.com", int(os.environ.get("MAIL_PORT") or 465)
    UP, DOWN = "#d1342f", "#11865a"
    first = hits[0]
    subj = ("🎯 " if first["kind"] == "target" else "⚠ ") + f"{first['name']} {first['code']} " + \
        ("漲到目標價" if first["kind"] == "target" else "跌破停損價") + f" {fnum(first['th'])}（現價 {fnum(first['p'])}）"
    if len(hits) > 1:
        subj += f" 等 {len(hits)} 檔"
    cards, lines = [], []
    for h in hits:
        is_t = h["kind"] == "target"
        color = UP if is_t else DOWN
        chg = (h["p"] - h["y"]) if h["y"] else None
        pct = chg / h["y"] * 100 if chg is not None else None
        pnl = (h["p"] - h["cost"]) * h["shares"] if h["cost"] and h["shares"] else None
        pnl_pct = (h["p"] - h["cost"]) / h["cost"] * 100 if h["cost"] else None
        cc = lambda v: UP if v and v > 0 else DOWN if v and v < 0 else "#8b90a0"
        sg = lambda v, d=2: "—" if v is None else ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(v):,.{d}f}"
        cards.append(f'''<tr><td style="padding:0 0 12px"><table width="100%" cellpadding="0" cellspacing="0" style="border:2px solid {color};border-radius:12px">
<tr><td style="padding:14px 16px">
<div style="font-size:13px;font-weight:700;color:{color}">{("🎯 今天曾漲到目標價（最高 " + fnum(h["h"]) + "，現在已回落）") if h.get("touched") else "🎯 漲到目標價" if is_t else "⚠ 跌破停損價"}　{html.escape(h["t"][:5])}</div>
<div style="font-size:20px;font-weight:900;margin:4px 0 2px">{html.escape(h["name"])} <span style="font-size:13px;color:#8b90a0">{h["code"]}</span></div>
<div style="font-size:26px;font-weight:900;color:{cc(chg)}">{fnum(h["p"])} <span style="font-size:14px">{sg(chg)}（{sg(pct)}%）</span></div>
<table cellpadding="0" cellspacing="0" style="font-size:13px;color:#5b6070;margin-top:8px;line-height:1.8">
<tr><td style="padding-right:14px">{"目標價" if is_t else "停損價"}</td><td><b>{fnum(h["th"])}</b></td></tr>
<tr><td style="padding-right:14px">買進均價</td><td><b>{fnum(h["cost"])}</b>{f'　持有 {h["shares"]:,.0f} 股' if h["shares"] else ""}</td></tr>
<tr><td style="padding-right:14px">未實現損益</td><td><b style="color:{cc(pnl)}">{sg(pnl, 0)} 元（{sg(pnl_pct)}%）</b></td></tr>
<tr><td style="padding-right:14px">今日最高／最低</td><td>{fnum(h["h"])}／{fnum(h["l"])}</td></tr>
</table></td></tr></table></td></tr>''')
        lines.append(f"{'漲到目標價' if is_t else '跌破停損價'}：{h['name']} {h['code']} 現價 {fnum(h['p'])}（{'目標' if is_t else '停損'} {fnum(h['th'])}，買進均價 {fnum(h['cost'])}）")
    body = f'''<!doctype html><html><body style="margin:0;background:#f5f3ee;font-family:'Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif;color:#1b1d22">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f3ee;padding:20px 12px"><tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px">
<tr><td style="padding:0 0 12px;font-size:13px;color:#8b90a0">盤中到價通知 · {now():%m/%d %H:%M}</td></tr>
{"".join(cards)}
<tr><td style="padding:4px 0;font-size:14px"><a href="{url}realtime.html" style="color:#1f3a5f;font-weight:700">打開盤中即時看板 →</a></td></tr>
<tr><td style="padding:14px 0 0;font-size:12px;color:#8b90a0;line-height:1.7">這是你在「我的持股」設定的目標價提醒，每天每檔通知一次；修改目標價後會重新提醒。<br>
價格來自證交所即時報價，每分鐘檢查一次，可能和你看到的有幾十秒差距。本通知不構成投資建議。</td></tr>
</table></td></tr></table></body></html>'''
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subj, formataddr(("到價通知", user)), ", ".join(to)
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
    msg.set_content("\n".join(lines) + f"\n\n{url}realtime.html")
    msg.add_alternative(body, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(server, port, context=ctx, timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(server, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)


def main():
    once = "--once" in sys.argv          # 測試用：只檢查一次
    if not (os.environ.get("MAIL_USER") and os.environ.get("MAIL_PASS")):
        log("• 未設定 MAIL_USER / MAIL_PASS，略過到價通知")
        return
    H = load_holdings()
    if not H:
        log("• 沒有持股設定目標價，結束")
        return
    t = now()
    if t.weekday() >= 5:
        log("• 週末休市")
        return
    day = t.date().isoformat()
    mk, url = market_of(), site_url()
    codes = sorted(H)
    sent = load_state(day)
    log(f"• 監看 {len(codes)} 檔目標價，今天已通知 {len(sent)} 次")
    errors, n_mail, seen_today = 0, 0, False
    last_px = {}                            # 冷門股常常這一分鐘沒有成交（z 是 "-"），沿用上一筆成交價
    status = {"date": day, "started": t.strftime("%H:%M"), "watching": len(codes), "sent_today": len(sent), "mails": 0,
              "last_check": None, "priced": 0, "state": "waiting"}
    save_state(day, sent, status)
    reported = False
    while True:
        t = now()
        if mins(t) >= CLOSE:
            log("• 收盤，結束")
            break
        if mins(t) < OPEN and not once:
            time.sleep(min(60, (OPEN - mins(t)) * 60 - t.second + 1))
            continue
        start = time.time()
        try:
            q = fetch(codes, mk)
            errors = 0
        except Exception as e:  # noqa: BLE001
            errors += 1
            log(f"! 讀取報價失敗（第 {errors} 次）：{type(e).__name__}")
            q = {}
        today = t.strftime("%Y%m%d")
        if any(x["d"] == today for x in q.values()):
            seen_today = True
        elif q and mins(t) >= OPEN + 15 and not seen_today:
            log("• 證交所沒有今天的報價（休市日），結束")
            break
        hits = []
        for c in codes:
            x, h = q.get(c), H[c]
            if not x or x["d"] != today:
                continue
            p = x["z"] or last_px.get(c) or x.get("b") or x.get("a")    # 沒有成交價時用上一筆成交價，再沒有就用委買價
            if x["z"]:
                last_px[c] = x["z"]
            # 今天最高價碰到目標價也算（每分鐘才查一次，中間短暫漲上去又回落時不會漏掉）
            touched = h["target"] and x["h"] and x["h"] >= h["target"]
            if p is None and not touched:
                continue
            for kind, th, hit in (("target", h["target"], h["target"] and ((p and p >= h["target"]) or touched)),
                                  ("stop", h["stop"], h["stop"] and p <= h["stop"])):
                if not hit:
                    continue
                k = key(day, c, kind, th)
                if k in sent:
                    continue
                hits.append({"kind": kind, "code": c, "name": h["name"] or x["n"] or (mk.get(c) or (c,))[0] or c, "th": th, "p": p or x["h"], "touched": bool(kind == "target" and not (p and p >= th)),
                             "y": x["y"], "h": x["h"], "l": x["l"], "t": x["t"], "cost": h["cost"], "shares": h["shares"], "k": k})
        if hits:
            hits.sort(key=lambda z: z["kind"] != "stop")     # 停損排前面
            try:
                send(hits, url)
                n_mail += 1
                sent.update(z["k"] for z in hits)
                status.update(sent_today=len(sent), mails=n_mail, last_check=t.strftime("%H:%M"), last_mail=t.strftime("%H:%M"))
                save_state(day, sent, status)
                log(f"✓ 寄出 1 封通知（{len(hits)} 筆）")
            except smtplib.SMTPAuthenticationError:
                log("✗ 寄信登入失敗：請確認 MAIL_USER 與 MAIL_PASS（Gmail 需使用應用程式密碼）")
                return
            except Exception as e:  # noqa: BLE001
                log(f"! 寄信失敗：{type(e).__name__}，下一分鐘再試")
        status.update(last_check=t.strftime("%H:%M"), priced=sum(1 for c in codes if (q.get(c) or {}).get("d") == today and ((q.get(c) or {}).get("z") or last_px.get(c))),
                      state="running", errors=errors)
        if not reported and q:
            reported = True
            save_state(day, sent, status)      # 第一次成功查價後回報一次，網頁就看得到「正在監看」
        if once:
            break
        if errors >= 30:
            log("✗ 連續 30 分鐘讀不到報價，結束")
            break
        time.sleep(max(5, INTERVAL - (time.time() - start)))
    log(f"• 今天共寄出 {n_mail} 封")
    status.update(state="done", mails=n_mail, sent_today=len(sent))
    save_state(day, sent, status)


if __name__ == "__main__":
    main()
