/* ledger.js — 股票帳本（真實賣出紀錄）
   資料存在這台瀏覽器 localStorage「ledger_v1」：{ v:1, disc:1, sales:[...] }
   每筆賣出：{ id, code, name, date, shares, price, cost, pnl, note, left, at }
     price＝賣出成交價（選填）、cost＝賣出時的買進均價、pnl＝實際損益（以使用者填的為準）、left＝賣出後剩下的股數
   賣出時會同步扣掉「我的持股」（holdings_v1）的股數；全部賣完就移除持股，但股票仍留在追蹤清單。
   開啟雲端同步時，帳本用同一組同步密碼加密存到 data/ledger.enc（見 holdsync.js 的 LedgerSync）。 */
(() => {
  const KEY = "ledger_v1", HKEY = "holdings_v1";
  const get = k => { try { return localStorage.getItem(k); } catch(e){ return null; } };
  const set = (k, v) => { try { localStorage.setItem(k, v); return true; } catch(e){ return false; } };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt = (v, d = 0) => v == null || isNaN(v) ? "—" : Number(v).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d });
  const sgn = (v, d = 0) => v == null || isNaN(v) ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v), d);
  const cls = v => !v ? "lg-flat" : v > 0 ? "lg-up" : "lg-down";
  const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(new Date());
  const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  // ── 資料 ──
  function load(){
    let d; try { d = JSON.parse(get(KEY) || "null"); } catch(e){ d = null; }
    if (!d || typeof d !== "object") d = {};
    if (!Array.isArray(d.sales)) d.sales = [];
    if (!(d.disc > 0 && d.disc <= 1)) d.disc = 1;
    d.v = 1; return d;
  }
  function save(d){
    const ok = set(KEY, JSON.stringify(d));
    if (window.LedgerSync) LedgerSync.markChanged();
    emit(); return ok;
  }
  const loadH = () => { try { return JSON.parse(get(HKEY) || "{}") || {}; } catch(e){ return {}; } };
  function saveH(h){ set(HKEY, JSON.stringify(h)); if (window.HoldSync) HoldSync.markChanged(); }
  const listeners = [];
  const emit = () => listeners.forEach(f => { try { f(); } catch(e){} });

  // ── 手續費估算（券商實際金額可能因折扣、零股不同；以使用者填的實際損益為準） ──
  const isETF = c => /^00/.test(c || "");
  const feeOf = (amt, disc) => amt > 0 ? Math.max(20, Math.floor(amt * 0.001425 * (disc || 1))) : 0;
  const taxOf = (c, amt) => amt > 0 ? Math.floor(amt * (isETF(c) ? 0.001 : 0.003)) : 0;
  function estimate(code, shares, price, cost, disc){
    if (!(shares > 0) || !(price > 0) || !(cost > 0)) return null;
    const sellAmt = price * shares, buyAmt = cost * shares;
    const sFee = feeOf(sellAmt, disc), tax = taxOf(code, sellAmt), bFee = feeOf(buyAmt, disc);
    return { sellAmt, buyAmt, sFee, tax, bFee, pnl: Math.round(sellAmt - sFee - tax - buyAmt - bFee) };
  }

  // ── 樣式（用各頁自己的顏色變數，深淺色都會跟著變） ──
  function injectCSS(){
    if (document.getElementById("lgCSS")) return;
    const st = document.createElement("style"); st.id = "lgCSS";
    st.textContent = `
.lg-bg{position:fixed;inset:0;background:rgba(20,14,10,.48);z-index:90;display:none;align-items:flex-start;justify-content:center;padding:6vh 16px 16px;overflow-y:auto}
.lg-bg.show{display:flex}
.lg-m{background:var(--panel);color:var(--ink);border:2px solid var(--line);border-radius:22px;width:100%;max-width:560px;box-shadow:0 20px 60px rgba(0,0,0,.28);font-family:"Noto Sans TC",system-ui,sans-serif}
.lg-m header{display:flex;align-items:center;padding:14px 18px;border-bottom:2px solid var(--line-2)}
.lg-m header h3{margin:0;font-size:17px;flex:1;font-weight:900}
.lg-m .lg-x{border:0;background:none;color:var(--ink-3);font-size:24px;cursor:pointer;line-height:1;width:32px;height:32px;border-radius:8px}
.lg-m .lg-body{padding:14px 18px 6px;font-size:14px;color:var(--ink-2)}
.lg-info{padding:10px 12px;border-radius:14px;background:var(--line-2);font-size:13.5px;line-height:1.7}
.lg-info b{color:var(--ink)}
.lg-num{font-family:"Nunito","IBM Plex Mono","Noto Sans TC",system-ui,sans-serif;font-variant-numeric:tabular-nums}
.lg-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}
.lg-f{display:block;font-size:13px;font-weight:700;color:var(--ink-2);margin-top:10px}
.lg-f small{font-weight:500;color:var(--ink-3)}
.lg-f input,.lg-f textarea{display:block;width:100%;font:inherit;font-size:14.5px;padding:8px 12px;border-radius:12px;border:2px solid var(--line);background:var(--bg);color:var(--ink);margin-top:5px;font-variant-numeric:tabular-nums}
.lg-f input:focus,.lg-f textarea:focus{outline:none;border-color:var(--accent)}
.lg-f textarea{resize:vertical}
.lg-q{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.lg-q button,.lg-use{font:inherit;font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;border:1.5px solid var(--line);background:var(--panel);color:var(--ink-2);cursor:pointer}
.lg-q button:hover,.lg-use:hover{border-color:var(--ink-3);color:var(--ink)}
.lg-pnl input{font-size:17px;font-weight:800}
.lg-calc{margin-top:12px;padding:10px 12px;border-radius:14px;background:var(--line-2);font-size:12.5px;line-height:1.8}
.lg-calc:empty{display:none}
.lg-calc b{color:var(--ink)}
.lg-left{margin-top:10px;font-size:13px;font-weight:700;padding:8px 12px;border-radius:12px;border:2px dashed var(--line)}
.lg-m footer{display:flex;gap:8px;justify-content:flex-end;align-items:center;padding:12px 18px 16px;flex-wrap:wrap}
.lg-msg{flex:1;font-size:13px;font-weight:700;color:var(--up);min-width:140px}
.lg-btn{font:inherit;font-size:13.5px;height:36px;padding:0 16px;border-radius:999px;border:2px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer;font-weight:700}
.lg-btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink,var(--panel))}
.lg-btn:disabled{opacity:.5;cursor:default}
.lg-up{color:var(--up)} .lg-down{color:var(--down)} .lg-flat{color:var(--ink-3)}
.lg-toast{position:fixed;left:50%;bottom:max(20px,env(safe-area-inset-bottom));z-index:95;transform:translate(-50%,20px);opacity:0;pointer-events:none;transition:opacity .25s,transform .25s;
  display:flex;align-items:center;gap:12px;flex-wrap:wrap;max-width:calc(100vw - 32px);padding:10px 12px 10px 16px;border-radius:16px;background:var(--panel);color:var(--ink);border:2px solid var(--down);box-shadow:0 10px 30px rgba(0,0,0,.2);font-size:13.5px;font-weight:700;font-family:"Noto Sans TC",system-ui,sans-serif}
.lg-toast.show{opacity:1;transform:translate(-50%,0);pointer-events:auto}
.lg-toast a{color:var(--accent);white-space:nowrap}
.lg-toast button{border:0;background:none;color:var(--ink-3);font-size:18px;cursor:pointer;line-height:1}
@media (max-width:520px){.lg-grid{grid-template-columns:1fr}}`;
    document.head.appendChild(st);
  }

  // ── 表單視窗 ──
  // mode: "sell"（從持股賣出，會扣持股）／"add"（補記一筆過去的賣出，不動持股）／"edit"（修改帳本紀錄，不動持股）
  let el = null, ctx = null;
  function build(){
    injectCSS();
    el = document.createElement("div");
    el.className = "lg-bg"; el.id = "lgBg";
    el.innerHTML = `<div class="lg-m" role="dialog" aria-modal="true" aria-labelledby="lgTitle">
      <header><h3 id="lgTitle">賣出</h3><button class="lg-x" data-lgclose aria-label="關閉">×</button></header>
      <div class="lg-body">
        <div class="lg-info" id="lgInfo"></div>
        <label class="lg-f" id="lgCodeW">股票代號或名稱<input id="lgCode" list="lgList" placeholder="例如 2330 或 台積電" autocomplete="off"><datalist id="lgList"></datalist></label>
        <div class="lg-grid">
          <label class="lg-f">賣出日期<input type="date" id="lgDate"></label>
          <label class="lg-f">賣出股數 <small id="lgMax"></small><input type="number" id="lgShares" step="1" min="1" inputmode="numeric"></label>
        </div>
        <div class="lg-q" id="lgQuick"></div>
        <div class="lg-grid">
          <label class="lg-f">成交價（元）<small>選填，用來估算損益</small><input type="number" id="lgPrice" step="0.01" min="0" inputmode="decimal"></label>
          <label class="lg-f">買進均價（元）<input type="number" id="lgCost" step="0.01" min="0" inputmode="decimal"></label>
        </div>
        <label class="lg-f lg-pnl">實際損益金額（元）<small>賺填正數、賠填負數，例如 -3200</small><input type="number" id="lgPnl" step="1" inputmode="numeric" placeholder="請以券商 App 的實際金額為準"></label>
        <div class="lg-q"><button type="button" class="lg-use" id="lgUse" hidden>改用估算值</button></div>
        <label class="lg-f">備註 <small>選填，例如：跌破停損照計畫賣</small><textarea id="lgNote" rows="2" maxlength="300"></textarea></label>
        <div class="lg-calc" id="lgCalc"></div>
        <div class="lg-left" id="lgLeft" hidden></div>
      </div>
      <footer><span class="lg-msg" id="lgMsg"></span><button class="lg-btn" data-lgclose>取消</button><button class="lg-btn pri" id="lgOk">確定賣出</button></footer>
    </div>`;
    document.body.appendChild(el);
    const $ = s => el.querySelector(s);
    el.addEventListener("click", e => { if (e.target === el || e.target.closest("[data-lgclose]")) close(); });
    addEventListener("keydown", e => { if (e.key === "Escape" && el.classList.contains("show")) close(); });
    $("#lgQuick").addEventListener("click", e => { const b = e.target.closest("[data-sh]"); if (b){ $("#lgShares").value = b.dataset.sh; recalc(); } });
    ["#lgShares", "#lgPrice", "#lgCost"].forEach(s => $(s).addEventListener("input", recalc));
    $("#lgPnl").addEventListener("input", () => { ctx.pnlDirty = $("#lgPnl").value !== ""; recalc(); });
    $("#lgUse").addEventListener("click", () => { ctx.pnlDirty = false; recalc(); });
    $("#lgCode").addEventListener("input", onCode);
    $("#lgCode").addEventListener("change", onCode);
    $("#lgOk").addEventListener("click", submit);
  }
  const q = s => el.querySelector(s);
  function close(){ el.classList.remove("show"); ctx = null; }

  function parseCode(v){
    v = String(v || "").trim(); const m = v.match(/^([0-9]{4,6}[A-Z]?)\b/);
    if (m) return m[1];
    const all = (ctx && ctx.stocks) || {};
    const hit = Object.entries(all).find(([c, a]) => (a[0] || a) === v);
    return hit ? hit[0] : "";
  }
  function onCode(){
    const all = ctx.stocks || {}, v = q("#lgCode").value.trim();
    if (v.length >= 1){
      const list = Object.entries(all).filter(([c, a]) => c.startsWith(v) || String(a[0] || a).includes(v)).slice(0, 30);
      q("#lgList").innerHTML = list.map(([c, a]) => `<option value="${esc(c)} ${esc(a[0] || a)}">`).join("");
    }
    const code = parseCode(v);
    ctx.code = code; ctx.name = code ? ((all[code] || [])[0] || all[code] || "") : "";
    if (typeof ctx.name !== "string") ctx.name = "";
    q("#lgInfo").innerHTML = code ? `<b>${esc(ctx.name || code)}</b> <span class="lg-num">${esc(code)}</span>　補記一筆過去的賣出，不會改動「我的持股」。` : "補記一筆過去的賣出（例如開始用這個網站之前賣的），不會改動「我的持股」。";
    recalc();
  }

  function recalc(){
    if (!ctx) return;
    const sh = parseInt(q("#lgShares").value, 10), p = parseFloat(q("#lgPrice").value), c = parseFloat(q("#lgCost").value);
    const est = estimate(ctx.code, sh, p, c, load().disc);
    ctx.est = est;
    if (est && !ctx.pnlDirty) q("#lgPnl").value = est.pnl;
    q("#lgUse").hidden = !(est && ctx.pnlDirty && +q("#lgPnl").value !== est.pnl);
    const pnl = parseFloat(q("#lgPnl").value), base = c > 0 && sh > 0 ? c * sh : null;
    let h = "";
    if (est) h += `估算：賣出 <b class="lg-num">${fmt(Math.round(est.sellAmt))}</b> − 手續費 <b class="lg-num">${fmt(est.sFee)}</b> − 證交稅 <b class="lg-num">${fmt(est.tax)}</b> − 買進成本 <b class="lg-num">${fmt(Math.round(est.buyAmt))}</b> − 買進手續費 <b class="lg-num">${fmt(est.bFee)}</b> ＝ <b class="lg-num ${cls(est.pnl)}">${sgn(est.pnl)}</b> 元<br>`;
    if (!isNaN(pnl) && base) h += `這筆損益 <b class="lg-num ${cls(pnl)}">${sgn(pnl)} 元</b>（報酬率 <b class="lg-num ${cls(pnl)}">${sgn(pnl / base * 100, 2)}%</b>）`;
    else if (!est) h += `填入成交價和買進均價，會幫你估算損益；也可以直接填券商 App 上的實際損益。`;
    if (est) h += `<br><span style="color:var(--ink-3)">估算以手續費 0.1425%${load().disc < 1 ? "×" + load().disc + " 折" : ""}（最低 20 元）、證交稅 ${isETF(ctx.code) ? "0.1%（ETF）" : "0.3%"} 計算；請以券商對帳單的實際金額為準。</span>`;
    q("#lgCalc").innerHTML = h;
    if (ctx.mode === "sell"){
      const left = ctx.hold.shares - (sh || 0);
      q("#lgLeft").hidden = false;
      q("#lgLeft").innerHTML = !(sh > 0) ? "" : sh > ctx.hold.shares ? `<span class="lg-up">賣出股數超過持有的 ${fmt(ctx.hold.shares)} 股</span>`
        : left === 0 ? `賣完後：<b>全部賣出</b>，會從「我的持股」移除；這檔仍留在追蹤清單，可以繼續觀察。`
        : `賣完後：還剩 <b class="lg-num">${fmt(left)}</b> 股${left % 1000 === 0 ? `（${fmt(left / 1000)} 張）` : ""}，買進均價維持 <b class="lg-num">${fmt(ctx.hold.cost, 2)}</b> 元，停損價不變。`;
    } else q("#lgLeft").hidden = true;
  }

  /* 開啟視窗
     opts.mode   "sell" | "add" | "edit"
     opts.code / opts.name / opts.price（最新價，sell 模式預填）
     opts.rec    edit 模式要修改的紀錄
     opts.stocks add 模式的代號清單 { code: [name, market] }
     opts.onDone(result) 完成後呼叫 */
  function open(opts){
    if (!el) build();
    const mode = opts.mode || "sell";
    ctx = { mode, onDone: opts.onDone, code: opts.code || "", name: opts.name || "", stocks: opts.stocks || {}, pnlDirty: false };
    q("#lgMsg").textContent = ""; q("#lgOk").disabled = false;
    q("#lgCodeW").hidden = mode !== "add";
    q("#lgDate").max = today();
    if (mode === "sell"){
      const h = loadH()[ctx.code];
      if (!h || !(h.shares > 0)){ alert("找不到這檔的持股資料。請先在「我的持股」記錄買進價格與股數。"); ctx = null; return; }
      ctx.hold = h; ctx.name = ctx.name || h.name || ctx.code;
      q("#lgTitle").textContent = "賣出 · 記到股票帳本";
      q("#lgInfo").innerHTML = `<b>${esc(ctx.name)}</b> <span class="lg-num">${esc(ctx.code)}</span>　目前持有 <b class="lg-num">${fmt(h.shares)}</b> 股，買進均價 <b class="lg-num">${fmt(h.cost, 2)}</b> 元${opts.price ? `，最新價 <b class="lg-num">${fmt(opts.price, 2)}</b>` : ""}`;
      q("#lgDate").value = today();
      q("#lgShares").value = h.shares; q("#lgShares").max = h.shares;
      q("#lgMax").textContent = `（最多 ${fmt(h.shares)} 股）`;
      const qs = [[h.shares, "全部賣出"]];
      if (h.shares >= 2000 && h.shares % 2000 === 0) qs.push([h.shares / 2, "賣一半"]);
      if (h.shares > 1000) qs.push([1000, "賣 1 張"]);
      q("#lgQuick").innerHTML = qs.map(([n, t]) => `<button type="button" data-sh="${n}">${t}</button>`).join("");
      q("#lgPrice").value = opts.price || ""; q("#lgCost").value = h.cost;
      q("#lgPnl").value = ""; q("#lgNote").value = "";
      q("#lgOk").textContent = "確定賣出";
    } else if (mode === "add"){
      q("#lgTitle").textContent = "補記一筆賣出";
      q("#lgCode").value = ""; q("#lgDate").value = today(); q("#lgShares").value = "1000"; q("#lgShares").removeAttribute("max");
      q("#lgMax").textContent = ""; q("#lgQuick").innerHTML = "";
      q("#lgPrice").value = ""; q("#lgCost").value = ""; q("#lgPnl").value = ""; q("#lgNote").value = "";
      q("#lgOk").textContent = "加入帳本";
      onCode();
    } else {
      const r = opts.rec; ctx.rec = r; ctx.code = r.code; ctx.name = r.name; ctx.pnlDirty = true;
      q("#lgTitle").textContent = "修改帳本紀錄";
      q("#lgInfo").innerHTML = `<b>${esc(r.name || r.code)}</b> <span class="lg-num">${esc(r.code)}</span>　修改這筆紀錄不會改動「我的持股」。`;
      q("#lgDate").value = r.date; q("#lgShares").value = r.shares; q("#lgShares").removeAttribute("max");
      q("#lgMax").textContent = ""; q("#lgQuick").innerHTML = "";
      q("#lgPrice").value = r.price || ""; q("#lgCost").value = r.cost || ""; q("#lgPnl").value = r.pnl; q("#lgNote").value = r.note || "";
      q("#lgOk").textContent = "儲存";
    }
    recalc();
    el.classList.add("show");
    setTimeout(() => (mode === "add" ? q("#lgCode") : q("#lgShares")).focus(), 50);
  }

  async function submit(){
    const m = q("#lgMsg"), mode = ctx.mode;
    const date = q("#lgDate").value, sh = parseInt(q("#lgShares").value, 10), p = parseFloat(q("#lgPrice").value), c = parseFloat(q("#lgCost").value);
    const pnlS = q("#lgPnl").value.trim(), pnl = parseFloat(pnlS), note = q("#lgNote").value.trim().slice(0, 300);
    if (mode === "add" && !ctx.code) return m.textContent = "請輸入股票代號";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date > today()) return m.textContent = "請選擇今天或之前的日期";
    if (!(sh > 0)) return m.textContent = "請輸入賣出股數";
    if (mode === "sell" && sh > ctx.hold.shares) return m.textContent = `最多只能賣 ${fmt(ctx.hold.shares)} 股`;
    if (pnlS === "" || isNaN(pnl)) return m.textContent = "請填入實際損益金額（賠錢填負數）";
    if (mode !== "sell" && !(c > 0)) return m.textContent = "請輸入買進均價（用來算報酬率）";
    q("#lgOk").disabled = true; m.style.color = "var(--ink-3)"; m.textContent = "儲存中…";
    try {
      // 先把雲端最新的帳本、持股拉下來，避免覆蓋其他電腦剛記的紀錄
      if (window.LedgerSync && LedgerSync.enabled()) await LedgerSync.sync({ silent: true }).catch(() => {});
      if (mode === "sell" && window.HoldSync && HoldSync.enabled()) await HoldSync.sync({ silent: true }).catch(() => {});
    } finally { m.style.color = ""; m.textContent = ""; }
    const d = load();
    let result = { mode };
    if (mode === "sell"){
      const H = loadH(), h = H[ctx.code];
      if (!h || sh > h.shares){ q("#lgOk").disabled = false; ctx.hold = h || { shares: 0 }; recalc(); return m.textContent = h ? `持股已在其他裝置變更，最多只能賣 ${fmt(h.shares)} 股` : "這檔持股已在其他裝置被移除"; }
      const left = h.shares - sh;
      const rec = { id: newId(), code: ctx.code, name: ctx.name || h.name || "", date, shares: sh, price: p > 0 ? p : null, cost: c > 0 ? c : h.cost, pnl: Math.round(pnl), note, left, at: new Date().toISOString() };
      d.sales.push(rec); save(d);
      if (left > 0) H[ctx.code] = { ...h, shares: left }; else delete H[ctx.code];
      saveH(H);
      result = { mode, rec, left, soldAll: left === 0 };
    } else if (mode === "add"){
      const rec = { id: newId(), code: ctx.code, name: ctx.name || "", date, shares: sh, price: p > 0 ? p : null, cost: c, pnl: Math.round(pnl), note, left: null, at: new Date().toISOString() };
      d.sales.push(rec); save(d); result = { mode, rec };
    } else {
      const r = d.sales.find(x => x.id === ctx.rec.id);
      if (r){ Object.assign(r, { date, shares: sh, price: p > 0 ? p : null, cost: c, pnl: Math.round(pnl), note }); save(d); }
      result = { mode, rec: r };
    }
    const done = ctx.onDone;
    close();
    if (done) try { done(result); } catch(e){}
  }

  /* 刪除帳本紀錄；restore=true 時把股數加回「我的持股」（買進均價以加權平均合併） */
  function remove(id, restore){
    const d = load(), r = d.sales.find(x => x.id === id); if (!r) return;
    d.sales = d.sales.filter(x => x !== r); save(d);
    if (restore){
      const H = loadH(), h = H[r.code];
      if (h && h.shares > 0) H[r.code] = { ...h, cost: Math.round((h.cost * h.shares + r.cost * r.shares) / (h.shares + r.shares) * 100) / 100, shares: h.shares + r.shares };
      else H[r.code] = { cost: r.cost, shares: r.shares, stop: null, name: r.name || "" };
      saveH(H);
    }
  }

  /* 右下角小提示（附「看股票帳本」連結） */
  let toastT = null;
  function toast(html){
    injectCSS();
    let t = document.getElementById("lgToast");
    if (!t){ t = document.createElement("div"); t.id = "lgToast"; t.className = "lg-toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    const inFrame = window.top !== window;
    t.innerHTML = `<span>✓ ${html}</span><a href="ledger.html"${inFrame ? ' target="_top"' : ""}>看股票帳本 →</a><button type="button" aria-label="關閉">×</button>`;
    t.querySelector("button").onclick = () => t.classList.remove("show");
    requestAnimationFrame(() => t.classList.add("show"));
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 7000);
  }

  addEventListener("storage", e => { if (e.key === KEY) emit(); });

  window.Ledger = {
    KEY, load, save, remove, estimate, toast, onChange: f => listeners.push(f),
    sell: (code, o = {}) => open({ ...o, mode: "sell", code }),
    add: (o = {}) => open({ ...o, mode: "add" }),
    edit: (rec, o = {}) => open({ ...o, mode: "edit", rec }),
  };
})();
