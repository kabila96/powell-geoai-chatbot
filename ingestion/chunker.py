"""Section-aware chunking: keep each heading's content together, split long sections on line
boundaries with a small overlap, and prefix every chunk with its page/section title so the
embedding (and the model) always know where a fact came from."""
from __future__ import annotations

import hashlib


def split_text(text: str, max_chars: int = 1200, overlap_lines: int = 2) -> list[str]:
    lines = [line for line in text.split("\n") if line.strip()]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        # Hard-wrap pathological single lines.
        while len(line) > max_chars:
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if current and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current = current[-overlap_lines:] if overlap_lines else []
            size = sum(len(c) + 1 for c in current)
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def make_chunks(*, title: str, section: str, text: str, url: str, source_type: str, max_chars: int = 1200) -> list[dict]:
    out = []
    for part_no, body in enumerate(split_text(text, max_chars=max_chars)):
        header = f"{title} > {section}"
        content = f"{header}\n{body}"
        digest = hashlib.sha1(f"{url}|{section}|{part_no}|{body}".encode()).hexdigest()[:12]
        out.append(
            {
                "id": digest,
                "title": title,
                "section": section,
                "url": url,
                "source_type": source_type,
                "text": content,
            }
        )
    return out
