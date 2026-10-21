/* motion.js — 小動畫（只在資料改變時動一下）
   1. 數字滾動：元素加 data-tw="唯一鍵"，內容變了就從舊數字滾到新數字（約 0.45 秒）
   2. 線條畫出來：<svg data-draw="唯一鍵"> 裡的 path.draw 第一次出現時由左往右畫出（同一個鍵只畫一次）
   3. 視窗淡入淡出、輕微放大（.modal-bg / .dm-bg / .lg-bg）
   4. 提醒閃一下：.pulse-once（外框閃 3 下就停）
   使用者開啟「減少動態效果」時全部關閉。 */
(() => {
  const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ── CSS ──
  const css = `
html .modal-bg,html .dm-bg,html .lg-bg{display:flex;visibility:hidden;opacity:0;pointer-events:none;transition:opacity .18s ease,visibility 0s linear .18s}
html .modal-bg.show,html .dm-bg.show,html .lg-bg.show{visibility:visible;opacity:1;pointer-events:auto;transition:opacity .18s ease}
html .modal-bg>.modal,html .dm-bg>.dm,html .lg-bg>.lg-m{transform:translateY(10px) scale(.97);transition:transform .24s cubic-bezier(.2,.9,.3,1.15)}
html .modal-bg.show>.modal,html .dm-bg.show>.dm,html .lg-bg.show>.lg-m{transform:none}
@keyframes pulseOnce{0%,100%{box-shadow:0 0 0 0 var(--pulse,rgba(0,0,0,0))}40%{box-shadow:0 0 0 7px var(--pulse,rgba(0,0,0,.15))}}
.pulse-once.pulse-once{animation:pulseOnce .8s ease-out 3}
tr.pulse-once{animation:none}
tr.pulse-once>td{animation:pulseRow .8s ease-out 3;animation-delay:var(--pd,0ms)}
@keyframes pulseRow{40%{background:var(--pulse-bg,rgba(255,200,120,.25))}}
@media (prefers-reduced-motion:reduce){
  html .modal-bg,html .dm-bg,html .lg-bg,html .modal-bg>.modal,html .dm-bg>.dm,html .lg-bg>.lg-m{transition:none}
  .pulse-once,tr.pulse-once>td{animation:none}
}`;
  const st = document.createElement("style"); st.id = "motionCSS"; st.textContent = css;
  (document.head || document.documentElement).appendChild(st);

  // ── 數字滾動 ──
  const prev = new Map();          // key → 上一次的文字
  const busy = new WeakSet();      // 正在滾動中的元素（自己改的文字不要再觸發）
  const RE = /^([^\d−+\-]*)([−+\-]?)([\d,]*\d(?:\.\d+)?)(.*)$/s;
  function parse(t){
    const m = String(t).trim().match(RE); if (!m) return null;
    const dec = (m[3].split(".")[1] || "").length, v = parseFloat(m[3].replace(/,/g, "")) * (m[2] === "−" || m[2] === "-" ? -1 : 1);
    return isFinite(v) ? { pre: m[1], sign: m[2], minus: m[2] === "-" ? "-" : "−", v, dec, comma: m[3].includes(","), post: m[4] } : null;
  }
  function format(p, v){
    const a = Math.abs(v), s = p.comma || a >= 1000 ? a.toLocaleString("en-US", { minimumFractionDigits: p.dec, maximumFractionDigits: p.dec })
      : a.toFixed(p.dec);
    const sign = p.sign === "" ? (v < 0 ? p.minus : "") : v > 0 ? "+" : v < 0 ? p.minus : "";
    return p.pre + sign + s + p.post;
  }
  const active = new Map();        // key → 進行中的滾動（畫面重畫時接續，不會中斷）
  function tween(el, from, to, key, cont){
    const a = cont ? cont.a : parse(from), b = cont ? cont.b : parse(to);
    if (!a || !b || a.v === b.v || el.children.length) return;     // 只處理純文字
    const t0 = cont ? cont.t0 : performance.now(), dur = 450;
    active.set(key, { a, b, to, t0 });
    busy.add(el);
    const step = now => {
      if (!el.isConnected){ busy.delete(el); return; }
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = k >= 1 ? to : format(b, a.v + (b.v - a.v) * e);
      if (k < 1) requestAnimationFrame(step); else { setTimeout(() => busy.delete(el), 0); if (active.get(key) && active.get(key).t0 === t0) active.delete(key); }
    };
    requestAnimationFrame(step);
  }
  function scanNumbers(root){
    const list = root.matches && root.matches("[data-tw]") ? [root] : [];
    if (root.querySelectorAll) list.push(...root.querySelectorAll("[data-tw]"));
    for (const el of list){
      if (busy.has(el)) continue;
      const key = el.dataset.tw, now = el.textContent;
      const old = prev.get(key), run = active.get(key);
      prev.set(key, now);
      if (run && run.to === now && performance.now() - run.t0 < 450){ tween(el, null, now, key, run); continue; }   // 重畫時接續
      if (old != null && old !== now && !reduce()) tween(el, old, now, key);
    }
  }

  // ── 線條畫出來 ──
  const drawn = new Set();
  function scanDraw(root){
    const svgs = root.matches && root.matches("svg[data-draw]") ? [root] : [];
    if (root.querySelectorAll) svgs.push(...root.querySelectorAll("svg[data-draw]"));
    for (const svg of svgs){
      const key = svg.dataset.draw;
      if (drawn.has(key) || !svg.querySelector("path.draw")) continue;
      drawn.add(key);
      if (reduce()) continue;
      svg.querySelectorAll("path.draw").forEach(p => {
        let len = 0; try { len = p.getTotalLength(); } catch(e){}
        if (!len) return;
        p.animate([{ strokeDasharray: `${len} ${len}`, strokeDashoffset: len }, { strokeDasharray: `${len} ${len}`, strokeDashoffset: 0 }],
          { duration: 650, easing: "cubic-bezier(.3,.7,.3,1)" });
      });
      svg.querySelectorAll(".draw-fade").forEach(el => el.animate([{ opacity: 0 }, { opacity: getComputedStyle(el).opacity || 1 }], { duration: 650, easing: "ease-out" }));
    }
  }

  function scan(root){ scanNumbers(root); scanDraw(root); }
  const mo = new MutationObserver(muts => {
    for (const m of muts){
      if (m.type === "characterData"){ const el = m.target.parentElement; if (el && el.dataset && el.dataset.tw) scanNumbers(el); continue; }
      m.addedNodes.forEach(n => { if (n.nodeType === 1) scan(n); else if (n.nodeType === 3 && n.parentElement && n.parentElement.dataset.tw) scanNumbers(n.parentElement); });
    }
  });
  const start = () => { scan(document.body); mo.observe(document.body, { childList: true, subtree: true }); };
  if (document.body) start(); else addEventListener("DOMContentLoaded", start);

  window.Motion = { reduce, forget: k => drawn.delete(k) };
})();
