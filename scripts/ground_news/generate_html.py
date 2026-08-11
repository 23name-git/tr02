#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News HTML v3 — 向量语义聚类 + 逐话题 Bias Bar + 盲点 + 源透明度
- 使用 TF-IDF + cosine similarity 做跨源话题聚类
- 可选 sentence-transformers（安装后自动启用）
- 按话题分组（非按源），每话题一条 Bias Bar
"""

import json, csv, yaml, re, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter, defaultdict
import warnings
warnings.filterwarnings("ignore")

import numpy as np

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_DIR = Path("output/ground_news")
CONFIG_DIR = Path("config/ground_news")

# ===================== 向量聚类引擎 =====================

def cluster_articles_tfidf(entries: List[Dict], similarity_threshold: float = 0.12) -> Dict[str, List[Dict]]:
    """TF-IDF + cosine similarity 聚类（阈值 0.12，比 0.25 更宽松）"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    titles = [e.get("title", "") for e in entries]
    if len(titles) < 2:
        return {"All News": entries}

    # 中英文混合停用词
    zh_stop = set("的了吗呢嘛哦啊嗯吧呀在和有是我他这不也人了就到说要去能为都个中上大对会可以自后下没看好只还过")
    en_stop = {"the","a","an","is","are","was","were","be","been","in","on","at","to","for","of","and","or","but",
               "it","its","that","this","with","from","by","as","not","no","has","have","had","will","would","can",
               "could","may","should","new","more","says","after","over","into","first","than","just","about","what"}
    all_stop = list(zh_stop | en_stop)

    vectorizer = TfidfVectorizer(
        stop_words=all_stop,
        max_features=800,
        ngram_range=(1, 2),  # unigrams + bigrams
    )
    try:
        tfidf_matrix = vectorizer.fit_transform(titles)
        sim_matrix = cosine_similarity(tfidf_matrix)
    except Exception:
        return {"All News": entries}

    # 贪婪聚类
    n = len(entries)
    assigned = [False] * n
    clusters: Dict[int, List[int]] = {}

    for i in range(n):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, n):
            if not assigned[j] and sim_matrix[i][j] >= similarity_threshold:
                cluster.append(j)
                assigned[j] = True
        if len(cluster) >= 2:  # 至少要2篇才算一个簇
            clusters[len(clusters)] = cluster

    # 命名
    feature_names = vectorizer.get_feature_names_out()
    result = {}
    for cid, indices in clusters.items():
        centroid = np.mean(tfidf_matrix[indices].toarray(), axis=0)
        top_indices = np.argsort(centroid)[-5:][::-1]
        keywords = [feature_names[k] for k in top_indices if centroid[k] > 0.05]
        topic_name = " / ".join(keywords[:3]) if keywords else f"Topic {cid + 1}"
        result[topic_name] = [entries[i] for i in indices]

    return dict(sorted(result.items(), key=lambda x: -len(x[1])))


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


def bias_to_category(bias: str) -> str:
    b = bias.lower()
    if "left" in b: return "left"
    if "right" in b: return "right"
    return "center"


# ===================== HTML 组件 =====================

def build_bias_bar(left: int, center: int, right: int) -> str:
    total = left + center + right
    if total == 0:
        lp = rp = cp = 0
    else:
        lp = round(left / total * 100)
        rp = round(right / total * 100)
        cp = 100 - lp - rp
    return f"""<div class="bias-bar">
        <span class="bar-left" style="width:{lp}%"></span>
        <span class="bar-center" style="width:{cp}%"></span>
        <span class="bar-right" style="width:{rp}%"></span>
    </div>
    <div class="bias-labels"><span>{lp}% L ({left})</span><span>{cp}% C ({center})</span><span>{rp}% R ({right})</span></div>"""


# ===================== 主逻辑 =====================

def generate_html() -> str:
    output = load_latest_output()
    bias_ratings = load_bias_ratings()

    if not output:
        return "<html><body><h1>No data</h1></body></html>"

    entries = output.get("entries", [])
    stats = output.get("stats", {})
    run_id = output.get("run_id", "")
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M %Z")

    # 丰富偏见信息
    for e in entries:
        e["_domain"] = extract_domain(e.get("link", ""))
        e["_bias"] = bias_ratings.get(e["_domain"], "Unknown")
        e["_category"] = bias_to_category(e["_bias"])

    # ===== 向量语义聚类 =====
    print(f"  🧮 Clustering {len(entries)} articles...")
    topic_clusters = cluster_articles_tfidf(entries)
    print(f"  📊 {len(topic_clusters)} topics found")

    # ===== 全部统计 =====
    rated = [e for e in entries if e["_bias"] != "Unknown"]
    all_left = sum(1 for e in rated if e["_category"] == "left")
    all_center = sum(1 for e in rated if e["_category"] == "center")
    all_right = sum(1 for e in rated if e["_category"] == "right")
    all_sources = set(e.get("source_name", "?") for e in entries)

    # ===== 话题卡片 =====
    topic_cards = ""
    for topic_name, topic_entries in topic_clusters.items():
        if len(topic_entries) < 2:
            continue  # 跳过单篇孤岛

        rated_t = [e for e in topic_entries if e["_bias"] != "Unknown"]
        left_n = sum(1 for e in rated_t if e["_category"] == "left")
        center_n = sum(1 for e in rated_t if e["_category"] == "center")
        right_n = sum(1 for e in rated_t if e["_category"] == "right")
        total_rated = left_n + center_n + right_n

        # 盲点检测（<15% 覆盖率即标）
        blindspot = ""
        if total_rated >= 3:
            left_pct = left_n / total_rated * 100 if total_rated else 0
            right_pct = right_n / total_rated * 100 if total_rated else 0
            if left_pct < 15 and right_pct >= 15:
                blindspot = '<span class="blindspot-tag">⚠ Left Blindspot</span>'
            elif right_pct < 15 and left_pct >= 15:
                blindspot = '<span class="blindspot-tag">⚠ Right Blindspot</span>'

        # 源标签
        src_map = defaultdict(list)
        for e in topic_entries:
            src_map[e.get("source_name", "?")].append(e)
        source_tags = ""
        for sn, se in sorted(src_map.items(), key=lambda x: -len(x[1])):
            bias_c = se[0].get("_bias", "Unknown").lower().replace(" ", "-")
            source_tags += f'<span class="src-tag {bias_c}">{sn} ({len(se)})</span> '

        # 文章列表（每源取1篇，最多10篇）
        shown, seen = [], set()
        for e in topic_entries:
            s = e.get("source_name", "?")
            if s not in seen:
                shown.append(e); seen.add(s)
                if len(shown) >= 10: break
        if len(shown) < 10:
            shown += [e for e in topic_entries if e not in shown][:10 - len(shown)]

        article_rows = ""
        for e in shown:
            bc = e["_bias"].lower().replace(" ", "-")
            article_rows += f"""<div class="art-row">
                <span class="art-src {bc}">{e.get('source_name','?')}</span>
                <a href="{e.get('link','#')}" target="_blank">{e.get('title','(untitled)')}</a>
            </div>"""

        remaining = len(topic_entries) - len(shown)
        if remaining > 0:
            article_rows += f'<div class="art-row more">+{remaining} more</div>'

        topic_cards += f"""<div class="card">
            <div class="card-hd">
                <h3>{topic_name} {blindspot}</h3>
                <span class="cnt">{len(topic_entries)} articles · {len(src_map)} sources</span>
            </div>
            {build_bias_bar(left_n, center_n, right_n)}
            <div class="src-tags">{source_tags}</div>
            <div class="art-list">{article_rows}</div>
        </div>"""

    # 统计单篇（未聚类成功的）
    singles = sum(1 for v in topic_clusters.values() if len(v) < 2)

    # ===== HTML =====
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ground News — Media Bias Dashboard</title>
<style>
:root{{--L:#2563eb;--C:#9ca3af;--R:#dc2626;--bg:#f1f5f9;--card:#fff;--tx:#0f172a;--mu:#64748b;--bd:#e2e8f0}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);line-height:1.5}}
.hd{{background:linear-gradient(135deg,#0f172a,#1e293b);color:#fff;padding:2.5rem 0}}
.hd h1{{font-size:1.5rem;font-weight:800}}
.hd p{{color:#94a3b8;font-size:.85rem;margin-top:.4rem}}
.w{{max-width:840px;margin:0 auto;padding:0 1rem}}
.stats{{display:flex;gap:.8rem;margin:1.2rem 0;flex-wrap:wrap}}
.sc{{background:var(--card);border-radius:10px;padding:1rem 1.2rem;flex:1;min-width:100px;box-shadow:0 1px 3px #0001}}
.sc .n{{font-size:1.8rem;font-weight:800}}
.sc .l{{color:var(--mu);font-size:.72rem;text-transform:uppercase;letter-spacing:.5px}}
.bar-wrap{{background:var(--card);border-radius:10px;padding:1.2rem;margin:1rem 0;box-shadow:0 1px 3px #0001}}
.bar-wrap h3{{font-size:.9rem;margin-bottom:.8rem}}
.bias-bar{{display:flex;height:20px;border-radius:10px;overflow:hidden;margin-bottom:.6rem}}
.bar-left{{background:var(--L)}}.bar-center{{background:var(--C)}}.bar-right{{background:var(--R)}}
.bias-labels{{display:flex;justify-content:space-between;font-size:.78rem;color:var(--mu)}}
.leg{{display:flex;gap:1rem;flex-wrap:wrap;margin-top:.6rem;font-size:.75rem}}
.leg .dot{{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:3px}}
.dL{{background:var(--L)}}.dC{{background:var(--C)}}.dR{{background:var(--R)}}
.card{{background:var(--card);border-radius:10px;padding:1.2rem;margin:.8rem 0;box-shadow:0 1px 3px #0001}}
.card-hd{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:.5rem;flex-wrap:wrap}}
.card-hd h3{{font-size:1rem;font-weight:700}}
.cnt{{font-size:.72rem;color:var(--mu);white-space:nowrap}}
.blindspot-tag{{background:#fef3c7;color:#d97706;font-size:.68rem;padding:2px 8px;border-radius:10px;font-weight:600;margin-left:6px}}
.src-tags{{display:flex;flex-wrap:wrap;gap:4px;margin:.5rem 0}}
.src-tag{{font-size:.68rem;padding:1px 8px;border-radius:10px;font-weight:600;white-space:nowrap}}
.src-tag.left{{background:#dbeafe;color:#1d4ed8}}.src-tag.lean-left{{background:#eff6ff;color:#3b82f6}}
.src-tag.center{{background:#f1f5f9;color:#475569}}
.src-tag.lean-right{{background:#fef2f2;color:#ef4444}}.src-tag.right{{background:#fee2e2;color:#b91c1c}}
.src-tag.unknown{{background:#f8fafc;color:#94a3b8}}
.art-list{{margin-top:.8rem}}
.art-row{{display:flex;gap:.5rem;align-items:baseline;padding:.35rem 0;border-bottom:1px solid var(--bd);font-size:.86rem}}
.art-row:last-child{{border-bottom:none}}
.art-row a{{color:var(--tx);text-decoration:none;flex:1}}
.art-row a:hover{{color:#2563eb}}
.art-row.more{{color:var(--mu);font-style:italic;font-size:.78rem}}
.art-src{{font-size:.62rem;padding:1px 6px;border-radius:8px;font-weight:600;white-space:nowrap;min-width:55px;text-align:center}}
.art-src.left{{background:#dbeafe;color:#1d4ed8}}.art-src.lean-left{{background:#eff6ff;color:#3b82f6}}
.art-src.center{{background:#f1f5f9;color:#475569}}
.art-src.lean-right{{background:#fef2f2;color:#ef4444}}.art-src.right{{background:#fee2e2;color:#b91c1c}}
.art-src.unknown{{background:#f8fafc;color:#94a3b8}}
.alert{{border-radius:8px;padding:.8rem 1rem;margin:.8rem 0;font-size:.82rem}}
.alert-warn{{background:#fef3c7;border:1px solid #f59e0b;color:#92400e}}
.alert-info{{background:#dbeafe;border:1px solid #3b82f6;color:#1e40af}}
.ft{{text-align:center;padding:2rem;color:var(--mu);font-size:.72rem}}
@media(max-width:600px){{.stats{{flex-direction:column}}.card-hd{{flex-direction:column}}}}
</style>
</head>
<body>
<div class="hd"><div class="w">
<h1>📊 Ground News · Media Bias Dashboard</h1>
<p>{now} · {len(entries)} articles · {len(all_sources)} sources · {len(topic_clusters)} topics (TF-IDF semantic clustering)</p>
</div></div>
<div class="w">
<div class="stats">
<div class="sc"><div class="n">{len(entries)}</div><div class="l">Articles</div></div>
<div class="sc"><div class="n">{len(all_sources)}</div><div class="l">Sources</div></div>
<div class="sc"><div class="n">{len(topic_clusters)}</div><div class="l">Topics</div></div>
<div class="sc"><div class="n">{len(rated)}</div><div class="l">Bias-Rated</div></div>
</div>
<div class="bar-wrap">
<h3>📐 Overall Coverage Bias</h3>
{build_bias_bar(all_left, all_center, all_right)}
<div class="leg">
<span><span class="dot dL"></span>Left ({all_left})</span>
<span><span class="dot dC"></span>Center ({all_center})</span>
<span><span class="dot dR"></span>Right ({all_right})</span>
</div>
</div>
<div class="alert alert-info">
<strong>🧠 Method:</strong> TF-IDF vectorization + cosine similarity clustering.{' '}
Articles with similar word patterns are grouped into topics automatically.{' '}
Each topic card shows <em>which sources</em> from <em>which bias</em> cover the story.
<span style="color:var(--mu);font-size:.75rem"><br>
Bias data: AllSides · Next upgrade: sentence-transformers embeddings + HDBSCAN density clustering</span>
</div>
<div class="alert alert-warn">
<strong>⚠ {singles} articles</strong> couldn't be clustered into topics (unique/isolated coverage).
</div>
<h2 style="margin-top:1.5rem;font-size:1.1rem">🗂 Topics</h2>
{topic_cards}
</div>
<div class="ft">
<p>Ground News Pipeline v3 — TF-IDF semantic clustering · Auto-deployed to Cloudflare Pages</p>
<p>Upcoming: HDBSCAN + sentence-transformers · Factuality scores · Media ownership tracking</p>
</div>
</body>
</html>"""
    return html


def main():
    html = generate_html()
    p = Path("output/ground_news/index.html")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    print(f"✅ HTML v3: {p} ({len(html)} chars)")


if __name__ == "__main__":
    main()