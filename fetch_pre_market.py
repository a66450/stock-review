#!/usr/bin/env python3
"""盘前脚本: 昨日首板池竞价数据抓取 + 爆量标记 -> 写入SQLite"""
import sys
from datetime import date, datetime
from db import (init_db, get_conn, get_yesterday_limit_ups,
                get_recent_auction_avg, insert_auction)
from eastmoney import fetch_auction_quotes


def determine_trade_date() -> str:
    """确定今日交易日。支持命令行覆盖"""
    if len(sys.argv) > 1:
        return sys.argv[1]
    today = date.today()
    return today.isoformat()


def judge_volume_boom(conn, stock_code: str, auction_amount: float) -> int:
    """判定是否爆量: 竞价额 > 前5日均值*2, 或无历史时 > 2000万"""
    if auction_amount <= 0:
        return 0
    avg = get_recent_auction_avg(conn, stock_code, days=5)
    if avg > 0:
        return 1 if auction_amount > avg * 2 else 0
    else:
        return 1 if auction_amount > 2000 else 0


def main():
    trade_date = determine_trade_date()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 盘前脚本启动")
    print(f"  交易日: {trade_date}")

    init_db()
    conn = get_conn()

    # 1. 获取昨日首板池
    print("[1/3] 读取昨日首板池...")
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

    # 2. 抓竞价数据
    print("[2/3] 抓取竞价数据...")
    quotes = fetch_auction_quotes(codes)

    if not quotes:
        print("  [WARN] 未获取到竞价数据 (可能非交易日或API异常)")
        conn.close()
        return

    # 3. 判定爆量并写入
    print("[3/3] 判定爆量并写入数据库...")
    quote_map = {q['stock_code']: q for q in quotes}
    auction_rows = []
    boom_count = 0

    for stock in yesterday_stocks:
        code = stock['stock_code']
        q = quote_map.get(code)
        if not q:
            continue  # 竞价无数据 (可能停牌)

        auction_amount = q.get('auction_amount', 0)
        is_boom = judge_volume_boom(conn, code, auction_amount)
        if is_boom:
            boom_count += 1

        auction_rows.append({
            'trade_date': trade_date,
            'stock_code': code,
            'auction_change_pct': q.get('auction_change_pct', 0),
            'auction_amount': auction_amount,
            'auction_turnover': q.get('auction_turnover', 0),
            'unmatched_volume': 0,  # V1暂不取
            'match_price': q.get('match_price', 0),
            'is_volume_boom': is_boom,
        })

    if auction_rows:
        n = insert_auction(conn, auction_rows)
        print(f"  写入 {n} 条竞价数据, 其中爆量 {boom_count} 只")

    conn.close()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 盘前脚本结束")


if __name__ == '__main__':
    main()
