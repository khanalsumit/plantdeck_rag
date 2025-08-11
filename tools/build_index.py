#!/usr/bin/env python3
import sqlite3, pickle, numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

DB = "data/plants.db"
MODEL_DIR = "models/all-MiniLM-L6-v2"
EMB_NPY = "build/embeddings.npy"
MAP_PKL = "build/emb_map.pkl"

def get_model():
    return SentenceTransformer(MODEL_DIR if Path(MODEL_DIR).exists() else "sentence-transformers/all-MiniLM-L6-v2")

def rows(conn):
    q = """
    SELECT s.id, s.latin_name,
           IFNULL((SELECT GROUP_CONCAT(name) FROM common_name WHERE species_id=s.id),'') as common,
           IFNULL(s.id_features,'') || ' ' ||
           IFNULL((SELECT GROUP_CONCAT(indication) FROM usecase WHERE species_id=s.id),'') || ' ' ||
           IFNULL((SELECT GROUP_CONCAT(text) FROM preparation WHERE species_id=s.id),'') || ' ' ||
           IFNULL((SELECT notes FROM safety WHERE species_id=s.id LIMIT 1),'')
    FROM species s
    """
    for sid, latin, common, blob in conn.execute(q):
        txt = f"{latin}\nCommon: {common}\n{blob}"
        yield sid, latin, txt

def main():
    conn = sqlite3.connect(DB)
    ids, labels, corpus = [], [], []
    for sid, latin, txt in rows(conn):
        ids.append(sid); labels.append(latin); corpus.append(txt)
    model = get_model()
    emb = model.encode(corpus, convert_to_numpy=True, normalize_embeddings=True).astype("float32")
    Path("build").mkdir(exist_ok=True)
    np.save(EMB_NPY, emb)
    with open(MAP_PKL, "wb") as f:
        pickle.dump({"ids": ids, "labels": labels}, f)
    print(f"[✓] Indexed {len(ids)} plants → {EMB_NPY}")

if __name__ == "__main__":
    main()
