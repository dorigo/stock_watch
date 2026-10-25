"""暫時用：測試從 GitHub Actions 能不能抓到公開資訊觀測站的法人說明會資料"""
import json, urllib.request, urllib.parse, ssl, datetime as dt
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
out = []
def tryit(name, url, data=None, headers=None, js=False):
    h = {"User-Agent": UA, "Accept": "*/*", **(headers or {})}
    body = None
    if data is not None:
        body = json.dumps(data).encode() if js else urllib.parse.urlencode(data).encode()
        h["Content-Type"] = "application/json" if js else "application/x-www-form-urlencoded"
    try:
        req = urllib.request.Request(url, data=body, headers=h)
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode("utf-8", "replace")
            out.append(f"## {name}\nstatus={r.status} len={len(t)}\n{t[:3000]}\n")
    except Exception as e:
        out.append(f"## {name}\nERROR {e!r}\n")
y = dt.date.today().year - 1911; m = dt.date.today().month
for typek in ("sii", "otc"):
    tryit(f"mopsov ajax_t100sb02_1 {typek}", "https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1",
          {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "TYPEK": typek, "year": y, "month": f"{m:02d}", "co_id": ""})
tryit("mops new api t100sb02_1", "https://mops.twse.com.tw/mops/api/t100sb02_1", {"companyId": "", "marketKind": "sii", "year": str(y), "month": f"{m:02d}"}, js=True)
tryit("mops new api t100sb02_1 b", "https://mops.twse.com.tw/mops/api/t100sb02_1", {"marketKind": "sii", "year": str(y), "month": str(m)}, js=True)
tryit("mops new api t100sb02_1 single 2330", "https://mops.twse.com.tw/mops/api/t100sb02_1", {"companyId": "2330", "marketKind": "sii", "year": str(y), "month": ""}, js=True)
tryit("twse openapi list", "https://openapi.twse.com.tw/v1/swagger.json")
open("data/probe_mops.txt", "w").write("\n".join(out))
