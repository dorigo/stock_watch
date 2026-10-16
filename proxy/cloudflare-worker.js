// 盤中即時看板 — Cloudflare Worker 轉送站（免費方案即可）  版本 2：支援大盤與類股指數
// 用途：瀏覽器不能直接讀證交所的即時報價（沒有 CORS），由這支程式代為轉送。
// 只允許兩種請求：證交所即時報價、Yahoo 當日分時線；只接受下面列出的網站呼叫。

const ALLOW_ORIGINS = [
  "https://dorigo.github.io",   // 你的 GitHub Pages 網址（換帳號時改這裡）
];

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36";

export default {
  async fetch(request) {
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
      return json({ ok: true, from: "cloudflare-worker", v: 2 }, cors);
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
