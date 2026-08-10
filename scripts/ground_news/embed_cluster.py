#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News Like Pipeline - Step 3: Embed & Cluster
对清洗后的文本生成向量嵌入，使用 HDBSCAN 聚类归并同一事件
产出：vectors/ (嵌入向量), clusters/ (聚类结果)
"""

import os
import sys
import json
import jsonlines
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import hashlib
from sentence_transformers import SentenceTransformer
import hdbscan
import umap

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# ============ 配置区 ============
COS_ENDPOINT = os.getenv("S3_ENDPOINT_URL")
COS_BUCKET = os.getenv("S3_BUCKET_NAME")
COS_AK = os.getenv("S3_ACCESS_KEY_ID")
COS_SK = os.getenv("S3_SECRET_ACCESS_KEY")
COS_REGION = os.getenv("S3_REGION", "")

CLEAN_PREFIX = "ground_news/clean/"
VECTORS_PREFIX = "ground_news/vectors/"
CLUSTERS_PREFIX = "ground_news/clusters/"

# 嵌入模型配置
EMBED_MODEL_NAME = os.getenv("GROUND_NEWS_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBED_BATCH_SIZE = 64
EMBED_MAX_LENGTH = 512

# HDBSCAN 配置
HDBSCAN_MIN_CLUSTER_SIZE = int(os.getenv("GROUND_NEWS_MIN_CLUSTER_SIZE", "3"))
HDBSCAN_MIN_SAMPLES = int(os.getenv("GROUND_NEWS_MIN_SAMPLES", "2"))
HDBSCAN_METRIC = "cosine"
HDBSCAN_CLUSTER_SELECTION_METHOD = "eom"

# UMAP 降维配置（用于可视化/加速聚类）
UMAP_N_COMPONENTS = 5
UMAP_N_NEIGHBORS = 15
UMAP_MIN_DIST = 0.1
UMAP_METRIC = "cosine"


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


def download_clean_data(client, run_id: str) -> List[Dict[str, Any]]:
    """下载并解析 clean/ 下的 JSONL"""
    key = f"{CLEAN_PREFIX}{run_id}.jsonl"
    try:
        response = client.get_object(Bucket=COS_BUCKET, Key=key)
        content = response['Body'].read().decode('utf-8')
        items = []
        for line in content.strip().split('\n'):
            if line:
                items.append(json.loads(line))
        print(f"📥 下载清洗数据: {len(items)} 条")
        return items
    except Exception as e:
        print(f"❌ 下载清洗数据失败: {e}")
        return []


def prepare_texts(items: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """准备用于嵌入的文本：标题 + 摘要 + 全文前500字"""
    texts = []
    ids = []
    for item in items:
        title = item.get("title", "")
        summary = item.get("summary", "")
        full_text = item.get("full_text", "")[:500] if item.get("full_text") else ""
        combined = f"{title}. {summary}. {full_text}".strip()
        if combined and len(combined) > 10:  # 过滤太短文本
            texts.append(combined)
            ids.append(item["id"])
    return texts, ids


def generate_embeddings(texts: List[str], model: SentenceTransformer) -> np.ndarray:
    """批量生成嵌入向量"""
    print(f"🔮 生成嵌入向量: {len(texts)} 条文本, 模型: {EMBED_MODEL_NAME}")
    embeddings = model.encode(
        texts,
        batch_size=EMBED_BATCH_SIZE,
        max_length=EMBED_MAX_LENGTH,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,  # 归一化便于余弦相似度
    )
    print(f"  ✅ 嵌入形状: {embeddings.shape}")
    return embeddings


def reduce_dimensions(embeddings: np.ndarray) -> np.ndarray:
    """UMAP 降维用于聚类加速和可视化"""
    print(f"📐 UMAP 降维: {embeddings.shape[1]} -> {UMAP_N_COMPONENTS} 维")
    reducer = umap.UMAP(
        n_components=UMAP_N_COMPONENTS,
        n_neighbors=UMAP_N_NEIGHBORS,
        min_dist=UMAP_MIN_DIST,
        metric=UMAP_METRIC,
        random_state=42,
    )
    reduced = reducer.fit_transform(embeddings)
    print(f"  ✅ 降维后形状: {reduced.shape}")
    return reduced


def cluster_embeddings(reduced_embeddings: np.ndarray) -> Tuple[np.ndarray, hdbscan.HDBSCAN]:
    """HDBSCAN 聚类"""
    print(f"🎯 HDBSCAN 聚类: min_cluster_size={HDBSCAN_MIN_CLUSTER_SIZE}, min_samples={HDBSCAN_MIN_SAMPLES}")
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples=HDBSCAN_MIN_SAMPLES,
        metric=HDBSCAN_METRIC,
        cluster_selection_method=HDBSCAN_CLUSTER_SELECTION_METHOD,
        prediction_data=True,
    )
    labels = clusterer.fit_predict(reduced_embeddings)
    
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = np.sum(labels == -1)
    print(f"  ✅ 聚类完成: {n_clusters} 个簇, {n_noise} 个噪声点 ({n_noise/len(labels)*100:.1f}%)")
    return labels, clusterer


def build_cluster_results(items: List[Dict], ids: List[str], labels: np.ndarray, embeddings: np.ndarray) -> Dict[str, Any]:
    """构建聚类结果结构"""
    id_to_item = {item["id"]: item for item in items}
    
    clusters = {}
    for idx, (item_id, label) in enumerate(zip(ids, labels)):
        if label == -1:
            continue  # 跳过噪声点
        cluster_id = int(label)
        if cluster_id not in clusters:
            clusters[cluster_id] = {
                "cluster_id": cluster_id,
                "items": [],
                "embeddings": [],
            }
        clusters[cluster_id]["items"].append(id_to_item[item_id])
        clusters[cluster_id]["embeddings"].append(embeddings[idx].tolist())
    
    # 计算每个簇的统计信息
    for cluster in clusters.values():
        cluster["size"] = len(cluster["items"])
        # 簇中心向量
        cluster["centroid"] = np.mean(cluster["embeddings"], axis=0).tolist()
        # 代表性标题（最接近中心的）
        centroid = np.array(cluster["centroid"])
        distances = [np.linalg.norm(np.array(e) - centroid) for e in cluster["embeddings"]]
        rep_idx = int(np.argmin(distances))
        cluster["representative_title"] = cluster["items"][rep_idx]["title"]
        cluster["representative_url"] = cluster["items"][rep_idx]["url"]
        # 语言分布
        langs = [item.get("language", "unknown") for item in cluster["items"]]
        cluster["language_dist"] = {lang: langs.count(lang) for lang in set(langs)}
        # 源分布
        sources = [item.get("source_name", "unknown") for item in cluster["items"]]
        cluster["source_dist"] = {src: sources.count(src) for src in set(sources)}
    
    # 按簇大小排序
    sorted_clusters = sorted(clusters.values(), key=lambda c: c["size"], reverse=True)
    
    return {
        "run_id": items[0].get("run_id", "") if items else "",
        "total_items": len(items),
        "clustered_items": sum(c["size"] for c in sorted_clusters),
        "noise_items": len(items) - sum(c["size"] for c in sorted_clusters),
        "n_clusters": len(sorted_clusters),
        "clusters": sorted_clusters,
        "created_at": datetime.now(BEIJING_TZ).isoformat(),
    }


def upload_to_cos(client, key: str, data: Dict[str, Any], content_type: str = "application/json"):
    """上传到 COS（显式设置 ContentLength 避免 chunked encoding 导致 SigV2 签名失败）"""
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    client.put_object(
        Bucket=COS_BUCKET,
        Key=key,
        Body=body,
        ContentLength=len(body),
        ContentType=content_type,
    )


def main(run_id: Optional[str] = None):
    if not run_id:
        client = get_cos_client()
        import boto3
        paginator = client.get_paginator('list_objects_v2')
        run_ids = set()
        for page in paginator.paginate(Bucket=COS_BUCKET, Prefix=CLEAN_PREFIX):
            if 'Contents' in page:
                for obj in page['Contents']:
                    if obj['Key'].endswith('.jsonl'):
                        run_id_candidate = obj['Key'].split('/')[-1].replace('.jsonl', '')
                        run_ids.add(run_id_candidate)
        if not run_ids:
            print("❌ 未找到任何 clean 数据")
            sys.exit(1)
        run_id = sorted(run_ids)[-1]
    
    print(f"🚀 Ground News Embed & Cluster - Run ID: {run_id}")
    
    required = ["S3_ENDPOINT_URL", "S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ 缺少环境变量: {', '.join(missing)}")
        sys.exit(1)
    
    client = get_cos_client()
    
    # 1. 下载清洗数据
    items = download_clean_data(client, run_id)
    if not items:
        print("⚠️ 无清洗数据，跳过")
        return
    
    # 2. 准备文本
    texts, ids = prepare_texts(items)
    if not texts:
        print("⚠️ 无有效文本，跳过")
        return
    print(f"📝 有效文本: {len(texts)} 条")
    
    # 3. 加载嵌入模型
    print(f"📦 加载嵌入模型: {EMBED_MODEL_NAME}")
    model = SentenceTransformer(EMBED_MODEL_NAME)
    
    # 4. 生成嵌入
    embeddings = generate_embeddings(texts, model)
    
    # 5. 降维
    reduced = reduce_dimensions(embeddings)
    
    # 6. 聚类
    labels, clusterer = cluster_embeddings(reduced)
    
    # 7. 构建结果
    cluster_result = build_cluster_results(items, ids, labels, embeddings)
    
    # 8. 上传向量（仅上传簇内项目的向量，节省空间）
    vector_data = {
        "run_id": run_id,
        "model": EMBED_MODEL_NAME,
        "dimension": embeddings.shape[1],
        "ids": ids,
        "embeddings": embeddings.tolist(),
        "labels": labels.tolist(),
        "created_at": datetime.now(BEIJING_TZ).isoformat(),
    }
    vector_key = f"{VECTORS_PREFIX}{run_id}.json"
    upload_to_cos(client, vector_key, vector_data)
    print(f"💾 向量已上传: {vector_key}")
    
    # 9. 上传聚类结果
    cluster_key = f"{CLUSTERS_PREFIX}{run_id}.json"
    upload_to_cos(client, cluster_key, cluster_result)
    print(f"💾 聚类结果已上传: {cluster_key}")
    
    print(f"\n✅ 完成: {cluster_result['n_clusters']} 个簇, {cluster_result['clustered_items']}/{cluster_result['total_items']} 条归入簇")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", help="指定 run_id，默认自动取最新")
    args = parser.parse_args()
    main(args.run_id)