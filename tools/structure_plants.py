#!/usr/bin/env python3
import json, re, hashlib
from pathlib import Path
from ruamel.yaml import YAML

yaml = YAML()
CFG = yaml.load(open("tools/headings.yml", "r"))
RAW = Path("build/raw_pages.jsonl")
OUTDIR = Path("build/plants"); OUTDIR.mkdir(parents=True, exist_ok=True)

# ---------- helpers ----------
def normalize_spaces(s: str) -> str:
    if not s: return ""
    s = s.replace("–", "-").replace("—", "-").replace("•", "*")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()

def heading_pattern(key: str) -> re.Pattern:
    # Accept "Key:", "Key -", "Key —", or "Key" on its own; stop at next Heading-like line
    return re.compile(
        rf"(?:^|\n)\s*{re.escape(key)}\s*(?::|-)?\s*(.+?)(?=\n[A-Z][^\n]{{0,40}}(?::|-)|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )

def find_field(text: str, keys) -> str | None:
    text = text or ""
    for key in keys:
        m = heading_pattern(key).search(text)
        if m:
            return m.group(1).strip()
    return None

def extract_binomial(s: str) -> str | None:
    """Find a plausible Latin binomial (optionally with infraspecific rank)."""
    if not s: return None
    s = normalize_spaces(s)
    # Genus Species [optional: (subsp.|ssp.|var.|f.) epithet]
    m = re.search(r"\b([A-Z][a-z-]{2,})\s+([a-z-]{2,})(?:\s+(?:subsp\.|ssp\.|var\.|f\.)\s+([a-z-]{2,}))?", s)
    if not m: return None
    parts = [m.group(1), m.group(2)]
    if m.group(3): parts.append(m.group(3))
    return " ".join(parts)

_bullet_start = re.compile(r"^\s*(?:[-*•]\s+)", re.M)
def split_list(s: str) -> list[str]:
    if not s: return []
    s = normalize_spaces(s)
    if _bullet_start.search(s):
        items = [re.sub(r"^\s*(?:[-*•]\s+)", "", line).strip()
                 for line in re.split(r"\n+", s) if line.strip()]
    else:
        items = re.split(r";|\n", s)
        tmp = []
        for it in items:
            tmp.extend(re.split(r",\s+(?=[A-Za-z])", it))
        items = tmp
    cleaned = []
    for x in items:
        x = x.strip(" .;,-")
        if x: cleaned.append(x)
    return cleaned

def dedupe_keep_order(xs):
    seen = set(); out = []
    for x in xs:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def safe_slug(name: str, maxlen: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    if len(slug) <= maxlen:
        return slug
    h = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:maxlen-9]}_{h}"

# ---------- main ----------
plants: dict[str, dict] = {}
last_key = None

with open(RAW, "r", encoding="utf-8") as f:
    for line in f:
        rec = json.loads(line)

        # Skip extractor meta rows
        if "_meta" in rec:
            continue

        t = rec.get("text", "") or ""
        images = rec.get("images", []) or []

        raw_latin = find_field(t, CFG["headings"]["latin_name"])
        if raw_latin:
            latin = extract_binomial(raw_latin)  # << normalize to Genus species
            if latin:
                plants.setdefault(latin, {
                    "latin_name": latin, "common_names": [], "family": None,
                    "id_features": "", "parts_used": [], "constituents": [],
                    "actions": [], "uses": [], "preparations": [], "dosage": "",
                    "safety": {"notes": "", "toxicity": "", "contraindications": "", "interactions": ""},
                    "lookalikes": [], "images": [], "citations": []
                })
                last_key = latin
            # If no valid binomial found, we DO NOT switch last_key (prevents table rows from hijacking)

        if not plants or not last_key:
            continue
        cur = plants[last_key]

        cmn   = find_field(t, CFG["headings"]["common_names"])
        fam   = find_field(t, CFG["headings"]["family"])
        ida   = find_field(t, CFG["headings"]["id_features"])
        parts = find_field(t, CFG["headings"]["parts_used"])
        cons  = find_field(t, CFG["headings"]["constituents"])
        act   = find_field(t, CFG["headings"]["actions"])
        uses  = find_field(t, CFG["headings"]["uses"])
        prep  = find_field(t, CFG["headings"]["preparations"])
        dose  = find_field(t, CFG["headings"]["dosage"])
        safe  = find_field(t, CFG["headings"]["safety"])
        look  = find_field(t, CFG["headings"]["lookalikes"])
        syns  = find_field(t, CFG["headings"].get("synonyms", [])) if "synonyms" in CFG["headings"] else None

        if cmn:   cur["common_names"] += split_list(cmn)
        if fam and not cur["family"]: cur["family"] = normalize_spaces(fam)
        if ida:
            cur["id_features"] += ("\n" if cur["id_features"] else "") + ida.strip()
        if parts: cur["parts_used"]   += split_list(parts)
        if cons:  cur["constituents"] += split_list(cons)
        if act:   cur["actions"]      += split_list(act)
        if uses:
            for u in split_list(uses):
                cur["uses"].append({"indication": u, "evidence": "unspecified"})
        if prep:
            for p in split_list(prep):
                cur["preparations"].append({"text": p})
        if dose and not cur["dosage"]:
            cur["dosage"] = normalize_spaces(dose)
        if look:  cur["lookalikes"]   += split_list(look)
        if syns:  cur["common_names"] += split_list(syns)

        if safe:
            s = safe.lower()
            if "toxic" in s and not cur["safety"]["toxicity"]:
                cur["safety"]["toxicity"] = "possible/mentioned"
            if "contra" in s and not cur["safety"]["contraindications"]:
                cur["safety"]["contraindications"] = safe
            if "interact" in s and not cur["safety"]["interactions"]:
                cur["safety"]["interactions"] = safe
            cur["safety"]["notes"] += ("\n" if cur["safety"]["notes"] else "") + safe

        # images & citations
        for im in images:
            path = im.get("path")
            if path:
                cur["images"].append({"path": path, "source_pdf": rec["pdf"], "page": rec["page"]})
        if t.strip():
            cur["citations"].append({"pdf": rec["pdf"], "page": rec["page"]})

# de-dupe lists and tidy
for p in plants.values():
    p["common_names"] = dedupe_keep_order([x for x in map(normalize_spaces, p["common_names"]) if x])
    p["parts_used"]   = dedupe_keep_order(p["parts_used"])
    p["constituents"] = dedupe_keep_order(p["constituents"])
    p["actions"]      = dedupe_keep_order(p["actions"])
    p["lookalikes"]   = dedupe_keep_order(p["lookalikes"])

    # de-dupe images (by path) and citations (by pdf+page)
    seen_paths = set(); imgs = []
    for im in p["images"]:
        if im["path"] not in seen_paths:
            imgs.append(im); seen_paths.add(im["path"])
    p["images"] = imgs

    seen_cite = set(); cites = []
    for c in p["citations"]:
        key = (c["pdf"], c["page"])
        if key not in seen_cite:
            cites.append(c); seen_cite.add(key)
    p["citations"] = cites

# write files (short, safe filenames)
for latin, obj in plants.items():
    fn = safe_slug(latin, maxlen=80)
    Path("build/plants").joinpath(f"{fn}.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )

print(f"[✓] Wrote {len(plants)} plant JSON files to build/plants/")
