#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Step 1: Fetch Sources v3
- RSS 抓取（18+ 活跃源）
- agent-reach 中文搜索（自动安装 CLI）
- COS 上传（修复：.strip() 密钥 + upload_file + tempfile）
"""

import os, sys, json, yaml, subprocess, tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any
from urllib.parse import urlparse
import feedparser

BEIJING_TZ = timezone(timedelta(hours=8))
CONFIG_DIR = Path("config/ground_news")
OUTPUT_DIR = Path("output/ground_news")


def load_config() -> Dict:
    with open(CONFIG_DIR / "sources.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_rss(feed_config: Dict) -> List[Dict]:
    entries = []
    try:
        feed = feedparser.parse(feed_config["url"])
        if feed.bozo and not feed.entries:
            print(f"  ⚠️ parse warning: {feed_config['name']}")
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
        print(f"  ❌ {feed_config['name']}: {e}")
    return entries


def ensure_agent_reach_tools():
    """确保 agent-reach CLI 工具已安装"""
    tools_ok = True
    # mcporter (Exa search)
    try:
        subprocess.run(["mcporter", "--version"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  📦 Installing mcporter (Exa search)...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "mcporter"], capture_output=True, timeout=60)
            tools_ok = True
        except Exception:
            tools_ok = False
    
    # bili-cli (B站)
    try:
        subprocess.run(["bili", "--version"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  📦 Installing bili-cli...")
        try:
            subprocess.run(["npm", "install", "-g", "bili-cli"], capture_output=True, timeout=60)
        except Exception:
            pass  # non-critical

    return tools_ok


def fetch_agent_reach(agent_config: Dict) -> List[Dict]:
    if not agent_config.get("enabled", False):
        return []

    print(f"\n🔍 agent-reach search...")
    tools_available = ensure_agent_reach_tools()
    if not tools_available:
        print("  ⚠️ CLI tools unavailable, skipping agent-reach")
        return []

    entries = []
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
                        print(f"  ✅ exa: {query}")
                elif pname == "bilibili":
                    cmd = platform["command"].replace("{query}", query).split()
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    if result.stdout:
                        print(f"  ✅ bili: {query}")
            except subprocess.TimeoutExpired:
                print(f"  ⏱️ timeout: {pname}")
            except FileNotFoundError:
                print(f"  ⚠️ not found: {pname}")
            except Exception as e:
                print(f"  ❌ {pname}: {e}")
    return entries


def upload_to_cos(key: str, data: bytes):
    """COS 上传：RemoteStorageBackend + upload_file（修复签名问题）"""
    try:
        from trendradar.storage.remote import RemoteStorageBackend

        # 关键修复：strip() 去掉 secrets 中可能的换行符
        endpoint = os.getenv("S3_ENDPOINT_URL", "").strip()
        bucket = os.getenv("S3_BUCKET_NAME", "").strip()
        ak = os.getenv("S3_ACCESS_KEY_ID", "").strip()
        sk = os.getenv("S3_SECRET_ACCESS_KEY", "").strip()
        region = os.getenv("S3_REGION", "").strip()

        if not all([endpoint, bucket, ak, sk]):
            print(f"  ℹ️ COS env vars empty after strip, skip upload")
            return False

        storage = RemoteStorageBackend(
            bucket_name=bucket,
            access_key_id=ak,
            secret_access_key=sk,
            endpoint_url=endpoint,
            region=region if region else None,
        )

        # 改用 upload_file（SDK 推荐方式，自动处理 multipart + MD5）
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            storage.s3_client.upload_file(
                Filename=tmp_path,
                Bucket=bucket,
                Key=key,
                ExtraArgs={"ContentType": "application/json"},
            )
            print(f"  ✅ COS uploaded: {key} ({len(data)} bytes)")
            return True
        finally:
            os.unlink(tmp_path)

    except ImportError:
        print(f"  ℹ️ trendradar unavailable, skip COS")
        return False
    except Exception as e:
        print(f"  ⚠️ COS: {e}")
        return False


def main():
    run_id = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
    print(f"🚀 Ground News Fetch v3 — {run_id}\n")

    config = load_config()
    rss_feeds = config.get("rss_feeds", [])
    agent_config = config.get("agent_reach", {})

    # ===== RSS =====
    all_entries, source_stats = [], []
    print(f"📡 {len(rss_feeds)} RSS sources...")
    for feed in rss_feeds:
        fe = fetch_rss(feed)
        all_entries.extend(fe)
        source_stats.append({"name": feed["name"], "count": len(fe)})
        print(f"  {'✅' if fe else '⚪'} {feed['name']}: {len(fe)}")

    # ===== agent-reach =====
    ar_entries = fetch_agent_reach(agent_config)
    for e in ar_entries:
        e.setdefault("source_name", "agent-reach")
        e.setdefault("source_id", "agent_reach")
    all_entries.extend(ar_entries)

    # ===== 输出 =====
    output = {
        "run_id": run_id,
        "fetched_at": datetime.now(BEIJING_TZ).isoformat(),
        "stats": {
            "total": len(all_entries),
            "rss_total": len(all_entries) - len(ar_entries),
            "agent_reach_total": len(ar_entries),
            "sources": source_stats,
        },
        "entries": all_entries,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_data = json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8")
    local_path = OUTPUT_DIR / f"{run_id}.json"
    local_path.write_bytes(json_data)
    print(f"\n💾 Local: {local_path} ({len(json_data)} bytes)")

    # COS
    upload_to_cos(f"ground_news/raw/{run_id}.json", json_data)

    print(f"\n✅ Done: {output['stats']['total']} total")


if __name__ == "__main__":
    main()