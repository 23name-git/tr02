#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News HTML v5 — sentence-transformers 语义聚类 + B站 API + 趋势追踪
- all-MiniLM-L6-v2（80MB，CI 友好，uv cache 自动缓存）
- 回退 TF-IDF（sentence-transformers 不可用时）
- B站搜索 API 直连（无需 CLI）
"""

import json, csv, os, re, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import Counter, defaultdict
import warnings
warnings.filterwarnings("ignore")

import numpy as np

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_DIR = Path("output/ground_news")
CONFIG_DIR = Path("config/ground_news")

# ===================== 数据加载 =====================

def load_latest_output() -> Optional[Dict]:
    if not OUTPUT_DIR.exists(): return None
    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return json.loads(files[0].read_text(encoding="utf-8")) if files else None

def load_previous_output() -> Optional[Dict]:
    try:
        from trendradar.storage.remote import RemoteStorageBackend
        ep=os.getenv("S3_ENDPOINT_URL","").strip()
        bk=os.getenv("S3_BUCKET_NAME","").strip()
        ak=os.getenv("S3_ACCESS_KEY_ID","").strip()
        sk=os.getenv("S3_SECRET_ACCESS_KEY","").strip()
        rg=os.getenv("S3_REGION","").strip()
        if all([ep,bk,ak,sk]):
            s=RemoteStorageBackend(bucket_name=bk,access_key_id=ak,secret_access_key=sk,endpoint_url=ep,region=rg if rg else None)
            resp=s.s3_client.list_objects_v2(Bucket=bk,Prefix="ground_news/raw/",MaxKeys=5)
            keys=sorted([o["Key"] for o in resp.get("Contents",[])],reverse=True)
            if len(keys)>=2:
                r=s.s3_client.get_object(Bucket=bk,Key=keys[1])
                print(f"  📥 COS prev: {keys[1]}")
                return json.loads(r["Body"].read())
    except: pass
    if OUTPUT_DIR.exists():
        files=sorted(OUTPUT_DIR.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)
        if len(files)>=2: return json.loads(files[1].read_text(encoding="utf-8"))
    return None

def load_bias_ratings()->Dict[str,str]:
    r={}
    p=CONFIG_DIR/"allsides_ratings.csv"
    if p.exists():
        with open(p,"r",encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                d=row.get("source_domain","").strip().lower()
                b=row.get("bias_rating","").strip()
                if d and b: r[d]=b
    return r

def extract_domain(url:str)->str:
    from urllib.parse import urlparse
    try: return urlparse(url).netloc.lower().replace("www.","")
    except: return ""

def bias_to_category(b:str)->str:
    bl=b.lower()
    if "left" in bl: return "left"
    if "right" in bl: return "right"
    return "center"

# ===================== 语义聚类引擎 =====================

def cluster_semantic(entries:List, threshold:float=0.65)->Dict:
    """sentence-transformers 语义聚类 + TF-IDF 回退"""
    titles=[e.get("title","") for e in entries]
    if len(titles)<2: return {"All News":entries}

    # 方案 A：sentence-transformers（语义嵌入）
    try:
        from sentence_transformers import SentenceTransformer
        from sklearn.metrics.pairwise import cosine_similarity
        model=SentenceTransformer("all-MiniLM-L6-v2")
        embeddings=model.encode(titles,show_progress_bar=False)
        sim=cosine_similarity(embeddings)
        print(f"  🧠 sentence-transformers: {len(titles)} titles → {embeddings.shape[1]}d")
    except Exception as e:
        # 方案 B：TF-IDF 回退
        print(f"  ⚠️ sentence-transformers unavailable ({e}), using TF-IDF fallback")
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        zh=set("的了吗呢嘛哦啊嗯吧呀在和有是我他这不也人了就到说要去能为都个中上大对会可以自后下没看好只还过")
        en={"the","a","an","is","are","was","were","be","in","on","at","to","for","of","and","or","but","it","its","that","this","with","from","by","as","not","no","has","have","had","will","would","can","could","may","should","new","more","says","after","over","into","first","than","just","about","what"}
        vec=TfidfVectorizer(stop_words=list(zh|en),max_features=800,ngram_range=(1,2))
        m=vec.fit_transform(titles)
        sim=cosine_similarity(m)

    # 贪婪聚类（min 3）
    n=len(entries); assigned=[False]*n; clusters={}
    for i in range(n):
        if assigned[i]: continue
        cl=[i]; assigned[i]=True
        for j in range(i+1,n):
            if not assigned[j] and sim[i][j]>=threshold: cl.append(j); assigned[j]=True
        if len(cl)>=3: clusters[len(clusters)]=cl

    # 命名：取簇内最高频词（TF-IDF）
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec=TfidfVectorizer(max_features=500,stop_words="english")
        m=vec.fit_transform(titles); fn=vec.get_feature_names_out()
    except: fn=[]
    result={}
    for cid,idx in clusters.items():
        if len(fn)>0:
            centroid=np.mean(m[idx].toarray(),axis=0)
            top=np.argsort(centroid)[-3:][::-1]
            kw=[fn[k] for k in top if centroid[k]>0.05]
            name=" / ".join(kw) if kw else f"Topic {cid+1}"
        else: name=f"Topic {cid+1}"
        result[name]=[entries[i] for i in idx]
    return dict(sorted(result.items(),key=lambda x:-len(x[1])))

def compute_trends(today:Dict,prev:Optional[Dict])->Dict[str,str]:
    if not prev: return {t:"" for t in today}
    prev_titles=set(e.get("title","")[:80] for e in prev.get("entries",[]))
    trends={}
    for tn,entries in today.items():
        today_titles=set(e.get("title","")[:80] for e in entries)
        overlap=today_titles&prev_titles
        if len(overlap)==0: trends[tn]="🔥 NEW"
        elif len(overlap)<len(today_titles)*0.3: trends[tn]="📈 rising"
        else: trends[tn]=""
    return trends

# ===================== HTML =====================

def bias_bar(l,c,r)->str:
    t=l+c+r
    if t==0: lp=rp=cp=0
    else: lp=round(l/t*100); rp=round(r/t*100); cp=100-lp-rp
    return f'<div class="bb"><span class="bl" style="width:{lp}%"></span><span class="bc" style="width:{cp}%"></span><span class="br" style="width:{rp}%"></span></div><div class="bll"><span>{lp}% L ({l})</span><span>{cp}% C ({c})</span><span>{rp}% R ({r})</span></div>'

def generate_html()->str:
    output=load_latest_output()
    if not output: return "<html><body><h1>No data</h1></body></html>"
    prev=load_previous_output()
    bias_r=load_bias_ratings()

    entries=output.get("entries",[])
    stats=output.get("stats",{})
    now=datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    for e in entries:
        e["_d"]=extract_domain(e.get("link",""))
        e["_b"]=bias_r.get(e["_d"],"Unknown")
        e["_c"]=bias_to_category(e["_b"])

    print(f"  🧮 Clustering {len(entries)} articles...")
    topics=cluster_semantic(entries)
    trends=compute_trends(topics,prev)
    print(f"  📊 {len(topics)} topics")

    rated=[e for e in entries if e["_b"]!="Unknown"]
    al=sum(1 for e in rated if e["_c"]=="left")
    ac=sum(1 for e in rated if e["_c"]=="center")
    ar=sum(1 for e in rated if e["_c"]=="right")
    all_src=set(e.get("source_name","?") for e in entries)

    new_count=sum(1 for v in trends.values() if "NEW" in v)
    rising_count=sum(1 for v in trends.values() if "rising" in v)
    trend_summary=""
    if prev:
        trend_summary=f'<div class="alert alert-info">🔄 <strong>vs Previous Run:</strong> {new_count} new · {rising_count} rising · {len(prev.get("entries",[]))}→{len(entries)} articles</div>'

    topic_cards=""
    for tn,te in topics.items():
        if len(te)<3: continue
        rt=[e for e in te if e["_b"]!="Unknown"]
        ln=sum(1 for e in rt if e["_c"]=="left")
        cn=sum(1 for e in rt if e["_c"]=="center")
        rn=sum(1 for e in rt if e["_c"]=="right")
        tr=ln+cn+rn
        trend=trends.get(tn,"")

        bsp=""
        if tr>=3:
            lp=ln/tr*100 if tr else 0; rp=rn/tr*100 if tr else 0
            if lp<15 and rp>=15: bsp='<span class="bs">⚠ Left Blindspot</span>'
            elif rp<15 and lp>=15: bsp='<span class="bs">⚠ Right Blindspot</span>'

        sm=defaultdict(list)
        for e in te: sm[e.get("source_name","?")].append(e)
        st=""
        for sn,se in sorted(sm.items(),key=lambda x:-len(x[1])):
            bc=se[0].get("_b","Unknown").lower().replace(" ","-")
            st+=f'<span class="st {bc}">{sn} ({len(se)})</span> '

        shown,seen=[],set()
        for e in te:
            s=e.get("source_name","?")
            if s not in seen: shown.append(e); seen.add(s)
            if len(shown)>=8: break
        if len(shown)<8: shown+=[e for e in te if e not in shown][:8-len(shown)]

        ars=""
        for e in shown:
            bc=e["_b"].lower().replace(" ","-")
            ars+=f'<div class="ar"><span class="as {bc}">{e.get("source_name","?")}</span><a href="{e.get("link","#")}" target="_blank">{e.get("title","?")}</a></div>'
        rem=len(te)-len(shown)
        if rem>0: ars+=f'<div class="ar m">+{rem} more</div>'

        topic_cards+=f"""<div class="card">
<div class="ch"><h3>{trend} {tn} {bsp}</h3><span class="cnt">{len(te)} articles · {len(sm)} sources</span></div>
{bias_bar(ln,cn,rn)}
<div class="sts">{st}</div>
<div class="alist">{ars}</div>
</div>"""

    singles=sum(1 for v in topics.values() if len(v)<3)

    html=f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ground News · Bias Dashboard</title>
<style>
:root{{--L:#2563eb;--C:#9ca3af;--R:#dc2626;--bg:#f1f5f9;--card:#fff;--tx:#0f172a;--mu:#64748b;--bd:#e2e8f0}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);line-height:1.5}}
.hd{{background:linear-gradient(135deg,#0f172a,#1e293b);color:#fff;padding:2.5rem 0}}
.hd h1{{font-size:1.5rem;font-weight:800}}
.hd p{{color:#94a3b8;font-size:.82rem;margin-top:.4rem}}
.w{{max-width:840px;margin:0 auto;padding:0 1rem}}
.stats{{display:flex;gap:.8rem;margin:1.2rem 0;flex-wrap:wrap}}
.sc{{background:var(--card);border-radius:10px;padding:1rem 1.2rem;flex:1;min-width:100px;box-shadow:0 1px 3px #0001}}
.sc .n{{font-size:1.8rem;font-weight:800}}
.sc .l{{color:var(--mu);font-size:.7rem;text-transform:uppercase;letter-spacing:.5px}}
.bw{{background:var(--card);border-radius:10px;padding:1.2rem;margin:1rem 0;box-shadow:0 1px 3px #0001}}
.bw h3{{font-size:.9rem;margin-bottom:.8rem}}
.bb{{display:flex;height:20px;border-radius:10px;overflow:hidden;margin-bottom:.6rem}}
.bl{{background:var(--L)}}.bc{{background:var(--C)}}.br{{background:var(--R)}}
.bll{{display:flex;justify-content:space-between;font-size:.76rem;color:var(--mu)}}
.leg{{display:flex;gap:1rem;flex-wrap:wrap;margin-top:.6rem;font-size:.72rem}}
.leg .d{{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:3px}}
.dL{{background:var(--L)}}.dC{{background:var(--C)}}.dR{{background:var(--R)}}
.card{{background:var(--card);border-radius:10px;padding:1.2rem;margin:.8rem 0;box-shadow:0 1px 3px #0001}}
.ch{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:.5rem;flex-wrap:wrap}}
.ch h3{{font-size:1rem;font-weight:700}}
.cnt{{font-size:.7rem;color:var(--mu);white-space:nowrap}}
.bs{{background:#fef3c7;color:#d97706;font-size:.65rem;padding:2px 8px;border-radius:10px;font-weight:600;margin-left:6px}}
.sts{{display:flex;flex-wrap:wrap;gap:4px;margin:.5rem 0}}
.st{{font-size:.66rem;padding:1px 8px;border-radius:10px;font-weight:600;white-space:nowrap}}
.st.left{{background:#dbeafe;color:#1d4ed8}}.st.lean-left{{background:#eff6ff;color:#3b82f6}}
.st.center{{background:#f1f5f9;color:#475569}}
.st.lean-right{{background:#fef2f2;color:#ef4444}}.st.right{{background:#fee2e2;color:#b91c1c}}
.st.unknown{{background:#f8fafc;color:#94a3b8}}
.alist{{margin-top:.8rem}}
.ar{{display:flex;gap:.5rem;align-items:baseline;padding:.35rem 0;border-bottom:1px solid var(--bd);font-size:.84rem}}
.ar:last-child{{border-bottom:none}}
.ar a{{color:var(--tx);text-decoration:none;flex:1}}
.ar a:hover{{color:#2563eb}}
.ar.m{{color:var(--mu);font-style:italic;font-size:.76rem}}
.as{{font-size:.6rem;padding:1px 6px;border-radius:8px;font-weight:600;white-space:nowrap;min-width:50px;text-align:center}}
.as.left{{background:#dbeafe;color:#1d4ed8}}.as.lean-left{{background:#eff6ff;color:#3b82f6}}
.as.center{{background:#f1f5f9;color:#475569}}
.as.lean-right{{background:#fef2f2;color:#ef4444}}.as.right{{background:#fee2e2;color:#b91c1c}}
.as.unknown{{background:#f8fafc;color:#94a3b8}}
.alert{{border-radius:8px;padding:.8rem 1rem;margin:.8rem 0;font-size:.8rem}}
.alert-info{{background:#dbeafe;border:1px solid #3b82f6;color:#1e40af}}
.alert-warn{{background:#fef3c7;border:1px solid #f59e0b;color:#92400e}}
.ft{{text-align:center;padding:2rem;color:var(--mu);font-size:.7rem}}
@media(max-width:600px){{.stats{{flex-direction:column}}.ch{{flex-direction:column}}}}
</style>
</head>
<body>
<div class="hd"><div class="w">
<h1>📊 Ground News · Bias Dashboard</h1>
<p>{now} · {len(entries)} articles · {len(all_src)} sources · {len(topics)} topics · sentence-transformers clustering</p>
</div></div>
<div class="w">
<div class="stats">
<div class="sc"><div class="n">{len(entries)}</div><div class="l">Articles</div></div>
<div class="sc"><div class="n">{len(all_src)}</div><div class="l">Sources</div></div>
<div class="sc"><div class="n">{len(topics)}</div><div class="l">Topics</div></div>
<div class="sc"><div class="n">{len(rated)}</div><div class="l">Rated</div></div>
</div>
<div class="bw">
<h3>📐 Overall Bias</h3>
{bias_bar(al,ac,ar)}
<div class="leg"><span><span class="d dL"></span>Left ({al})</span><span><span class="d dC"></span>Center ({ac})</span><span><span class="d dR"></span>Right ({ar})</span></div>
</div>
{trend_summary}
<div class="alert alert-info"><strong>🧠 Method:</strong> sentence-transformers (all-MiniLM-L6-v2) semantic clustering · TF-IDF fallback · 🔥NEW/📈rising trend tracking · Source bias: AllSides</div>
<div class="alert alert-warn"><strong>⚠ {singles} articles</strong> unclustered</div>
<h2 style="margin-top:1.5rem;font-size:1.1rem">🗂 Topics</h2>
{topic_cards}
</div>
<div class="ft">
<p>Ground News v5 · sentence-transformers + Trend tracking · Auto-deployed to Cloudflare Pages</p>
<p>Next: HDBSCAN density clustering · factuality scores · media ownership</p>
</div>
</body></html>"""
    return html

def main():
    html=generate_html()
    p=Path("output/ground_news/index.html")
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(html,encoding="utf-8")
    print(f"✅ HTML v5: {p} ({len(html)} chars)")

if __name__=="__main__":
    main()