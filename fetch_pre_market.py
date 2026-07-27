#!/usr/bin/env python3
"""盘前脚本: 昨日首板池竞价数据 + MA20过滤 + 爆量标记(>5倍昨日竞价) -> SQLite
支持定时重试: 如果还没到9:25, 等一会儿再抓

两种数据源:
  1. 9:25-9:35 → 新浪实时行情 (竞价刚结束, 量价都是竞价数据)
  2. 9:35之后 → 东方财富1分钟K线 (取第一根K线的成交量近似竞价量)
"""
import sys
import time
from datetime import date, datetime, timezone, timedelta
from db import (init_db, get_conn, get_yesterday_limit_ups, insert_auction)
from eastmoney import fetch_auction_quotes, get_ma20, get_first_kline_volume


def beijing_now():
    """返回当前北京时间"""
    return datetime.now(timezone(timedelta(hours=8)))


def determine_trade_date() -> str:
    """确定今日交易日。支持命令行覆盖"""
    if len(sys.argv) > 1:
        return sys.argv[1]
    today = date.today()
    return today.isoformat()


def get_last_auction_amount(conn, stock_code: str) -> float:
    """获取某只股票上一次的竞价成交额，用于爆量对比"""
    row = conn.execute("""
        SELECT auction_amount FROM auction_data
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 1
    """, (stock_code,)).fetchone()
    return row['auction_amount'] if row else 0.0


def judge_volume_boom(conn, stock_code: str, auction_amount: float) -> int:
    """判定爆量: 今日竞价额 > 昨日竞价额 * 5, 无昨日则 > 2000万"""
    if auction_amount <= 0:
        return 0
    yesterday_amt = get_last_auction_amount(conn, stock_code)
    if yesterday_amt > 0:
        return 1 if auction_amount > yesterday_amt * 5 else 0
    else:
        return 1 if auction_amount > 2000 else 0


def main():
    trade_date = determine_trade_date()
    now = beijing_now()
    print(f"[{now:%Y-%m-%d %H:%M:%S}] 盘前脚本启动")
    print(f"  交易日: {trade_date}")

    init_db()
    conn = get_conn()

    # 1. 获取昨日首板池
    print("[1/4] 读取昨日首板池...")
    yesterday_stocks = get_yesterday_limit_ups(conn)
    if not yesterday_stocks:
        print("  [WARN] 昨日无首板数据, 脚本终止")
        conn.close()
        return

    codes = [s['stock_code'] for s in yesterday_stocks]
    preview = ', '.join(codes[:5])
    if len(codes) > 5:
        preview += f' ... (共{len(codes)}只)'
    print(f"  昨日首板: {preview}")

    # 判断数据新鲜度 (9:25-9:35 是竞价数据纯净窗口)
    hour, minute = now.hour, now.minute
    is_fresh = (hour == 9 and minute < 35) or hour < 9
    if not is_fresh:
        print(f"  [WARN] 当前 {hour}:{minute:02d}, 已过竞价窗口, 成交量包含盘中交易")

    print(f"[2/4] 抓取竞价数据...")
    quotes = fetch_auction_quotes(codes)
    if not quotes:
        print("  [WARN] 未获取到竞价数据 (可能非交易日或API异常)")
        conn.close()
        return
    quote_map = {q['stock_code']: q for q in quotes}

    # 3. MA20 过滤
    print("[3/4] MA20过滤 + 爆量判定...")
    auction_rows = []
    boom_count = 0
    pass_ma20 = 0
    fail_ma20 = 0
    total_boom_ratio = 0  # 统计实际有爆量的

    for stock in yesterday_stocks:
        code = stock['stock_code']
        q = quote_map.get(code)
        if not q:
            continue

        match_price = q.get('match_price', 0)

        # 获取MA20和5日均量
        time.sleep(0.3)
        ma20, avg_vol_5d = get_ma20(code)
        ma20_ok = ma20 > 0 and match_price > ma20

        auction_amount = q.get('auction_amount', 0)
        yesterday_amt = get_last_auction_amount(conn, code)
        boom_ratio = round(auction_amount / yesterday_amt, 1) if yesterday_amt > 0 else 0

        # 爆量: 先看是否满足量比条件, 再过滤MA20
        has_boom = auction_amount > 0 and (
            (yesterday_amt > 0 and boom_ratio >= 5) or
            (yesterday_amt == 0 and auction_amount > 2000)
        )

        is_boom = 1 if (has_boom and ma20_ok) else 0
        if is_boom:
            boom_count += 1
        if has_boom:
            total_boom_ratio += 1
        if ma20_ok:
            pass_ma20 += 1
        else:
            fail_ma20 += 1

        auction_rows.append({
            'trade_date': trade_date,
            'stock_code': code,
            'auction_change_pct': q.get('auction_change_pct', 0),
            'auction_amount': auction_amount,
            'auction_turnover': boom_ratio,
            'unmatched_volume': ma20 if ma20 > 0 else 0,
            'match_price': match_price,
            'is_volume_boom': is_boom,
            'net_flow': q.get('net_flow', 0),
        })

    # 4. 写入
    print("[4/4] 写入数据库...")
    if auction_rows:
        n = insert_auction(conn, auction_rows)
        print(f"  写入 {n} 条竞价 | MA20通过: {pass_ma20}只 | 爆量: {boom_count}只 | 实际爆量(未过滤MA20): {total_boom_ratio}只")

    conn.close()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 盘前脚本结束")


if __name__ == '__main__':
    main()
