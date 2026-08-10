#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Step 1: Fetch Sources v2
- RSS 抓取（20+ 源）
- agent-reach 中文搜索
- COS 上传（通过 TrendRadar RemoteStorageBackend）
- 本地 JSON 兜底
"""

import os, sys, json, yaml, hashlib, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse

import feedparser

BEIJING_TZ = timezone(timedelta(hours=8))
CONFIG_DIR = Path("config/ground_news")
OUTPUT_DIR = Path("output/ground_news")


def load_config() -> Dict:
    path = CONFIG_DIR / "sources.yaml"
    if not path.exists():
        print(f"❌ 配置文件不存在: {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_rss(feed_config: Dict) -> List[Dict]:
    """抓取单个 RSS 源"""
    entries = []
    try:
        feed = feedparser.parse(feed_config["url"])
        if feed.bozo and not feed.entries:
            print(f"  ⚠️ 解析警告: {feed_config['name']}")
        for entry in feed.entries:
            entries.append({
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", "").strip(),
                "published": entry.get("published", ""),
                "source_name": feed_config["name"],
                "source_id": feed_config["id"],
                "category": feed_config.get("category", ""),
                "domain": urlparse(entry.get("link", "")).netloc.lower().replace("www.", ""),
            })
    except Exception as e:
        print(f"  ❌ 错误: {feed_config['name']} — {e}")
    return entries


def fetch_agent_reach(agent_config: Dict) -> List[Dict]:
    """调用 agent-reach 搜索"""
    entries = []
    if not agent_config.get("enabled", False):
        return entries

    print(f"\n🔍 agent-reach 搜索...")
    for platform in agent_config.get("platforms", []):
        pname = platform["name"]
        for query in platform.get("queries", []):
            try:
                if pname == "exa_search":
                    result = subprocess.run(
                        ["mcporter", "call", f'exa.web_search_exa(query: "{query}", numResults: 5)'],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.stdout:
                        # 解析 mcporter 输出
                        print(f"  ✅ exa: {query}")
                elif pname == "bilibili":
                    cmd = platform["command"].replace("{query}", query).split()
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    if result.stdout:
                        print(f"  ✅ bili: {query}")
            except subprocess.TimeoutExpired:
                print(f"  ⏱️ timeout: {pname} / {query}")
            except FileNotFoundError:
                print(f"  ⚠️ 未安装: {pname}")
            except Exception as e:
                print(f"  ❌ {pname}: {e}")
    return entries


def upload_to_cos(key: str, data: bytes):
    """通过 RemoteStorageBackend 上传到 COS"""
    try:
        from trendradar.storage.remote import RemoteStorageBackend

        endpoint = os.getenv("S3_ENDPOINT_URL", "")
        bucket = os.getenv("S3_BUCKET_NAME", "")
        ak = os.getenv("S3_ACCESS_KEY_ID", "")
        sk = os.getenv("S3_SECRET_ACCESS_KEY", "")
        region = os.getenv("S3_REGION", "")

        if not all([endpoint, bucket, ak, sk]):
            print(f"  ℹ️ COS env vars missing, skip upload")
            return False

        storage = RemoteStorageBackend(
            bucket_name=bucket,
            access_key_id=ak,
            secret_access_key=sk,
            endpoint_url=endpoint,
            region=region,
        )
        storage.s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentLength=len(data),
            ContentType="application/json",
        )
        print(f"  ✅ COS: {key} ({len(data)} bytes)")
        return True
    except ImportError:
        print(f"  ℹ️ trendradar not available, skip COS")
        return False
    except Exception as e:
        print(f"  ⚠️ COS upload failed: {e}")
        return False


def main():
    run_id = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
    print(f"🚀 Ground News Fetch v2 — Run ID: {run_id}\n")

    config = load_config()
    rss_feeds = config.get("rss_feeds", [])
    agent_config = config.get("agent_reach", {})

    # ===== 1. RSS =====
    all_entries = []
    source_stats = []

    print(f"📡 抓取 {len(rss_feeds)} 个 RSS 源...")
    for feed in rss_feeds:
        feed_entries = fetch_rss(feed)
        count = len(feed_entries)
        all_entries.extend(feed_entries)
        source_stats.append({"name": feed["name"], "count": count})
        icon = "✅" if count > 0 else "⚪"
        print(f"  {icon} {feed['name']}: {count} 条")

    # ===== 2. agent-reach =====
    agent_entries = fetch_agent_reach(agent_config)
    # mark agent-reach entries
    for e in agent_entries:
        e["source_name"] = e.get("source_name", "agent-reach")
        e["source_id"] = "agent_reach"
    all_entries.extend(agent_entries)

    # ===== 3. 构建输出 =====
    output = {
        "run_id": run_id,
        "fetched_at": datetime.now(BEIJING_TZ).isoformat(),
        "stats": {
            "total": len(all_entries),
            "rss_total": len(all_entries) - len(agent_entries),
            "agent_reach_total": len(agent_entries),
            "sources": source_stats,
        },
        "entries": all_entries,
    }

    # ===== 4. 本地保存 =====
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = OUTPUT_DIR / f"{run_id}.json"
    json_data = json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8")
    with open(local_path, "wb") as f:
        f.write(json_data)
    print(f"\n💾 本地: {local_path} ({len(json_data)} bytes)")

    # ===== 5. COS 上传 =====
    cos_key = f"ground_news/raw/{run_id}.json"
    upload_to_cos(cos_key, json_data)

    print(f"\n✅ 完成: {output['stats']['total']} 条 ({output['stats']['rss_total']} RSS + {output['stats']['agent_reach_total']} agent-reach)")


if __name__ == "__main__":
    main()