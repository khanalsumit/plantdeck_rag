#!/usr/bin/env python3
import json, re, pickle
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer

RAW = Path("build/raw_pages.jsonl")
MODEL_DIR = "models/all-MiniLM-L6-v2"
OUT_EMB = Path("build/page_embeddings.npy")
OUT_MAP = Path("build/page_map.pkl")

def normalize(s): 
    return re.sub(r"\s+", " ", (s or "")).strip()

def chunk_text(txt, maxlen=1000, overlap=150):
    txt = normalize(txt)
    if len(txt) <= maxlen: 
        return [txt]
    chunks = []
    i = 0
    while i < len(txt):
        j = i + maxlen
        if j < len(txt):
            # try to break at a sentence boundary
            k = txt.rfind(".", i, j)
            if k != -1 and k - i > 200:
                j = k + 1
        chunks.append(txt[i:j].strip())
        i = max(0, j - overlap)
        if len(chunks) > 5000: break  # safeguard
    return [c for c in chunks if len(c) >= 200]

def main():
    records = []
    with open(RAW, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if "_meta" in rec: 
                continue
            txt = normalize(rec.get("text",""))
            if not txt: 
                continue
            for ch in chunk_text(txt, 1000, 150):
                snippet = (ch[:280] + "…") if len(ch) > 280 else ch
                records.append({
                    "pdf": rec["pdf"], "page": rec["page"], "text": ch, "snippet": snippet
                })

    print(f"[+] page-chunks: {len(records)}")
    model = SentenceTransformer(MODEL_DIR)
    emb = model.encode([r["text"] for r in records], convert_to_numpy=True, normalize_embeddings=True).astype("float32")
    np.save(OUT_EMB, emb)
    with open(OUT_MAP, "wb") as f:
        pickle.dump(records, f)
    print(f"[✓] Saved {OUT_EMB} and {OUT_MAP}")

if __name__ == "__main__":
    main()
