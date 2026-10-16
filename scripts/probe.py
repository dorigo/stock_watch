import urllib.request, json
out=[]
codes=["tse_t00.tw","otc_o00.tw","tse_FRMSA.tw"]+[f"tse_t{n:02d}.tw" for n in range(1,40)]
for i in range(0,len(codes),20):
    q="|".join(codes[i:i+20])
    u=f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={q}&json=1&delay=0"
    try:
        req=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0","Referer":"https://mis.twse.com.tw/stock/index.jsp"})
        j=json.loads(urllib.request.urlopen(req,timeout=30).read().decode())
        for r in j.get("msgArray",[]):
            out.append(f'{r.get("ch")} {r.get("n")} z={r.get("z")} y={r.get("y")} h={r.get("h")} l={r.get("l")} t={r.get("t")} d={r.get("d")}')
    except Exception as e: out.append(f"ERR {e!r}")
for s in ["%5ETWII","%5ETWOII","%5ETWO"]:
    try:
        req=urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=1d&interval=1m",headers={"User-Agent":"Mozilla/5.0"})
        out.append(s+" "+urllib.request.urlopen(req,timeout=30).read().decode()[:300])
    except Exception as e: out.append(f"{s} ERR {e!r}")
open("data/probe.log","w").write("\n".join(out))
