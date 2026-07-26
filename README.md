# 一进二复盘工具

A股主板首板涨停复盘 + 集合竞价爆量分析，iPhone端直接查看。

## 功能

- **盘后 (15:30)**：自动抓取当日主板首板票 → 板块效应归类 → 生成复盘页面
- **盘前 (9:25)**：自动抓取昨日首板池竞价数据 → 标记爆量票 → 更新页面
- **历史数据**：SQLite数据库留存，随时SQL分析

## 快速开始

1. Fork 本仓库
2. Settings → Pages → Source: `main` 分支 → `/ (root)` → Save
3. Actions 页面确认 workflow 已激活，或手动触发 `both`
4. iPhone 打开 `https://<你的用户名>.github.io/stock-review/`

## 本地运行

```bash
pip install -r requirements.txt

# 盘后抓取（可指定日期）
python fetch_after_market.py                # 默认今天
python fetch_after_market.py 2026-07-25     # 指定日期

# 盘前抓取
python fetch_pre_market.py

# 生成页面
python generate.py

# 浏览器打开 index.html
```

## 数据分析

```bash
sqlite3 data.db
```

```sql
-- 查看历史首板
SELECT trade_date, COUNT(*) FROM daily_limit_ups GROUP BY trade_date;

-- 查看爆量票
SELECT * FROM auction_data WHERE is_volume_boom = 1 ORDER BY auction_amount DESC;

-- 按板块看首板分布
SELECT tag_name, COUNT(DISTINCT stock_code) as cnt
FROM limit_up_tags
GROUP BY tag_name HAVING cnt >= 3 ORDER BY cnt DESC;

-- 某日涨停票带板块
SELECT l.stock_code, l.stock_name, l.float_market_val,
       GROUP_CONCAT(t.tag_name) as tags
FROM daily_limit_ups l
JOIN limit_up_tags t ON l.stock_code = t.stock_code
WHERE l.trade_date = '2026-07-24'
GROUP BY l.stock_code;
```

## 数据源

| 数据 | 来源 |
|------|------|
| 涨停排名 | 新浪财经 rank API |
| 板块/概念 | 东方财富 CoreConception |
| 实时行情(竞价) | push2his.eastmoney.com |

## 定时调度

| 任务 | 北京时间 | GitHub Actions |
|------|---------|----------------|
| 盘后 | 15:30 | `30 7 * * 1-5` |
| 盘前 | 09:25 | `25 1 * * 1-5` |

## 技术栈

Python 3 + SQLite + GitHub Actions + GitHub Pages（纯静态HTML，无JS框架）
