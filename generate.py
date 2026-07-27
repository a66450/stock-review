#!/usr/bin/env python3
"""生成 index.html - 从SQLite读取数据, 渲染移动端适配的复盘页面"""
from datetime import date, datetime
from db import (init_db, get_conn, get_yesterday_limit_ups,
                get_sectors_by_date, get_auction_by_date, get_last_trade_date)
from config import INDEX_PATH


def _fmt_amount(val) -> str:
    """格式化金额: 23456 -> 2.35亿"""
    val = abs(float(val or 0))
    if val >= 1e8:
        return f"{val / 1e8:.2f}亿"
    elif val >= 1e4:
        return f"{val / 1e4:.0f}万"
    return f"{val:.0f}"


def _fmt_pct(val: float) -> str:
    """格式化百分比, 带正负号"""
    if val > 0:
        return f"+{val:.2f}%"
    return f"{val:.2f}%"


def _build_after_html(limit_ups: list[dict], sectors: list[dict]) -> str:
    """构建盘后标签页HTML"""
    # 分离行业板块(sector)和概念板块(concept)
    concept_secs = [s for s in sectors if s.get('tag_type') == 'concept']
    sector_secs = [s for s in sectors if s.get('tag_type') == 'sector']

    html = """
    <div class="tab-content active" id="tab-after">
      <div class="kpi-row">
        <div class="kpi-card">
          <div class="kpi-value">%d</div>
          <div class="kpi-label">首板总计</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-value">%d</div>
          <div class="kpi-label">概念板块</div>
        </div>
      </div>
    """ % (len(limit_ups), len(concept_secs))

    # 概念板块区域 (算力/MLCC/医药等)
    if concept_secs:
        html += '<div class="section-title">概念题材 (&ge;3只)</div>'
        for sec in concept_secs[:20]:
            names = sec['names'].split(',')
            codes = sec['codes'].split(',')
            html += f"""
            <details class="sector-card concept-card">
              <summary><span class="tag-badge">概念</span> {sec['tag_name']} ({sec['cnt']}只)</summary>
              <div class="stock-list">
            """
            for code, name in zip(codes, names):
                stock = next((s for s in limit_ups if s['stock_code'] == code), None)
                if stock:
                    html += f"""
                <div class="stock-item">
                  <span class="stock-name">{name}</span>
                  <span class="stock-code">{code}</span>
                  <span class="stock-data">换{stock['turnover_rate']:.1f}%</span>
                  <span class="stock-data">市值{stock['float_market_val']:.1f}亿</span>
                </div>
                    """
            html += """
              </div>
            </details>
            """
    else:
        html += '<div class="empty-state">暂无概念板块数据</div>'

    # 行业板块区域 (医药生物/电子等, 折叠收起)
    if sector_secs:
        html += '<details class="sector-group"><summary>行业板块 ({})</summary>'.format(len(sector_secs))
        for sec in sector_secs[:15]:
            names = sec['names'].split(',')
            codes = sec['codes'].split(',')
            html += f"""
            <div class="sector-card">
              <div class="sec-title">{sec['tag_name']} ({sec['cnt']}只)</div>
              <div class="stock-list">
            """
            for code, name in zip(codes, names):
                stock = next((s for s in limit_ups if s['stock_code'] == code), None)
                if stock:
                    html += f"""
                <div class="stock-item">
                  <span class="stock-name">{name}</span>
                  <span class="stock-code">{code}</span>
                  <span class="stock-data">换{stock['turnover_rate']:.1f}%</span>
                  <span class="stock-data">市值{stock['float_market_val']:.1f}亿</span>
                </div>
                    """
            html += """
              </div>
            </div>
            """
        html += '</details>'

    # 全部首板表格
    html += '<div class="section-title">全部首板 (按涨停时间)</div>'
    html += '<div class="table-wrap"><table><thead><tr>'
    for h in ['代码', '名称', '涨幅', '换手', '流通市值']:
        html += f'<th>{h}</th>'
    html += '</tr></thead><tbody>'

    for s in limit_ups:
        html += f"""
        <tr>
          <td>{s['stock_code']}</td>
          <td class="td-name">{s['stock_name']}</td>
          <td class="td-red">{_fmt_pct(s['change_pct'])}</td>
          <td>{s['turnover_rate']:.1f}%</td>
          <td>{s['float_market_val']:.1f}亿</td>
        </tr>
        """
    html += '</tbody></table></div></div>'
    return html


def _build_pre_html(auctions: list[dict]) -> str:
    """构建竞价标签页HTML — 爆量优先, MA20仅标记不硬过滤"""
    # 所有有爆量标记的票 (is_volume_boom > 0)
    boom_all = [a for a in auctions if a['is_volume_boom']]
    # 非爆量票 (有竞价数据但没爆量)
    no_boom_all = [a for a in auctions if not a['is_volume_boom']]

    # 爆量票按高开降序
    boom_all.sort(key=lambda a: a['auction_change_pct'], reverse=True)
    no_boom_all.sort(key=lambda a: a['auction_change_pct'], reverse=True)

    # 统计
    total = len(auctions)
    boom_cnt = len(boom_all)
    above_ma20 = sum(1 for a in auctions
                     if a.get('unmatched_volume', 0) > 0
                     and a['match_price'] > a['unmatched_volume'])
    high5_cnt = sum(1 for a in auctions if a['auction_change_pct'] >= 5)
    below_ma20 = total - above_ma20

    # MA20信息字段: unmatched_volume 存的是 MA20 值
    def _ma20_ok(a) -> bool:
        mv = a.get('unmatched_volume', 0)
        return mv > 0 and a['match_price'] > mv

    def _ma20_badge(a) -> str:
        if _ma20_ok(a):
            return '<span class="badge badge-ma20">MA20↑</span>'
        return '<span class="badge badge-ma20-down">MA20↓</span>'

    html = """
    <div class="tab-content" id="tab-pre">
      <div class="kpi-row">
        <div class="kpi-card boom">
          <div class="kpi-value">%d</div>
          <div class="kpi-label">竞价爆量(>5倍)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-value">%d</div>
          <div class="kpi-label">首板总数</div>
        </div>
        <div class="kpi-card toggle off" id="btn-high" onclick="toggleHigh()">
          <div class="kpi-value">%d</div>
          <div class="kpi-label">高开5%%+</div>
        </div>
      </div>
    """ % (boom_cnt, total, high5_cnt)

    if below_ma20 > 0:
        html += f'<div class="note">MA20下方: {below_ma20}只 (标记为MA20↓, 未过滤)</div>'

    # ======== 爆量票列表 ========
    if boom_all:
        html += '<div class="section-title">竞价爆量 (高开降序)</div>'
        for i, a in enumerate(boom_all, 1):
            tags = (a.get('tags') or '').replace(',', ', ')
            ma20_val = a.get('unmatched_volume', 0)
            html += f"""
            <div class="auction-card boom-card" data-high="{'1' if a['auction_change_pct'] >= 5 else '0'}">
              <div class="ac-rank">#{i}</div>
              <div class="ac-body">
                <div class="ac-header">
                  <span class="ac-name">{a['stock_name']}</span>
                  <span class="ac-code">{a['stock_code']}</span>
                  {_ma20_badge(a)}
                  <span class="ac-badge boom-badge">爆量{a.get('auction_turnover',0):.1f}倍</span>
                </div>
                <div class="ac-data-row">
                  <span>高开 {_fmt_pct(a['auction_change_pct'])}</span>
                  <span>{_fmt_amount(a['auction_amount'])}</span>
                  <span class="net-{'pos' if a.get('net_flow',0) >= 0 else 'neg'}">{_fmt_amount(a.get('net_flow',0))}</span>
                </div>
                <div class="ac-yesterday">
                  昨换{a.get('turnover_rate',0):.1f}% | 市值{a.get('float_market_val',0):.1f}亿 | MA20={ma20_val:.1f}
                </div>
                <div class="ac-tags">{tags}</div>
              </div>
            </div>
            """

    # ======== 非爆量票列表 ========
    if no_boom_all:
        html += f'<div class="section-title">其他首板 (未爆量, {len(no_boom_all)}只)</div>'
        for i, a in enumerate(no_boom_all, 1):
            tags = (a.get('tags') or '').replace(',', ', ')
            ma20_val = a.get('unmatched_volume', 0)
            ratio = a.get('auction_turnover', 0)
            html += f"""
            <div class="auction-card" data-high="{'1' if a['auction_change_pct'] >= 5 else '0'}">
              <div class="ac-rank" style="color:#999">#{i}</div>
              <div class="ac-body">
                <div class="ac-header">
                  <span class="ac-name">{a['stock_name']}</span>
                  <span class="ac-code">{a['stock_code']}</span>
                  {_ma20_badge(a)}
                  {f'<span class="badge badge-ratio">{ratio:.1f}倍</span>' if ratio > 0 else ''}
                </div>
                <div class="ac-data-row" style="color:#333">
                  <span>高开 {_fmt_pct(a['auction_change_pct'])}</span>
                  <span>{_fmt_amount(a['auction_amount'])}</span>
                  <span class="net-{'pos' if a.get('net_flow',0) >= 0 else 'neg'}">{_fmt_amount(a.get('net_flow',0))}</span>
                </div>
                <div class="ac-yesterday">
                  昨换{a.get('turnover_rate',0):.1f}% | 市值{a.get('float_market_val',0):.1f}亿 | MA20={ma20_val:.1f}
                </div>
                <div class="ac-tags">{tags}</div>
              </div>
            </div>
            """

    return html


def generate():
    """主函数: 读取数据 -> 构建完整HTML -> 写入文件"""
    init_db()
    conn = get_conn()

    limit_ups = get_yesterday_limit_ups(conn)
    last_date = get_last_trade_date(conn)
    sectors = get_sectors_by_date(conn, last_date) if last_date else []

    today = date.today().isoformat()
    auctions = get_auction_by_date(conn, today)

    conn.close()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>一进二复盘</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
  background: #f5f5f5; color: #333; line-height: 1.5;
  max-width: 480px; margin: 0 auto; padding: 12px;
}}
/* 头部 */
.header {{
  text-align: center; padding: 16px 0 8px;
}}
.header h1 {{ font-size: 20px; font-weight: 700; }}
.header .date {{ font-size: 12px; color: #999; margin-top: 4px; }}

/* 标签切换 */
.tab-radio {{ display: none; }}
.tabs {{
  display: flex; border-radius: 10px; overflow: hidden;
  margin-bottom: 16px; background: #e8e8e8;
}}
.tab-btn {{
  flex: 1; text-align: center; padding: 10px; font-size: 15px;
  font-weight: 600; cursor: pointer; transition: all .2s; color: #666;
}}

/* KPI卡片 */
.kpi-row {{
  display: flex; gap: 10px; margin-bottom: 16px;
}}
.kpi-card {{
  flex: 1; background: #fff; border-radius: 12px;
  padding: 16px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.08);
}}
.kpi-card.boom {{
  background: linear-gradient(135deg, #fff5f5, #fff);
  border: 1px solid #feb2b2;
}}
.kpi-value {{ font-size: 28px; font-weight: 800; color: #e53e3e; }}
.kpi-label {{ font-size: 12px; color: #666; margin-top: 4px; }}

/* 板块卡片 */
.section-title {{
  font-size: 15px; font-weight: 700; margin: 16px 0 8px; color: #1a202c;
}}
.sector-card {{
  background: #fff; border-radius: 10px; margin-bottom: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,.06); overflow: hidden;
}}
.sector-card {{
  background: #fff; border-radius: 10px; margin-bottom: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,.06); overflow: hidden;
}}
.sector-card summary {{
  padding: 12px 14px; font-weight: 600; font-size: 14px;
  cursor: pointer; color: #2d3748; list-style: none;
}}
.sector-card summary::-webkit-details-marker {{ display: none; }}
.sector-card[open] summary {{ border-bottom: 1px solid #f0f0f0; }}
.concept-card summary {{ background: #fff5f5; }}
.concept-card[open] summary {{ border-bottom: 1px solid #fed7d7; }}
.tag-badge {{
  font-size: 10px; background: #e53e3e; color: #fff;
  padding: 1px 5px; border-radius: 3px; margin-right: 4px;
}}
.sec-title {{
  padding: 10px 14px; font-weight: 600; font-size: 13px; color: #4a5568;
  border-bottom: 1px solid #f0f0f0;
}}
.sector-group {{
  margin-top: 12px; background: #fff; border-radius: 10px;
  overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.06);
}}
.sector-group > summary {{
  padding: 10px 14px; font-weight: 600; font-size: 13px; color: #718096;
  cursor: pointer; list-style: none;
}}

.stock-list {{ padding: 8px 14px 12px; }}
.stock-item {{
  display: flex; align-items: center; gap: 8px; padding: 8px 0;
  border-bottom: 1px solid #f7f7f7; font-size: 13px;
}}
.stock-item:last-child {{ border-bottom: none; }}
.stock-name {{ font-weight: 600; min-width: 52px; }}
.stock-code {{ color: #999; font-size: 12px; min-width: 52px; }}
.stock-data {{ color: #666; font-size: 12px; white-space: nowrap; }}

/* 表格 */
.table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
table {{
  width: 100%; border-collapse: collapse; font-size: 13px;
  background: #fff; border-radius: 10px; overflow: hidden;
}}
th, td {{ padding: 10px 8px; text-align: left; white-space: nowrap; }}
th {{ background: #f7fafc; font-weight: 600; color: #4a5568; font-size: 12px; }}
td {{ border-bottom: 1px solid #f0f0f0; }}
tr:last-child td {{ border-bottom: none; }}
.td-name {{ font-weight: 600; }}
.td-red {{ color: #e53e3e; font-weight: 600; }}

/* 竞价卡片 */
.auction-card {{
  background: #fff; border-radius: 10px; padding: 14px;
  margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.06);
  display: flex; gap: 12px; align-items: flex-start;
}}
.auction-card.boom-card {{
  border-left: 4px solid #e53e3e; background: #fffbfb;
}}
.ac-rank {{ font-size: 16px; font-weight: 800; color: #e53e3e; min-width: 24px; }}
.ac-body {{ flex: 1; min-width: 0; }}
.ac-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
.ac-name {{ font-weight: 700; font-size: 15px; }}
.ac-code {{ color: #999; font-size: 12px; }}
.ac-badge {{
  font-size: 11px; padding: 2px 6px; border-radius: 4px; font-weight: 600;
}}
.boom-badge {{ background: #e53e3e; color: #fff; }}
.badge {{
  font-size: 10px; padding: 1px 5px; border-radius: 3px; font-weight: 600;
}}
.badge-ma20 {{ background: #c6f6d5; color: #276749; }}
.badge-ma20-down {{ background: #fed7d7; color: #c53030; }}
.badge-ratio {{ background: #fefcbf; color: #975a16; }}
.ac-data-row {{
  display: flex; gap: 16px; font-size: 13px; color: #e53e3e; font-weight: 600;
  margin-bottom: 4px;
}}
.ac-yesterday {{ font-size: 12px; color: #999; }}
.ac-tags {{ font-size: 11px; color: #718096; margin-top: 4px; line-height: 1.4; }}
.net-pos {{ color: #e53e3e; }}
.net-neg {{ color: #38a169; }}
.kpi-card.toggle {{ cursor: pointer; user-select: none; transition: all .15s; }}
.kpi-card.toggle:active {{ transform: scale(0.96); }}
.kpi-card.toggle.on {{ border: 2px solid #e53e3e; background: #fff5f5; }}
.kpi-card.toggle.off {{ opacity: 0.6; }}

/* 提示信息 */
.note {{
  text-align: center; padding: 8px; font-size: 12px;
  color: #718096; background: #f7fafc; border-radius: 8px; margin-bottom: 8px;
}}

/* 空状态 */
.empty-state {{
  text-align: center; padding: 40px; color: #999; font-size: 14px;
}}

/* 标签切换联动 */
.tab-content {{ display: none; }}
#tab-radio-after:checked ~ .tabs > .tab-btn[for="tab-radio-after"],
#tab-radio-pre:checked ~ .tabs > .tab-btn[for="tab-radio-pre"] {{
  background: #fff; color: #e53e3e; border-radius: 10px;
}}
#tab-radio-after:checked ~ .main-content > #tab-after {{ display: block; }}
#tab-radio-pre:checked ~ .main-content > #tab-pre {{ display: block; }}
</style>
</head>
<body>
<div class="header">
  <h1>一进二复盘</h1>
  <div class="date">数据更新: {datetime.now():%Y-%m-%d %H:%M} | 首板日: {last_date or '-'}</div>
</div>

<input type="radio" class="tab-radio" name="tab" id="tab-radio-after" checked>
<input type="radio" class="tab-radio" name="tab" id="tab-radio-pre">

<div class="tabs">
  <label class="tab-btn" for="tab-radio-after">盘后</label>
  <label class="tab-btn" for="tab-radio-pre">竞价</label>
</div>

<div class="main-content">
{_build_after_html(limit_ups, sectors)}
{_build_pre_html(auctions)}
</div>

<script>
function toggleHigh() {{
  var btn = document.getElementById('btn-high');
  var on = btn.classList.contains('off');
  btn.classList.toggle('on', on);
  btn.classList.toggle('off', !on);
  var cards = document.querySelectorAll('.auction-card');
  cards.forEach(function(c) {{
    if (on) {{ c.style.display = c.dataset.high === '1' ? 'flex' : 'none'; }}
    else {{ c.style.display = 'flex'; }}
  }});
}}
</script>
</body>
</html>"""

    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(html)

    boom_count = sum(1 for a in auctions if a['is_volume_boom'])
    print(f"Generated: {INDEX_PATH}")
    print(f"  首板: {len(limit_ups)}只 | 板块效应: {len(sectors)}个 | 竞价爆量: {boom_count}只")


if __name__ == '__main__':
    generate()
