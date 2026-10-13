"""
RAG Ingestion Pipeline -- PDF to Vector Store.

Usage:
  python ingest.py --file paper.pdf
  python ingest.py --file paper.pdf --db-path ./my_index
  python ingest.py --file paper.pdf --force-reingest
  python ingest.py --file paper.pdf --text-only    # skip image embedding
  python ingest.py --file paper.pdf --image-only   # skip text embedding

Pipeline:
  1. Extract: PDF pages -> DocLayout-YOLO -> saved images
  2. OCR: Vision API transcribes each page -> markdown (saved for resume)
  3. Chunk: MarkdownTopicChunker -> chunks (in memory)
  4. Embed: Jina CLIP v2 (text) + Gemini Embedding 2 (images) -> FAISS + LanceDB

Resume:
  If pipeline stops mid-way, re-running skips completed phases.
  Images already present -> skip extraction.
  OCR markdown already saved -> skip OCR.
  Vector DB already built -> done.
"""

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import json
import sys
import time
import threading

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from rag_system.kb_manager import RAGKnowledgeBase
from rag_system.logging_setup import setup_rag_logging

setup_rag_logging(level="INFO")

DEFAULT_DB_PATH = os.path.join(current_dir, "rag_index")
OCR_TIMEOUT_SECONDS = 120
OCR_MAX_RETRIES = 5
OCR_RETRY_BACKOFF = 2


# ==============================================================
# PIPELINE STATE (checkpoint/resume)
# ==============================================================

class PipelineState:
    """
    Tracks pipeline progress via pipeline_state.json.
    Enables resume from last completed phase.
    """

    def __init__(self, db_path: str):
        self.state_path = os.path.join(db_path, "pipeline_state.json")
        self.state = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "pdf_path": "",
            "extracted": False,
            "ocr_done": False,
            "ocr_markdown_path": "",
            "text_embedded": False,
            "image_embedded": False,
            "done": False,
            "text_only": False,
            "image_only": False,
        }

    def save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def mark_extracted(self, pdf_path: str):
        self.state["pdf_path"] = pdf_path
        self.state["extracted"] = True
        self.save()

    def mark_ocr_done(self, markdown_path: str):
        self.state["ocr_done"] = True
        self.state["ocr_markdown_path"] = markdown_path
        self.save()

    def mark_text_embedded(self):
        self.state["text_embedded"] = True
        self._check_done()
        self.save()

    def mark_image_embedded(self):
        self.state["image_embedded"] = True
        self._check_done()
        self.save()

    def _check_done(self):
        text_ok = self.state.get("text_embedded", False) or self.state.get("text_only", False)
        image_ok = self.state.get("image_embedded", False) or self.state.get("image_only", False)
        self.state["done"] = text_ok and image_ok

    @property
    def is_text_embedded(self) -> bool:
        return self.state.get("text_embedded", False)

    @property
    def is_image_embedded(self) -> bool:
        return self.state.get("image_embedded", False)

    @property
    def is_extracted(self) -> bool:
        return self.state.get("extracted", False)

    @property
    def is_ocr_done(self) -> bool:
        return self.state.get("ocr_done", False)

    @property
    def is_embedded(self) -> bool:
        return self.state.get("text_embedded", False) and self.state.get("image_embedded", False)

    @property
    def is_done(self) -> bool:
        return self.state.get("done", False)

    @property
    def ocr_markdown_path(self) -> str:
        return self.state.get("ocr_markdown_path", "")


def _images_exist(images_dir: str) -> bool:
    """Check if extracted images already exist."""
    if not os.path.isdir(images_dir):
        return False
    for name in os.listdir(images_dir):
        if name.lower().endswith((".png", ".jpg", ".jpeg")):
            return True
    return False


# ==============================================================
# DB EXISTENCE CHECK
# ==============================================================

def db_exists(db_path: str) -> bool:
    """Check if a complete vector DB already exists at the given path."""
    faiss_index = os.path.join(db_path, "text_index.faiss")
    faiss_meta = os.path.join(db_path, "text_metadata.json")
    return os.path.exists(faiss_index) and os.path.exists(faiss_meta)


def db_status(db_path: str):
    """Print status of existing vector DB."""
    print("=" * 60)
    print("VECTOR DB FOUND")
    print("=" * 60)
    print(f"  Location: {db_path}")

    try:
        kb = RAGKnowledgeBase(index_dir=db_path)
        status = kb.status()
        print(f"  Text chunks: {status['text_chunks']}")
        print(f"  Images:      {status['image_count']}")
        print(f"  Files:       {status['indexed_files']}")
        print(f"  Multimodal:  {status['multimodal_enabled']}")
    except Exception:
        faiss_path = os.path.join(db_path, "text_index.faiss")
        if os.path.exists(faiss_path):
            size_kb = os.path.getsize(faiss_path) / 1024
            print(f"  FAISS index: {size_kb:.1f} KB")

    print()
    print("  Use --force-reingest to rebuild from scratch.")
    print("=" * 60)


# ==============================================================
# FULL PIPELINE (with checkpoint/resume)
# ==============================================================

def run_pipeline(file_path: str, db_path: str, force: bool = False,
                 text_only: bool = False, image_only: bool = False) -> bool:
    """
    Run the full ingestion pipeline with checkpoint/resume.

    If pipeline stopped mid-way previously, it resumes from the
    last completed phase:
      - Images already extracted -> skip extraction
      - OCR markdown already saved -> skip OCR
      - Vector DB already built -> done

    Modes:
      --text-only:  skip image embedding (saves Cohere API calls)
      --image-only: skip text embedding (saves Jina CPU time)
    """
    os.makedirs(db_path, exist_ok=True)
    state = PipelineState(db_path)
    images_dir = os.path.join(db_path, "extracted_images")

    # If force, reset state
    if force:
        state.state = {
            "pdf_path": file_path,
            "extracted": False,
            "ocr_done": False,
            "ocr_markdown_path": "",
            "text_embedded": False,
            "image_embedded": False,
            "done": False,
            "text_only": text_only,
            "image_only": image_only,
        }
        state.save()
    else:
        # Set mode flags on first run
        state.state["text_only"] = text_only
        state.state["image_only"] = image_only
        state.save()

    print("=" * 60)
    print("RAG INGESTION PIPELINE")
    print("=" * 60)
    print(f"  PDF:     {os.path.basename(file_path)}")
    print(f"  DB path: {db_path}")
    if text_only:
        print(f"  Mode:    TEXT ONLY (skip image embedding)")
    elif image_only:
        print(f"  Mode:    IMAGE ONLY (skip text embedding)")
    else:
        print(f"  Mode:    FULL (text + images)")

    # Show resume state
    if state.is_extracted or state.is_ocr_done or state.is_embedded:
        print("  RESUME:  detected previous run, skipping completed phases")
    print()

    t_start = time.time()

    # ── Phase 1: Parse PDF (extract text + layout regions) ────────
    blocks = None
    text_blocks = []
    image_blocks = []

    if state.is_extracted and _images_exist(images_dir):
        print("─" * 60)
        print("PHASE 1/4: PARSE PDF")
        print("─" * 60)
        print("  SKIPPED: images already extracted")
        print(f"  Images dir: {images_dir}")
        count = len([f for f in os.listdir(images_dir)
                     if f.lower().endswith((".png", ".jpg", ".jpeg"))])
        print(f"  Found: {count} existing images")
        print()
    else:
        print("─" * 60)
        print("PHASE 1/4: PARSE PDF")
        print("─" * 60)

        kb = RAGKnowledgeBase(index_dir=db_path, strategy="topic")

        t0 = time.time()
        from rag_system.parser import PDFParser
        parser = PDFParser(output_dir=images_dir)
        blocks = parser.parse(file_path)
        elapsed_parse = time.time() - t0

        text_blocks = [b for b in blocks if b.type in ("text", "section_header")]
        image_blocks = [b for b in blocks if b.type == "image"]
        print(f"  Parsed: {len(blocks)} blocks ({len(text_blocks)} text, {len(image_blocks)} images) in {elapsed_parse:.1f}s")
        print()

        if not blocks:
            print("  ERROR: No content extracted from PDF")
            return False

        state.mark_extracted(file_path)

    # ── Phase 2: OCR via Vision API -> markdown ───────────────────
    markdown_content = ""

    if state.is_ocr_done and state.ocr_markdown_path and os.path.exists(state.ocr_markdown_path):
        print("─" * 60)
        print("PHASE 2/4: OCR (Vision API)")
        print("─" * 60)
        print("  SKIPPED: OCR already completed")
        with open(state.ocr_markdown_path, "r", encoding="utf-8") as f:
            markdown_content = f.read()
        print(f"  Loaded: {len(markdown_content)} chars from saved markdown")
        print()
    else:
        print("─" * 60)
        print("PHASE 2/4: OCR (Vision API)")
        print("─" * 60)

        t0 = time.time()
        ocr_md_path = os.path.join(db_path, "ocr_output.md")
        markdown_content = _ocr_pdf_to_markdown(file_path, images_dir, ocr_md_path)
        elapsed_ocr = time.time() - t0
        print(f"  OCR complete: {len(markdown_content)} chars in {elapsed_ocr:.1f}s")
        print()

        if not markdown_content.strip():
            print("  ERROR: OCR produced empty content")
            return False

        state.mark_ocr_done(ocr_md_path)

    # ── Phase 3: Chunk (in memory) ───────────────────────────────
    print("─" * 60)
    print("PHASE 3/4: CHUNK (topic-aware, in memory)")
    print("─" * 60)

    t0 = time.time()
    chunks = _chunk_markdown_in_memory(markdown_content, images_dir, file_path)
    elapsed_chunk = time.time() - t0
    print(f"  Chunked: {len(chunks)} chunks in {elapsed_chunk:.1f}s")
    print()

    if not chunks:
        print("  ERROR: No chunks produced")
        return False

    # ── Phase 4: Embed + Store ───────────────────────────────────
    print("─" * 60)
    print("PHASE 4/4: EMBED + STORE")
    print("─" * 60)

    kb = RAGKnowledgeBase(index_dir=db_path, strategy="topic")

    t0 = time.time()

    # Embed text chunks into FAISS (skip if --image-only)
    if not image_only:
        print(f"  Embedding {len(chunks)} text chunks into FAISS (Jina CLIP v2) ...", flush=True)
        kb._text_store.add_chunks(chunks)
        print(f"  FAISS: {kb._text_store.size} vectors indexed")
        state.mark_text_embedded()
    else:
        print(f"  Text embedding SKIPPED (--image-only mode)")

    # Embed images into LanceDB (skip if --text-only)
    if not text_only:
        # If we didn't parse this run, we need to discover existing image_blocks
        if not image_blocks and _images_exist(images_dir):
            image_blocks = _discover_image_blocks(images_dir, file_path)

        if image_blocks and kb._image_store and kb._has_multimodal:
            print(f"  Embedding {len(image_blocks)} images into LanceDB (Gemini Embedding 2) ...", flush=True)
            kb._index_images(image_blocks, file_path)
            print(f"  LanceDB: {kb._image_store.count()} images indexed")
            state.mark_image_embedded()
        else:
            print(f"  Image embedding skipped (no images or multimodal disabled)")
    else:
        print(f"  Image embedding SKIPPED (--text-only mode)")

    elapsed_embed = time.time() - t0
    print(f"  Embed + store: {elapsed_embed:.1f}s")
    print()

    # ── Done ─────────────────────────────────────────────────────
    total_elapsed = time.time() - t_start
    status = kb.status()

    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Text chunks:  {status['text_chunks']}")
    print(f"  Images:       {status['image_count']}")
    print(f"  DB location:  {db_path}")
    print(f"  Total time:   {total_elapsed:.1f}s")
    print()
    print(f"  Query with: python query.py --db-path {db_path}")
    print("=" * 60)

    return True


def _discover_image_blocks(images_dir: str, source_file: str):
    """Build ContentBlock-like objects from existing extracted images."""
    import re
    from rag_system.parser import ContentBlock

    blocks = []
    if not os.path.isdir(images_dir):
        return blocks

    for name in sorted(os.listdir(images_dir)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue

        # Extract page number from filename: ..._p5_...
        page_match = re.search(r"_p(\d+)_", name)
        page_num = int(page_match.group(1)) if page_match else 0

        # Extract section from filename: ..._5_2_1_...
        sec_match = re.search(r"_p\d+_((?:\d+_)+)", name)
        section_path = []
        if sec_match:
            section_path = sec_match.group(1).rstrip("_").split("_")

        blocks.append(ContentBlock(
            type="image",
            content=os.path.join(images_dir, name),
            page_number=page_num,
            section_path=section_path,
            metadata={"source": source_file, "filename": name},
        ))

    return blocks


# ==============================================================
# OCR HELPER (with streaming timeout)
# ==============================================================

def _ocr_pdf_to_markdown(pdf_path: str, images_dir: str, save_path: str = None) -> str:
    """
    Render each PDF page, send to vision API, return markdown string.
    Saves markdown to disk after each page for crash recovery.

    Streaming timeout: if no token arrives within OCR_TIMEOUT_SECONDS,
    the request is aborted and retried immediately.
    """
    import base64
    import re
    import tempfile
    from collections import defaultdict

    try:
        import fitz
    except ImportError:
        print("  ERROR: PyMuPDF required. pip install pymupdf")
        return ""

    try:
        from openai import OpenAI
    except ImportError:
        print("  ERROR: openai required. pip install openai")
        return ""

    try:
        from PIL import Image
    except ImportError:
        print("  ERROR: Pillow required. pip install pillow")
        return ""

    # Vision API config
    api_key = os.environ.get("OPENAI_API_KEY", "skxxxxxxxxxxxxxx")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://opencode.ai/zen/v1")
    model = os.environ.get("OCR_MODEL", "mimo-v2.5-free")

    if not api_key:
        print("  WARNING: No OPENAI_API_KEY set. Skipping OCR.")
        print("  Set OPENAI_API_KEY environment variable to enable OCR.")
        return ""

    client = OpenAI(api_key=api_key, base_url=base_url)

    instruction = (
        "You are an OCR engine. Transcribe ONLY the main body content of the "
        "given page into clean, well-structured markdown.\n\n"
        "STRICT OUTPUT RULES:\n"
        "- Output ONLY the page's actual body text: paragraphs, real headings, "
        "bullet/numbered lists, tables, and figure captions.\n"
        "- NEVER add meta text: no summaries, no commentary, no page labels.\n"
        "- OMIT page furniture: page numbers, running heads, watermarks, QR codes.\n"
        "- Transcribe verbatim, preserving headings and paragraph structure.\n"
        "- Do not reference or embed images. Text transcription only."
    )

    max_image_dim = 1280

    def _images_by_page(idx_dir):
        pages = defaultdict(list)
        if not os.path.isdir(idx_dir):
            return pages
        for name in sorted(os.listdir(idx_dir)):
            if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            match = re.search(r"_p(\d+)_", name)
            if match:
                page_num = int(match.group(1))
                pages[page_num].append(os.path.join(idx_dir, name))
        return pages

    def _downscale(path):
        with Image.open(path) as img:
            if max(img.size) <= max_image_dim:
                return path, False
            img.thumbnail((max_image_dim, max_image_dim), Image.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            out = os.path.join(tempfile.gettempdir(), f"viz_{os.path.basename(path)}")
            img.save(out, format="PNG", optimize=True)
            return out, True

    def _data_url(path):
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")
        with open(path, "rb") as f:
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode()

    def _ocr_page_with_timeout(page_num, clip_paths, rendered_paths):
        """
        OCR a single page with streaming timeout.

        If no token arrives within OCR_TIMEOUT_SECONDS, abort and retry.
        Returns the transcribed text.
        """
        images = clip_paths + rendered_paths
        content = [{"type": "text", "text": f"Page {page_num}. {instruction}"}]
        temp_copies = []
        for img_path in images:
            send_path, is_temp = _downscale(img_path)
            if is_temp:
                temp_copies.append(send_path)
            content.append({"type": "image_url", "image_url": {"url": _data_url(send_path)}})

        try:
            for attempt in range(1, OCR_MAX_RETRIES + 1):
                tokens = []
                last_token_time = time.time()
                abort_flag = {"flag": False}

                def _timeout_watchdog():
                    """Monitor for streaming stall."""
                    while not abort_flag["flag"]:
                        elapsed_no_token = time.time() - last_token_time
                        if elapsed_no_token > OCR_TIMEOUT_SECONDS:
                            abort_flag["flag"] = True
                            return
                        time.sleep(1)

                watchdog = threading.Thread(target=_timeout_watchdog, daemon=True)
                watchdog.start()

                try:
                    stream = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": content}],
                        stream=True,
                    )
                    for chunk in stream:
                        if abort_flag["flag"]:
                            print(f"\n  [TIMEOUT] Page {page_num}: no token for {OCR_TIMEOUT_SECONDS}s, aborting attempt {attempt}", flush=True)
                            break
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                            token = chunk.choices[0].delta.content
                            tokens.append(token)
                            last_token_time = time.time()
                            print(token, end="", flush=True)

                    abort_flag["flag"] = True  # stop watchdog

                    if tokens:
                        # Got some tokens -- success
                        break
                    else:
                        # No tokens at all -- retry
                        if attempt < OCR_MAX_RETRIES:
                            wait = OCR_RETRY_BACKOFF * attempt
                            print(f"\n  [RETRY {attempt}/{OCR_MAX_RETRIES}] Page {page_num}: no response (waiting {wait}s)", flush=True)
                            time.sleep(wait)
                            continue
                        else:
                            return f"\n[ERROR] Page {page_num}: no response after {OCR_MAX_RETRIES} attempts\n"

                except Exception as exc:
                    abort_flag["flag"] = True
                    if attempt < OCR_MAX_RETRIES:
                        wait = OCR_RETRY_BACKOFF * attempt
                        print(f"\n  [RETRY {attempt}/{OCR_MAX_RETRIES}] Page {page_num}: {exc} (waiting {wait}s)", flush=True)
                        time.sleep(wait)
                    else:
                        return f"\n[ERROR] Page {page_num}: {exc}\n"

                finally:
                    abort_flag["flag"] = True
                    for path in temp_copies:
                        try:
                            os.remove(path)
                        except OSError:
                            pass

            return "".join(tokens) if tokens else ""

        finally:
            for path in temp_copies:
                try:
                    os.remove(path)
                except OSError:
                    pass

    # ── Main OCR loop ────────────────────────────────────────────
    figures = _images_by_page(images_dir)
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    all_markdown = []

    print(f"  OCR: {total_pages} pages, model={model}, timeout={OCR_TIMEOUT_SECONDS}s", flush=True)
    t0 = time.time()

    try:
        for page_idx in range(total_pages):
            page_num = page_idx + 1
            clips = figures.get(page_num, [])

            # Render full page
            pix = doc[page_idx].get_pixmap(dpi=120)
            rendered_path = os.path.join(tempfile.gettempdir(), f"ocr_page_{page_num}.png")
            pix.save(rendered_path)

            print(f"\n  --- Page {page_num}/{total_pages} ({len(clips) + 1} images) ---", flush=True)
            page_text = _ocr_page_with_timeout(page_num, clips, [rendered_path])

            # Build markdown for this page
            page_md = f"\n## Page {page_num}\n\n"
            for img in clips:
                page_md += f"![]({img.replace(os.sep, '/')})\n"
            page_md += "\n" + page_text + "\n\n"
            all_markdown.append(page_md)

            # Save after each page for crash recovery
            if save_path:
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write("".join(all_markdown))

            # Cleanup rendered page
            try:
                os.remove(rendered_path)
            except OSError:
                pass
    finally:
        doc.close()

    elapsed = time.time() - t0
    print(f"\n  OCR done: {total_pages} pages in {elapsed:.1f}s")

    return "".join(all_markdown)


# ==============================================================
# IN-MEMORY CHUNKING
# ==============================================================

def _chunk_markdown_in_memory(markdown_content: str, images_dir: str, source_file: str):
    """
    Chunk markdown content in memory using MarkdownTopicChunker.
    No intermediate chunks.json is written.
    """
    import tempfile

    from rag_system.md_chunker import MarkdownTopicChunker

    chunker = MarkdownTopicChunker(
        images_dir=images_dir,
        max_chunk_size=6000,
        min_chunk_size=200,
    )

    # Write markdown to a temp file (chunker needs a file path)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(markdown_content)
        tmp_path = tmp.name

    try:
        chunks = chunker.chunk_markdown(tmp_path, source_file=source_file)
    finally:
        os.remove(tmp_path)

    return chunks


# ==============================================================
# MAIN
# ==============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="RAG Ingestion Pipeline -- PDF to Vector Store",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic ingestion (auto-resumes if interrupted)
  python ingest.py --file paper.pdf

  # Custom DB location
  python ingest.py --file paper.pdf --db-path ./my_index

  # Force rebuild from scratch (ignore checkpoint)
  python ingest.py --file paper.pdf --force-reingest

  # Text only (skip image embedding — saves Cohere API calls)
  python ingest.py --file paper.pdf --text-only

  # Image only (skip text embedding — saves Jina CPU time)
  python ingest.py --file paper.pdf --image-only
        """,
    )

    parser.add_argument(
        "--file", required=True,
        help="Path to PDF file to ingest",
    )
    parser.add_argument(
        "--db-path", default=DEFAULT_DB_PATH,
        help="Vector DB storage path (default: %(default)s)",
    )
    parser.add_argument(
        "--force-reingest", action="store_true",
        help="Force full pipeline even if vector DB already exists",
    )
    parser.add_argument(
        "--text-only", action="store_true",
        help="Only embed text chunks (skip image embedding, saves Cohere API calls)",
    )
    parser.add_argument(
        "--image-only", action="store_true",
        help="Only embed images (skip text embedding, saves Jina CPU time)",
    )

    args = parser.parse_args()

    # Validate PDF exists
    abs_path = os.path.abspath(args.file)
    if not os.path.exists(abs_path):
        print(f"ERROR: File not found: {abs_path}")
        sys.exit(1)

    if not abs_path.lower().endswith(".pdf"):
        print(f"ERROR: Expected a PDF file, got: {os.path.basename(abs_path)}")
        sys.exit(1)

    db_path = os.path.abspath(args.db_path)

    # Check if fully done (skip if --force, --text-only, or --image-only)
    skip_check = args.force_reingest or args.text_only or args.image_only
    if db_exists(db_path) and not skip_check:
        db_status(db_path)
        sys.exit(0)

    # Run pipeline (with resume)
    success = run_pipeline(
        abs_path, db_path,
        force=args.force_reingest,
        text_only=args.text_only,
        image_only=args.image_only,
    )
    sys.exit(0 if success else 1)
