#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground News HTML v6 — 真·Ground News 风格面板
- 多栏布局：Bias Bar + 话题卡片 + 地区分布 + 源透明度 + 趋势追踪
- 搜索/筛选交互
- 暗色顶栏 + 统计仪表盘
- 响应式设计
"""

import json, csv, os, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from collections import Counter, defaultdict
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

# ===================== 聚类 =====================

def cluster_articles(entries:List, threshold:float=0.65)->Dict:
    titles=[e.get("title","") for e in entries]
    if len(titles)<2: return {"All News":entries}
    try:
        from sentence_transformers import SentenceTransformer
        from sklearn.metrics.pairwise import cosine_similarity
        model=SentenceTransformer("all-MiniLM-L6-v2")
        embeddings=model.encode(titles,show_progress_bar=False)
        sim=cosine_similarity(embeddings)
        print(f"  🧠 sentence-transformers: {len(titles)} → 384d")
    except Exception as e:
        print(f"  ⚠️ TF-IDF fallback ({e})")
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        zh=set("的了吗呢嘛哦啊嗯吧呀在和有是我他这不也人了就到说要去能为都个中上大对会可以自后下没看好只还过")
        en={"the","a","an","is","are","was","were","be","in","on","at","to","for","of","and","or","but","it"}
        vec=TfidfVectorizer(stop_words=list(zh|en),max_features=800,ngram_range=(1,2))
        m=vec.fit_transform(titles); sim=cosine_similarity(m)
    n=len(entries); assigned=[False]*n; clusters={}
    for i in range(n):
        if assigned[i]: continue
        cl=[i]; assigned[i]=True
        for j in range(i+1,n):
            if not assigned[j] and sim[i][j]>=threshold: cl.append(j); assigned[j]=True
        if len(cl)>=3: clusters[len(clusters)]=cl
    result={}
    for cid,idx in clusters.items():
        cl_entries=[entries[i] for i in idx]
        # 取簇内最高频词命名
        words=Counter()
        for e in cl_entries:
            for w in re.findall(r'[A-Za-z\u4e00-\u9fff]{3,}',e.get("title","").lower()):
                if w not in {"the","and","for","with","from","this","that","news","says","more","new","over","after","can","has","its","not","will"}:
                    words[w]+=1
        top=[w for w,_ in words.most_common(3)]
        name=" / ".join(top) if top else f"Topic {cid+1}"
        result[name]=cl_entries
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

def generate_html()->str:
    output=load_latest_output()
    if not output: return "<html><body><h1>No data</h1></body></html>"
    prev=load_previous_output()
    bias_r=load_bias_ratings()
    entries=output.get("entries",[])
    now=datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    for e in entries:
        e["_d"]=extract_domain(e.get("link",""))
        e["_b"]=bias_r.get(e["_d"],"Unknown")
        e["_c"]=bias_to_category(e["_b"])

    print(f"  🧮 Clustering {len(entries)} articles...")
    topics=cluster_articles(entries)
    trends=compute_trends(topics,prev)
    print(f"  📊 {len(topics)} topics")

    rated=[e for e in entries if e["_b"]!="Unknown"]
    al=sum(1 for e in rated if e["_c"]=="left")
    ac=sum(1 for e in rated if e["_c"]=="center")
    ar=sum(1 for e in rated if e["_c"]=="right")
    all_src=set(e.get("source_name","?") for e in entries)

    # 源统计
    src_stats=defaultdict(lambda:{"count":0,"bias":"Unknown"})
    for e in entries:
        sn=e.get("source_name","?")
        src_stats[sn]["count"]+=1
        src_stats[sn]["bias"]=e["_b"]
    top_sources=sorted(src_stats.items(),key=lambda x:-x[1]["count"])[:15]

    # 地区统计
    region_map={"china":"🇨🇳","world":"🌍","asia":"🌏","europe":"🇪🇺","middle_east":"🕌","africa":"🌍","tech":"💻","finance":"📈","science":"🔬","opinion":"✍️"}
    reg_stats=Counter()
    for e in entries:
        sid=e.get("source_id",""); cat=sid.split("_")[0] if "_" in sid else "world"
        reg_stats[cat]+=1

    # 话题卡片
    topic_cards=""
    for tn,te in topics.items():
        if len(te)<3: continue
        rt=[e for e in te if e["_b"]!="Unknown"]
        ln=sum(1 for e in rt if e["_c"]=="left")
        cn=sum(1 for e in rt if e["_c"]=="center")
        rn=sum(1 for e in rt if e["_c"]=="right")
        tr=ln+cn+rn
        trend=trends.get(tn,"")
        if tr==0: tr=1
        lp=int(ln/tr*100); rp=int(rn/tr*100); cp=100-lp-rp

        bsp=""
        if tr>=3:
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
            if len(shown)>=6: break
        if len(shown)<6: shown+=[e for e in te if e not in shown][:6-len(shown)]
        ars=""
        for e in shown:
            bc=e["_b"].lower().replace(" ","-")
            ars+=f'<div class="ar"><span class="as {bc}">{e.get("source_name","?")}</span><a href="{e.get("link","#")}" target="_blank" rel="noopener">{e.get("title","?")}</a></div>'
        rem=len(te)-len(shown)
        if rem>0: ars+=f'<div class="ar m">+{rem} more articles from {len(sm)-len(shown)} sources</div>'

        bb_html=f'<div class="bb"><div class="bl" style="width:{lp}%"></div><div class="bc" style="width:{cp}%"></div><div class="br" style="width:{rp}%"></div></div><div class="bll"><span><span class="d dL"></span>L {lp}%</span><span><span class="d dC"></span>C {cp}%</span><span><span class="d dR"></span>R {rp}%</span></div>'

        topic_cards+=f'''<div class="card" data-topic="{tn.lower()}">
<div class="ch"><h3>{trend} {tn} {bsp}</h3><span class="cnt">{len(te)} articles · {len(sm)} sources</span></div>
{bb_html}
<div class="sts">{st}</div>
<div class="alist">{ars}</div>
</div>'''

    # 源列表
    src_rows=""
    for sn,info in top_sources:
        bc=info["bias"].lower().replace(" ","-")
        src_rows+=f'<div class="sr"><span class="as {bc}">{sn}</span><span>{info["count"]}</span><span>{info["bias"]}</span></div>'

    # 地区分布
    reg_rows=""
    total_reg=sum(reg_stats.values())
    for cat,count in reg_stats.most_common():
        if count==0: continue
        pct=round(count/total_reg*100)
        emoji=region_map.get(cat,"📰")
        reg_rows+=f'<div class="rr"><span>{emoji}</span><span>{cat}</span><div class="rbar"><div style="width:{pct}%"></div></div><span>{count} ({pct}%)</span></div>'

    # 总览 Bias Bar
    t=al+ac+ar; lp2=int(al/t*100) if t else 33; cp2=int(ac/t*100) if t else 34; rp2=int(ar/t*100) if t else 33
    overall_bb=f'<div class="bb"><div class="bl" style="width:{lp2}%"><span>{al}</span></div><div class="bc" style="width:{cp2}%"><span>{ac}</span></div><div class="br" style="width:{rp2}%"><span>{ar}</span></div></div><div class="bll"><span><span class="d dL"></span>Left {lp2}%</span><span><span class="d dC"></span>Center {cp2}%</span><span><span class="d dR"></span>Right {rp2}%</span></div>'

    trend_alert=""
    if prev:
        new_c=sum(1 for v in trends.values() if "NEW" in v)
        rising_c=sum(1 for v in trends.values() if "rising" in v)
        trend_alert=f'<div class="alert"><span>🔄</span> vs Previous Run: <strong>{new_c}</strong> new topics · <strong>{rising_c}</strong> rising · <strong>{len(prev.get("entries",[]))}→{len(entries)}</strong> articles</div>'

    singles=sum(1 for v in topics.values() if len(v)<3)
    clustered=len(entries)-singles
    method_alert=f'<div class="alert"><span>🧠</span> Method: sentence-transformers (all-MiniLM-L6-v2) semantic clustering · TF-IDF fallback · 🔥NEW/📈rising trend tracking · Source bias: AllSides</div>'
    unclustered_alert=f'<div class="alert alert-warn"><span>📌</span> {singles} articles unclustered (min 3 per topic required) — {clustered} clustered into {len(topics)} topics</div>' if singles>0 else ""

    html=f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ground News · Bias Dashboard</title>
<style>
:root{{--L:#2563eb;--LC:#93c5fd;--R:#dc2626;--RC:#fca5a5;--C:#9ca3af;--CC:#e5e7eb;--bg:#f1f5f9;--card:#fff;--tx:#0f172a;--mu:#64748b;--bd:#e2e8f0;--hd:#0f172a}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--tx);line-height:1.5}}
/* Header */
.hd{{background:linear-gradient(135deg,var(--hd),#1e293b);color:#fff;padding:2.5rem 0 1.5rem;position:sticky;top:0;z-index:100}}
.hd h1{{font-size:1.4rem;font-weight:800;letter-spacing:-0.3px}}
.hd .sub{{color:#94a3b8;font-size:.78rem;margin-top:.3rem}}
.w{{max-width:1100px;margin:0 auto;padding:0 1.2rem}}
/* Stats */
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:.6rem;margin:1rem 0}}
.sc{{background:var(--card);border-radius:10px;padding:.9rem 1rem;box-shadow:0 1px 3px #0000000d;text-align:center}}
.sc .n{{font-size:1.6rem;font-weight:800;line-height:1.2}}
.sc .l{{color:var(--mu);font-size:.65rem;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}}
/* Overall Bias */
.bw{{background:var(--card);border-radius:10px;padding:1.2rem;margin:1rem 0;box-shadow:0 1px 3px #0000000d}}
.bw h3{{font-size:.85rem;margin-bottom:.8rem;font-weight:700}}
.bb{{display:flex;height:28px;border-radius:14px;overflow:hidden;margin-bottom:.5rem;position:relative}}
.bl{{background:var(--L);display:flex;align-items:center;justify-content:center;color:#fff;font-size:.7rem;font-weight:700;min-width:2rem;transition:width .3s}}
.bc{{background:var(--C);display:flex;align-items:center;justify-content:center;color:#fff;font-size:.7rem;font-weight:700;min-width:2rem}}
.br{{background:var(--R);display:flex;align-items:center;justify-content:center;color:#fff;font-size:.7rem;font-weight:700;min-width:2rem}}
.bll{{display:flex;justify-content:space-between;font-size:.72rem;color:var(--mu)}}
.bll span{{display:flex;align-items:center;gap:4px}}
.d{{width:8px;height:8px;border-radius:50%;display:inline-block}}
.dL{{background:var(--L)}}.dC{{background:var(--C)}}.dR{{background:var(--R)}}
/* Alert */
.alert{{background:#dbeafe;border:1px solid #93c5fd;border-radius:8px;padding:.7rem 1rem;margin:.8rem 0;font-size:.78rem;color:#1e40af;display:flex;align-items:center;gap:.5rem}}
.alert-warn{{background:#fef3c7;border-color:#f59e0b;color:#92400e}}
/* Layout */
.main{{display:grid;grid-template-columns:1fr 280px;gap:1rem;margin:1rem 0}}
@media(max-width:768px){{body{{overflow-x:hidden}}.hd{{padding:1.5rem 0 1rem}}.hd h1{{font-size:1.1rem}}.w{{padding:0 .7rem;max-width:100%}}.main{{grid-template-columns:1fr!important}}.sidebar{{width:100%}}.sidebar .bw{{position:static!important;margin:.5rem 0;padding:.8rem}}.stats{{grid-template-columns:repeat(2,1fr);gap:.3rem}}.sc{{padding:.5rem .4rem}}.sc .n{{font-size:1.1rem}}.card{{padding:.7rem;border-left-width:2px}}.ch h3{{font-size:.78rem}}.bb{{height:18px}}.toolbar{{gap:.3rem}}.toolbar button{{padding:.25rem .5rem;font-size:.6rem}}.rr{{grid-template-columns:18px 44px 1fr 40px;font-size:.64rem}}.sr{{font-size:.68rem}}.ar{{font-size:.72rem}}.ar a{{font-size:.72rem;white-space:normal;word-break:break-word}}.cnt{{font-size:.62rem}}}}
/* Sidebar */
.sidebar h4{{font-size:.78rem;font-weight:700;margin-bottom:.6rem;color:var(--mu);text-transform:uppercase;letter-spacing:.5px}}
/* Search */
.search{{margin-bottom:1rem}}
.search input{{width:100%;padding:.5rem .8rem;border:1px solid var(--bd);border-radius:8px;font-size:.78rem;outline:none;background:var(--card)}}
.search input:focus{{border-color:var(--L);box-shadow:0 0 0 2px #3b82f61a}}
/* Source list */
.sr{{display:flex;align-items:center;gap:.5rem;padding:.4rem 0;border-bottom:1px solid var(--bd);font-size:.75rem}}
.sr:last-child{{border-bottom:none}}
.sr span:last-child{{color:var(--mu);margin-left:auto}}
/* Region bar */
.rr{{display:grid;grid-template-columns:24px 60px 1fr 60px;align-items:center;gap:.4rem;padding:.3rem 0;font-size:.74rem}}
.rr span:first-child{{font-size:1rem}}
.rr span:last-child{{color:var(--mu);text-align:right;font-size:.68rem}}
.rbar{{height:6px;background:var(--bd);border-radius:3px;overflow:hidden}}
.rbar div{{height:100%;background:var(--L);border-radius:3px;transition:width .3s}}
/* Topic cards */
.toolbar{{display:flex;gap:.5rem;margin:1rem 0;flex-wrap:wrap}}
.toolbar button{{padding:.4rem .8rem;border:1px solid var(--bd);border-radius:20px;background:var(--card);font-size:.7rem;cursor:pointer;color:var(--mu);transition:all .15s}}
.toolbar button:hover,.toolbar button.active{{background:var(--hd);color:#fff;border-color:var(--hd)}}
.card{{background:var(--card);border-radius:10px;padding:1rem 1.2rem;margin:.7rem 0;box-shadow:0 1px 3px #0000000d;border-left:3px solid var(--C);transition:opacity .2s}}
.card.left-bias{{border-left-color:var(--L)}}
.card.right-bias{{border-left-color:var(--R)}}
.ch{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.5rem;gap:.5rem;flex-wrap:wrap}}
.ch h3{{font-size:.95rem;font-weight:700;line-height:1.3}}
.cnt{{font-size:.68rem;color:var(--mu);white-space:nowrap;margin-top:2px}}
.bs{{background:#fef3c7;color:#d97706;font-size:.62rem;padding:1px 8px;border-radius:10px;font-weight:600;margin-left:4px;vertical-align:middle}}
.sts{{display:flex;flex-wrap:wrap;gap:3px;margin:.5rem 0}}
.st{{font-size:.64rem;padding:1px 7px;border-radius:10px;font-weight:600}}
.st.left,.as.left{{background:#dbeafe;color:#1d4ed8}}.st.lean-left,.as.lean-left{{background:#eff6ff;color:#3b82f6}}
.st.center,.as.center{{background:#f1f5f9;color:#475569}}
.st.lean-right,.as.lean-right{{background:#fef2f2;color:#ef4444}}.st.right,.as.right{{background:#fee2e2;color:#b91c1c}}
.st.unknown,.as.unknown{{background:#f8fafc;color:#94a3b8}}
.alist{{margin-top:.6rem}}
.ar{{display:flex;gap:.5rem;align-items:baseline;padding:.3rem 0;border-bottom:1px solid #f1f5f9;font-size:.8rem}}
.ar:last-child,.ar.m{{border-bottom:none}}
.ar a{{color:var(--tx);text-decoration:none;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.ar a:hover{{color:var(--L);text-decoration:underline}}
.ar.m{{color:var(--mu);font-style:italic;font-size:.72rem}}
.as{{font-size:.58rem;padding:1px 5px;border-radius:6px;font-weight:600;white-space:nowrap;flex-shrink:0;min-width:40px;text-align:center}}
.ft{{text-align:center;padding:2rem;color:var(--mu);font-size:.68rem;border-top:1px solid var(--bd);margin-top:2rem}}
.hidden{{display:none}}
</style>
</head>
<body>
<div class="hd"><div class="w">
<h1>📊 Ground News · Global Bias Dashboard</h1>
<div class="sub">{now} · {len(entries)} articles · {len(all_src)} sources · {len(topics)} topics · 10 regions</div>
</div></div>

<div class="w">
<div class="stats">
<div class="sc"><div class="n">{len(entries)}</div><div class="l">Articles</div></div>
<div class="sc"><div class="n">{len(all_src)}</div><div class="l">Sources</div></div>
<div class="sc"><div class="n">{len(topics)}</div><div class="l">Topics</div></div>
<div class="sc"><div class="n">{len(rated)}</div><div class="l">Rated</div></div>
<div class="sc"><div class="n">{sum(1 for v in trends.values() if "NEW" in v)}</div><div class="l">New Today</div></div>
</div>

<div class="bw">
<h3>🌐 Overall Political Bias Distribution</h3>
{overall_bb}
</div>
{trend_alert}
{method_alert}
{unclustered_alert}

<div class="main">
<div class="content">
<div class="toolbar">
<button class="active" onclick="filterAll()">All</button>
<button onclick="filterBlindspot()">⚠ Blindspots</button>
<button onclick="filterNew()">🔥 New</button>
<input class="search" style="width:auto;flex:1;min-width:140px;margin:0" type="text" placeholder="🔍 Search topics..." oninput="searchTopics(this.value)">
</div>
<div id="topics">{topic_cards}</div>
</div>

<aside class="sidebar">
<div class="bw" style="position:sticky;top:80px">
<h4>🌍 Region Coverage</h4>
{reg_rows}
</div>
<div class="bw" style="position:sticky;top:calc(80px + 200px)">
<h4>📡 Top Sources</h4>
{src_rows}
</div>
</aside>
</div>
</div>

<div class="ft">
<p>Ground News v6 · sentence-transformers semantic clustering · 69 global RSS sources · 10 regions · Auto-deployed to Cloudflare Pages</p>
</div>

<script>
function filterAll(){{document.querySelectorAll(".card").forEach(c=>c.classList.remove("hidden"));updateBtns()}}
function filterBlindspot(){{document.querySelectorAll(".card").forEach(c=>c.classList.toggle("hidden",!c.querySelector(".bs")));updateBtns()}}
function filterNew(){{document.querySelectorAll(".card").forEach(c=>c.classList.toggle("hidden",!c.querySelector("h3").textContent.includes("🔥")));updateBtns()}}
function searchTopics(q){{document.querySelectorAll(".card").forEach(c=>{{var t=c.getAttribute("data-topic")||"";c.classList.toggle("hidden",!t.includes(q.toLowerCase()))}})}}
function updateBtns(){{document.querySelectorAll(".toolbar button").forEach(b=>b.classList.remove("active"));event.target.classList.add("active")}}
</script>
</body></html>'''
    return html

def main():
    html=generate_html()
    p=Path("output/ground_news/index.html")
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(html,encoding="utf-8")
    print(f"✅ HTML v6: {p} ({len(html)} chars)")

if __name__=="__main__":
    main()