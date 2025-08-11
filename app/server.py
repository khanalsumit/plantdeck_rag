#!/usr/bin/env python3
"""
PlantDeck RAG API — FastAPI server that:
- loads your SQLite plant DB + local embeddings
- searches with sentence-transformers (local model)
- optionally does "Deep search" over page-level PDF chunks
- asks your local Ollama model to compose an answer with citations
- serves a simple browser UI at /ui/ and raw images at /images/
- returns a helpful JSON at / when no UI is present
"""

import os
import json
import pickle
import sqlite3
from pathlib import Path
from typing import List, Tuple

import numpy as np
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# ---------- paths / config ----------
DB = "data/plants.db"

# species-level index (from tools/build_index.py)
EMB_NPY = "build/embeddings.npy"
MAP_PKL = "build/emb_map.pkl"

# page-level index (from tools/build_page_index.py)
PAGE_EMB_NPY = "build/page_embeddings.npy"
PAGE_MAP_PKL = "build/page_map.pkl"

# raw pages (to collect page-level image paths)
RAW_PAGES = "build/raw_pages.jsonl"
IMAGES_DIR = "images"  # where extractor wrote page/embedded PNGs

# local sentence-transformers model
MODEL_DIR = "models/all-MiniLM-L6-v2"  # local copy recommended

# Ollama (local LLM)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3:latest")  # use a model you already have

# ---------- app ----------
app = FastAPI(title="PlantDeck RAG", version="0.3")

# Serve static UI if present
STATIC_DIR = Path("app/static")
if STATIC_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")

# Serve extracted images
if Path(IMAGES_DIR).exists():
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

# ---------- load assets ----------
required = [DB, EMB_NPY, MAP_PKL]
missing = [p for p in required if not Path(p).exists()]
if missing:
    raise RuntimeError(f"Missing files: {missing}. Run tools/build_sqlite.py and tools/build_index.py first.")

# species embeddings + id map
emb = np.load(EMB_NPY)
with open(MAP_PKL, "rb") as f:
    _map = pickle.load(f)  # {"ids": [...], "labels": [...]}

# page embeddings (optional)
page_emb = None
page_map = None
if Path(PAGE_EMB_NPY).exists() and Path(PAGE_MAP_PKL).exists():
    page_emb = np.load(PAGE_EMB_NPY)
    with open(PAGE_MAP_PKL, "rb") as f:
        page_map = pickle.load(f)  # list of {"pdf","page","text","snippet"}

# build (pdf,page) -> [image file names] map from raw_pages.jsonl
def _load_page_image_map():
    m = {}
    rp = Path(RAW_PAGES)
    if not rp.exists():
        return m
    with rp.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if "_meta" in rec:
                continue
            k = (rec.get("pdf"), int(rec.get("page", 0)))
            imgs = []
            for im in rec.get("images", []):
                p = im.get("path", "")
                if not p:
                    continue
                # store just the filename; served under /images/<name>
                name = Path(p).name
                imgs.append(name)
            if imgs:
                m.setdefault(k, []).extend(imgs)
    return m

page_img_map = _load_page_image_map()

# embedder (local folder if present; otherwise will download once)
_model = SentenceTransformer(MODEL_DIR if Path(MODEL_DIR).exists()
                             else "sentence-transformers/all-MiniLM-L6-v2")

# ---------- helpers ----------
def embed(q: str) -> np.ndarray:
    v = _model.encode([q], convert_to_numpy=True, normalize_embeddings=True).astype("float32")
    return v[0]

def topk(q: str, k: int = 5) -> List[Tuple[int, str, float]]:
    """Species-level nearest neighbors."""
    v = embed(q)
    scores = emb @ v  # cosine similarity (rows are normalized)
    idx = np.argsort(-scores)[:max(1, k)]
    return [(int(_map["ids"][i]), _map["labels"][i], float(scores[i])) for i in idx]

def fetch_context(sids: List[int]):
    """Fetch structured species context from SQLite."""
    if not sids:
        return []
    conn = sqlite3.connect(DB); cur = conn.cursor()
    out = []
    for sid in sids:
        cur.execute("SELECT latin_name,family,id_features,dosage FROM species WHERE id=?", (sid,))
        s = cur.fetchone()
        if not s:
            continue
        cur.execute("SELECT name FROM common_name WHERE species_id=?", (sid,))
        common = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT indication,evidence FROM usecase WHERE species_id=?", (sid,))
        uses = [{"indication": u, "evidence": e} for u, e in cur.fetchall()]
        cur.execute("SELECT toxicity,contraindications,interactions,notes FROM safety WHERE species_id=?", (sid,))
        saf = cur.fetchone()
        cur.execute("SELECT pdf,page FROM citation WHERE species_id=? LIMIT 3", (sid,))
        cits = [{"pdf": p, "page": pg} for p, pg in cur.fetchall()]
        out.append({
            "latin_name": s[0],
            "common_names": common,
            "family": s[1],
            "id_features": s[2],
            "dosage": s[3],
            "uses": uses,
            "safety": {
                "toxicity": saf[0] if saf else "",
                "contraindications": saf[1] if saf else "",
                "interactions": saf[2] if saf else "",
                "notes": saf[3] if saf else "",
            },
            "citations": cits
        })
    conn.close()
    return out

def page_topk(q: str, k: int = 8) -> List[Tuple[int, float]]:
    """Page-level nearest neighbors (chunked text)."""
    if page_emb is None:
        return []
    v = embed(q)
    scores = page_emb @ v
    idx = np.argsort(-scores)[:max(1, k)]
    return [(int(i), float(scores[i])) for i in idx]

def fetch_page_context(idxs: List[Tuple[int, float]]):
    """Return concise page snippets + image URLs for inclusion in the prompt/UI."""
    if not idxs or page_map is None:
        return []
    out = []
    for i, _ in idxs:
        rec = page_map[i]  # {"pdf","page","snippet"...}
        pdf, page = rec["pdf"], int(rec["page"])
        # attach up to 6 images from that page (if any)
        names = page_img_map.get((pdf, page), [])[:6]
        urls = [f"/images/{n}".replace("\\", "/") for n in names]
        out.append({"pdf": pdf, "page": page, "snippet": rec.get("snippet", ""), "images": urls})
    return out

def call_ollama(question: str, species_docs: list, page_docs: list) -> str:
    """Compose the final answer using Ollama with grounded context."""
    system = (
        "You are a cautious herbal field guide. Use ONLY the provided context. "
        "If info is missing, say so clearly. "
        "Always include: 'Field guide only; not medical advice.' "
        "Answer concisely with bullet points and cite Latin names and/or PDF pages."
    )
    ctx = "== Species summaries ==\n"
    for d in species_docs:
        cites = ", ".join([f"{c['pdf']} p{c['page']}" for c in d.get('citations', [])]) or "no page cites"
        ctx += (
            f"- {d.get('latin_name','?')} (Common: {', '.join(d.get('common_names', [])[:3])})\n"
            f"  Family: {d.get('family','')}\n"
            f"  Uses: {', '.join([u.get('indication','') for u in d.get('uses', [])[:6]])}\n"
            f"  Safety: {d.get('safety',{}).get('toxicity','')} "
            f"{d.get('safety',{}).get('contraindications','')}\n"
            f"  Dosage: {d.get('dosage','')}\n"
            f"  Cites: {cites}\n"
        )
    if page_docs:
        ctx += "\n== Source snippets ==\n"
        for s in page_docs:
            ctx += f"- {s['pdf']} p{s['page']}: {s['snippet']}\n"

    prompt = f"{system}\n\nContext:\n{ctx}\n\nQuestion: {question}\n\nAnswer:"
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Ollama error: {r.text[:300]}")
    return r.json().get("response", "").strip()

# ---------- models ----------
class AskReq(BaseModel):
    q: str
    k: int = 5          # species results
    deep: bool = False  # enable page-level search
    k_pages: int = 8    # number of page chunks to retrieve

# ---------- routes ----------
@app.get("/health")
def health():
    return {
        "ok": True,
        "model": OLLAMA_MODEL,
        "deep_available": bool(page_emb is not None and page_map is not None),
        "images_available": len(page_img_map) > 0
    }

@app.get("/plants")
def list_plants(limit: int = 100, offset: int = 0):
    conn = sqlite3.connect(DB); cur = conn.cursor()
    cur.execute("SELECT latin_name FROM species ORDER BY latin_name LIMIT ? OFFSET ?", (limit, offset))
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return {"plants": rows}

@app.post("/ask")
def ask(body: AskReq):
    # species-level context
    hits = topk(body.q, body.k)
    sids = [sid for sid, _, _ in hits]
    species_ctx = fetch_context(sids)

    # optional page-level context
    page_hits = []
    page_ctx = []
    if body.deep and page_emb is not None and page_map is not None:
        page_hits = page_topk(body.q, body.k_pages)
        page_ctx = fetch_page_context(page_hits)

    answer = call_ollama(body.q, species_ctx, page_ctx)

    return {
        "answer": answer,
        "hits": [{"species_id": sid, "latin_name": name, "score": score} for sid, name, score in hits],
        "context": species_ctx,
        "page_hits": [{"idx": int(i), "score": float(s)} for i, s in page_hits],
        "page_context": page_ctx
    }

# ---------- root ----------
if STATIC_DIR.exists():
    @app.get("/")
    def root_redirect():
        return RedirectResponse(url="/ui/")
else:
    @app.get("/")
    def root_json():
        return JSONResponse({
            "ok": True,
            "message": "UI not found. Create app/static/index.html to serve a page.",
            "try": ["/docs", "/health", "/plants", "/ask"]
        })

# Quiet the favicon 404 in logs
@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)
