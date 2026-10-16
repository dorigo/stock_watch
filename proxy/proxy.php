<?php
// 盤中即時看板 — PHP 轉送站（放在任何支援 PHP + cURL 的主機即可）  版本 2：支援大盤與類股指數
// 用途：瀏覽器不能直接讀證交所的即時報價（沒有 CORS），由這支程式代為轉送。
// 只允許兩種請求：證交所即時報價、Yahoo 當日分時線；只接受下面列出的網站呼叫。

$ALLOW_ORIGINS = [
    'https://dorigo.github.io',   // 你的 GitHub Pages 網址（換帳號時改這裡）
];
$UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36';

$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
header('Access-Control-Allow-Origin: ' . (in_array($origin, $ALLOW_ORIGINS, true) ? $origin : $ALLOW_ORIGINS[0]));
header('Access-Control-Allow-Methods: GET, OPTIONS');
header('Vary: Origin');
header('Cache-Control: no-store');
header('Content-Type: application/json; charset=utf-8');
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') exit;

function out($obj, $code = 200) { http_response_code($code); echo json_encode($obj, JSON_UNESCAPED_UNICODE); exit; }

$src = $_GET['src'] ?? '';
$headers = ['Accept: application/json'];

if ($src === 'ping') {
    out(['ok' => true, 'from' => 'php', 'v' => 2]);
} elseif ($src === 'mis') {
    $q = $_GET['q'] ?? '';
    if (!preg_match('/^(?:(?:tse|otc)_[0-9A-Za-z]{2,8}\.tw)(?:\|(?:tse|otc)_[0-9A-Za-z]{2,8}\.tw){0,79}$/', $q)) out(['error' => 'bad q'], 400);
    $target = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=' . $q . '&json=1&delay=0&_=' . round(microtime(true) * 1000);
    $headers[] = 'Referer: https://mis.twse.com.tw/stock/index.jsp';
} elseif ($src === 'yh') {
    $s = $_GET['s'] ?? '';
    if (!preg_match('/^(?:[0-9A-Z]{4,6}\.TWO?|\^TWII)$/', $s)) out(['error' => 'bad s'], 400);
    $target = 'https://query1.finance.yahoo.com/v8/finance/chart/' . rawurlencode($s) . '?range=1d&interval=1m';
} else {
    out(['error' => 'unknown src'], 400);
}

$ch = curl_init($target);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => true,
    CURLOPT_TIMEOUT => 10,
    CURLOPT_USERAGENT => $UA,
    CURLOPT_HTTPHEADER => $headers,
    CURLOPT_ENCODING => '',
]);
$body = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$err = curl_error($ch);
curl_close($ch);
if ($body === false) out(['error' => $err], 502);
http_response_code($code ?: 200);
echo trim($body);
