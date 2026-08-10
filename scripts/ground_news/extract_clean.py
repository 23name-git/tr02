#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Like Pipeline - Step 2: Extract & Clean
从 raw/ 读取原始数据，提取全文、清洗、实体识别、语言检测
产出：clean/ 目录下的结构化 JSONL 文件
"""

import os
import sys
import json
import jsonlines
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import hashlib
import trafilatura
import spacy
from langdetect import detect, LangDetectException

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# ============ 配置区 ============
COS_ENDPOINT = os.getenv("S3_ENDPOINT_URL")
COS_BUCKET = os.getenv("S3_BUCKET_NAME")
COS_AK = os.getenv("S3_ACCESS_KEY_ID")
COS_SK = os.getenv("S3_SECRET_ACCESS_KEY")
COS_REGION = os.getenv("S3_REGION", "")

RAW_PREFIX = "ground_news/raw/"
CLEAN_PREFIX = "ground_news/clean/"

# 加载 spaCy 模型（中英双语）
try:
    NLP_ZH = spacy.load("zh_core_web_sm")
except OSError:
    print("⚠️ 未安装 zh_core_web_sm，中文 NER 将不可用")
    NLP_ZH = None

try:
    NLP_EN = spacy.load("en_core_web_sm")
except OSError:
    print("⚠️ 未安装 en_core_web_sm，英文 NER 将不可用")
    NLP_EN = None


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
    """从 COS 下载对象内容"""
    try:
        response = client.get_object(Bucket=COS_BUCKET, Key=key)
        return response['Body'].read()
    except Exception as e:
        print(f"  ❌ 下载失败 {key}: {e}")
        return None


def list_raw_keys(client, run_id: str) -> List[str]:
    """列出指定 run_id 下的所有 raw 文件"""
    import boto3
    paginator = client.get_paginator('list_objects_v2')
    keys = []
    for page in paginator.paginate(Bucket=COS_BUCKET, Prefix=f"{RAW_PREFIX}"):
        if 'Contents' in page:
            for obj in page['Contents']:
                if run_id in obj['Key'] and obj['Key'].endswith('.json'):
                    keys.append(obj['Key'])
    return keys


def extract_full_text(url: str, html: Optional[str] = None) -> Optional[str]:
    """使用 trafilatura 提取全文"""
    try:
        if html:
            downloaded = html
        else:
            downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            return text
    except Exception as e:
        print(f"  ⚠️ 全文提取失败 {url}: {e}")
    return None


def detect_language(text: str) -> str:
    """语言检测"""
    try:
        return detect(text)
    except LangDetectException:
        return "unknown"


def extract_entities(text: str, lang: str) -> List[Dict[str, str]]:
    """实体识别"""
    entities = []
    if lang == "zh" and NLP_ZH:
        doc = NLP_ZH(text[:10000])  # 限制长度
        for ent in doc.ents:
            entities.append({"text": ent.text, "label": ent.label_})
    elif lang == "en" and NLP_EN:
        doc = NLP_EN(text[:10000])
        for ent in doc.ents:
            entities.append({"text": ent.text, "label": ent.label_})
    return entities


def clean_text(text: str) -> str:
    """基础清洗：去除多余空白、控制字符"""
    import re
    text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)  # 控制字符
    text = re.sub(r'\s+', ' ', text)  # 合并空白
    return text.strip()


def process_rss_entry(entry: Dict[str, Any], source_meta: Dict[str, Any]) -> Dict[str, Any]:
    """处理单条 RSS 条目"""
    title = entry.get("title", "")
    link = entry.get("link", "")
    summary = entry.get("summary", "")
    
    # 尝试提取全文
    full_text = extract_full_text(link)
    
    # 合并文本用于语言检测和实体识别
    combined_text = f"{title}. {summary}. {full_text or ''}"
    combined_text = clean_text(combined_text)
    
    lang = detect_language(combined_text) if combined_text else "unknown"
    entities = extract_entities(combined_text, lang) if combined_text else []
    
    return {
        "id": hashlib.md5(link.encode()).hexdigest()[:16],
        "source_id": source_meta["source_id"],
        "source_name": source_meta["source_name"],
        "source_type": source_meta["source_type"],
        "title": title,
        "url": link,
        "summary": summary,
        "full_text": full_text,
        "language": lang,
        "entities": entities,
        "published_at": entry.get("published", ""),
        "author": entry.get("author", ""),
        "tags": entry.get("tags", []),
        "fetched_at": source_meta.get("fetched_at", ""),
        "run_id": source_meta.get("run_id", ""),
        "processed_at": datetime.now(BEIJING_TZ).isoformat(),
    }


def main(run_id: Optional[str] = None):
    """主入口"""
    if not run_id:
        # 自动找最新的 run_id
        client = get_cos_client()
        import boto3
        paginator = client.get_paginator('list_objects_v2')
        run_ids = set()
        for page in paginator.paginate(Bucket=COS_BUCKET, Prefix=f"{RAW_PREFIX}"):
            if 'Contents' in page:
                for obj in page['Contents']:
                    parts = obj['Key'].split('/')
                    for part in parts:
                        if part.startswith("20") and "_" in part and len(part) >= 15:
                            run_ids.add(part)
        if not run_ids:
            print("❌ 未找到任何 run_id")
            sys.exit(1)
        run_id = sorted(run_ids)[-1]
    
    print(f"🚀 Ground News Extract Clean - Run ID: {run_id}")
    
    required = ["S3_ENDPOINT_URL", "S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ 缺少环境变量: {', '.join(missing)}")
        sys.exit(1)
    
    client = get_cos_client()
    raw_keys = list_raw_keys(client, run_id)
    print(f"📥 找到 {len(raw_keys)} 个原始文件")
    
    all_cleaned = []
    
    for key in raw_keys:
        print(f"  处理: {key}")
        content = download_from_cos(client, key)
        if not content:
            continue
        
        try:
            raw_data = json.loads(content.decode('utf-8'))
        except json.JSONDecodeError:
            print(f"    ❌ JSON 解析失败")
            continue
        
        source_meta = {
            "source_id": raw_data.get("source_id", ""),
            "source_name": raw_data.get("source_name", ""),
            "source_type": raw_data.get("source_type", ""),
            "fetched_at": raw_data.get("fetched_at", ""),
            "run_id": raw_data.get("run_id", ""),
        }
        
        entries = raw_data.get("entries", [])
        if not entries and "results" in raw_data:
            entries = raw_data["results"]  # agent-reach 格式兼容
        
        cleaned_count = 0
        for entry in entries:
            cleaned = process_rss_entry(entry, source_meta)
            all_cleaned.append(cleaned)
            cleaned_count += 1
        
        print(f"    ✅ 清洗 {cleaned_count} 条")
    
    # 写入 JSONL 到 COS
    if all_cleaned:
        clean_key = f"{CLEAN_PREFIX}{run_id}.jsonl"
        jsonl_content = "\n".join(json.dumps(item, ensure_ascii=False) for item in all_cleaned)
        client.put_object(
            Bucket=COS_BUCKET,
            Key=clean_key,
            Body=jsonl_content.encode("utf-8"),
            ContentLength=len(jsonl_content.encode("utf-8")),
            ContentType="application/jsonl",
        )
        print(f"\n✅ 完成: 共 {len(all_cleaned)} 条清洗数据 -> {clean_key}")
    else:
        print("\n⚠️ 无有效清洗数据")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", help="指定 run_id，默认自动取最新")
    args = parser.parse_args()
    main(args.run_id)