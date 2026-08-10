#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Like Pipeline - Step 5: Generate Bias Bar Report
复用 generate_periodic_summary.py 的 AI 调用结构，生成结构化 Bias Bar 报告 + Blindspot 周报
产出：结构化 JSON + Markdown 报告，上传到 COS
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# ============ 配置区 ============
COS_ENDPOINT = os.getenv("S3_ENDPOINT_URL")
COS_BUCKET = os.getenv("S3_BUCKET_NAME")
COS_AK = os.getenv("S3_ACCESS_KEY_ID")
COS_SK = os.getenv("S3_SECRET_ACCESS_KEY")
COS_REGION = os.getenv("S3_REGION", "")

BIAS_BARS_PREFIX = "ground_news/bias_bars/"
REPORTS_PREFIX = "ground_news/reports/"

AI_API_KEY = os.getenv("AI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "deepseek/deepseek-v4-flash")
AI_BASE = os.getenv("AI_API_BASE", "https://api.deepseek.com/v1")


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


def download_from_cos(client, key: str) -> Optional[bytes]:
    try:
        response = client.get_object(Bucket=COS_BUCKET, Key=key)
        return response['Body'].read()
    except Exception as e:
        print(f"  ❌ 下载失败 {key}: {e}")
        return None


def upload_to_cos(client, key: str, data: Any, content_type: str = "application/json"):
    if isinstance(data, str):
        body = data.encode("utf-8")
    else:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    client.put_object(
        Bucket=COS_BUCKET,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def load_latest_bias_bars(client, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """加载最新的 Bias Bar 数据"""
    if run_id:
        key = f"{BIAS_BARS_PREFIX}{run_id}.json"
        content = download_from_cos(client, key)
        if content:
            return json.loads(content.decode('utf-8'))
        return None
    
    import boto3
    paginator = client.get_paginator('list_objects_v2')
    latest_key = None
    latest_time = None
    
    for page in paginator.paginate(Bucket=COS_BUCKET, Prefix=BIAS_BARS_PREFIX):
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


def build_bias_bar_prompt(bias_data: Dict[str, Any]) -> str:
    """构建生成 Bias Bar 报告的提示词"""
    bias_bars = bias_data.get("bias_bars", [])
    blindspot_clusters = [b for b in bias_bars if b.get("blindspot")]
    
    # Top 10 热点簇
    top_clusters = bias_bars[:10]
    
    cluster_summaries = []
    for c in top_clusters:
        dist = c["bias_distribution"]
        cluster_summaries.append({
            "cluster_id": c["cluster_id"],
            "title": c["representative_title"][:80],
            "size": c["size"],
            "heat": c["heat"],
            "bias": f"L:{dist['Left']['percentage']}% LL:{dist['Lean Left']['percentage']}% C:{dist['Center']['percentage']}% LR:{dist['Lean Right']['percentage']}% R:{dist['Right']['percentage']}%",
            "blindspot": c.get("blindspot"),
        })
    
    blindspot_summaries = []
    for b in blindspot_clusters[:10]:
        blindspot_summaries.append({
            "cluster_id": b["cluster_id"],
            "title": b["representative_title"][:80],
            "blindspot_type": b["blindspot"],
            "left_pct": b["left_percentage"],
            "right_pct": b["right_percentage"],
        })
    
    prompt = f"""你是专业的媒体偏见分析师。请基于以下数据生成一份 **Ground News 风格的偏见分析报告**。

【数据概览】
- 统计周期：单次抓取运行（约过去几小时）
- 总簇数：{bias_data.get('total_clusters', 0)}
- 盲点簇数：{bias_data.get('blindspot_clusters', 0)}
- 权重配置：AllSides={bias_data.get('weights',{}).get('AllSides',1)}, AdFontes={bias_data.get('weights',{}).get('AdFontes',1)}, MBFC={bias_data.get('weights',{}).get('MBFC',1)}

【Top 10 热点簇偏见分布】
{json.dumps(cluster_summaries, ensure_ascii=False, indent=2)}

【盲点簇详情】
{json.dumps(blindspot_summaries, ensure_ascii=False, indent=2)}

【输出要求】
请输出结构化 JSON，包含以下字段：

```json
{{
  "daily_bias_bar": {{
    "summary": "一句话总结今日媒体偏见全景",
    "top_stories": [
      {{
        "cluster_id": 0,
        "headline": "代表性标题",
        "bias_breakdown": {{"Left": 10, "Lean Left": 20, "Center": 30, "Lean Right": 25, "Right": 15}},
        "blindspot": null,
        "key_insight": "核心洞察：该事件左倾媒体侧重X，右倾媒体侧重Y，中间媒体相对平衡"
      }}
    ]
  }},
  "blindspot_report": {{
    "left_blindspots": [
      {{"cluster_id": 1, "headline": "标题", "description": "右倾媒体大量报道，左倾媒体极少提及，可能遗漏了..."}}
    ],
    "right_blindspots": [
      {{"cluster_id": 2, "headline": "标题", "description": "左倾媒体大量报道，右倾媒体极少提及，可能遗漏了..."}}
    ],
    "summary": "盲点整体评述"
  }},
  "markdown_report": "完整的 Markdown 格式报告，可直接发送到飞书"
}}
```

【Markdown 报告结构】
## 📊 偏见晴雨表 - {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M')}

### 今日核心热点 (Top 5)
| 事件 | 偏见分布 | 盲点 | 核心洞察 |
|------|---------|------|----------|
| 标题 | 🔴████ ████ ░░░░ ░░░░ ░░░░ | 无/左/右 | 一句话洞察 |

### ⚠️ 盲点预警
#### 🟡 左盲点（右媒热炒、左媒缺位）
- **事件**：标题 - 右倾媒体占比 XX%，左倾仅 XX%
- **可能遗漏**：...

#### 🔵 右盲点（左媒热炒、右媒缺位）
- **事件**：标题 - 左倾媒体占比 XX%，右倾仅 XX%
- **可能遗漏**：...

### 💡 给你的建议
- 关注 XX 事件的多方视角
- 警惕单一叙事框架
- ...

要求：
- 中文输出
- 专业、客观、可读性强
- 避免空泛套话，给具体案例支撑
- Markdown 表格对齐整齐
"""
    return prompt


def call_ai(prompt: str) -> str:
    """调用 AI 生成报告"""
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": "你是专业的媒体偏见分析师，擅长从聚类新闻中提炼偏见分布与盲点洞察，输出结构化 JSON 与可读性强的 Markdown 报告。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 6000,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(f"{AI_BASE}/chat/completions", headers=headers, json=data, timeout=180)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_ai_response(response: str) -> Dict[str, Any]:
    """解析 AI 返回的 JSON"""
    try:
        return json.loads(response)
    except json.JSONDecodeError as e:
        print(f"❌ AI 返回非 JSON: {e}")
        # 尝试提取 JSON 部分
        import re
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
        return {}


def main(run_id: Optional[str] = None):
    print(f"🚀 Ground News Generate Bias Bar Report - Run ID: {run_id or 'auto'}")
    
    required = ["S3_ENDPOINT_URL", "S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "AI_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ 缺少环境变量: {', '.join(missing)}")
        sys.exit(1)
    
    client = get_cos_client()
    
    # 1. 加载 Bias Bar 数据
    bias_data = load_latest_bias_bars(client, run_id)
    if not bias_data:
        print("❌ 未找到 Bias Bar 数据")
        sys.exit(1)
    
    actual_run_id = bias_data.get("run_id", run_id or datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S"))
    print(f"📥 加载 Bias Bar: {actual_run_id}, 簇数: {bias_data.get('total_clusters', 0)}")
    
    # 2. 构建提示词并调用 AI
    prompt = build_bias_bar_prompt(bias_data)
    print("🤖 正在调用 AI 生成报告...")
    ai_response = call_ai(prompt)
    
    # 3. 解析结果
    report = parse_ai_response(ai_response)
    if not report:
        print("❌ AI 返回解析失败")
        sys.exit(1)
    
    # 4. 补充元数据
    report["run_id"] = actual_run_id
    report["generated_at"] = datetime.now(BEIJING_TZ).isoformat()
    report["source_bias_data"] = {
        "total_clusters": bias_data.get("total_clusters"),
        "blindspot_clusters": bias_data.get("blindspot_clusters"),
    }
    
    # 5. 上传结构化 JSON
    json_key = f"{REPORTS_PREFIX}{actual_run_id}.json"
    upload_to_cos(client, json_key, report)
    print(f"💾 结构化报告已上传: {json_key}")
    
    # 6. 上传 Markdown 版本
    md_key = f"{REPORTS_PREFIX}{actual_run_id}.md"
    markdown_content = report.get("markdown_report", "# 报告生成失败\n\nAI 未返回有效 Markdown。")
    upload_to_cos(client, md_key, markdown_content, "text/markdown")
    print(f"💾 Markdown 报告已上传: {md_key}")
    
    print(f"\n✅ 完成: 报告生成成功")
    print(f"   - 每日偏见条形图: {len(report.get('daily_bias_bar', {}).get('top_stories', []))} 条")
    print(f"   - 左盲点: {len(report.get('blindspot_report', {}).get('left_blindspots', []))} 个")
    print(f"   - 右盲点: {len(report.get('blindspot_report', {}).get('right_blindspots', []))} 个")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", help="指定 run_id，默认自动取最新")
    args = parser.parse_args()
    main(args.run_id)