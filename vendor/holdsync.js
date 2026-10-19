/* holdsync.js — 「我的持股」與「模擬投資」跨裝置同步
   持股以「同步密碼」加密（PBKDF2-SHA256 25 萬次 + AES-GCM 256）後，存成 repo 裡的 data/holdings.enc。
   repo 是公開的，所以只上傳加密後的內容；沒有同步密碼的人無法解開。
   需要：這台瀏覽器已存 GitHub 金鑰（管理自選股用的那組，Contents 可寫入），並設定同步密碼。 */
(() => {
  const m = location.hostname.match(/^([^.]+)\.github\.io$/);
  const seg = location.pathname.split("/").filter(Boolean)[0];
  const OWNER = m ? m[1] : "dorigo", REPO = m && seg ? seg : "stock_watch";
  const ITER = 250000;
  const TOKEN = "gh_token_" + REPO, PASS = "sync_pass_v1";

  const get = k => { try { return localStorage.getItem(k); } catch(e){ return null; } };
  const set = (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch(e){} };
  const enc = new TextEncoder(), dec = new TextDecoder();
  const b64 = u8 => { let s = ""; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000)); return btoa(s); };
  const unb64 = s => Uint8Array.from(atob(s.replace(/\s/g, "")), c => c.charCodeAt(0));

  async function deriveKey(pass, salt){
    const base = await crypto.subtle.importKey("raw", enc.encode(pass), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: ITER, hash: "SHA-256" }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function encrypt(obj, pass){
    const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKey(pass, salt);
    const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, enc.encode(JSON.stringify(obj))));
    return { v: 1, alg: "PBKDF2-SHA256/AES-GCM", iter: ITER, salt: b64(salt), iv: b64(iv), data: b64(ct), updated: obj.updated };
  }
  async function decrypt(file, pass){
    try {
      const key = await deriveKey(pass, unb64(file.salt));
      const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(file.iv) }, key, unb64(file.data));
      return JSON.parse(dec.decode(pt));
    } catch(e){ const err = new Error("同步密碼和雲端資料不符"); err.code = "badpass"; throw err; }
  }

  function makeSync(cfg){
    async function api(method, body){
      const r = await fetch(cfg.url + (method === "GET" ? "?ref=main&t=" + Date.now() : ""), {
        method, cache: "no-store",
        headers: { "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   ...(get(TOKEN) ? { "Authorization": "Bearer " + get(TOKEN) } : {}),   // 公開 repo：沒有金鑰也能讀
                   ...(body ? { "Content-Type": "application/json" } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (method === "GET" && r.status === 404) return null;
      const j = await r.json().catch(() => ({}));
      if (!r.ok){ const e = new Error(j.message || "HTTP " + r.status); e.status = r.status; throw e; }
      return j;
    }
    async function readRemote(){
      const f = await api("GET");
      if (!f) return null;
      return { sha: f.sha, file: JSON.parse(dec.decode(unb64(f.content))) };
    }

    const localHold = () => { try { return JSON.parse(get(cfg.data) || "null") || cfg.empty(); } catch(e){ return cfg.empty(); } };
    const state = () => { try { return JSON.parse(get(cfg.st) || "null"); } catch(e){ return null; } };
    const setState = o => { set(cfg.st, JSON.stringify({ ...o, at: new Date().toISOString() })); emit(); };
    const listeners = [];
    const emit = () => listeners.forEach(f => { try { f(state()); } catch(e){} });

    let busy = null, timer = null;
    async function push(sha, force){
      const pass = get(PASS);
      if (!pass) return { skip: true };
      if (!get(TOKEN)){ setState({ ok: true, dir: "readonly", n: cfg.count(localHold()) }); return { readonly: true }; }
      if (!get(cfg.upd)) set(cfg.upd, new Date().toISOString());
      const payload = await encrypt({ [cfg.field]: localHold(), updated: get(cfg.upd) }, pass);
      const body = { message: cfg.msg, content: btoa(JSON.stringify(payload)), branch: "main" };
      if (sha === undefined && !force){ const r = await readRemote(); sha = r ? r.sha : undefined; }
      if (sha) body.sha = sha;
      try { await api("PUT", body); }
      catch(e){
        if (e.status === 409 || e.status === 422){ const r = await readRemote(); if (r) body.sha = r.sha; await api("PUT", body); }
        else throw e;
      }
      setState({ ok: true, dir: "up", n: cfg.count(localHold()) });
      return { pushed: true };
    }

    /* 讀雲端：雲端較新就覆蓋本機；本機較新就上傳。回傳 {changed} 代表本機持股被更新了。 */
    async function sync(opts = {}){
      if (busy) return busy;
      busy = (async () => {
        const pass = get(PASS);
        if (!pass) return { skip: true };
        try {
          const r = await readRemote();
          if (!r){ if (get(TOKEN)) await push(undefined, true); else setState({ ok: true, dir: "empty", n: 0 }); return { changed: false }; }
          const remote = await decrypt(r.file, pass);
          const lu = get(cfg.upd) || "", ru = remote.updated || "";
          if (ru > lu){
            const mine = localHold(), nMine = cfg.count(mine), theirs = remote[cfg.field] || cfg.empty();
            if (!lu && nMine && JSON.stringify(mine) !== JSON.stringify(theirs) && !opts.silent){
              const ok = confirm(`雲端已有 ${cfg.count(theirs)} 筆${cfg.label}（${ru.slice(0, 16).replace("T", " ")} 更新）。\n要用雲端的資料取代這台瀏覽器目前的 ${nMine} 筆${cfg.label}嗎？\n\n按「取消」則改用這台的資料覆蓋雲端。`);
              if (!ok){ set(cfg.upd, new Date().toISOString()); await push(r.sha); return { changed: false }; }
            }
            set(cfg.data, JSON.stringify(theirs)); set(cfg.upd, ru);
            setState({ ok: true, dir: "down", n: cfg.count(theirs) });
            return { changed: true };
          }
          if (lu > ru && get(TOKEN)){ await push(r.sha); return { changed: false }; }
          setState({ ok: true, dir: get(TOKEN) ? "same" : "readonly", n: cfg.count(localHold()) });
          return { changed: false };
        } catch(e){
          setState({ ok: false, code: e.code || (e.status === 401 || e.status === 403 ? "perm" : "err"), err: e.message });
          return { error: e };
        }
      })();
      try { return await busy; } finally { busy = null; }
    }

    function markChanged(){
      set(cfg.upd, new Date().toISOString());
      if (!get(PASS)) return;
      if (!get(TOKEN)){ setState({ ok: true, dir: "readonly", n: cfg.count(localHold()) }); return; }
      clearTimeout(timer);
      timer = setTimeout(() => { push().catch(e => setState({ ok: false, code: e.code || "err", err: e.message })); }, 1200);
    }

    return {
      enabled: () => !!get(PASS),
      canWrite: () => !!(get(PASS) && get(TOKEN)),
      hasToken: () => !!get(TOKEN),
      hasPass: () => !!get(PASS),
      setPass: p => { set(PASS, p || null); if (!p){ set(cfg.st, null); } emit(); },
      state, sync, markChanged,
      overwriteRemote: async () => { set(cfg.upd, new Date().toISOString()); return push(); },
      onChange: f => listeners.push(f),
    };
  }

  const fileUrl = (path) => `https://api.github.com/repos/${OWNER}/${REPO}/contents/${path}`;
  window.HoldSync = makeSync({ url: fileUrl("data/holdings.enc"), data: "holdings_v1", upd: "holdings_updated", st: "cloud_sync_v1",
    field: "holdings", label: "持股", msg: "同步持股（加密）", empty: () => ({}), count: o => Object.keys(o || {}).length });
  window.SimSync = makeSync({ url: fileUrl("data/sim.enc"), data: "sim_v1", upd: "sim_updated", st: "cloud_sync_sim_v1",
    field: "sim", label: "模擬交易", msg: "同步模擬投資（加密）", empty: () => null, count: o => (o && o.trades ? o.trades.length : 0) });
})();
