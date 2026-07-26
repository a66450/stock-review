"""数据库层 — SQLite操作封装"""
import sqlite3
from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    """获取数据库连接，启用WAL模式和外键"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """幂等建表，不存在则创建"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_limit_ups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            market TEXT NOT NULL CHECK(market IN ('sh','sz')),
            limit_time TEXT DEFAULT '',
            close_price REAL NOT NULL,
            limit_amount REAL DEFAULT 0,
            float_market_val REAL DEFAULT 0,
            turnover_rate REAL DEFAULT 0,
            change_pct REAL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(trade_date, stock_code)
        );

        CREATE TABLE IF NOT EXISTS limit_up_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            tag_type TEXT NOT NULL CHECK(tag_type IN ('sector','concept')),
            UNIQUE(stock_code, tag_name, tag_type)
        );

        CREATE TABLE IF NOT EXISTS auction_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            auction_change_pct REAL DEFAULT 0,
            auction_amount REAL DEFAULT 0,
            auction_turnover REAL DEFAULT 0,
            unmatched_volume REAL DEFAULT 0,
            match_price REAL DEFAULT 0,
            is_volume_boom INTEGER DEFAULT 0,
            net_flow REAL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(trade_date, stock_code)
        );

        CREATE INDEX IF NOT EXISTS idx_limit_date ON daily_limit_ups(trade_date);
        CREATE INDEX IF NOT EXISTS idx_limit_code ON daily_limit_ups(stock_code);
        CREATE INDEX IF NOT EXISTS idx_auction_date ON auction_data(trade_date);
        CREATE INDEX IF NOT EXISTS idx_auction_code ON auction_data(stock_code);
        CREATE INDEX IF NOT EXISTS idx_tag_code ON limit_up_tags(stock_code);
        CREATE INDEX IF NOT EXISTS idx_tag_name ON limit_up_tags(tag_name);
    """)
    conn.commit()
    conn.close()
    print("Database initialized OK")


def insert_limit_ups(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """批量写入涨停票，忽略重复。返回实际插入行数"""
    count = 0
    for r in rows:
        try:
            cur = conn.execute("""
                INSERT OR IGNORE INTO daily_limit_ups
                (trade_date, stock_code, stock_name, market, limit_time,
                 close_price, limit_amount, float_market_val, turnover_rate, change_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                r['trade_date'], r['stock_code'], r['stock_name'],
                r['market'], r.get('limit_time', ''),
                r['close_price'], r.get('limit_amount', 0),
                r.get('float_market_val', 0), r.get('turnover_rate', 0),
                r.get('change_pct', 0)
            ))
            if cur.rowcount > 0:
                count += 1
        except Exception as e:
            print(f"  skip {r.get('stock_code')}: {e}")
    conn.commit()
    return count


def insert_tags(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """批量写入标签，忽略重复。rows: [{stock_code, tag_name, tag_type}]"""
    count = 0
    for r in rows:
        try:
            cur = conn.execute("""
                INSERT OR IGNORE INTO limit_up_tags (stock_code, tag_name, tag_type)
                VALUES (?,?,?)
            """, (r['stock_code'], r['tag_name'], r['tag_type']))
            if cur.rowcount > 0:
                count += 1
        except Exception as e:
            print(f"  skip tag {r}: {e}")
    conn.commit()
    return count


def insert_auction(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """批量写入竞价数据，忽略重复。返回实际插入行数"""
    count = 0
    for r in rows:
        try:
            cur = conn.execute("""
                INSERT OR IGNORE INTO auction_data
                (trade_date, stock_code, auction_change_pct, auction_amount,
                 auction_turnover, unmatched_volume, match_price, is_volume_boom, net_flow)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                r['trade_date'], r['stock_code'], r['auction_change_pct'],
                r['auction_amount'], r['auction_turnover'],
                r.get('unmatched_volume', 0), r.get('match_price', 0),
                r['is_volume_boom'], r.get('net_flow', 0)
            ))
            if cur.rowcount > 0:
                count += 1
        except Exception as e:
            print(f"  skip auction {r.get('stock_code')}: {e}")
    conn.commit()
    return count


def get_last_trade_date(conn: sqlite3.Connection) -> str | None:
    """获取数据库中最近的一个交易日"""
    row = conn.execute(
        "SELECT MAX(trade_date) as d FROM daily_limit_ups"
    ).fetchone()
    return row['d'] if row else None


def get_yesterday_limit_ups(conn: sqlite3.Connection) -> list[dict]:
    """获取最近一个交易日的所有首板票"""
    last_date = get_last_trade_date(conn)
    if not last_date:
        return []
    rows = conn.execute("""
        SELECT * FROM daily_limit_ups WHERE trade_date = ?
        ORDER BY limit_time
    """, (last_date,)).fetchall()
    return [dict(r) for r in rows]


def get_sectors_by_date(conn: sqlite3.Connection, trade_date: str) -> list[dict]:
    """获取某日所有首板票的板块tag，按板块聚合（≥3只才返回）"""
    rows = conn.execute("""
        SELECT t.tag_name, t.tag_type, COUNT(DISTINCT t.stock_code) as cnt,
               GROUP_CONCAT(DISTINCT l.stock_code) as codes,
               GROUP_CONCAT(DISTINCT l.stock_name) as names
        FROM limit_up_tags t
        JOIN daily_limit_ups l ON t.stock_code = l.stock_code AND l.trade_date = ?
        GROUP BY t.tag_name, t.tag_type
        HAVING cnt >= 3
        ORDER BY cnt DESC
    """, (trade_date,)).fetchall()
    return [dict(r) for r in rows]


def get_auction_by_date(conn: sqlite3.Connection, trade_date: str) -> list[dict]:
    """获取某日竞价数据，附带昨日涨停信息和板块标签"""
    last_date = get_last_trade_date(conn)
    rows = conn.execute("""
        SELECT a.*, l.stock_name, l.close_price, l.limit_amount,
               l.float_market_val, l.turnover_rate,
               GROUP_CONCAT(DISTINCT t.tag_name) as tags
        FROM auction_data a
        LEFT JOIN daily_limit_ups l ON a.stock_code = l.stock_code
            AND l.trade_date = ?
        LEFT JOIN limit_up_tags t ON a.stock_code = t.stock_code
        WHERE a.trade_date = ?
        GROUP BY a.stock_code
        ORDER BY a.is_volume_boom DESC, a.auction_amount DESC
    """, (last_date, trade_date)).fetchall()
    return [dict(r) for r in rows]


def get_recent_auction_avg(conn: sqlite3.Connection,
                           stock_code: str, days: int = 5) -> float:
    """获取某只股票近N日竞价成交额均值，用于爆量判定"""
    row = conn.execute("""
        SELECT AVG(auction_amount) as avg_amt
        FROM (
            SELECT auction_amount FROM auction_data
            WHERE stock_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
        )
    """, (stock_code, days)).fetchone()
    return row['avg_amt'] if row and row['avg_amt'] else 0.0
