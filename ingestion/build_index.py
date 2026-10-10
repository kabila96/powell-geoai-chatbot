"""Build (or rebuild) the local knowledge base.

Usage
  python -m ingestion.build_index                 # Word document + all SOURCE_URLS
  python -m ingestion.build_index --skip-web      # Word document only
  python -m ingestion.build_index --offline-html data/html   # use saved HTML copies
  python -m ingestion.build_index --dry-run       # parse + chunk + report, no API calls

Output (in INDEX_DIR, default ./index):
  chunks.jsonl      one JSON object per chunk (text + metadata)
  embeddings.npy    float32 matrix, one normalised row per chunk
  manifest.json     build time, models, per-source stats and content hashes
  snapshots/        raw HTML of each page as fetched (for auditing)
  preview.txt       human-readable dump of every chunk (review before deploying)

The index is written to a temporary folder and swapped in only after every step succeeds,
so a failed rebuild never leaves the running chatbot with a half-built index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.config import settings
from app.guardrails import sanitise_source_text
from app.retriever import CHUNKS_FILE, EMBEDDINGS_FILE, MANIFEST_FILE
from ingestion.chunker import make_chunks
from ingestion.docx_loader import load_docx_sections
from ingestion.web_loader import load_page

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("ingest")

SITE_LABELS = {
    "powellndlovuportfolio": "Main portfolio website",
    "online-cv-resume": "Online CV",
    "GeoAI-Portfolio": "GeoAI portfolio website",
}


def _label_for(url: str, fallback: str) -> str:
    for key, label in SITE_LABELS.items():
        if key.lower() in url.lower():
            return label
    return fallback


def collect_document_chunks(path: Path) -> tuple[list[dict], dict]:
    if not path.exists():
        raise FileNotFoundError(f"Word document not found: {path}. Set DOCX_PATH in .env.")
    doc_title, sections = load_docx_sections(path)
    chunks, removed_total = [], 0
    for section in sections:
        text, removed = sanitise_source_text(section.text)
        removed_total += removed
        if text:
            chunks += make_chunks(
                title="Consultancy profile (Word document)",
                section=section.heading,
                text=text,
                url=f"document:{path.name}",
                source_type="document",
            )
    stats = {
        "source": str(path.name),
        "type": "document",
        "title": doc_title,
        "sections": len(sections),
        "chunks": len(chunks),
        "suspicious_lines_removed": removed_total,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return chunks, stats


def collect_web_chunks(url: str, snapshot_dir: Path, offline_dir: Path | None) -> tuple[list[dict], dict]:
    page = load_page(url, snapshot_dir=snapshot_dir, offline_dir=offline_dir)
    label = _label_for(url, page.title)
    chunks, removed_total = [], 0
    for section in page.sections:
        text, removed = sanitise_source_text(section.text)
        removed_total += removed
        if len(text) >= 40:  # skip nav crumbs and empty headings
            chunks += make_chunks(title=label, section=section.heading, text=text, url=url, source_type="website")

    # Index the page's outbound links (e.g. live dashboards) so the bot can point users to them.
    useful_links = [
        f"- {text}: {href}"
        for text, href in dict.fromkeys(page.links)
        if not href.startswith(("mailto:", "tel:")) and not any(s in href for s in ("facebook.", "twitter.", "instagram.", "behance."))
    ]
    if useful_links:
        links_text, removed = sanitise_source_text("\n".join(useful_links[:80]))
        removed_total += removed
        chunks += make_chunks(title=label, section="Links on this page", text=links_text, url=url, source_type="website")

    stats = {
        "source": url,
        "type": "website",
        "title": page.title,
        "sections": len(page.sections),
        "chunks": len(chunks),
        "suspicious_lines_removed": removed_total,
        "sha256": hashlib.sha256("\n".join(c["text"] for c in chunks).encode()).hexdigest(),
    }
    return chunks, stats


def write_preview(chunks: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(f"===== [{c['source_type']}] {c['url']} | {c['section']} ({len(c['text'])} chars)\n")
            fh.write(c["text"] + "\n\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the chatbot knowledge base.")
    parser.add_argument("--skip-web", action="store_true", help="Index the Word document only.")
    parser.add_argument("--offline-html", type=Path, help="Folder of saved HTML pages to use instead of fetching.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk only; no embeddings, no index written.")
    parser.add_argument("--allow-partial", action="store_true", help="Build even if some websites fail.")
    args = parser.parse_args(argv)

    index_dir: Path = settings.index_dir
    tmp_dir = index_dir.with_name(index_dir.name + ".tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)

    all_chunks: list[dict] = []
    manifest_sources: list[dict] = []
    failures: list[str] = []

    doc_chunks, doc_stats = collect_document_chunks(settings.docx_path)
    all_chunks += doc_chunks
    manifest_sources.append(doc_stats)
    log.info("Document: %d sections -> %d chunks", doc_stats["sections"], doc_stats["chunks"])

    if not args.skip_web:
        for url in settings.source_urls:
            try:
                web_chunks, web_stats = collect_web_chunks(url, tmp_dir / "snapshots", args.offline_html)
                all_chunks += web_chunks
                manifest_sources.append(web_stats)
                log.info("Website %s: %d chunks", url, web_stats["chunks"])
                if web_stats["chunks"] == 0:
                    log.warning("No usable text extracted from %s (JavaScript-rendered page?)", url)
            except Exception as exc:  # noqa: BLE001
                failures.append(url)
                log.error("FAILED %s: %s", url, exc)

    if failures and not args.allow_partial:
        log.error("Aborting: %d website(s) failed. Fix the problem or re-run with --allow-partial / --skip-web.", len(failures))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1

    # De-duplicate identical chunks (e.g. repeated footers).
    unique: dict[str, dict] = {}
    for chunk in all_chunks:
        unique.setdefault(chunk["id"], chunk)
    all_chunks = list(unique.values())

    for s in manifest_sources:
        if s["suspicious_lines_removed"]:
            log.warning("Removed %d instruction-like line(s) from %s - review preview.txt", s["suspicious_lines_removed"], s["source"])

    write_preview(all_chunks, tmp_dir / "preview.txt")
    log.info("Total chunks: %d (preview written to %s)", len(all_chunks), tmp_dir / "preview.txt")

    if args.dry_run:
        log.info("Dry run complete - nothing written to %s", index_dir)
        print((tmp_dir / "preview.txt").read_text(encoding="utf-8")[:3000])
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 0

    from app.openai_client import embed_texts  # imported late so --dry-run needs no API key

    log.info("Embedding %d chunks with %s ...", len(all_chunks), settings.embedding_model)
    matrix = embed_texts([c["text"] for c in all_chunks])
    np.save(tmp_dir / EMBEDDINGS_FILE, matrix.astype(np.float32))
    with (tmp_dir / CHUNKS_FILE).open("w", encoding="utf-8") as fh:
        for chunk in all_chunks:
            fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "embedding_model": settings.embedding_model,
        "embedding_dim": int(matrix.shape[1]),
        "chunk_count": len(all_chunks),
        "sources": manifest_sources,
        "failed_sources": failures,
    }
    # Manifest written last: the server reloads when it changes.
    (tmp_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Atomic-ish swap: old index -> .bak, tmp -> index.
    backup = index_dir.with_name(index_dir.name + ".bak")
    shutil.rmtree(backup, ignore_errors=True)
    if index_dir.exists():
        index_dir.rename(backup)
    tmp_dir.rename(index_dir)
    log.info("Index ready in %s (%d chunks). Previous index kept in %s", index_dir, len(all_chunks), backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
