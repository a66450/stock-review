#!/usr/bin/env python3
"""盘后脚本: 抓取当日主板首板涨停票 + 板块/概念标签 -> 写入SQLite"""
import sys
from datetime import date, datetime
from db import init_db, get_conn, insert_limit_ups, insert_tags, get_last_trade_date
from eastmoney import fetch_limit_up_stocks, fetch_stock_sectors


def ensure_trade_date() -> str:
    """确定交易日: 支持命令行传参 python fetch_after_market.py 2026-07-25"""
    if len(sys.argv) > 1:
        return sys.argv[1]
    today = date.today()
    if today.weekday() >= 5:  # 周六/日 -> 回退到周五
        offset = today.weekday() - 4
        today = today.replace(day=today.day - offset)
    return today.isoformat()


def main():
    trade_date = ensure_trade_date()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 盘后脚本启动")
    print(f"  交易日: {trade_date}")

    # 1. 初始化数据库
    init_db()
    conn = get_conn()

    # 2. 抓涨停票
    print("[1/3] 抓取涨停板数据...")
    stocks = fetch_limit_up_stocks(trade_date)
    if not stocks:
        print("  [WARN] 未抓到任何主板首板票 (可能非交易日或数据延迟)")
        conn.close()
        return

    n = insert_limit_ups(conn, stocks)
    print(f"  写入 {n} 只涨停票 (新增)")

    # 3. 抓板块标签
    print("[2/3] 抓取板块/概念标签...")
    codes = [s['stock_code'] for s in stocks]
    all_tags = fetch_stock_sectors(codes)

    tag_rows = []
    for code, tags in all_tags.items():
        tag_rows.extend(tags)

    if tag_rows:
        m = insert_tags(conn, tag_rows)
        print(f"  写入 {m} 条板块/概念标签")

    # 4. 汇总
    print("[3/3] 盘后数据写入完成")
    last = get_last_trade_date(conn)
    print(f"  数据库最新交易日: {last}")

    # 统计板块效应
    from db import get_sectors_by_date
    sectors = get_sectors_by_date(conn, trade_date)
    if sectors:
        print(f"  板块效应 ({len(sectors)}个):")
        for sec in sectors[:5]:
            print(f"    {sec['tag_name']} ({sec['cnt']}只)")

    conn.close()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 盘后脚本结束")


if __name__ == '__main__':
    main()
