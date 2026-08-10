#!/usr/bin/env python3
"""Ground News Step 6: Push Feishu v2 — 含 Cloudflare Pages 链接"""
import os, sys, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_DIR = Path("output/ground_news")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK_URL")
# Cloudflare Pages 部署别名 URL（master 分支固定）
CF_PAGES_URL = os.getenv("CF_PAGES_URL", "https://master.tr02.pages.dev")


def load_latest() -> Optional[Dict]:
    if not OUTPUT_DIR.exists():
        return None
    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return json.loads(files[0].read_text(encoding="utf-8")) if files else None


def send_card(card: Dict) -> bool:
    if not FEISHU_WEBHOOK:
        print("❌ FEISHU_WEBHOOK_URL missing")
        return False
    r = requests.post(FEISHU_WEBHOOK, json=card, timeout=30)
    ok = r.json().get("code") == 0
    print(f"{'✅' if ok else '❌'} Feishu: {r.json().get('msg','')}")
    return ok


def build_card(output: Dict) -> Dict:
    stats = output.get("stats", {})
    total = stats.get("total", 0)
    sources = stats.get("sources", [])
    run_id = output.get("run_id", "")
    now = datetime.now(BEIJING_TZ).strftime("%m/%d %H:%M")

    active = [s for s in sources if s.get("count", 0) > 0]
    inactive = [s for s in sources if s.get("count", 0) == 0]

    lines = [f"**总计 {total} 篇** | {len(active)} 活跃源 / {len(inactive)} 零数据源"]
    lines.append("")
    for s in active[:8]:
        lines.append(f"• **{s['name']}**: {s['count']} 篇")
    if len(active) > 8:
        lines.append(f"• ...还有 {len(active)-8} 个源")

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📡 Ground News · {now}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"🌐 **Dashboard**: [{CF_PAGES_URL}]({CF_PAGES_URL})"}},
                {"tag": "hr"},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": f"Run: {run_id} | Bias: AllSides | Auto-deployed to Cloudflare Pages"}
                ]}
            ]
        }
    }


def main():
    output = load_latest()
    if not output:
        sys.exit(1)
    card = build_card(output)
    send_card(card)


if __name__ == "__main__":
    main()