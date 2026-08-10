#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Fetch Sources — Step 1 (No COS mode)
直接输出 JSON 到 stdout 供 downstream 消费，不写 COS。
待 COS 权限问题解决后切换回 cos_helper。
"""

import os
import sys
import json
import yaml
import feedparser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any
import hashlib

BEIJING_TZ = timezone(timedelta(hours=8))
CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "ground_news" / "sources.yaml"


def load_sources(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_rss_feed(feed_config: Dict[str, Any]) -> Dict[str, Any]:
    feed_id = feed_config["id"]
    url = feed_config["url"]
    name = feed_config.get("name", feed_id)
    print(f"[FETCH] RSS: {name} ({url})")

    try:
        parsed = feedparser.parse(url)
        entries = []
        for entry in parsed.entries[:30]:
            entries.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": entry.get("published", ""),
            })
        print(f"  ✅ {len(entries)} 条")
        return {"source_id": feed_id, "source_name": name, "source_type": "rss", "count": len(entries), "entries": entries}
    except Exception as e:
        print(f"  ❌ {e}")
        return {"source_id": feed_id, "source_name": name, "error": str(e), "entries": []}


def main():
    run_id = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
    print(f"🚀 Ground News Fetch Sources - Run ID: {run_id} (stdout mode)")

    sources_config = load_sources(CONFIG_PATH)
    rss_feeds = sources_config.get("rss_feeds", [])

    all_entries = []
    stats = {"run_id": run_id, "total": 0, "sources": []}

    print(f"\n📡 抓取 {len(rss_feeds)} 个 RSS 源...")
    for feed in rss_feeds:
        if feed.get("enabled", True):
            result = fetch_rss_feed(feed)
            all_entries.extend(result.get("entries", []))
            stats["sources"].append({
                "id": result["source_id"],
                "name": result.get("source_name", ""),
                "count": result.get("count", 0),
                "error": result.get("error"),
            })

    stats["total"] = len(all_entries)
    print(f"\n✅ 完成: {stats['total']} 条 (via stdout, not COS)")

    # 输出 JSON 供 stdout 消费 / downstream 使用
    output = {
        "run_id": run_id,
        "stats": stats,
        "entries": all_entries,
    }
    # Write to a local file for downstream steps
    output_dir = Path("output/ground_news")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{run_id}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"💾 本地存储: {output_file}")


if __name__ == "__main__":
    main()