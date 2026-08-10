#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Like Pipeline - Step 4: Bias Label
加载三大偏见评分源，为聚类内的每个新闻源打标，计算簇级偏见分布
产出：bias_bars/ 目录下的偏见条形图数据
"""

import os
import sys
import json
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import Counter

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# ============ 配置区 ============
COS_ENDPOINT = os.getenv("S3_ENDPOINT_URL")
COS_BUCKET = os.getenv("S3_BUCKET_NAME")
COS_AK = os.getenv("S3_ACCESS_KEY_ID")
COS_SK = os.getenv("S3_SECRET_ACCESS_KEY")
COS_REGION = os.getenv("S3_REGION", "")

CLUSTERS_PREFIX = "ground_news/clusters/"
BIAS_META_PREFIX = "ground_news/meta/"
BIAS_BARS_PREFIX = "ground_news/bias_bars/"

# 三大评分源权重（可通过环境变量覆盖）
WEIGHT_ALLSIDES = float(os.getenv("BIAS_WEIGHT_ALLSIDES", "1.0"))
WEIGHT_ADFONTES = float(os.getenv("BIAS_WEIGHT_ADFONTES", "1.0"))
WEIGHT_MBFC = float(os.getenv("BIAS_WEIGHT_MBFC", "1.0"))

# 偏见标签标准化映射
BIAS_MAP = {
    # AllSides
    "left": "Left",
    "lean left": "Lean Left",
    "center": "Center",
    "lean right": "Lean Right",
    "right": "Right",
    # Ad Fontes Media
    "extreme left": "Left",
    "skews left": "Lean Left",
    "balanced": "Center",
    "skews right": "Lean Right",
    "extreme right": "Right",
    # MBFC
    "left": "Left",
    "left-center": "Lean Left",
    "least biased": "Center",
    "right-center": "Lean Right",
    "right": "Right",
    "questionable": "Questionable",
    "conspiracy": "Questionable",
    "pseudoscience": "Questionable",
}

# 偏见数值映射（用于加权平均）
BIAS_SCORE = {
    "Left": -2,
    "Lean Left": -1,
    "Center": 0,
    "Lean Right": 1,
    "Right": 2,
    "Questionable": 0,  # 不可靠源不参与偏见平均
}


def get_cos_client():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=COS_ENDPOINT,
        aws_access_key_id=COS_AK,
        aws_secret_access_key=COS_SK,
        region_name=COS_REGION,
        config=Config(signature_version="s3v4"),
    )


def download_from_cos(client, key: str) -> Optional[bytes]:
    try:
        response = client.get_object(Bucket=COS_BUCKET, Key=key)
        return response['Body'].read()
    except Exception as e:
        print(f"  ❌ 下载失败 {key}: {e}")
        return None


def upload_to_cos(client, key: str, data: Dict[str, Any], content_type: str = "application/json"):
    client.put_object(
        Bucket=COS_BUCKET,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType=content_type,
    )


def load_allsides_ratings(client) -> Dict[str, str]:
    """加载 AllSides 评分表"""
    key = f"{BIAS_META_PREFIX}allsides_ratings.csv"
    content = download_from_cos(client, key)
    if not content:
        print(f"  ⚠️ 未找到 AllSides 评分表: {key}")
        return {}
    
    ratings = {}
    try:
        reader = csv.DictReader(content.decode('utf-8-sig').splitlines())
        for row in reader:
            # 预期列: source_domain, bias_rating
            domain = row.get("source_domain", "").strip().lower()
            bias = row.get("bias_rating", "").strip()
            if domain and bias:
                ratings[domain] = BIAS_MAP.get(bias.lower(), bias)
    except Exception as e:
        print(f"  ❌ 解析 AllSides 失败: {e}")
    return ratings


def load_adfontes_ratings(client) -> Dict[str, str]:
    """加载 Ad Fontes Media 评分表"""
    key = f"{BIAS_META_PREFIX}adfontes_ratings.json"
    content = download_from_cos(client, key)
    if not content:
        print(f"  ⚠️ 未找到 Ad Fontes 评分表: {key}")
        return {}
    
    ratings = {}
    try:
        data = json.loads(content.decode('utf-8'))
        for item in data:
            domain = item.get("source_domain", "").strip().lower()
            bias = item.get("bias_rating", "").strip()
            if domain and bias:
                ratings[domain] = BIAS_MAP.get(bias.lower(), bias)
    except Exception as e:
        print(f"  ❌ 解析 Ad Fontes 失败: {e}")
    return ratings


def load_mbfc_ratings(client) -> Dict[str, str]:
    """加载 Media Bias/Fact Check 评分表"""
    key = f"{BIAS_META_PREFIX}mbfc_ratings.json"
    content = download_from_cos(client, key)
    if not content:
        print(f"  ⚠️ 未找到 MBFC 评分表: {key}")
        return {}
    
    ratings = {}
    try:
        data = json.loads(content.decode('utf-8'))
        for item in data:
            domain = item.get("source_domain", "").strip().lower()
            bias = item.get("bias_rating", "").strip()
            if domain and bias:
                ratings[domain] = BIAS_MAP.get(bias.lower(), bias)
    except Exception as e:
        print(f"  ❌ 解析 MBFC 失败: {e}")
    return ratings


def merge_ratings(allsides: Dict, adfontes: Dict, mbfc: Dict) -> Dict[str, Dict]:
    """合并三源评分，计算加权平均"""
    all_domains = set(allsides.keys()) | set(adfontes.keys()) | set(mbfc.keys())
    merged = {}
    
    for domain in all_domains:
        scores = []
        weights = []
        labels = []
        
        if domain in allsides:
            label = allsides[domain]
            if label in BIAS_SCORE:
                scores.append(BIAS_SCORE[label])
                weights.append(WEIGHT_ALLSIDES)
                labels.append(("AllSides", label))
        
        if domain in adfontes:
            label = adfontes[domain]
            if label in BIAS_SCORE:
                scores.append(BIAS_SCORE[label])
                weights.append(WEIGHT_ADFONTES)
                labels.append(("AdFontes", label))
        
        if domain in mbfc:
            label = mbfc[domain]
            if label in BIAS_SCORE:
                scores.append(BIAS_SCORE[label])
                weights.append(WEIGHT_MBFC)
                labels.append(("MBFC", label))
        
        if scores:
            weighted_avg = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
            # 映射回标签
            if weighted_avg <= -1.5:
                final_label = "Left"
            elif weighted_avg <= -0.5:
                final_label = "Lean Left"
            elif weighted_avg <= 0.5:
                final_label = "Center"
            elif weighted_avg <= 1.5:
                final_label = "Lean Right"
            else:
                final_label = "Right"
        else:
            weighted_avg = 0
            final_label = "Unknown"
        
        merged[domain] = {
            "domain": domain,
            "bias_label": final_label,
            "bias_score": round(weighted_avg, 2),
            "sources": labels,
            "source_count": len(labels),
        }
    
    return merged


def extract_domain(url: str) -> str:
    """从 URL 提取域名"""
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def load_latest_clusters(client) -> Optional[Dict[str, Any]]:
    """加载最新的聚类结果"""
    import boto3
    paginator = client.get_paginator('list_objects_v2')
    latest_key = None
    latest_time = None
    
    for page in paginator.paginate(Bucket=COS_BUCKET, Prefix=CLUSTERS_PREFIX):
        if 'Contents' in page:
            for obj in page['Contents']:
                if obj['Key'].endswith('.json'):
                    if latest_time is None or obj['LastModified'] > latest_time:
                        latest_time = obj['LastModified']
                        latest_key = obj['Key']
    
    if not latest_key:
        return None
    
    content = download_from_cos(client, latest_key)
    if content:
        return json.loads(content.decode('utf-8'))
    return None


def compute_cluster_bias_bars(clusters: List[Dict], merged_ratings: Dict[str, Dict]) -> List[Dict]:
    """为每个簇计算偏见分布（Bias Bar 数据）"""
    results = []
    
    for cluster in clusters:
        cluster_id = cluster["cluster_id"]
        items = cluster["items"]
        
        # 统计每个源域名的偏见
        bias_counter = Counter()
        domain_counter = Counter()
        
        for item in items:
            url = item.get("url", "")
            domain = extract_domain(url)
            if not domain:
                continue
            
            domain_counter[domain] += 1
            
            if domain in merged_ratings:
                bias_label = merged_ratings[domain]["bias_label"]
                if bias_label != "Unknown":
                    bias_counter[bias_label] += 1
            else:
                bias_counter["Unknown"] += 1
        
        total = sum(bias_counter.values())
        if total == 0:
            continue
        
        # 计算百分比
        bias_dist = {}
        for label in ["Left", "Lean Left", "Center", "Lean Right", "Right", "Unknown"]:
            count = bias_counter.get(label, 0)
            bias_dist[label] = {
                "count": count,
                "percentage": round(count / total * 100, 1),
            }
        
        # 判定盲点
        left_pct = bias_dist["Left"]["percentage"] + bias_dist["Lean Left"]["percentage"]
        right_pct = bias_dist["Right"]["percentage"] + bias_dist["Lean Right"]["percentage"]
        center_pct = bias_dist["Center"]["percentage"]
        
        blindspot = None
        if left_pct > 70 and right_pct < 10:
            blindspot = "Right Blindspot"
        elif right_pct > 70 and left_pct < 10:
            blindspot = "Left Blindspot"
        
        # 簇热度（文章数 × 源唯一数）
        heat = cluster["size"] * len(domain_counter)
        
        results.append({
            "cluster_id": cluster_id,
            "representative_title": cluster.get("representative_title", ""),
            "representative_url": cluster.get("representative_url", ""),
            "size": cluster["size"],
            "unique_sources": len(domain_counter),
            "heat": heat,
            "bias_distribution": bias_dist,
            "left_percentage": round(left_pct, 1),
            "center_percentage": round(center_pct, 1),
            "right_percentage": round(right_pct, 1),
            "blindspot": blindspot,
            "source_bias_details": {
                domain: merged_ratings.get(domain, {"bias_label": "Unknown", "bias_score": 0})
                for domain in domain_counter.keys()
            },
        })
    
    # 按热度排序
    results.sort(key=lambda x: x["heat"], reverse=True)
    return results


def main(run_id: Optional[str] = None):
    print(f"🚀 Ground News Bias Label - Run ID: {run_id or 'auto'}")
    
    required = ["S3_ENDPOINT_URL", "S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ 缺少环境变量: {', '.join(missing)}")
        sys.exit(1)
    
    client = get_cos_client()
    
    # 1. 加载三大评分源
    print("📊 加载偏见评分源...")
    allsides = load_allsides_ratings(client)
    adfontes = load_adfontes_ratings(client)
    mbfc = load_mbfc_ratings(client)
    print(f"  AllSides: {len(allsides)} 个域名")
    print(f"  Ad Fontes: {len(adfontes)} 个域名")
    print(f"  MBFC: {len(mbfc)} 个域名")
    
    # 2. 合并评分
    merged = merge_ratings(allsides, adfontes, mbfc)
    print(f"  合并后: {len(merged)} 个域名有评分")
    
    # 保存合并表
    meta_key = f"{BIAS_META_PREFIX}merged_ratings_{datetime.now(BEIJING_TZ).strftime('%Y%m%d')}.json"
    upload_to_cos(client, meta_key, merged)
    print(f"  💾 合并评分表已保存: {meta_key}")
    
    # 3. 加载聚类结果
    if run_id:
        cluster_key = f"{CLUSTERS_PREFIX}{run_id}.json"
        content = download_from_cos(client, cluster_key)
        if not content:
            print(f"❌ 未找到聚类结果: {cluster_key}")
            sys.exit(1)
        cluster_data = json.loads(content.decode('utf-8'))
    else:
        cluster_data = load_latest_clusters(client)
        if not cluster_data:
            print("❌ 未找到任何聚类结果")
            sys.exit(1)
        run_id = cluster_data.get("run_id", "")
    
    clusters = cluster_data.get("clusters", [])
    print(f"📥 加载聚类结果: {len(clusters)} 个簇")
    
    # 4. 计算 Bias Bar
    bias_bars = compute_cluster_bias_bars(clusters, merged)
    
    # 5. 统计盲点
    blindspot_clusters = [b for b in bias_bars if b["blindspot"]]
    print(f"🎯 盲点簇: {len(blindspot_clusters)} 个")
    for b in blindspot_clusters[:5]:
        print(f"  - 簇 {b['cluster_id']}: {b['blindspot']} ({b['representative_title'][:50]})")
    
    # 6. 上传 Bias Bar 结果
    result = {
        "run_id": run_id,
        "total_clusters": len(bias_bars),
        "blindspot_clusters": len(blindspot_clusters),
        "bias_bars": bias_bars,
        "created_at": datetime.now(BEIJING_TZ).isoformat(),
        "weights": {
            "AllSides": WEIGHT_ALLSIDES,
            "AdFontes": WEIGHT_ADFONTES,
            "MBFC": WEIGHT_MBFC,
        },
    }
    
    bar_key = f"{BIAS_BARS_PREFIX}{run_id}.json"
    upload_to_cos(client, bar_key, result)
    print(f"\n✅ 完成: Bias Bar 已上传 -> {bar_key}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", help="指定 run_id，默认自动取最新")
    args = parser.parse_args()
    main(args.run_id)