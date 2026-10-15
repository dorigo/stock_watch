# 自選股籌碼追蹤

只追蹤你在 `watchlist.txt` 填的股票。每個交易日盤後，GitHub Actions 會自動抓證交所、櫃買中心、集保結算所的公開資料，計算三大法人、千張大戶與融資變化的分數，再更新到 GitHub Pages。

## 檔案結構

```
watchlist.txt                 ← 你的自選股（一行一檔）
index.html                    ← 網站頁面
scripts/fetch.py              ← 抓資料＋計分（只用 Python 標準函式庫）
.github/workflows/update.yml  ← 排程：台北 17:17、22:07（週一至週五）
data/latest.json              ← 最新結果（頁面讀這個）
data/report/YYYY-MM-DD.json   ← 每日結果（歷史清單）
data/daily/                   ← 每日原始資料快取
data/tdcc/                    ← 每週千張大戶資料
```

## 部署步驟

1. 在 GitHub 建立新的 repository（例如 `stock_watch`），把整個資料夾內容上傳到 `main` 分支（包含 `.github` 與 `.nojekyll` 這兩個隱藏項目）。
2. **Settings → Actions → General → Workflow permissions**，選「Read and write permissions」並儲存。
3. **Settings → Pages**，Source 選「Deploy from a branch」，分支選 `main`、資料夾選 `/ (root)`，儲存。
4. **Actions → 更新籌碼資料 → Run workflow**，手動執行第一次。第一次會往回補 10 個交易日的資料，大約需要 5 到 8 分鐘。
5. 完成後打開 `https://你的帳號.github.io/stock_watch/`。

壓縮檔內附有示範資料，上傳後就能先看版面，第一次執行 Actions 後會換成真實資料。

## 修改自選股

直接編輯 `watchlist.txt` 並 commit，Actions 會自動重新抓資料。上市、上櫃、ETF 都可以填，系統會自動判斷市場。

## 調整計分

`scripts/fetch.py` 開頭的 `W_*`（權重）與 `FULL_*`（滿分門檻）可以自行修改；`NEED_DAYS` 控制保留幾個交易日。

## 常見問題

- **Actions 執行失敗，log 顯示連線錯誤或回傳 HTML**：證交所偶爾會限制國外 IP 或請求頻率。可以稍後重跑；若持續失敗，可在自己的 Windows 電腦用工作排程器執行 `python scripts/fetch.py` 後 `git push`，或設定 self-hosted runner。
- **傍晚顯示「部分資料尚未公布」**：融資餘額通常晚上才公布，22:07 的排程會補上。
- **千張大戶週增減是空的**：集保資料每週更新一次，需要累積兩週後才會有增減數字。
- **櫃買中心欄位對不上**：櫃買網站改版時欄位可能變動，請把 Actions log 中該段的錯誤訊息貼給我修正。

## 免責聲明

本專案僅整理公開資料並依固定規則計分，不構成任何投資建議，投資盈虧請自行負責。
