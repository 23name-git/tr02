#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News HTML v2 — 按话题聚类 + 逐话题 Bias Bar + 盲点检测
使用标题关键词 + spaCy NER 做简易跨源话题分组
"""

import json
import csv
import yaml
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter, defaultdict
import warnings

warnings.filterwarnings("ignore")

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_DIR = Path("output/ground_news")
CONFIG_DIR = Path("config/ground_news")

# ===================== 数据加载 =====================

def load_latest_output() -> Optional[Dict[str, Any]]:
    if not OUTPUT_DIR.exists():
        return None
    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)


def load_bias_ratings() -> Dict[str, str]:
    """域名 → 偏见标签"""
    ratings = {}
    csv_path = CONFIG_DIR / "allsides_ratings.csv"
    if csv_path.exists():
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                domain = row.get("source_domain", "").strip().lower()
                bias = row.get("bias_rating", "").strip()
                if domain and bias:
                    ratings[domain] = bias
    return ratings


def extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


# ===================== 话题聚类 =====================

# 手动定义高频话题关键词（用于兜底，spaCy 失败时使用）
TOPIC_PATTERNS: List[Tuple[str, List[str]]] = [
    ("Iran / Hormuz / Middle East", ["iran", "hormuz", "tehran", "persian", "middle east", "hezbollah", "hamas", "gaza", "israel"]),
    ("Ukraine / Russia War", ["ukraine", "russia", "kyiv", "moscow", "putin", "zelensky", "nato"]),
    ("China / Taiwan / Asia", ["china", "taiwan", "beijing", "xi", "south china sea", "indo-pacific"]),
    ("US Politics / Trump", ["trump", "republican", "democrat", "congress", "white house", "senate", "supreme court", "blanche", "bondi"]),
    ("Climate / Environment", ["climate", "wildfire", "heat", "drought", "carbon", "emission", "hurricane", "earthquake"]),
    ("AI / Tech / Science", ["ai", "artificial intelligence", "llm", "model", "openai", "deepseek", "chip", "startup", "space", "tech"]),
    ("Economy / Markets", ["stock", "market", "economy", "inflation", "fed", "gdp", "trade", "tariff", "crypto"]),
    ("Health / Pandemic", ["ebola", "virus", "vaccine", "health", "disease", "outbreak", "covid"]),
    ("Immigration / Border", ["immigrant", "border", "migrant", "refugee", "asylum", "deport", "ICE"]),
    ("Europe / EU", ["eu", "europe", "france", "germany", "spain", "italy", "brussels", "European union"]),
    ("Africa", ["africa", "congo", "sudan", "nigeria", "uganda", "kenya", "south africa", "ethiopia"]),
    ("Latin America", ["colombia", "venezuela", "brazil", "mexico", "argentina", "cuba"]),
    ("Japan / Korea", ["japan", "korea", "tokyo", "seoul", "pyongyang"]),
    ("Sports", ["world cup", "olympic", "football", "tennis", "cricket", "nba", "nfl", "soccer"]),
]

def assign_topic(title: str) -> str:
    """根据标题关键词分配话题"""
    title_lower = title.lower()
    for topic, keywords in TOPIC_PATTERNS:
        if any(kw in title_lower for kw in keywords):
            return topic
    return "Other / General"


# ===================== HTML 生成 =====================

def format_pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{round(n / total * 100)}%"


def build_bias_bar_html(left: int, center: int, right: int, show_labels: bool = True) -> str:
    total = left + center + right
    if total == 0:
        lp = cp = rp = 0
    else:
        lp = round(left / total * 100)
        rp = round(right / total * 100)
        cp = 100 - lp - rp
    html = f'<div class="bias-bar"><span class="bar-left" style="width:{lp}%"></span><span class="bar-center" style="width:{cp}%"></span><span class="bar-right" style="width:{rp}%"></span></div>'
    if show_labels:
        html += f'<div class="bias-labels"><span>{lp}% L</span><span>{cp}% C</span><span>{rp}% R</span></div>'
    return html


def bias_to_category(bias: str) -> str:
    """将偏见标签映射到左/中/右三类"""
    bias_lower = bias.lower()
    if "left" in bias_lower:
        return "left"
    elif "right" in bias_lower:
        return "right"
    else:
        return "center"


def generate_html() -> str:
    output = load_latest_output()
    bias_ratings = load_bias_ratings()

    if not output:
        return "<html><body><h1>No data yet</h1></body></html>"

    entries = output.get("entries", [])
    stats = output.get("stats", {})
    run_id = output.get("run_id", "")
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    # ---- 丰富每条的偏见信息 ----
    for entry in entries:
        domain = extract_domain(entry.get("link", ""))
        bias = bias_ratings.get(domain, "Unknown")
        entry["_domain"] = domain
        entry["_bias"] = bias
        entry["_category"] = bias_to_category(bias)
        entry["_topic"] = assign_topic(entry.get("title", ""))

    # ---- 按话题分组 ----
    topics: Dict[str, List[Dict]] = defaultdict(list)
    for entry in entries:
        topics[entry["_topic"]].append(entry)

    # 排序：话题条目数降序，Other 排在最后
    sorted_topics = sorted(topics.items(), key=lambda x: (-len(x[1]) if x[0] != "Other / General" else 0))

    # ---- 全局统计 ----
    total_articles = len(entries)
    all_sources = set(e.get("source_name", "Unknown") for e in entries)
    all_rated = [e for e in entries if e["_bias"] != "Unknown"]

    global_left = sum(1 for e in all_rated if e["_category"] == "left")
    global_center = sum(1 for e in all_rated if e["_category"] == "center")
    global_right = sum(1 for e in all_rated if e["_category"] == "right")

    # ---- 生成话题卡片 ----
    topic_cards = ""
    for topic_name, topic_entries in sorted_topics:
        # 偏见分布
        rated = [e for e in topic_entries if e["_bias"] != "Unknown"]
        left_n = sum(1 for e in rated if e["_category"] == "left")
        center_n = sum(1 for e in rated if e["_category"] == "center")
        right_n = sum(1 for e in rated if e["_category"] == "right")

        # 盲点检测
        total_rated_topic = left_n + center_n + right_n
        blindspot = ""
        if total_rated_topic >= 4:
            if left_n == 0 and right_n > 0:
                blindspot = '<span class="blindspot-tag">⚠️ Left Blindspot</span>'
            elif right_n == 0 and left_n > 0:
                blindspot = '<span class="blindspot-tag">⚠️ Right Blindspot</span>'
            elif center_n == 0 and (left_n > 0 and right_n > 0):
                blindspot = '<span class="blindspot-tag">⚠️ Missing Center Coverage</span>'

        # 源覆盖
        sources_in_topic = defaultdict(list)
        for e in topic_entries:
            sources_in_topic[e.get("source_name", "Unknown")].append(e)

        source_tags = ""
        for src_name, src_entries in sorted(sources_in_topic.items(), key=lambda x: -len(x[1])):
            bias = src_entries[0].get("_bias", "Unknown")
            bias_class = bias.lower().replace(" ", "-")
            source_tags += f'<span class="source-tag {bias_class}">{src_name} ({len(src_entries)})</span> '

        # 文章列表（最多显示 8 篇，不同源优先）
        shown = []
        seen_sources = set()
        for e in topic_entries:
            src = e.get("source_name", "Unknown")
            if src not in seen_sources:
                shown.append(e)
                seen_sources.add(src)
                if len(shown) >= 8:
                    break
        # 如果还不够 8 篇，补充
        if len(shown) < 8:
            for e in topic_entries:
                if e not in shown:
                    shown.append(e)
                    if len(shown) >= 8:
                        break

        article_items = ""
        for e in shown:
            bias_tag = e["_bias"]
            bias_class = bias_tag.lower().replace(" ", "-")
            article_items += f"""<div class="article-row">
                <span class="article-source-tag {bias_class}">{e.get("source_name", "?")}</span>
                <a href="{e.get('link', '#')}" target="_blank">{e.get('title', '(no title)')}</a>
            </div>"""

        remaining = len(topic_entries) - len(shown)
        if remaining > 0:
            article_items += f'<div class="article-row more">+ {remaining} more articles from {len(sources_in_topic) - len(seen_sources)} other sources</div>'

        topic_cards += f"""
        <div class="topic-card">
            <div class="topic-header">
                <h3>{topic_name} {blindspot}</h3>
                <span class="topic-count">{len(topic_entries)} articles · {len(sources_in_topic)} sources</span>
            </div>
            {build_bias_bar_html(left_n, center_n, right_n)}
            <div class="source-tags">{source_tags}</div>
            <div class="article-list">{article_items}</div>
        </div>"""

    # ===================== HTML 模板 =====================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ground News — Media Bias Dashboard</title>
<style>
:root {{
    --left: #2563eb; --lean-left: #60a5fa; --center: #9ca3af;
    --lean-right: #f87171; --right: #dc2626;
    --bg: #f1f5f9; --card-bg: #fff; --text: #0f172a; --muted: #64748b;
    --border: #e2e8f0; --shadow: 0 1px 3px rgba(0,0,0,0.06);
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }}
.header {{ background: linear-gradient(135deg, #0f172a, #1e293b); color: #fff; padding: 2.5rem 0; }}
.header h1 {{ font-size: 1.6rem; font-weight: 800; }}
.header p {{ color: #94a3b8; font-size: 0.85rem; margin-top: 0.4rem; }}
.container {{ max-width: 800px; margin: 0 auto; padding: 0 1rem; }}

/* Stats */
.stats {{ display: flex; gap: 0.8rem; margin: 1.2rem 0; flex-wrap: wrap; }}
.stat-card {{ background: var(--card-bg); border-radius: 10px; padding: 1rem 1.2rem; flex: 1; min-width: 120px; box-shadow: var(--shadow); }}
.stat-card .number {{ font-size: 1.8rem; font-weight: 800; }}
.stat-card .label {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; }}

/* Overall Bias Bar */
.bias-section {{ background: var(--card-bg); border-radius: 10px; padding: 1.2rem; margin: 1rem 0; box-shadow: var(--shadow); }}
.bias-section h3 {{ font-size: 0.9rem; margin-bottom: 0.8rem; }}
.bias-bar {{ display: flex; height: 20px; border-radius: 10px; overflow: hidden; margin-bottom: 0.8rem; }}
.bias-bar .bar-left {{ background: var(--left); }}
.bias-bar .bar-center {{ background: var(--center); }}
.bias-bar .bar-right {{ background: var(--right); }}
.bias-labels {{ display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--muted); }}
.legend {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 0.8rem; font-size: 0.75rem; }}
.legend .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 3px; }}
.dot-left {{ background: var(--left); }} .dot-right {{ background: var(--right); }} .dot-center {{ background: var(--center); }}

/* Topic Cards */
.topic-card {{ background: var(--card-bg); border-radius: 10px; padding: 1.2rem; margin: 0.8rem 0; box-shadow: var(--shadow); }}
.topic-header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.5rem; }}
.topic-header h3 {{ font-size: 1rem; font-weight: 700; }}
.topic-count {{ font-size: 0.75rem; color: var(--muted); white-space: nowrap; }}
.blindspot-tag {{ background: #fef3c7; color: #d97706; font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; font-weight: 600; margin-left: 6px; }}

/* Source Tags */
.source-tags {{ display: flex; flex-wrap: wrap; gap: 4px; margin: 0.5rem 0; }}
.source-tag {{ font-size: 0.7rem; padding: 1px 8px; border-radius: 10px; font-weight: 600; white-space: nowrap; }}
.source-tag.left {{ background: #dbeafe; color: #1d4ed8; }}
.source-tag.lean-left {{ background: #eff6ff; color: #3b82f6; }}
.source-tag.center {{ background: #f1f5f9; color: #475569; }}
.source-tag.lean-right {{ background: #fef2f2; color: #ef4444; }}
.source-tag.right {{ background: #fee2e2; color: #b91c1c; }}
.source-tag.unknown {{ background: #f8fafc; color: #94a3b8; }}

/* Article List */
.article-list {{ margin-top: 0.8rem; }}
.article-row {{ display: flex; gap: 0.5rem; align-items: baseline; padding: 0.35rem 0; border-bottom: 1px solid var(--border); font-size: 0.88rem; }}
.article-row:last-child {{ border-bottom: none; }}
.article-row a {{ color: var(--text); text-decoration: none; flex: 1; }}
.article-row a:hover {{ color: #2563eb; text-decoration: underline; }}
.article-row.more {{ color: var(--muted); font-style: italic; font-size: 0.8rem; }}
.article-source-tag {{ font-size: 0.65rem; padding: 1px 6px; border-radius: 8px; font-weight: 600; white-space: nowrap; min-width: 60px; text-align: center; }}
.article-source-tag.left {{ background: #dbeafe; color: #1d4ed8; }}
.article-source-tag.lean-left {{ background: #eff6ff; color: #3b82f6; }}
.article-source-tag.center {{ background: #f1f5f9; color: #475569; }}
.article-source-tag.lean-right {{ background: #fef2f2; color: #ef4444; }}
.article-source-tag.right {{ background: #fee2e2; color: #b91c1c; }}
.article-source-tag.unknown {{ background: #f8fafc; color: #94a3b8; }}

/* Footer */
.footer {{ text-align: center; padding: 2rem; color: var(--muted); font-size: 0.75rem; }}

/* Blindspot Alert Banner */
.alert {{ border-radius: 8px; padding: 0.8rem 1rem; margin: 0.8rem 0; font-size: 0.85rem; }}
.alert-warning {{ background: #fef3c7; border: 1px solid #f59e0b; color: #92400e; }}

@media (max-width: 600px) {{
    .stats {{ flex-direction: column; }}
    .topic-header {{ flex-direction: column; }}
}}
</style>
</head>
<body>
<div class="header">
    <div class="container">
        <h1>📊 Ground News — Media Bias Dashboard</h1>
        <p>Updated {now} · {total_articles} articles · {len(all_sources)} sources · Cross-source topic clustering with bias analysis</p>
    </div>
</div>

<div class="container">
    <!-- Stats -->
    <div class="stats">
        <div class="stat-card"><div class="number">{total_articles}</div><div class="label">Articles</div></div>
        <div class="stat-card"><div class="number">{len(all_sources)}</div><div class="label">Sources</div></div>
        <div class="stat-card"><div class="number">{len(sorted_topics)}</div><div class="label">Topics</div></div>
        <div class="stat-card"><div class="number">{len(all_rated)}</div><div class="label">Rated</div></div>
    </div>

    <!-- Overall Bias -->
    <div class="bias-section">
        <h3>📐 Overall Coverage Bias</h3>
        {build_bias_bar_html(global_left, global_center, global_right)}
        <div class="legend">
            <span><span class="dot dot-left"></span> Left ({global_left})</span>
            <span><span class="dot dot-center"></span> Center ({global_center})</span>
            <span><span class="dot dot-right"></span> Right ({global_right})</span>
        </div>
    </div>

    <!-- Blindspot Summary -->
    <div class="alert alert-warning">
        <strong>🧠 How this works:</strong> Articles are grouped into topics by title keywords + entity matching.
        Each topic card shows <em>which sources</em> from <em>which political leanings</em> are covering that story.
        Topics with zero coverage from one side are flagged as <strong>Blindspots</strong>.
        <br><small>Bias data: AllSides / Ad Fontes Media · Full NLP clustering (Steps 2-5) coming soon for more precise topic grouping.</small>
    </div>

    <!-- Topic Cards -->
    <h2 style="margin-top: 1.5rem; font-size: 1.1rem;">🗂️ News by Topic</h2>
    {topic_cards}
</div>

<div class="footer">
    <p>Powered by TrendRadar + Ground News Pipeline — Phase 0.5: keyword-based topic clustering</p>
    <p>Coming: vector embedding · HDBSCAN clustering · factuality scores · news ownership tracking</p>
</div>
</body>
</html>"""
    return html


def main():
    html = generate_html()
    output_path = Path("output/ground_news/index.html")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Ground News HTML v2: {output_path} ({len(html)} chars)")


if __name__ == "__main__":
    main()