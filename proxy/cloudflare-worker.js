// 盤中即時看板 — Cloudflare Worker 轉送站（免費方案即可）  版本 3：加上「準時觸發 GitHub 更新」
// 版本 3：Cloudflare 的定時器（Cron Trigger，每 5 分鐘）會在下面 SCHEDULE 的時間，請 GitHub 立刻執行更新。
//         GitHub 自己的排程常延遲好幾個小時，Cloudflare 的定時器很準時。
//         需要在 Worker 的 Settings → Variables and Secrets 加一個 Secret「GH_TOKEN」（GitHub 金鑰，Actions 權限 Read and write）。
// 用途：瀏覽器不能直接讀證交所的即時報價（沒有 CORS），由這支程式代為轉送。
// 只允許兩種請求：證交所即時報價、Yahoo 當日分時線；只接受下面列出的網站呼叫。

const ALLOW_ORIGINS = [
  "https://dorigo.github.io",   // 你的 GitHub Pages 網址（換帳號時改這裡）
];

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36";

// ── 準時更新的時間表（台北時間，必須是 5 的倍數分鐘）──
const REPO = "dorigo/stock_watch";        // 你的 GitHub repo（換帳號時改這裡）
const SCHEDULE = [
  // [時:分, 星期幾（0=日…6=六）, workflow 檔名, 模式]
  ["06:25", [2, 3, 4, 5, 6], "update.yml", "market"],   // 補前一晚美股收盤
  ["08:05", [2, 3, 4, 5, 6], "update.yml", "market"],   // 再補一次（開盤前一定要有）
  ["08:45", [1, 2, 3, 4, 5], "alert.yml", null],        // 啟動盤中到價通知
  ["13:50", [1, 2, 3, 4, 5], "update.yml", "market"],   // 台股收盤指數
  ["17:20", [1, 2, 3, 4, 5], "update.yml", "full"],     // 收盤、三大法人
  ["19:40", [1, 2, 3, 4, 5], "update.yml", "full"],     // 備援
  ["22:10", [1, 2, 3, 4, 5], "update.yml", "full"],     // 補融資餘額
];

async function runSchedule(env, now = new Date()) {
  const tw = new Date(now.getTime() + 8 * 3600e3);
  const hm = String(tw.getUTCHours()).padStart(2, "0") + ":" + String(Math.floor(tw.getUTCMinutes() / 5) * 5).padStart(2, "0");
  const due = SCHEDULE.filter(([t, days]) => t === hm && days.includes(tw.getUTCDay()));
  const out = [];
  for (const [, , wf, mode] of due) out.push(await dispatch(env, wf, mode));
  return { time: hm, ran: out };
}

async function dispatch(env, wf, mode) {
  if (!env.GH_TOKEN) return { wf, ok: false, error: "沒有設定 GH_TOKEN" };
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${wf}/dispatches`, {
    method: "POST",
    headers: { "Authorization": "Bearer " + env.GH_TOKEN, "Accept": "application/vnd.github+json", "User-Agent": "stock-watch-worker",
               "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", ...(mode ? { inputs: { mode } } : {}) }),
  });
  return { wf, mode, ok: r.status === 204, status: r.status, error: r.status === 204 ? undefined : (await r.text()).slice(0, 200) };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runSchedule(env, new Date(event.scheduledTime)));
  },

  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allow = ALLOW_ORIGINS.includes(origin) ? origin : ALLOW_ORIGINS[0];
    const cors = {
      "Access-Control-Allow-Origin": allow,
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Vary": "Origin",
      "Cache-Control": "no-store",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    const url = new URL(request.url);
    const src = url.searchParams.get("src");
    let target, headers = { "User-Agent": UA, "Accept": "application/json" };

    if (src === "ping") {
      return json({ ok: true, from: "cloudflare-worker", v: 3, timer: !!(env && env.GH_TOKEN) }, cors);
    } else if (src === "trigger-test") {
      // 測試用：立刻請 GitHub 跑一次「只更新大盤」，確認金鑰設定正確
      return json(await dispatch(env || {}, "update.yml", "market"), cors);
    } else if (src === "mis") {
      const q = url.searchParams.get("q") || "";
      if (!/^(?:(?:tse|otc)_[0-9A-Za-z]{2,8}\.tw)(?:\|(?:tse|otc)_[0-9A-Za-z]{2,8}\.tw){0,79}$/.test(q))
        return json({ error: "bad q" }, cors, 400);
      target = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${q}&json=1&delay=0&_=${Date.now()}`;
      headers["Referer"] = "https://mis.twse.com.tw/stock/index.jsp";
    } else if (src === "yh") {
      const s = url.searchParams.get("s") || "";
      if (!/^(?:[0-9A-Z]{4,6}\.TWO?|\^TWII)$/.test(s)) return json({ error: "bad s" }, cors, 400);
      target = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(s)}?range=1d&interval=1m`;
    } else {
      return json({ error: "unknown src" }, cors, 400);
    }

    try {
      const r = await fetch(target, { headers, cf: { cacheTtl: 0 } });
      const body = (await r.text()).trim();
      return new Response(body, { status: r.status, headers: { ...cors, "Content-Type": "application/json; charset=utf-8" } });
    } catch (e) {
      return json({ error: String(e) }, cors, 502);
    }
  },
};

function json(obj, cors, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { ...cors, "Content-Type": "application/json; charset=utf-8" } });
}
