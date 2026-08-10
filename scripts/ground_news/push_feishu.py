#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Like Pipeline - Step 6: Push to Feishu
发送 Bias Bar 卡片和 Blindspot 卡片到飞书群
复用现有 send_feishu 逻辑，新增卡片模版
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# ============ 配置区 ============
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")

COS_ENDPOINT = os.getenv("S3_ENDPOINT_URL")
COS_BUCKET = os.getenv("S3_BUCKET_NAME")
COS_AK = os.getenv("S3_ACCESS_KEY_ID")
COS_SK = os.getenv("S3_SECRET_ACCESS_KEY")
COS_REGION = os.getenv("S3_REGION", "")

REPORTS_PREFIX = "ground_news/reports/"


def get_cos_client():
    """创建 boto3 S3 客户端（完全复用 TrendRadar RemoteStorageBackend 的配置逻辑）"""
    import boto3
    from botocore.config import Config

    use_sigv2 = "myqcloud.com" in COS_ENDPOINT.lower() or "aliyuncs.com" in COS_ENDPOINT.lower()
    signature_version = 's3' if use_sigv2 else 's3v4'

    print(f"[DEBUG] COS: endpoint={COS_ENDPOINT}, region={COS_REGION}, sig={signature_version}")

    client_kwargs = {
        "endpoint_url": COS_ENDPOINT,
        "aws_access_key_id": COS_AK,
        "aws_secret_access_key": COS_SK,
        "config": Config(
            signature_version=signature_version,
            s3={"addressing_style": "virtual"},
        ),
    }
    # 与 RemoteStorageBackend 完全一致：region 非空时传入 region_name
    if COS_REGION:
        client_kwargs["region_name"] = COS_REGION

    return boto3.client("s3", **client_kwargs)


def download_from_cos(client, key: str) -> Optional[bytes]:
    try:
        response = client.get_object(Bucket=COS_BUCKET, Key=key)
        return response['Body'].read()
    except Exception as e:
        print(f"  ❌ 下载失败 {key}: {e}")
        return None


def load_latest_report(client, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """加载最新的结构化报告"""
    if run_id:
        key = f"{REPORTS_PREFIX}{run_id}.json"
        content = download_from_cos(client, key)
        if content:
            return json.loads(content.decode('utf-8'))
        return None
    
    import boto3
    paginator = client.get_paginator('list_objects_v2')
    latest_key = None
    latest_time = None
    
    for page in paginator.paginate(Bucket=COS_BUCKET, Prefix=REPORTS_PREFIX):
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


def build_bias_bar_card(report: Dict[str, Any]) -> Dict[str, Any]:
    """构建 Bias Bar 卡片"""
    daily = report.get("daily_bias_bar", {})
    top_stories = daily.get("top_stories", [])[:5]  # 只展示前5
    
    # 偏见分布可视化函数
    def bias_bar_visual(breakdown: Dict[str, int]) -> str:
        total = sum(breakdown.values())
        if total == 0:
            return "░░░░░░░░░░"
        bar = ""
        for label in ["Left", "Lean Left", "Center", "Lean Right", "Right"]:
            pct = breakdown.get(label, 0) / total * 100
            blocks = int(pct / 10)
            bar += "█" * blocks + "░" * (10 - blocks)
        return bar
    
    # 表格内容
    table_rows = []
    for story in top_stories:
        breakdown = story.get("bias_breakdown", {})
        blindspot = story.get("blindspot")
        blindspot_emoji = {"Left Blindspot": "🔵", "Right Blindspot": "🟡"}.get(blindspot, "⚪")
        table_rows.append(
            f"| {story['headline'][:40]} | `{bias_bar_visual(breakdown)}` | {blindspot_emoji} {blindspot or '无'} | {story.get('key_insight', '')[:50]} |"
        )
    
    table_md = (
        "| 事件 | 偏见分布 (L←→R) | 盲点 | 核心洞察 |\n"
        "|------|----------------|------|----------|\n"
        + "\n".join(table_rows)
    )
    
    summary = daily.get("summary", "暂无总结")
    
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 Ground News 偏见晴雨表 - {datetime.now(BEIJING_TZ).strftime('%m/%d %H:%M')}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**{summary}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": table_md}},
                {"tag": "hr"},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": "🔴 Left  🟠 Lean Left  ⚪ Center  🟢 Lean Right  🔵 Right  |  数据源: AllSides + Ad Fontes + MBFC 加权平均"}
                ]}
            ]
        }
    }


def build_blindspot_card(report: Dict[str, Any]) -> Dict[str, Any]:
    """构建 Blindspot 盲点预警卡片"""
    blindspot_report = report.get("blindspot_report", {})
    left_blindspots = blindspot_report.get("left_blindspots", [])[:5]
    right_blindspots = blindspot_report.get("right_blindspots", [])[:5]
    summary = blindspot_report.get("summary", "暂无盲点总结")
    
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"⚠️ **盲点预警** - {datetime.now(BEIJING_TZ).strftime('%m/%d %H:%M')}"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
        {"tag": "hr"},
    ]
    
    if left_blindspots:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "### 🔵 左盲点（右媒热炒、左媒缺位）"}})
        for b in left_blindspots:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"- **{b['headline'][:60]}**\n  {b.get('description', '')}"}
            })
        elements.append({"tag": "hr"})
    
    if right_blindspots:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "### 🟡 右盲点（左媒热炒、右媒缺位）"}})
        for b in right_blindspots:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"- **{b['headline'][:60]}**\n  {b.get('description', '')}"}
            })
        elements.append({"tag": "hr"})
    
    if not left_blindspots and not right_blindspots:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "✅ 本周期未检测到明显盲点"}})
    
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": "盲点判定：单边占比>70% 且另一边<10% | 请主动搜索对立视角补充认知"}]
    })
    
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "⚠️ Ground News 盲点预警"},
                "template": "orange"
            },
            "elements": elements
        }
    }


def send_feishu_card(card: Dict[str, Any]) -> bool:
    """发送飞书卡片"""
    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=card, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            print("✅ 飞书卡片推送成功")
            return True
        else:
            print(f"❌ 飞书推送失败: {result}")
            return False
    except Exception as e:
        print(f"❌ 飞书推送异常: {e}")
        return False


def main(run_id: Optional[str] = None):
    print(f"🚀 Ground News Push Feishu - Run ID: {run_id or 'auto'}")
    
    if not FEISHU_WEBHOOK_URL:
        print("❌ 缺少 FEISHU_WEBHOOK_URL 环境变量")
        sys.exit(1)
    
    required = ["S3_ENDPOINT_URL", "S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ 缺少 COS 环境变量: {', '.join(missing)}")
        sys.exit(1)
    
    client = get_cos_client()
    
    # 加载报告
    report = load_latest_report(client, run_id)
    if not report:
        print("❌ 未找到报告数据")
        sys.exit(1)
    
    actual_run_id = report.get("run_id", run_id or datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S"))
    print(f"📥 加载报告: {actual_run_id}")
    
    # 1. 发送 Bias Bar 卡片
    print("📤 发送 Bias Bar 卡片...")
    bias_card = build_bias_bar_card(report)
    send_feishu_card(bias_card)
    
    # 2. 发送 Blindspot 卡片
    print("📤 发送 Blindspot 卡片...")
    blindspot_card = build_blindspot_card(report)
    send_feishu_card(blindspot_card)
    
    print("\n✅ 完成: 两张卡片已推送到飞书")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", help="指定 run_id，默认自动取最新")
    args = parser.parse_args()
    main(args.run_id)