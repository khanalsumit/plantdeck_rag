
---

# PlantDeck – Offline Herbal RAG

I built **PlantDeck** so I can take my herbal PDFs off-grid and still ask smart, grounded questions about plant uses, preparations, and safety—without sending anything to the cloud. It runs entirely on my machine (Windows in my case), uses my **local PDFs** as the single source of truth, and answers with page-level citations (and images) using a **local LLM via Ollama**.

> **Field guide only; not medical advice.** Always verify with the original PDFs and a qualified professional.

---

## What it does (in plain English)

* I drop herbal PDFs into `./pdfs/`.
* A script extracts **text + embedded images**, runs **OCR** with Tesseract on scanned pages, and writes a big `raw_pages.jsonl`.
* Another script reshapes that into clean **per-plant JSON**, then packs it into **SQLite**.
* I build two vector indexes:

  * **Species index** (plant summaries) for quick matching.
  * **Page index** (small text chunks) for “Deep search” with **source snippets and images**.
* A small **FastAPI** server:

  * Finds the most relevant plants/pages,
  * Asks my **local Ollama** model to compose an answer,
  * Serves a simple **browser UI** at `/ui/`.
* It never leaves my machine.

---

## Project structure

```
plantdeck/
├─ app/
│  ├─ server.py                # FastAPI + RAG pipeline + static/image mounts
│  └─ static/
│     └─ index.html            # Minimal UI (dark theme, Deep search toggle)
├─ data/
│  └─ plants.db                # SQLite with structured herb data
├─ build/
│  ├─ raw_pages.jsonl          # one line per page (text + images + meta)
│  ├─ embeddings.npy           # species-level embeddings
│  ├─ emb_map.pkl              # id/label map for species embeddings
│  ├─ page_embeddings.npy      # page-level embeddings (Deep search)
│  └─ page_map.pkl             # page metadata/snippets (Deep search)
├─ images/                     # extracted page PNGs and embedded figures
├─ models/                     # (optional) local SentenceTransformer folder
├─ pdfs/                       # I put my PDFs here
├─ tools/
│  ├─ extract_pdfs.py          # PyMuPDF + OCR (Tesseract) + fallbacks
│  ├─ structure_plants.py      # shape pages → per-plant JSON
│  ├─ build_sqlite.py          # write SQLite (species, uses, safety, cites…)
│  ├─ build_index.py           # species-level embedding index
│  └─ build_page_index.py      # page-level embedding index + snippets
└─ .venv/                      # my Python virtualenv (optional but recommended)
```

---

## How I run it (quick start)

### 1) Requirements I installed

* **Python** 3.10+ (I’m on 3.13)
* **Ollama** (running locally) and at least one model. I use `llama3:latest` or `mistral:latest`.
* **Tesseract OCR** (Windows): I installed the UB Mannheim build, then copied the folder to a user-writable “portable” location.

> My portable Tesseract lives at
> `C:\Users\moham\tools\tesseract-portable\`
> and contains `tesseract.exe` and `tessdata\eng.traineddata`.

### 2) Set up the Python environment

```powershell
cd C:\Users\moham\plantdeck
.\.venv\Scripts\Activate.ps1   # if you created a venv
pip install --upgrade pip
pip install pymupdf pillow pikepdf pdfminer.six tqdm sentence-transformers fastapi uvicorn requests
# optional fallback renderer:
pip install pdf2image
```

### 3) Point to Tesseract (I do this in each new shell)

```powershell
$env:TESSERACT_EXE   = "$HOME\tools\tesseract-portable\tesseract.exe"
$env:TESSDATA_PREFIX = "$HOME\tools\tesseract-portable\tessdata"
$env:PYTHONIOENCODING = "utf-8"
& $env:TESSERACT_EXE --version
```

### 4) Drop my PDFs

I copy herbal monographs/books into `./pdfs/`.

### 5) Extract text + images + OCR

```powershell
python .\tools\extract_pdfs.py --ocr --dpi 300 --lang eng
```

What this does (in my words): for each PDF page, it prefers native text; if it sees a “scanned” page or I force `--ocr`, it renders the page to PNG and runs Tesseract. Embedded images get extracted too. Everything is written into `build/raw_pages.jsonl`, and all PNGs land in `/images`.

### 6) Shape → SQLite → indexes

```powershell
python .\tools\structure_plants.py
python .\tools\build_sqlite.py
python .\tools\build_index.py        # species index
python .\tools\build_page_index.py   # page index for Deep search
```

* The SQLite has tables like `species`, `common_name`, `usecase`, `safety`, `citation`.
* The page index is what powers the snippets + image previews.

### 7) Run the server

```powershell
$env:OLLAMA_URL   = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "llama3:latest"   # or mistral:latest, llama2:7b, etc.
uvicorn app.server:app --host 0.0.0.0 --port 8088 --reload
```

Then I open **[http://localhost:8088/](http://localhost:8088/)** — it redirects to `/ui/`.

* I tick **Deep search** when I want page snippets + images.
* I try questions like:

  * “What are the uses and cautions of ginger?”
  * “Is yarrow poisonous?”
  * “How do I prepare peppermint for indigestion?”

---

## Docker usage & recommended settings

I ship a hardened Docker setup so I can run PlantDeck without polluting my host machine, and keep private data (PDFs, DB, images) **outside** the image.

### What’s inside the image

* Base: `python:3.11-slim`
* System deps: `tesseract-ocr` (+ `eng` data), `poppler-utils` (for optional `pdf2image` fallback)
* Runs as a **non-root user** by default
* Healthcheck hitting `/health`
* No large data baked in (code only)

### Quick start

```bash
# build the image
docker compose build

# run the API + UI at http://localhost:8088/
docker compose up -d

# tail logs
docker compose logs -f
```

> On Linux without Docker Desktop, if `host.docker.internal` isn’t available, add:
>
> ```yaml
> extra_hosts:
>   - "host.docker.internal:host-gateway"
> ```
>
> or set `OLLAMA_URL` to your host IP.

### Volumes (bind mounts)

I keep all large and private data on the host and mount it into the container:

* `./pdfs` → `/app/pdfs` (**read-only**)
* `./build` → `/app/build` (read-write)
* `./images` → `/app/images` (read-write)
* `./data` → `/app/data` (read-write)
* `./models` → `/app/models` (optional SentenceTransformer cache, read-write)
* `./app/static` → `/app/app/static` (so I can edit the UI live, read-only)

These match the default `docker-compose.yml`.

### Environment variables

* `OLLAMA_URL` (default in image: `http://host.docker.internal:11434`)
* `OLLAMA_MODEL` (default in image: `llama3:latest`)
* (Optional) `SENTENCE_TRANSFORMERS_HOME=/app/models` if I want to cache models inside the `models/` volume.

### Security hardening (what I use)

* **Non-root user** in the image (`appuser` uid 10001)
* **Read-only root filesystem** in Compose:

  ```yaml
  read_only: true
  tmpfs:
    - /tmp
  security_opt:
    - no-new-privileges:true
  cap_drop:
    - ALL
  ```
* Only the mounted data dirs are writable. This blocks accidental writes elsewhere and reduces blast radius.

> If I hit permission issues on Linux host volumes (because the container user is `10001`), either:
>
> * `chown -R 10001:100 <folders>`, or
> * relax permissions: `chmod -R u+rwX,g+rwX <folders>`

### Networking

* The container talks to my **local Ollama** on the host via `http://host.docker.internal:11434`.
* On Linux without Docker Desktop, use `extra_hosts` (above) or point `OLLAMA_URL` to the host IP.

### Healthcheck

The Dockerfile includes a healthcheck that pings `http://127.0.0.1:8088/health`. I can see `healthy` once the API is up:

```bash
docker inspect --format='{{json .State.Health}}' plantdeck | jq
```

### Production-ish tips

* Pin a model I actually have locally, e.g.:

  ```yaml
  environment:
    - OLLAMA_MODEL=mistral:latest
  ```
* Resource limits (optional):

  ```yaml
  deploy:
    resources:
      limits:
        cpus: '4'
        memory: 6g
  ```
* Keep the image small by **not** copying data into it; everything is mounted.
* If I want more OCR languages, extend the image and install `tesseract-ocr-<lang>` (e.g., `tesseract-ocr-deu`).

### Typical container workflow

```bash
# 1) Extract (OCR inside container)
docker compose exec plantdeck bash -lc \
  "python tools/extract_pdfs.py --ocr --dpi 300 --lang eng"

# 2) Build structured data + indexes
docker compose exec plantdeck bash -lc \
  "python tools/structure_plants.py && \
   python tools/build_sqlite.py && \
   python tools/build_index.py && \
   python tools/build_page_index.py"

# 3) Browse http://localhost:8088/ (Deep search toggle in the UI)
```

### Troubleshooting

* **403/connection refused to Ollama** → verify `OLLAMA_URL` and that Ollama is running on the host.
* **No images in UI** → confirm `images/` has PNGs and `build/raw_pages.jsonl` contains `images` arrays; `/health` should say `"images_available": true`.
* **Permission denied on writes** → I’m running with `read_only: true`. Only `/tmp` and bind mounts are writable; ensure `build/`, `images/`, `data/` are mounted and writable by the container user.
* **Slow first run** → SentenceTransformer downloads its weights; I mount `./models` so it caches.

---

## CI/CD (GitHub Actions)

I wired up Actions so every push/PR runs lint + Docker build, and release tags publish a container image to GHCR (GitHub Container Registry).

### Workflows

* `.github/workflows/ci.yml` (runs on push/PR to `main`)

  * Python lint: **ruff** + **black**
  * Security lint: **bandit** (informational)
  * **Docker build** with Buildx (no push, just ensures Dockerfile stays healthy)
  * Optional **Trivy** filesystem scan of the repo (critical/high)

* `.github/workflows/docker-publish.yml` (runs on tags like `v0.1.0`, or manual dispatch)

  * Logs in to **GHCR** with the built-in `GITHUB_TOKEN`
  * Builds and **pushes**:

    * `ghcr.io/EzioDEVio/plantdeck_rag:<version>`
    * `ghcr.io/EzioDEVio/plantdeck_rag:<short-sha>`
    * `ghcr.io/EzioDEVio/plantdeck_rag:latest`
  * Optional **Trivy** image scan after push

* (Optional) `.github/dependabot.yml`

  * Weekly bumps for Actions and pip dependencies

### How I use it

* CI runs automatically on push/PR.
* To publish a versioned image:

  ```bash
  git tag v0.1.0
  git push origin v0.1.0
  ```

  Then pull it anywhere with:

  ```bash
  docker pull ghcr.io/EzioDEVio/plantdeck_rag:v0.1.0
  ```

> First time you publish, check **Repo → Settings → Packages** and make sure the package visibility is what you want (public/private).

---

## How the pipeline works (deeper dive)

### Extraction (`tools/extract_pdfs.py`)

* **PyMuPDF** is first choice: grabs page text and embedded images.
* If a page looks scanned or I pass `--ocr`, it renders the page (DPI 300) and runs **Tesseract**.
* If PyMuPDF explodes, it tries a **repair via pikepdf** and re-reads.
* If it still fails, it falls back to **pdfminer.six** (text-only).
* Optional: if I set `POPPLER_PATH` and installed `pdf2image`, it can render via Poppler and OCR that.

Each page becomes a JSON line like:

```json
{
  "pdf": "SomeBook.pdf",
  "page": 42,
  "text": "…page text…",
  "images": [{"path":"images/SomeBook_p42_img1.png","xref":123}]
}
```

### Structuring (`tools/structure_plants.py`)

I normalize headings (Latin name, common names, family, parts used, actions, uses, preparations, dosage, safety, lookalikes…) into a clean per-plant JSON. Then I dedupe lists and save `build/plants/<Latin_Name>.json`.

### SQLite (`tools/build_sqlite.py`)

I insert those JSONs into `data/plants.db`. The main tables I use are:

```sql
-- Simplified schema sketch
CREATE TABLE species(
  id INTEGER PRIMARY KEY,
  latin_name TEXT,
  family TEXT,
  id_features TEXT,
  dosage TEXT
);
CREATE TABLE common_name(species_id INTEGER, name TEXT);
CREATE TABLE usecase(species_id INTEGER, indication TEXT, evidence TEXT);
CREATE TABLE safety(species_id INTEGER,
  toxicity TEXT, contraindications TEXT, interactions TEXT, notes TEXT);
CREATE TABLE citation(species_id INTEGER, pdf TEXT, page INTEGER);
```

### Indexes (`tools/build_index.py` / `build_page_index.py`)

* I embed species summaries with **`sentence-transformers/all-MiniLM-L6-v2`** (local if I downloaded it into `./models/`).
* I also chunk page text and build a **page-level index**; I store short **snippets** with PDF+page pointers.

### API (`app/server.py`)

* `/ask` does:

  1. nearest neighbors on species index,
  2. (optional) nearest neighbors on page index (Deep search),
  3. fetches structured context from SQLite,
  4. composes a grounded prompt and calls **Ollama**,
  5. returns the answer + hits + context + page snippets + image URLs.

Small prompt snippet (the actual code is longer):

```python
system = ("You are a cautious herbal field guide. Use ONLY the context. "
          "If info is missing, say so. Always include: "
          "'Field guide only; not medical advice.'")
# … species summaries & source snippets …
requests.post(f"{OLLAMA_URL}/api/generate",
              json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False})
```

### UI (`app/static/index.html`)

* Pure HTML/CSS/JS.
* Shows the answer, top species matches, merged citations, **source snippets**, and **page images** (thumbnails link to `/images/...`).
* There’s a **Deep search** toggle that adds page-level context.

---

## Environment variables I use

* `TESSERACT_EXE` → full path to `tesseract.exe`
* `TESSERACTDATA_PREFIX` → folder containing `tessdata\eng.traineddata`
* `PYTHONIOENCODING` → I set to `utf-8` to keep console output sane on Windows
* `OLLAMA_URL` → `http://127.0.0.1:11434`
* `OLLAMA_MODEL` → `llama3:latest` (or any local model name)
* `POPPLER_PATH` → optional path to Poppler bin directory for `pdf2image`

---

## API quick reference

* `GET /health`
  Returns `{ ok, model, deep_available, images_available }`.

* `GET /plants?limit=100&offset=0`
  Lists Latin names.

* `POST /ask`
  Body:

  ```json
  { "q": "Is yarrow poisonous?", "k": 5, "deep": true, "k_pages": 8 }
  ```

  Returns:

  ```json
  {
    "answer": "…composed by the local LLM…",
    "hits": [{ "species_id": 1, "latin_name": "…", "score": 0.42 }],
    "context": [ { "latin_name": "…", "uses": [ … ], "citations": [ … ] } ],
    "page_context": [ { "pdf": "file.pdf", "page": 123, "snippet": "…", "images": ["/images/…png"] } ]
  }
  ```

PowerShell test:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8088/ask `
  -ContentType application/json `
  -Body '{"q":"Ginger dosage for nausea","deep":true,"k_pages":8}'
```

---

## Troubleshooting (stuff I ran into)

* **Tesseract not found**
  Make sure these work **in the same shell**:

  ```powershell
  $env:TESSERACT_EXE   = "$HOME\tools\tesseract-portable\tesseract.exe"
  $env:TESSDATA_PREFIX = "$HOME\tools\tesseract-portable\tessdata"
  & $env:TESSERACT_EXE --version
  ```
* **Huge console spam (MuPDF warnings)**
  I mute them inside the extractor; detailed issues go to `build\extract.log`.
* **`UnicodeDecodeError` with Tesseract output**
  I fixed it by **reading stdout as bytes** and decoding UTF-8 manually in the extractor.
* **UI shows “Not Found”**
  Use the server in this repo (`app/server.py`), which auto-mounts `/ui/` and redirects `/` → `/ui/`.
* **No images in the UI**
  Make sure `images/` has PNGs and `build/raw_pages.jsonl` has `images` arrays. The server mounts `/images`, and `/ask` returns image URLs in `page_context`.
* **Sparse answers**
  Ensure you’ve run `build_page_index.py` and the header says **Deep: on** in `/health`.

---

## Performance tips

* Use a smaller local LLM (e.g., `mistral:7b-instruct` or a quantized `llama3`) for faster answers.
* Reuse the `all-MiniLM-L6-v2` encoder locally by placing it in `./models/` so it doesn’t download every time.
* Limit `k` and `k_pages` to 5–8 for snappy responses.

---

## Roadmap (what I plan next)

* A `/plant/<Latin>` detail page with images, uses, dosage, and safety in one place.
* ESP32-CAM or Raspberry Pi camera capture → push JPEG into an on-device plant ID model → feed the Latin/common name into this RAG pipeline for safety/use lookups.
* Add keyword/BM25 to blend with embeddings for even sharper recall.

---

## Contributing / License

This is a practical, offline project I use in the field. PRs welcome if you keep it simple and local-first. I’ll add a license file after I push to GitHub:
`https://github.com/EzioDEVio/plantdeck_rag.git`

---

## One last reminder

This app is a **field guide**. It’s meant to help me locate relevant passages in my own books quickly, not to replace proper training or professional advice. Always cross-check the PDFs and be safe.
