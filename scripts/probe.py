import urllib.request, json
out=[]
for u in ["https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw|otc_5904.tw|tse_0050.tw&json=1&delay=0",
          "https://query1.finance.yahoo.com/v8/finance/chart/2330.TW?range=1d&interval=1m"]:
    try:
        req=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0","Origin":"https://dorigo.github.io","Referer":"https://mis.twse.com.tw/stock/index.jsp"})
        with urllib.request.urlopen(req,timeout=30) as r:
            out.append(f"{u}\nSTATUS {r.status}\nHEADERS {dict(r.headers)}\nBODY {r.read().decode('utf-8','replace')[:2500]}\n")
    except Exception as e:
        out.append(f"{u} ERR {e!r}")
open("data/probe.log","w").write("\n".join(out))
