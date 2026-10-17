#!/usr/bin/env python3
"""每日收盤 Email 日報。

讀取 data/latest.json、data/market.json，以及（選填）GitHub Secret HOLDINGS 裡的持股，
整理成一封 HTML 信寄出。只用 Python 標準函式庫。

環境變數（在 repo 的 Settings → Secrets and variables → Actions 設定）：
  MAIL_USER   寄件帳號（例如 Gmail 地址）            必填
  MAIL_PASS   寄件密碼（Gmail 請用「應用程式密碼」）   必填
  MAIL_TO     收件人，多個用逗號分隔（預設寄給 MAIL_USER）
  MAIL_SERVER SMTP 主機（預設 smtp.gmail.com）
  MAIL_PORT   SMTP 連接埠（預設 465，SSL）
  HOLDINGS    網站「我的持股 → 匯出」下載的 holdings.json 內容（選填）
  SITE_URL    網站網址（預設依 GITHUB_REPOSITORY 推算）

參數：
  --force  不管今天寄過沒有都寄（測試用）
"""
import datetime as dt
import html
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE = DATA / "notify.json"
TPE = dt.timezone(dt.timedelta(hours=8))
NEAR_STOP = 3.0        # 距停損小於這個百分比就提醒
EX_DAYS = 7            # 幾天內要除權息就提醒

UP, DOWN, FLAT = "#d6453d", "#1f8f63", "#9a8676"
INK, INK2, INK3, LINE, BG, PANEL = "#4a3428", "#7d6252", "#a8907f", "#f0dcc8", "#fbf5ee", "#fffaf4"


def log(*a):
    print(*a, flush=True)


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text("utf-8"))
    except Exception:
        return default


def esc(s):
    return html.escape(str(s if s is not None else ""))


def fnum(v, d=0):
    if v is None:
        return "—"
    return f"{v:,.{d}f}"


def sgn(v, d=0):
    if v is None:
        return "—"
    s = f"{abs(v):,.{d}f}"
    return ("+" if v > 0 else "−" if v < 0 else "") + s


def col(v):
    return FLAT if not v else UP if v > 0 else DOWN


def colored(v, text=None, bold=False):
    t = text if text is not None else sgn(v, 2) + "%"
    w = "font-weight:700;" if bold else ""
    return f'<span style="color:{col(v)};{w}">{esc(t)}</span>'


def md(d):
    return d[5:].replace("-", "/") if d else ""


def weekday(d):
    return "一二三四五六日"[dt.date.fromisoformat(d).weekday()]


def site_url():
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "dorigo/stock_watch")
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def load_holdings():
    raw = os.environ.get("HOLDINGS", "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except Exception as e:
        log(f"! HOLDINGS 不是正確的 JSON：{e}")
        return {}
    out = {}
    for c, h in (obj or {}).items():
        try:
            cost, shares = float(h.get("cost") or 0), float(h.get("shares") or 0)
            stop = float(h.get("stop") or 0) or None
        except Exception:
            continue
        if cost > 0 and shares > 0:
            out[str(c)] = {"cost": cost, "shares": shares, "stop": stop, "name": h.get("name") or ""}
    return out


# ───────────────────────── 內容 ─────────────────────────
def section(title, body, note=""):
    n = f'<div style="font-size:12px;color:{INK3};margin-top:2px">{note}</div>' if note else ""
    return f'''<tr><td style="padding:18px 20px 4px">
      <div style="font-size:16px;font-weight:800;color:{INK}">{title}</div>{n}
      <div style="margin-top:10px">{body}</div></td></tr>'''


def table(head, rows, align=None):
    align = align or ["left"] + ["right"] * (len(head) - 1)
    th = "".join(f'<th style="text-align:{a};font-size:12px;color:{INK3};font-weight:700;padding:6px 8px;border-bottom:2px solid {LINE};white-space:nowrap">{h}</th>'
                 for h, a in zip(head, align))
    trs = ""
    for r in rows:
        trs += "<tr>" + "".join(f'<td style="text-align:{a};padding:7px 8px;border-bottom:1px solid {LINE};font-size:14px;white-space:nowrap">{c}</td>'
                                for c, a in zip(r, align)) + "</tr>"
    return f'<table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">{th and "<tr>" + th + "</tr>"}{trs}</table>'


def bullet_list(items, color):
    if not items:
        return ""
    lis = "".join(f'<li style="margin:4px 0">{it}</li>' for it in items)
    return f'<ul style="margin:0;padding-left:20px;font-size:14px;color:{INK};border-left:4px solid {color};padding:6px 10px 6px 26px;background:{PANEL};border-radius:10px">{lis}</ul>'


def market_block(m):
    out = ""
    for key, label in (("tw", "台股"), ("us", "美股")):
        g = (m or {}).get(key)
        if not g or not g.get("items"):
            continue
        cells = ""
        for i, x in enumerate(g["items"]):
            arrow = "▲" if x["chg"] > 0 else "▼" if x["chg"] < 0 else ""
            cells += f'''<td style="padding:6px;width:25%;vertical-align:top">
              <div style="background:{PANEL};border-radius:12px;padding:8px 10px;border:1px solid {LINE}">
                <div style="font-size:12px;color:{INK2};font-weight:700">{esc(x["name"])}</div>
                <div style="font-size:17px;font-weight:800;color:{INK}">{fnum(x["close"], 2)}</div>
                <div style="font-size:12px;font-weight:700;color:{col(x["chg"])}">{arrow}{fnum(abs(x["chg"]), 2)} ({sgn(x["pct"], 2)}%)</div>
              </div></td>'''
            if i % 4 == 3:
                cells += "</tr><tr>"
        out += f'''<div style="font-size:13px;font-weight:800;color:{INK2};margin:6px 0 2px">{label} · {md(g.get("date"))}（{weekday(g["date"])}）收盤</div>
          <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse"><tr>{cells}</tr></table>'''
    return out


def flags(s):
    """會持續好幾天的狀態，用來判斷是不是「今天才新出現」。"""
    out, hist = set(), s.get("hist") or []
    if len(hist) >= 3 and all((x.get("i") or 0) < 0 for x in hist[-3:]) and (s.get("marginChg") or 0) > 0:
        out.add("sell3")
    tags = s.get("tags") or []
    if "分數大減" in tags:
        out.add("drop")
    if "分數大增" in tags:
        out.add("jump")
    return out


def prev_flags(date):
    files = sorted(p for p in (DATA / "report").glob("*.json") if p.stem < date)
    prev = load(files[-1]) if files else None
    return {s["code"]: flags(s) for s in (prev or {}).get("stocks", [])}


def build(report, market, holdings, url):
    date = report["date"]
    today = dt.date.fromisoformat(date)
    stocks = {s["code"]: s for s in report["stocks"]}
    alerts, goods, infos = [], [], []

    # ── 持股 ──
    hold_rows, tot_cost, tot_val, tot_day, missing = [], 0.0, 0.0, 0.0, []
    stop_hits = 0
    for code, h in holdings.items():
        s = stocks.get(code)
        name = (s or {}).get("name") or h["name"] or code
        if not s or s.get("close") is None:
            missing.append(f"{name}（{code}）")
            continue
        c = s["close"]
        cost, val = h["cost"] * h["shares"], c * h["shares"]
        pnl, pct = val - cost, (val - cost) / cost * 100
        day = (s.get("chg") or 0) * h["shares"]
        tot_cost += cost; tot_val += val; tot_day += day
        to_stop = (c - h["stop"]) / c * 100 if h["stop"] else None
        if h["stop"] and c <= h["stop"]:
            stop_hits += 1
            state = f'<span style="color:#fff;background:{DOWN};border-radius:99px;padding:1px 8px;font-size:12px;font-weight:700">跌破停損</span>'
            alerts.insert(0, f'<b>{esc(name)}</b> 收盤 {fnum(c, 2)}，已跌破停損價 {fnum(h["stop"], 2)}，請依照自己的計畫處理。')
        elif to_stop is not None and to_stop < NEAR_STOP:
            state = f'<span style="color:#b07a10;background:#fff0c9;border-radius:99px;padding:1px 8px;font-size:12px;font-weight:700">接近停損</span>'
            alerts.append(f'<b>{esc(name)}</b> 距離停損價 {fnum(h["stop"], 2)} 只剩 {to_stop:.1f}%。')
        else:
            state = f'<span style="color:{INK3};font-size:12px">{"距停損 " + format(to_stop, ".1f") + "%" if to_stop is not None else "未設停損"}</span>'
        hold_rows.append([
            f'<b>{esc(name)}</b> <span style="color:{INK3};font-size:12px">{esc(code)}</span>',
            fnum(c, 2), colored(s.get("chgPct")), colored(pnl, sgn(pnl)), colored(pct, sgn(pct, 1) + "%", True), state,
        ])
        ex = s.get("nextEx")
        if ex and ex.get("date"):
            dleft = (dt.date.fromisoformat(ex["date"]) - today).days
            if 0 <= dleft <= EX_DAYS:
                get = (ex.get("cash") or 0) * h["shares"]
                infos.append(f'<b>{esc(name)}</b> {md(ex["date"])} 除{"權息" if ex.get("stock") and ex.get("cash") else "權" if ex.get("stock") else "息"}'
                             + (f'，每股 {ex["cash"]} 元，預計可領約 {fnum(get)} 元' if ex.get("cash") else "")
                             + f'（{"今天" if dleft == 0 else f"{dleft} 天後"}，前一個交易日收盤前要持有）。')

    # ── 自選股的變化（只列「今天新發生」的，避免每天重複） ──
    before = prev_flags(date)
    for s in sorted(report["stocks"], key=lambda x: x["code"] not in holdings):
        now_f, old_f = flags(s), before.get(s["code"], set())
        new = now_f - old_f
        name, ma, tags = s["name"], s.get("ma") or {}, s.get("tags") or []
        held = "（持有）" if s["code"] in holdings else ""
        if ma.get("dn20") and ma["dn20"].get("ago") == 0:
            alerts.append(f'<b>{esc(name)}</b>{held} 月線今天跌破季線，中期走勢轉弱。')
        if "sell3" in new:
            alerts.append(f'<b>{esc(name)}</b>{held} 法人連賣 3 天、融資卻增加（回測中少數有參考價值的警示）。')
        if "drop" in new:
            alerts.append(f'<b>{esc(name)}</b>{held} 籌碼分數 5 日內大減 {sgn(s.get("scoreChg5"), 0)} 分。')
        if ma.get("up20") and ma["up20"].get("ago") == 0:
            goods.append(f'<b>{esc(name)}</b>{held} 月線今天突破季線。')
        elif ma.get("up5") and ma["up5"].get("ago") == 0:
            goods.append(f'<b>{esc(name)}</b>{held} 週線今天突破季線。')
        if (s.get("tStreak") or 0) == 5:
            goods.append(f'<b>{esc(name)}</b>{held} 投信連續買超第 5 天。')
        if "jump" in new:
            goods.append(f'<b>{esc(name)}</b>{held} 籌碼分數 5 日內大增 {sgn(s.get("scoreChg5"), 0)} 分。')
        if s["code"] not in holdings and s.get("nextEx") and s["nextEx"].get("date"):
            dleft = (dt.date.fromisoformat(s["nextEx"]["date"]) - today).days
            if 0 <= dleft <= 3:
                infos.append(f'<b>{esc(name)}</b> {md(s["nextEx"]["date"])} 除權息（{"今天" if dleft == 0 else f"{dleft} 天後"}）。')

    # ── 組信 ──
    tw = next((x for x in ((market or {}).get("tw") or {}).get("items", []) if x["id"] == "TAIEX"), None)
    subj = [f"【自選股日報】{md(date)}"]
    if tw:
        subj.append(f"加權 {sgn(tw['pct'], 2)}%")
    if hold_rows:
        subj.append(f"持股今日 {sgn(round(tot_day))}")
    if stop_hits:
        subj.append(f"⚠ {stop_hits} 檔跌破停損")
    elif alerts:
        subj.append(f"{len(alerts)} 則提醒")
    subject = "｜".join(subj)

    parts = []
    if alerts:
        parts.append(section("⚠ 需要注意", bullet_list(alerts, UP)))
    if hold_rows:
        tp = (tot_val - tot_cost) / tot_cost * 100 if tot_cost else 0
        summary = f'''<table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;margin-bottom:8px"><tr>
          <td style="padding:4px;vertical-align:top;width:33%"><div style="background:{PANEL};border:1px solid {LINE};border-radius:12px;padding:8px 12px;min-height:58px"><div style="font-size:12px;color:{INK3};font-weight:700">今日損益</div><div style="font-size:20px;font-weight:800;color:{col(tot_day)}">{sgn(round(tot_day))}</div></div></td>
          <td style="padding:4px;vertical-align:top;width:33%"><div style="background:{PANEL};border:1px solid {LINE};border-radius:12px;padding:8px 12px;min-height:58px"><div style="font-size:12px;color:{INK3};font-weight:700">總損益</div><div style="font-size:20px;font-weight:800;color:{col(tot_val - tot_cost)}">{sgn(round(tot_val - tot_cost))}</div><div style="font-size:12px;color:{col(tp)}">{sgn(tp, 2)}%</div></div></td>
          <td style="padding:4px;vertical-align:top;width:33%"><div style="background:{PANEL};border:1px solid {LINE};border-radius:12px;padding:8px 12px;min-height:58px"><div style="font-size:12px;color:{INK3};font-weight:700">市值</div><div style="font-size:20px;font-weight:800;color:{INK}">{fnum(round(tot_val))}</div></div></td>
        </tr></table>'''
        note = f'不在自選股清單、沒有資料：{"、".join(missing)}' if missing else ""
        parts.append(section("💼 我的持股", summary + table(["股票", "收盤", "今日", "損益", "報酬率", "停損"], hold_rows), note))
    elif not holdings:
        parts.append(section("💼 我的持股", f'<div style="font-size:13px;color:{INK2}">尚未設定持股，所以這封信不會提醒停損。設定方式見網站 README 的「Email 日報」。</div>'))
    if goods:
        parts.append(section("🌱 值得留意的變化", bullet_list(goods, DOWN), "只列出今天新發生的；不是買進建議"))
    if infos:
        parts.append(section("📅 除權息提醒", bullet_list(infos, "#c98a12")))
    mk = market_block(market)
    if mk:
        parts.append(section("🍊 大盤", mk))
    if not (alerts or goods):
        parts.insert(0, section("☕ 今天很平靜", f'<div style="font-size:14px;color:{INK2}">自選股沒有新的警示或突破訊號。</div>'))

    partial = "" if report.get("complete") else f'<div style="font-size:12px;color:#b07a10;margin-top:4px">部分資料（例如融資）尚未公布，數字以網站為準。</div>'
    body = f'''<!doctype html><html><body style="margin:0;padding:0;background:{BG}">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{BG};font-family:'Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif;color:{INK}">
<tr><td align="center" style="padding:20px 10px">
<table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;max-width:640px;background:#fff;border:2px solid {LINE};border-radius:20px">
  <tr><td style="padding:20px 20px 6px">
    <div style="font-size:12px;color:{INK3};font-weight:700;letter-spacing:.1em">自選股籌碼追蹤 · 每日收盤日報</div>
    <div style="font-size:24px;font-weight:900;margin-top:2px">🍊 {md(date)}（{weekday(date)}）收盤</div>{partial}
  </td></tr>
  {"".join(parts)}
  <tr><td style="padding:20px">
    <a href="{esc(url)}" style="display:inline-block;background:#d9733f;color:#fff;text-decoration:none;font-weight:700;padding:10px 18px;border-radius:99px">打開網站看完整資料</a>
    <a href="{esc(url)}realtime.html" style="display:inline-block;color:#d9733f;text-decoration:none;font-weight:700;padding:10px 12px">盤中即時看板 →</a>
    <div style="font-size:11.5px;color:{INK3};margin-top:14px;line-height:1.6">本信為公開資料整理與規則計算，不構成投資建議。
    持股資料來自 GitHub Secret「HOLDINGS」，不會出現在公開的 repo。要停止寄送，刪除 Secret「MAIL_TO」或「MAIL_PASS」即可。</div>
  </td></tr>
</table></td></tr></table></body></html>'''
    text = f"{subject}\n\n" + "\n".join(
        "• " + html.unescape(x).replace("<b>", "").replace("</b>", "") for x in alerts + goods + infos) + f"\n\n{url}"
    return subject, body, text


def send(subject, body_html, body_text):
    user, pw = os.environ.get("MAIL_USER", "").strip(), os.environ.get("MAIL_PASS", "").replace(" ", "").strip()
    to = [x.strip() for x in (os.environ.get("MAIL_TO") or user).split(",") if x.strip()]
    server, port = os.environ.get("MAIL_SERVER") or "smtp.gmail.com", int(os.environ.get("MAIL_PORT") or 465)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, formataddr(("自選股日報", user)), ", ".join(to)
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(server, port, context=ctx, timeout=30) as s:
            s.login(user, pw); s.send_message(msg)
    else:
        with smtplib.SMTP(server, port, timeout=30) as s:
            s.starttls(context=ctx); s.login(user, pw); s.send_message(msg)
    log(f"✓ 已寄出：{subject} → {', '.join(to)}")


def main():
    force = "--force" in sys.argv
    preview = "--preview" in sys.argv
    if not preview and not (os.environ.get("MAIL_USER") and os.environ.get("MAIL_PASS")):
        log("• 未設定 MAIL_USER / MAIL_PASS，略過 Email 日報")
        return
    report = load(DATA / "latest.json")
    if not report or not report.get("stocks"):
        log("! 沒有 data/latest.json，略過 Email 日報")
        return
    st = load(STATE, {}) or {}
    now = dt.datetime.now(TPE)
    if not force:
        if st.get("lastDate") == report["date"]:
            log(f"• {report['date']} 的日報已經寄過，略過")
            return
        if not report.get("complete") and now.hour < 21:
            log("• 資料尚未完整，等晚上的排程再寄")
            return
        if (now.date() - dt.date.fromisoformat(report["date"])).days > 4:
            log("• 資料日期太舊，略過")
            return
    subject, body, text = build(report, load(DATA / "market.json", {}), load_holdings(), site_url())
    if preview:
        Path(sys.argv[sys.argv.index("--preview") + 1]).write_text(body, "utf-8")
        log(subject)
        return
    send(subject, body, text)
    if not force:
        STATE.write_text(json.dumps({"lastDate": report["date"], "sentAt": now.isoformat(timespec="minutes")}, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    try:
        main()
    except smtplib.SMTPAuthenticationError:
        log("✗ 寄信登入失敗：請確認 MAIL_USER 與 MAIL_PASS（Gmail 需使用「應用程式密碼」，不是登入密碼）")
        sys.exit(1)
    except Exception as e:
        log(f"✗ 寄信失敗：{e!r}")
        sys.exit(1)
