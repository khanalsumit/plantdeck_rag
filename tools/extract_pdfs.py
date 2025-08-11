#!/usr/bin/env python3
import os, io, json, subprocess, sys, traceback, hashlib, time
from pathlib import Path

# Primary
import fitz  # PyMuPDF
from PIL import Image

# Fallbacks (no admin)
import pikepdf
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer

# Optional fallback (only if available AND POPPLER_PATH set)
try:
    from pdf2image import convert_from_path
    HAS_PDF2IMG = True
except Exception:
    HAS_PDF2IMG = False

try:
    from tqdm.auto import tqdm
except Exception:
    def tqdm(it, **kwargs): return it  # no-op if tqdm not installed

SRC = Path("pdfs")
OUT = Path("build")
IMG_DIR = Path("images")
LOG = OUT / "extract.log"
OUT.mkdir(exist_ok=True)
IMG_DIR.mkdir(exist_ok=True)

# --- Mute MuPDF's very noisy stderr spew (syntax warnings, zlib, etc.) ---
try:
    # PyMuPDF >= 1.23
    fitz.TOOLS.mupdf_display_errors(False)
except Exception:
    pass

# ---------------- CLI OPTIONS ----------------
import argparse
ap = argparse.ArgumentParser(description="Robust PDF extractor (text+images+OCR)")
ap.add_argument("--only", help="Process only PDFs matching substring (case-insensitive)")
ap.add_argument("--dpi", type=int, default=300, help="DPI for page rendering (OCR/page fallback)")
ap.add_argument("--ocr", action="store_true", help="Force OCR for all pages (render then OCR)")
ap.add_argument("--no-ocr", action="store_true", help="Disable OCR entirely")
ap.add_argument("--lang", default="eng", help="Tesseract language (e.g., 'eng', 'eng+spa')")
ap.add_argument("--max-pages", type=int, default=0, help="Limit pages per PDF (0 = all)")
args = ap.parse_args()

TESS_EXE = os.environ.get(
    "TESSERACT_EXE",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe" if os.name == "nt" else "tesseract"
)
POPPLER_PATH = os.environ.get("POPPLER_PATH")  # only needed if we use pdf2image

def have_tesseract():
    if args.no_ocr:
        return False
    try:
        # Don't decode as text (Windows cp1252 will choke) — just drop output.
        subprocess.run([TESS_EXE, "--version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True)
        return True
    except Exception:
        return False

HAS_TESS = have_tesseract()
FORCE_OCR = args.ocr and HAS_TESS

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

def page_hash(pdf: Path, pno: int) -> str:
    return hashlib.sha1(f"{pdf}:{pno}".encode("utf-8")).hexdigest()[:10]

def ocr_pil_to_text(pil_img: Image.Image, out_png: Path, dpi=300, lang="eng") -> str:
    """
    Save PIL image with DPI and run Tesseract. Decode stdout as UTF-8 bytes to
    avoid Windows cp1252 UnicodeDecodeError.
    """
    try:
        pil_img.save(out_png, dpi=(dpi, dpi))
    except Exception:
        # last resort: save without DPI
        pil_img.save(out_png)

    if not HAS_TESS:
        return ""

    cmd = [TESS_EXE, str(out_png), "stdout", "--psm", "6", "-l", lang]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        # Explicit UTF-8 decode; drop undecodable bytes silently
        text = proc.stdout.decode("utf-8", errors="ignore")
        if proc.returncode != 0 and not text.strip():
            # keep stderr short in logs
            err = (proc.stderr or b"")[:200].decode("utf-8", errors="ignore")
            log(f"[ocr-fail] {out_png.name}: rc={proc.returncode} stderr={err!r}")
        return text
    except Exception as e:
        log(f"[ocr-exc] {out_png.name}: {e}")
        return ""

def is_scanned_page_pymupdf(page) -> bool:
    """Heuristic: very little text OR many embedded images."""
    try:
        text = page.get_text("text")
    except Exception:
        return True
    if len(text.strip()) < 50:
        return True
    try:
        imgs = page.get_images(full=True)
        if len(imgs) >= 2 and len(text.strip()) < 200:
            return True
    except Exception:
        pass
    return False

def save_pixmap_safe(doc, page, xref, name_base: str) -> str:
    """Extract an embedded image safely (normalize colorspace/alpha). On failure, save page PNG."""
    try:
        pix = fitz.Pixmap(doc, xref)
        # Normalize to RGB if not already PNG-friendly (grayscale/indexed/CMYK/etc.)
        if pix.n not in (3, 4) or (pix.colorspace and getattr(pix.colorspace, "n", None) != 3):
            pix = fitz.Pixmap(fitz.csRGB, pix)
        # Drop alpha if present
        if getattr(pix, "alpha", 0):
            pix = fitz.Pixmap(pix, 0)
        pth = IMG_DIR / f"{name_base}.png"
        pix.save(pth)
        return str(pth)
    except Exception as e:
        # Fallback: render the page (lower DPI to avoid big files)
        try:
            pm = page.get_pixmap(dpi=min(args.dpi, 200))
            pth = IMG_DIR / f"{name_base}_page.png"
            with open(pth, "wb") as fh:
                fh.write(pm.tobytes("png"))
            return str(pth)
        except Exception as e2:
            log(f"[img-fail] {name_base}: {e} / page-render: {e2}")
            return ""

def extract_with_pymupdf(pdf: Path):
    """Primary: prefer native text; OCR only for scanned pages or when forced."""
    doc = fitz.open(pdf)
    try:
        meta = doc.metadata or {}
        yield {"_meta": {"pdf": pdf.name, "title": meta.get("title"), "author": meta.get("author")}}

        total = doc.page_count
        limit = args.max_pages if args.max_pages and args.max_pages < total else total
        for pno in range(limit):
            page = doc[pno]
            imgs_meta = []
            text = ""
            try:
                do_ocr = FORCE_OCR or is_scanned_page_pymupdf(page)
                if do_ocr:
                    pix = page.get_pixmap(dpi=args.dpi)
                    pil = Image.open(io.BytesIO(pix.tobytes("png")))
                    img_name = f"{pdf.stem}_p{pno+1}_{page_hash(pdf,pno)}"
                    img_path = IMG_DIR / f"{img_name}.png"
                    text = ocr_pil_to_text(pil, img_path, dpi=args.dpi, lang=args.lang)
                    imgs_meta.append({"path": str(img_path), "xref": -1})
                else:
                    text = page.get_text("text")
                    for i, img in enumerate(page.get_images(full=True)):
                        name_base = f"{pdf.stem}_p{pno+1}_img{i+1}"
                        pth = save_pixmap_safe(doc, page, img[0], name_base)
                        if pth:
                            imgs_meta.append({"path": pth, "xref": int(img[0])})
            except Exception as e:
                # last-ditch: render page then (maybe) OCR
                log(f"[page-fail] {pdf.name} p{pno+1}: {e}")
                try:
                    pm = page.get_pixmap(dpi=args.dpi)
                    pil = Image.open(io.BytesIO(pm.tobytes("png")))
                    img_name = f"{pdf.stem}_p{pno+1}_{page_hash(pdf,pno)}_fallback"
                    img_path = IMG_DIR / f"{img_name}.png"
                    text = ocr_pil_to_text(pil, img_path, dpi=args.dpi, lang=args.lang)
                    imgs_meta.append({"path": str(img_path), "xref": -1})
                except Exception as e2:
                    log(f"[page-render-fail] {pdf.name} p{pno+1}: {e2}")
                    text = ""

            yield {"pdf": pdf.name, "page": pno + 1, "text": text, "images": imgs_meta}
    finally:
        try:
            doc.close()
        except Exception:
            pass

def extract_with_repair_then_pymupdf(pdf: Path):
    """Try to repair structure with pikepdf, then re-open with PyMuPDF."""
    fixed = OUT / f"{pdf.stem}__fixed.pdf"
    with pikepdf.open(pdf) as d:
        d.save(fixed)
    for rec in extract_with_pymupdf(fixed):
        yield rec
    try:
        fixed.unlink()
    except Exception:
        pass

def extract_with_pdfminer(pdf: Path):
    """Last resort text-only extraction."""
    # Emit a simple meta row
    yield {"_meta": {"pdf": pdf.name, "title": None, "author": None}}
    pno = 0
    for page_layout in extract_pages(str(pdf)):
        pno += 1
        if args.max_pages and pno > args.max_pages:
            break
        text = ""
        for el in page_layout:
            if isinstance(el, LTTextContainer):
                text += el.get_text()
        yield {"pdf": pdf.name, "page": pno, "text": text, "images": []}

def extract_with_pdf2image(pdf: Path):
    """Optional: render pages via poppler + OCR (if Tesseract)."""
    if not (HAS_PDF2IMG and POPPLER_PATH and HAS_TESS and not args.no_ocr):
        raise RuntimeError("pdf2image path or Tesseract missing")
    # Emit a meta row
    yield {"_meta": {"pdf": pdf.name, "title": None, "author": None}}
    pages = convert_from_path(str(pdf), dpi=args.dpi, poppler_path=POPPLER_PATH)
    for pno, pil in enumerate(pages, start=1):
        if args.max_pages and pno > args.max_pages:
            break
        img_name = f"{pdf.stem}_p{pno}_{page_hash(pdf,pno)}_poppler"
        img_path = IMG_DIR / f"{img_name}.png"
        text = ocr_pil_to_text(pil, img_path, dpi=args.dpi, lang=args.lang)
        yield {"pdf": pdf.name, "page": pno, "text": text, "images": [{"path": str(img_path), "xref": -1}]}

def process_pdf(pdf: Path):
    # Primary
    try:
        for rec in extract_with_pymupdf(pdf):
            yield rec
        return
    except Exception as e:
        log(f"[pymupdf-fail] {pdf.name}: {e}")

    # Repair + retry
    try:
        log(f"[repair] trying pikepdf on {pdf.name}")
        for rec in extract_with_repair_then_pymupdf(pdf):
            yield rec
        return
    except Exception as e:
        log(f"[repair-fail] {pdf.name}: {e}")

    # Optional poppler path (only if available AND OCR allowed)
    if HAS_PDF2IMG and POPPLER_PATH and HAS_TESS and not args.no_ocr:
        try:
            log(f"[poppler] using pdf2image on {pdf.name}")
            for rec in extract_with_pdf2image(pdf):
                yield rec
            return
        except Exception as e:
            log(f"[poppler-fail] {pdf.name}: {e}")

    # Last resort text only
    log(f"[pdfminer] falling back to pdfminer for {pdf.name}")
    for rec in extract_with_pdfminer(pdf):
        yield rec

def main():
    # reset log
    LOG.write_text("", encoding="utf-8")
    records = []
    pdfs = sorted(SRC.glob("*.pdf"))
    if args.only:
        pdfs = [p for p in pdfs if args.only.lower() in p.name.lower()]
    for pdf in tqdm(pdfs, desc="Extracting PDFs"):
        print(f"[+] Extracting {pdf.name}")
        for rec in process_pdf(pdf):
            records.append(rec)

    # write output
    out = OUT / "raw_pages.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[✓] Wrote {out} and extracted images into {IMG_DIR}/")
    if not HAS_TESS and not args.no_ocr:
        print("[i] Tesseract not found. OCR skipped (you still have text when available and page PNGs).")
    if HAS_PDF2IMG and not POPPLER_PATH:
        print("[i] pdf2image installed, but POPPLER_PATH not set; skipping poppler fallback.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        sys.exit(1)
    except Exception as e:
        log(f"[fatal] {e}\n{traceback.format_exc()}")
        raise
