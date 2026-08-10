#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News COS 辅助模块 — 复用 TrendRadar RemoteStorageBackend（已验证可用）
避免直接创建 boto3 client 时出现签名相关问题
"""

import os
import sys
import json
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List

# 确保 TrendRadar 模块可导入
_TR02_ROOT = Path(__file__).parent.parent.parent
if str(_TR02_ROOT) not in sys.path:
    sys.path.insert(0, str(_TR02_ROOT))

from trendradar.storage.remote import RemoteStorageBackend
from botocore.exceptions import ClientError


def get_storage() -> RemoteStorageBackend:
    """创建 RemoteStorageBackend，完全复用爬虫的存储配置"""
    endpoint = os.getenv("S3_ENDPOINT_URL")
    bucket = os.getenv("S3_BUCKET_NAME")
    ak = os.getenv("S3_ACCESS_KEY_ID")
    sk = os.getenv("S3_SECRET_ACCESS_KEY")
    region = os.getenv("S3_REGION", "")

    return RemoteStorageBackend(
        bucket_name=bucket,
        access_key_id=ak,
        secret_access_key=sk,
        endpoint_url=endpoint,
        region=region,
        enable_txt=False,
        enable_html=False,
    )


def upload_json(key: str, data: Any) -> None:
    """上传 JSON 数据到 COS（通过 RemoteStorageBackend），key 不带 ground_news/ 前缀以避免写入策略限制"""
    storage = get_storage()
    # 先用 head_object 测试权限（读 crawler 的现有路径）
    test_read_key = "news/2025-01-01.db"
    try:
        storage.s3_client.head_object(Bucket=storage.bucket_name, Key=test_read_key)
        print(f"  [OK] head_object 可读: {test_read_key}")
    except Exception as e:
        print(f"  [WARN] head_object 失败: {test_read_key} — {e}")
    
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    # 尝试不带 ground_news/ 前缀（测试权限）
    test_key = f"news/{key.replace('ground_news/', '')}"
    try:
        storage.s3_client.put_object(
            Bucket=storage.bucket_name,
            Key=test_key,
            Body=body,
            ContentLength=len(body),
            ContentType="application/json",
        )
        print(f"  [OK] put_object 成功: {test_key}")
        # 删除测试文件
        storage.s3_client.delete_object(Bucket=storage.bucket_name, Key=test_key)
        print(f"  [OK] 测试文件已删除")
    except Exception as e:
        print(f"  [FAIL] put_object 到 news/ 前缀也失败: {e}")
    
    # 真正上传到指定 key
    storage.s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=key,
        Body=body,
        ContentLength=len(body),
        ContentType="application/json",
    )
    print(f"  ✅ 已上传: {key}")


def upload_bytes(key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
    """上传字节数据到 COS"""
    storage = get_storage()
    storage.s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=key,
        Body=body,
        ContentLength=len(body),
        ContentType=content_type,
    )
    print(f"  ✅ 已上传: {key}")


def download_bytes(key: str) -> Optional[bytes]:
    """从 COS 下载字节数据"""
    storage = get_storage()
    try:
        resp = storage.s3_client.get_object(Bucket=storage.bucket_name, Key=key)
        return resp['Body'].read()
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey"):
            print(f"  ⚠️ 文件不存在: {key}")
            return None
        raise


def download_json(key: str) -> Optional[Any]:
    """从 COS 下载并解析 JSON"""
    body = download_bytes(key)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def list_keys(prefix: str) -> List[str]:
    """列出指定前缀下的所有 key（使用 list_objects_v2 + head_object 兼容 COS）"""
    storage = get_storage()
    keys = []
    paginator = storage.s3_client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=storage.bucket_name, Prefix=prefix):
        if 'Contents' in page:
            for obj in page['Contents']:
                keys.append(obj['Key'])
    return keys


def object_exists(key: str) -> bool:
    """检查对象是否存在"""
    storage = get_storage()
    try:
        storage.s3_client.head_object(Bucket=storage.bucket_name, Key=key)
        return True
    except ClientError:
        return False