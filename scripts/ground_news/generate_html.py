#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News HTML 报告生成器
读取 output/ground_news/*.json，结合偏见评分表，生成 Ground News 风格的 index.html
"""

import json
import csv
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import Counter

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_DIR = Path("output/ground_news")
CONFIG_DIR = Path("config/ground_news")


def load_latest_output() -> Optional[Dict[str, Any]]:
    if not OUTPUT_DIR.exists():
        return None
    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)


def load_bias_ratings() -> Dict[str, Dict[str, str]]:
    """加载偏见评分表，返回 {domain: {bias, label}}"""
    ratings = {}
    csv_path = CONFIG_DIR / "allsides_ratings.csv"
    if csv_path.exists():
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                domain = row.get("source_domain", "").strip().lower()
                bias = row.get("bias_rating", "").strip()
                if domain and bias:
                    ratings[domain] = {"bias": bias, "label": bias}
    return ratings


def load_sources_config() -> Dict[str, Dict]:
    config_path = Path("config/ground_news/sources.yaml")
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    sources = {}
    for feed in config.get("rss_feeds", []):
        domain = feed.get("bias_reference", "").lower()
        sources[feed["id"]] = {
            "name": feed.get("name", feed["id"]),
            "category": feed.get("category", ""),
            "domain": domain,
        }
    return sources


def extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def build_bias_bar_html(left_pct: float, center_pct: float, right_pct: float) -> str:
    """生成偏见条形图 HTML"""
    l = int(left_pct)
    c = int(center_pct)
    r = int(right_pct)
    # 确保加起来 100
    total = l + c + r
    if total < 100:
        c += 100 - total
    return f"""
    <div class="bias-bar">
        <span class="bar-left" style="width:{l}%"></span>
        <span class="bar-center" style="width:{c}%"></span>
        <span class="bar-right" style="width:{r}%"></span>
    </div>
    <div class="bias-labels">
        <span>{l}% L</span><span>{c}% C</span><span>{r}% R</span>
    </div>"""


def generate_html() -> str:
    output = load_latest_output()
    bias_ratings = load_bias_ratings()
    sources_config = load_sources_config()

    if not output:
        return "<h1>No data yet</h1>"

    entries = output.get("entries", [])
    stats = output.get("stats", {})
    run_id = output.get("run_id", "")
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    # 按源分组
    by_source = {}
    for entry in entries:
        link = entry.get("link", "")
        source_name = entry.get("source_name", entry.get("source_id", "Unknown"))
        domain = extract_domain(link)

        # 查偏见评分
        bias_info = bias_ratings.get(domain, {"bias": "Unknown", "label": "Unknown"})

        if source_name not in by_source:
            by_source[source_name] = {"entries": [], "domain": domain, "bias": bias_info}
        by_source[source_name]["entries"].append(entry)

    # 偏见分布统计
    bias_counter = Counter()
    for source_name, data in by_source.items():
        bias_counter[data["bias"]["bias"]] += data["bias"]["bias"] != "Unknown"

    total_rated = sum(bias_counter.values())
    left_pct = round((bias_counter.get("Left", 0) + bias_counter.get("Lean Left", 0)) / max(total_rated, 1) * 100)
    center_pct = round(bias_counter.get("Center", 0) / max(total_rated, 1) * 100)
    right_pct = round((bias_counter.get("Right", 0) + bias_counter.get("Lean Right", 0)) / max(total_rated, 1) * 100)

    # 生成源条目
    source_rows = ""
    for source_name, data in sorted(by_source.items(), key=lambda x: -len(x[1]["entries"])):
        bias_label = data["bias"]["bias"]
        bias_class = bias_label.lower().replace(" ", "-").replace("lean-", "")
        entry_list = data["entries"]
        titles = "".join(
            f'<li><a href="{e.get("link","#")}" target="_blank">{e.get("title","(no title)")}</a></li>'
            for e in entry_list[:5]
        )
        more = f'<li class="more">... +{len(entry_list)-5} more</li>' if len(entry_list) > 5 else ""

        source_rows += f"""
        <div class="source-card {bias_class}">
            <div class="source-header">
                <span class="source-name">{source_name}</span>
                <span class="bias-tag {bias_class}">{bias_label}</span>
                <span class="entry-count">{len(entry_list)} articles</span>
            </div>
            <ul class="entry-list">{titles}{more}</ul>
        </div>"""

    # 统计
    active_sources = sum(1 for s in stats.get("sources", []) if s.get("count", 0) > 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ground News — Media Bias Dashboard</title>
<style>
:root {{
    --left: #2563eb;
    --lean-left: #60a5fa;
    --center: #9ca3af;
    --lean-right: #f87171;
    --right: #dc2626;
    --bg: #f8fafc;
    --card-bg: #fff;
    --text: #1e293b;
    --muted: #64748b;
    --border: #e2e8f0;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}
.container {{ max-width: 960px; margin: 0 auto; padding: 1.5rem; }}
/* Header */
.header {{ background: linear-gradient(135deg, #1e293b, #334155); color: #fff; padding: 2rem 0; }}
.header h1 {{ font-size: 1.8rem; font-weight: 800; }}
.header p {{ color: #cbd5e1; font-size: 0.9rem; margin-top: 0.5rem; }}
/* Stats row */
.stats {{ display: flex; gap: 1rem; margin: 1.5rem 0; flex-wrap: wrap; }}
.stat-card {{ background: var(--card-bg); border-radius: 12px; padding: 1rem 1.3rem; flex: 1; min-width: 140px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
.stat-card .number {{ font-size: 2rem; font-weight: 800; }}
.stat-card .label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px; }}
/* Bias Bar */
.bias-bar-container {{ background: var(--card-bg); border-radius: 12px; padding: 1.5rem; margin: 1rem 0; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
.bias-bar-container h3 {{ margin-bottom: 1rem; font-size: 1rem; }}
.bias-bar {{ display: flex; height: 24px; border-radius: 12px; overflow: hidden; margin-bottom: 1.5rem; }}
.bias-bar .bar-left {{ background: var(--left); }}
.bias-bar .bar-center {{ background: var(--center); }}
.bias-bar .bar-right {{ background: var(--right); }}
.bias-labels {{ display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--muted); }}
/* Legend */
.legend {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; font-size: 0.8rem; }}
.legend span {{ display: inline-flex; align-items: center; gap: 0.3rem; }}
.legend .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
.dot-left {{ background: var(--left); }}
.dot-lean-left {{ background: var(--lean-left); }}
.dot-center {{ background: var(--center); }}
.dot-lean-right {{ background: var(--lean-right); }}
.dot-right {{ background: var(--right); }}
/* Source Cards */
.source-card {{ background: var(--card-bg); border-radius: 12px; padding: 1.2rem; margin: 0.8rem 0; box-shadow: 0 1px 3px rgba(0,0,0,0.06); border-left: 4px solid var(--center); }}
.source-card.left {{ border-left-color: var(--left); }}
.source-card.lean-left {{ border-left-color: var(--lean-left); }}
.source-card.lean-right {{ border-left-color: var(--lean-right); }}
.source-card.right {{ border-left-color: var(--right); }}
.source-header {{ display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.8rem; }}
.source-name {{ font-weight: 700; font-size: 1.05rem; }}
.bias-tag {{ font-size: 0.75rem; padding: 2px 10px; border-radius: 20px; font-weight: 600; white-space: nowrap; }}
.bias-tag.left {{ background: #dbeafe; color: var(--left); }}
.bias-tag.lean-left {{ background: #eff6ff; color: var(--lean-left); }}
.bias-tag.center {{ background: #f1f5f9; color: var(--center); }}
.bias-tag.lean-right {{ background: #fef2f2; color: var(--lean-right); }}
.bias-tag.right {{ background: #fee2e2; color: var(--right); }}
.bias-tag.unknown {{ background: #f8fafc; color: #94a3b8; }}
.entry-count {{ font-size: 0.8rem; color: var(--muted); margin-left: auto; }}
.entry-list {{ list-style: none; font-size: 0.9rem; }}
.entry-list li {{ padding: 0.25rem 0; border-bottom: 1px solid var(--border); }}
.entry-list li:last-child {{ border-bottom: none; }}
.entry-list a {{ color: var(--text); text-decoration: none; }}
.entry-list a:hover {{ color: #2563eb; text-decoration: underline; }}
.entry-list .more {{ color: var(--muted); font-style: italic; }}
/* Footer */
.footer {{ text-align: center; padding: 2rem; color: var(--muted); font-size: 0.8rem; }}
/* Blindspot Alert */
.blindspot {{ background: #fef3c7; border: 1px solid #f59e0b; border-radius: 8px; padding: 1rem; margin: 1rem 0; font-size: 0.9rem; }}
.blindspot strong {{ color: #d97706; }}
/* Responsive */
@media (max-width: 600px) {{
    .stats {{ flex-direction: column; }}
    .stat-card {{ min-width: auto; }}
}}
</style>
</head>
<body>
<div class="header">
    <div class="container">
        <h1>📊 Ground News — Media Bias Dashboard</h1>
        <p>Last updated: {now} UTC+8 · {stats['total']} articles from {active_sources} sources · Run: {run_id}</p>
    </div>
</div>

<div class="container">
    <!-- Stats -->
    <div class="stats">
        <div class="stat-card">
            <div class="number">{stats['total']}</div>
            <div class="label">Articles Collected</div>
        </div>
        <div class="stat-card">
            <div class="number">{active_sources}</div>
            <div class="label">Active Sources</div>
        </div>
        <div class="stat-card">
            <div class="number">{total_rated}</div>
            <div class="label">Rated Sources</div>
        </div>
    </div>

    <!-- Overall Bias Bar -->
    <div class="bias-bar-container">
        <h3>📐 Overall Media Bias Distribution</h3>
        {build_bias_bar_html(left_pct, center_pct, right_pct)}
        <div class="legend">
            <span><span class="dot dot-left"></span> Left</span>
            <span><span class="dot dot-lean-left"></span> Lean Left</span>
            <span><span class="dot dot-center"></span> Center</span>
            <span><span class="dot dot-lean-right"></span> Lean Right</span>
            <span><span class="dot dot-right"></span> Right</span>
        </div>
    </div>

    <!-- Blindspot Alert (placeholder) -->
    <div class="blindspot">
        <strong>⚠️ Blindspot Alert</strong> — This is a <em>Phase 0 prototype</em>.
        Full NLP clustering, blindspot detection, and factuality scoring will be enabled
        when Steps 2–5 are activated. Right now we show source-level bias ratings from AllSides data.
    </div>

    <!-- Source Cards -->
    <h2 style="margin: 1.5rem 0 0.5rem;">📰 News by Source</h2>
    {source_rows}
</div>

<div class="footer">
    <p>Powered by TrendRadar + Ground News Pipeline · Bias data: AllSides / Ad Fontes Media / Media Bias Fact Check</p>
    <p>Phase 0 — Source-level bias · Coming: Topic clustering · Blindspot feed · Factuality scores</p>
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
    print(f"✅ HTML report: {output_path} ({len(html)} chars)")


if __name__ == "__main__":
    main()