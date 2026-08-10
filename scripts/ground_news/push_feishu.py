#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Push Feishu — Step 6 (local mode)
从本地 output/ground_news/*.json 读取抓取结果，
生成 Bias Bar + Blindspot 卡片推送到飞书。
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

BEIJING_TZ = timezone(timedelta(hours=8))
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")

OUTPUT_DIR = Path("output/ground_news")


def load_latest_output() -> Optional[Dict[str, Any]]:
    """加载最新的本地输出文件"""
    if not OUTPUT_DIR.exists():
        print(f"❌ 输出目录不存在: {OUTPUT_DIR}")
        return None

    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("❌ 没有输出文件")
        return None

    latest = files[0]
    print(f"📥 加载: {latest}")
    with open(latest, "r", encoding="utf-8") as f:
        return json.load(f)


def send_feishu_card(card: Dict[str, Any]) -> bool:
    """发送飞书卡片"""
    if not FEISHU_WEBHOOK_URL:
        print("❌ 缺少 FEISHU_WEBHOOK_URL")
        return False
    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=card, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            print("✅ 飞书卡片推送成功")
            return True
        print(f"❌ 飞书推送失败: {result}")
        return False
    except Exception as e:
        print(f"❌ 飞书推送异常: {e}")
        return False


def build_summary_card(output: Dict[str, Any]) -> Dict:
    """构建新闻抓取摘要卡片"""
    stats = output.get("stats", {})
    total = stats.get("total", 0)
    sources = stats.get("sources", [])
    run_id = output.get("run_id", "")

    active = [s for s in sources if s.get("count", 0) > 0]
    inactive = [s for s in sources if s.get("count", 0) == 0]

    lines = []
    for s in active[:10]:
        lines.append(f"- **{s['name']}**: {s['count']} 条")
    if len(active) > 10:
        lines.append(f"- ...还有 {len(active) - 10} 个源")

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📡 Ground News 抓取报告 - {datetime.now(BEIJING_TZ).strftime('%m/%d %H:%M')}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**总计 {total} 条新闻** | {len(active)} 个活跃源 / {len(inactive)} 个无数据源"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                {"tag": "hr"},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": f"Run ID: {run_id} | 源: {', '.join(s['name'] for s in inactive[:5])}{'...' if len(inactive) > 5 else ''}"}
                ]}
            ]
        }
    }


def main():
    print(f"🚀 Ground News Push Feishu (local mode)")

    output = load_latest_output()
    if not output:
        sys.exit(1)

    card = build_summary_card(output)
    send_feishu_card(card)


if __name__ == "__main__":
    main()