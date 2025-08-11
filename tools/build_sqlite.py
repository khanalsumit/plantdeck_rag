#!/usr/bin/env python3
import json, sqlite3
from pathlib import Path

PLANTS_DIR = Path("build/plants")
DB = Path("data/plants.db")
DB.parent.mkdir(exist_ok=True)

schema = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS species(
  id INTEGER PRIMARY KEY, latin_name TEXT UNIQUE, family TEXT, id_features TEXT, dosage TEXT
);
CREATE TABLE IF NOT EXISTS common_name(id INTEGER PRIMARY KEY, species_id INTEGER, name TEXT);
CREATE TABLE IF NOT EXISTS part_used(id INTEGER PRIMARY KEY, species_id INTEGER, name TEXT);
CREATE TABLE IF NOT EXISTS constituent(id INTEGER PRIMARY KEY, species_id INTEGER, name TEXT);
CREATE TABLE IF NOT EXISTS action(id INTEGER PRIMARY KEY, species_id INTEGER, name TEXT);
CREATE TABLE IF NOT EXISTS usecase(id INTEGER PRIMARY KEY, species_id INTEGER, indication TEXT, evidence TEXT);
CREATE TABLE IF NOT EXISTS preparation(id INTEGER PRIMARY KEY, species_id INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS safety(id INTEGER PRIMARY KEY, species_id INTEGER, toxicity TEXT, contraindications TEXT, interactions TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS image(id INTEGER PRIMARY KEY, species_id INTEGER, path TEXT, source_pdf TEXT, page INTEGER);
CREATE TABLE IF NOT EXISTS citation(id INTEGER PRIMARY KEY, species_id INTEGER, pdf TEXT, page INTEGER, snippet TEXT);
"""

def upsert_species(cur, obj):
    cur.execute("INSERT OR IGNORE INTO species(latin_name,family,id_features,dosage) VALUES(?,?,?,?)",
                (obj["latin_name"], obj.get("family"), obj.get("id_features",""), obj.get("dosage","")))
    cur.execute("SELECT id FROM species WHERE latin_name=?", (obj["latin_name"],))
    return cur.fetchone()[0]

def main():
    conn = sqlite3.connect(DB); cur = conn.cursor(); cur.executescript(schema)
    for jf in sorted(PLANTS_DIR.glob("*.json")):
        obj = json.loads(jf.read_text(encoding="utf-8"))
        sid = upsert_species(cur, obj)
        for n in obj.get("common_names", []):
            cur.execute("INSERT INTO common_name(species_id,name) VALUES(?,?)", (sid, n))
        for n in obj.get("parts_used", []):
            cur.execute("INSERT INTO part_used(species_id,name) VALUES(?,?)", (sid, n))
        for n in obj.get("constituents", []):
            cur.execute("INSERT INTO constituent(species_id,name) VALUES(?,?)", (sid, n))
        for n in obj.get("actions", []):
            cur.execute("INSERT INTO action(species_id,name) VALUES(?,?)", (sid, n))
        for u in obj.get("uses", []):
            cur.execute("INSERT INTO usecase(species_id,indication,evidence) VALUES(?,?,?)",
                        (sid, u.get("indication",""), u.get("evidence","")))
        for p in obj.get("preparations", []):
            cur.execute("INSERT INTO preparation(species_id,text) VALUES(?,?)", (sid, p.get("text","")))
        s = obj.get("safety", {})
        cur.execute("INSERT INTO safety(species_id,toxicity,contraindications,interactions,notes) VALUES(?,?,?,?,?)",
                    (sid, s.get("toxicity",""), s.get("contraindications",""), s.get("interactions",""), s.get("notes","")))
        for im in obj.get("images", []):
            cur.execute("INSERT INTO image(species_id,path,source_pdf,page) VALUES(?,?,?,?)",
                        (sid, im["path"], im["source_pdf"], im["page"]))
        for c in obj.get("citations", []):
            cur.execute("INSERT INTO citation(species_id,pdf,page,snippet) VALUES(?,?,?,?)",
                        (sid, c["pdf"], c["page"], ""))
    conn.commit(); conn.close()
    print(f"[✓] Built SQLite at {DB}")

if __name__ == "__main__":
    main()
