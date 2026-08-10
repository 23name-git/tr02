#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Like Pipeline - Step 1: Fetch Sources
抓取新闻源：RSS + agent-reach 社交/视频平台关键词搜索
产出：raw/ 目录下的原始 HTML/JSON 文件，供后续清洗使用
"""

import os
import sys
import json
import yaml
import feedparser
import httpx
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import hashlib

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# ============ 配置区 ============
COS_ENDPOINT = os.getenv("S3_ENDPOINT_URL")
COS_BUCKET = os.getenv("S3_BUCKET_NAME")
COS_AK = os.getenv("S3_ACCESS_KEY_ID")
COS_SK = os.getenv("S3_SECRET_ACCESS_KEY")
COS_REGION = os.getenv("S3_REGION", "")

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "ground_news" / "sources.yaml"
RAW_PREFIX = "ground_news/raw/"


def load_sources(config_path: Path) -> Dict[str, Any]:
    """加载源配置文件"""
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_cos_client():
    """创建 boto3 S3 客户端（复用 TrendRadar 的 COS 配置逻辑：SigV2 + virtual-hosted style）"""
    import boto3
    from botocore.config import Config
    
    # COS 必须用 SigV2 (signature_version='s3')，配合 virtual-hosted style
    use_sigv2 = "myqcloud.com" in COS_ENDPOINT.lower() or "aliyuncs.com" in COS_ENDPOINT.lower()
    signature_version = 's3' if use_sigv2 else 's3v4'
    
    # 调试：打印配置（不打印密钥）
    print(f"[DEBUG] COS config: endpoint={COS_ENDPOINT}, region={COS_REGION}, sigv2={use_sigv2}, sig_version={signature_version}")
    
    client_kwargs = {
        "endpoint_url": COS_ENDPOINT,
        "aws_access_key_id": COS_AK,
        "aws_secret_access_key": COS_SK,
        "config": Config(
            signature_version=signature_version,
            s3={"addressing_style": "virtual"},
        ),
    }
    # COS SigV2 不需要 region_name，传了反而可能导致签名不匹配
    # 只有非 COS (SigV4) 才需要 region_name
    if not use_sigv2 and COS_REGION:
        client_kwargs["region_name"] = COS_REGION
    
    return boto3.client("s3", **client_kwargs)


def upload_to_cos(client, key: str, content: bytes, content_type: str = "application/octet-stream"):
    """上传内容到 COS"""
    client.put_object(
        Bucket=COS_BUCKET,
        Key=key,
        Body=content,
        ContentType=content_type,
    )


def fetch_rss_feed(feed_config: Dict[str, Any], client, run_id: str) -> Dict[str, Any]:
    """抓取单个 RSS 源"""
    feed_id = feed_config["id"]
    url = feed_config["url"]
    name = feed_config.get("name", feed_id)
    
    print(f"[FETCH] RSS: {name} ({url})")
    
    try:
        # 使用 feedparser 抓取
        parsed = feedparser.parse(url)
        
        if parsed.bozo and parsed.bozo_exception:
            print(f"  ⚠️ 解析警告: {parsed.bozo_exception}")
        
        entries = []
        for entry in parsed.entries[:50]:  # 限制每源最多 50 条
            entries.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": entry.get("published", ""),
                "author": entry.get("author", ""),
                "tags": [tag.term for tag in entry.get("tags", [])],
            })
        
        # 构建原始数据
        raw_data = {
            "source_id": feed_id,
            "source_name": name,
            "source_type": "rss",
            "url": url,
            "fetched_at": datetime.now(BEIJING_TZ).isoformat(),
            "run_id": run_id,
            "entries": entries,
        }
        
        # 上传到 COS
        key = f"{RAW_PREFIX}rss/{feed_id}/{run_id}.json"
        upload_to_cos(client, key, json.dumps(raw_data, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
        print(f"  ✅ 已上传: {key} ({len(entries)} 条)")
        
        return {"source_id": feed_id, "status": "success", "count": len(entries), "key": key}
        
    except Exception as e:
        print(f"  ❌ 抓取失败: {e}")
        return {"source_id": feed_id, "status": "error", "error": str(e)}


def fetch_agent_reach_search(query: str, platforms: List[str], client, run_id: str) -> Dict[str, Any]:
    """调用 agent-reach CLI 搜索社交/视频平台（占位：后续接入真实 CLI）"""
    # TODO: 实际调用 agent-reach CLI
    # 示例: agent-reach search "AI 产品运营" --platforms xiaohongshu,bilibili,twitter --format json
    print(f"[FETCH] agent-reach search: '{query}' on {platforms}")
    
    # 占位返回空结果
    raw_data = {
        "source_type": "agent_reach",
        "query": query,
        "platforms": platforms,
        "fetched_at": datetime.now(BEIJING_TZ).isoformat(),
        "run_id": run_id,
        "results": [],
    }
    
    key = f"{RAW_PREFIX}agent_reach/{hashlib.md5(query.encode()).hexdigest()[:8]}/{run_id}.json"
    upload_to_cos(client, key, json.dumps(raw_data, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
    
    return {"query": query, "status": "skipped", "key": key, "note": "agent-reach CLI integration pending"}


def main():
    """主入口"""
    run_id = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
    print(f"🚀 Ground News Fetch Sources - Run ID: {run_id}")
    
    # 校验环境变量
    required = ["S3_ENDPOINT_URL", "S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ 缺少环境变量: {', '.join(missing)}")
        sys.exit(1)
    
    # 加载配置
    sources_config = load_sources(CONFIG_PATH)
    rss_feeds = sources_config.get("rss_feeds", [])
    agent_reach_queries = sources_config.get("agent_reach_queries", [])
    
    # 创建 COS 客户端
    client = get_cos_client()
    
    results = {"run_id": run_id, "rss": [], "agent_reach": []}
    
    # 1. 抓取 RSS
    print(f"\n📡 抓取 {len(rss_feeds)} 个 RSS 源...")
    for feed in rss_feeds:
        if feed.get("enabled", True):
            result = fetch_rss_feed(feed, client, run_id)
            results["rss"].append(result)
    
    # 2. 抓取 agent-reach 搜索（占位）
    print(f"\n🔍 执行 {len(agent_reach_queries)} 个 agent-reach 搜索...")
    for query_config in agent_reach_queries:
        query = query_config["query"]
        platforms = query_config.get("platforms", ["xiaohongshu", "bilibili", "twitter", "reddit"])
        result = fetch_agent_reach_search(query, platforms, client, run_id)
        results["agent_reach"].append(result)
    
    # 汇总上传
    summary_key = f"{RAW_PREFIX}_runs/{run_id}_summary.json"
    upload_to_cos(client, summary_key, json.dumps(results, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
    
    success_rss = sum(1 for r in results["rss"] if r["status"] == "success")
    total_entries = sum(r.get("count", 0) for r in results["rss"])
    print(f"\n✅ 完成: RSS 成功 {success_rss}/{len(rss_feeds)}, 共 {total_entries} 条; Agent-Reach {len(results['agent_reach'])} 个查询")
    print(f"📋 汇总: {summary_key}")


if __name__ == "__main__":
    main()