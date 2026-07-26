#!/usr/bin/env python3
"""盘前脚本: 昨日首板池竞价数据 + MA20过滤 + 爆量标记(>5倍昨日竞价) -> SQLite"""
import sys
import time
from datetime import date, datetime
from db import (init_db, get_conn, get_yesterday_limit_ups, insert_auction)
from eastmoney import fetch_auction_quotes, get_ma20


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
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 盘前脚本启动")
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

    # 2. 抓竞价数据
    print("[2/4] 抓取竞价数据...")
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

    for stock in yesterday_stocks:
        code = stock['stock_code']
        q = quote_map.get(code)
        if not q:
            continue

        match_price = q.get('match_price', 0)

        # 获取MA20和5日均量
        time.sleep(0.3)  # 避免K线API限流
        ma20, avg_vol_5d = get_ma20(code)
        ma20_ok = ma20 > 0 and match_price > ma20

        auction_amount = q.get('auction_amount', 0)
        yesterday_amt = get_last_auction_amount(conn, code)
        boom_ratio = round(auction_amount / yesterday_amt, 1) if yesterday_amt > 0 else 0
        is_boom = (1 if boom_ratio >= 5 else 0) if ma20_ok else 0
        if is_boom:
            boom_count += 1
        if ma20_ok:
            pass_ma20 += 1
        else:
            fail_ma20 += 1

        auction_rows.append({
            'trade_date': trade_date,
            'stock_code': code,
            'auction_change_pct': q.get('auction_change_pct', 0),
            'auction_amount': auction_amount,
            'auction_turnover': boom_ratio,               # 复用于爆量倍数
            'unmatched_volume': ma20 if ma20 > 0 else 0,   # 复用于MA20
            'match_price': q.get('match_price', 0),
            'is_volume_boom': is_boom,
            'net_flow': q.get('net_flow', 0),             # 竞价净额(万元)
        })

    # 4. 写入
    print("[4/4] 写入数据库...")
    if auction_rows:
        n = insert_auction(conn, auction_rows)
        print(f"  写入 {n} 条竞价 | MA20通过: {pass_ma20}只 | 爆量: {boom_count}只")

    conn.close()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 盘前脚本结束")


if __name__ == '__main__':
    main()
