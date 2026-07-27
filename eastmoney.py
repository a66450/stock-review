"""数据采集层 — 新浪+东方财富API封装
三个数据源:
  1. 新浪 rank API    → 全A股涨跌排名 → 过滤主板涨停
  2. 东方财富 CoreConception → 板块/概念标签
  3. push2his 实时行情 → 竞价数据 (9:25后调用)
"""
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}

# 主板代码前缀
MAIN_BOARD_PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003', '004')


def _is_main_board(code: str) -> bool:
    return any(code.startswith(p) for p in MAIN_BOARD_PREFIXES)


def _safe_get(url: str, params: dict = None, retries: int = 3) -> dict | list | None:
    """带重试的GET请求"""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
            return resp.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"  HTTP error ({attempt+1}/{retries}): {e}")
            time.sleep(2 ** attempt)
    return None


# ═══════════════════════════════════════════════════════════════
# 1. 涨停数据 — 新浪 rank API
# ═══════════════════════════════════════════════════════════════

SINA_RANK_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/Market_Center.getHQNodeData"
)


def fetch_limit_up_stocks(trade_date: str) -> list[dict]:
    """
    获取当日主板首板涨停票。
    新浪rank API按涨跌幅降序排列，逐页抓取直到涨幅<9.5%后过滤主板。
    """
    result = []
    page = 1
    page_size = 80

    while True:
        params = {
            "page": page,
            "num": page_size,
            "sort": "changepercent",
            "asc": 0,
            "node": "hs_a",
        }
        data = _safe_get(SINA_RANK_URL, params)
        if not data or not isinstance(data, list) or len(data) == 0:
            break

        for item in data:
            code = item.get("code", "")
            change = float(item.get("changepercent", 0) or 0)
            name = item.get("name", "")

            if change < 9.5:
                # 降序排列, 后面全是低于9.5%的
                print(f"  已扫描 {len(result)} 只涨停票 (page={page})")
                return result

            if not _is_main_board(code):
                continue

            # 排除ST
            if "ST" in name.upper():
                continue

            # 排除一字板: open≈high≈low≈trade (价格全天未动)
            open_p = float(item.get("open", 0) or 0)
            high_p = float(item.get("high", 0) or 0)
            low_p = float(item.get("low", 0) or 0)
            trade_p = float(item.get("trade", 0) or 0)
            if open_p > 0 and high_p > 0:
                # 全天振幅<0.5% 且涨停 = 一字板或T字板
                amplitude = (high_p - low_p) / open_p * 100 if open_p > 0 else 0
                if amplitude < 0.5:
                    continue

            # 温和放量: 换手率在0.5%~20%之间
            turnover = float(item.get("turnoverratio", 0) or 0)
            if turnover < 0.5 or turnover > 20:
                continue

            # 流通市值: nmc字段, 单位万元 → 亿
            nmc = float(item.get("nmc", 0) or 0)
            float_mv = round(nmc / 1e4, 2) if nmc > 0 else 0

            stock = {
                "trade_date": trade_date,
                "stock_code": code,
                "stock_name": name,
                "market": "sh" if code.startswith("6") else "sz",
                "limit_time": "",   # 新浪rank无涨停时间
                "close_price": float(item.get("trade", 0) or 0),
                "limit_amount": 0,  # 新浪rank无封单额
                "float_market_val": round(float_mv, 2),
                "turnover_rate": turnover,
                "change_pct": change,
            }
            result.append(stock)

        page += 1
        if page > 10:  # 最多10页, 够了
            break

    print(f"  扫描完成: {len(result)} 只主板涨停票")
    return result


# ═══════════════════════════════════════════════════════════════
# 2. 板块/概念标签 — 东方财富 CoreConception API
# ═══════════════════════════════════════════════════════════════

EM_CONCEPT_URL = (
    "https://emweb.securities.eastmoney.com"
    "/PC_HSF10/CoreConception/PageAjax"
)


def _fetch_one_stock_sectors(code: str) -> tuple[str, list[dict]]:
    """抓取单只股票的板块/概念标签"""
    market = "SH" if code.startswith('6') else "SZ"
    data = _safe_get(EM_CONCEPT_URL, {"code": f"{market}{code}"})
    if not data:
        return code, []

    tags = []
    # ssbk = 行业板块 (sector boards, 医药生物/电子等)
    for item in data.get("ssbk", []):
        name = item.get("BOARD_NAME", "")
        if name:
            tags.append({"stock_code": code, "tag_name": name, "tag_type": "sector"})
    # gnbk = 概念板块 (concept boards, 算力/MLCC/芯片等)
    for item in data.get("gnbk", []):
        name = item.get("BOARD_NAME", "")
        if name:
            tags.append({"stock_code": code, "tag_name": name, "tag_type": "concept"})

    return code, tags


def fetch_stock_sectors(stock_codes: list[str],
                        max_workers: int = 10) -> dict[str, list[dict]]:
    """并发抓取板块标签, 返回 {code: [{stock_code, tag_name, tag_type}]}"""
    result = {}
    if not stock_codes:
        return result

    print(f"  并发抓取 {len(stock_codes)} 只股票板块标签 (workers={max_workers})...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one_stock_sectors, c): c
                   for c in stock_codes}
        done = 0
        for future in as_completed(futures):
            code, tags = future.result()
            result[code] = tags
            done += 1
            if done % 10 == 0 or done == len(stock_codes):
                print(f"    板块抓取进度: {done}/{len(stock_codes)}")

    return result


# ═══════════════════════════════════════════════════════════════
# 3. 竞价/实时行情 — 新浪 real-time API (备选: push2his)
# ═══════════════════════════════════════════════════════════════

KLINE_URL = "http://push2his.eastmoney.com/api/qt/stock/kline/get"


def _get_secid(code: str) -> str:
    """6位代码 → secid: 1.600001(sh) 或 0.000001(sz)"""
    if code.startswith(('6', '9')):
        return f"1.{code}"
    return f"0.{code}"


def get_ma20(code: str) -> tuple[float, float]:
    """
    获取20日均线价格和5日均量。
    返回: (ma20_price, avg_volume_5d)
    """
    secid = _get_secid(code)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101",       # 日K线
        "fqt": "1",         # 前复权
        "end": "20500101",
        "lmt": "25",        # 取25条确保有20个有效值
    }
    data = _safe_get(KLINE_URL, params)
    if not data or "data" not in data or not data["data"]:
        return 0.0, 0.0

    klines = data["data"].get("klines", [])
    if not klines or len(klines) < 20:
        return 0.0, 0.0

    closes = []
    volumes = []
    for k in klines:
        parts = k.split(",")
        if len(parts) >= 6:
            try:
                closes.append(float(parts[2]))   # f53=收盘价
                volumes.append(float(parts[5]))  # f56=成交量
            except ValueError:
                continue

    if len(closes) < 20:
        return 0.0, 0.0

    ma20 = sum(closes[-20:]) / 20
    avg_vol = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0

    return round(ma20, 2), round(avg_vol, 0)


SINA_QUOTE_URL = "http://hq.sinajs.cn/list="


def fetch_auction_quotes(stock_codes: list[str]) -> list[dict]:
    """
    获取竞价数据(9:25后调用)，使用新浪批量实时行情。

    新浪返回格式 (逗号分隔):
      name, open, close_prev, current, high, low, buy, sell, volume, amount, ...

    9:25时: open=竞价开盘价, current=开盘价, volume=竞价成交量, amount=竞价成交额
    """
    results = []
    if not stock_codes:
        return results

    # 新浪前缀: sh600519, sz000001
    symbols = []
    for c in stock_codes:
        prefix = "sh" if c.startswith('6') else "sz"
        symbols.append(f"{prefix}{c}")

    # 分批查询，每批最多50只
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        url = SINA_QUOTE_URL + ','.join(batch)
        try:
            resp = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            }, timeout=15)
            resp.encoding = 'gbk'
            text = resp.text
        except Exception as e:
            print(f"  新浪行情请求失败: {e}")
            continue

        # 解析每行 var hq_str_sh600519="..."
        for line in text.strip().split('\n'):
            if '=' not in line or line.startswith('var '):
                line = line.replace('var hq_str_', '', 1) if line.startswith('var hq_str_') else line
            if '="' not in line:
                continue

            parts = line.split('="', 1)
            if len(parts) != 2:
                continue

            symbol = parts[0].strip()  # sh600519
            data_str = parts[1].strip().rstrip('";')

            if not data_str:
                continue

            fields = data_str.split(',')
            if len(fields) < 10:
                continue

            try:
                name = fields[0]
                open_price = float(fields[1]) if fields[1] else 0
                close_prev = float(fields[2]) if fields[2] else 0
                current = float(fields[3]) if fields[3] else 0
                volume = float(fields[8]) if fields[8] else 0    # 成交量(股)
                amount_raw = float(fields[9]) if fields[9] else 0  # 成交额(元)

                # 买卖盘: [10]=买一量(手), [20]=卖一量(手)
                buy1_vol = float(fields[10]) if len(fields) > 10 and fields[10] else 0
                sell1_vol = float(fields[20]) if len(fields) > 20 and fields[20] else 0
                # 净额(万元) = (买一量 - 卖一量) * 开盘价 / 100
                net_flow = (buy1_vol - sell1_vol) * open_price / 100

                # 计算涨跌幅: (开盘价 - 昨收) / 昨收
                if close_prev > 0:
                    change_pct = (open_price - close_prev) / close_prev * 100
                else:
                    change_pct = 0

                code = symbol[2:]  # 去掉sh/sz前缀

                results.append({
                    "stock_code": code,
                    "stock_name": name,
                    "match_price": open_price,
                    "auction_change_pct": round(change_pct, 2),
                    "auction_amount": amount_raw / 1e4,  # 元 → 万元
                    "auction_turnover": volume / 100,    # 股 → 手
                    "current_price": current,
                    "net_flow": round(net_flow, 2),      # 竞价净额(万元)
                })
            except (ValueError, IndexError) as e:
                continue

        time.sleep(0.2)  # 批次间隔

    print(f"  竞价数据获取: {len(results)} 只")
    return results
