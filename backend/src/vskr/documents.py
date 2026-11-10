"""Import teaching documents as searchable, graph-linked sections."""
import re
import uuid
from pathlib import Path

from .config import data_dir
from .util import read_json, write_json


def _extract(path):
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".rst"}:
        return path.read_text(encoding="utf-8-sig")
    if suffix == ".pdf":
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("Đọc PDF cần cài PyMuPDF (backend/requirements/docs.txt).") from exc
        with fitz.open(path) as pdf:
            return "\n\n".join(page.get_text() for page in pdf)
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("Đọc DOCX cần cài python-docx (backend/requirements/docs.txt).") from exc
        doc = Document(path)
        return "\n\n".join(p.text for p in doc.paragraphs)
    raise ValueError("Định dạng tài liệu chưa hỗ trợ. Dùng TXT, Markdown, PDF hoặc DOCX.")


def ingest_document(path, cfg, document_id=None):
    """Persist an uploaded/local document and return metadata for its graph sections."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy tài liệu: {source}")
    text = _extract(source)
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        raise ValueError("Không trích xuất được văn bản từ tài liệu.")
    doc_id = document_id or f"doc-{uuid.uuid4().hex[:12]}"
    root = data_dir(cfg) / "documents" / doc_id
    root.mkdir(parents=True, exist_ok=True)
    sections, current = [], ""
    for paragraph in paragraphs:
        while len(paragraph) > 1600:
            if current:
                sections.append(current)
                current = ""
            sections.append(paragraph[:1600])
            paragraph = paragraph[1600:]
        if current and len(current) + len(paragraph) + 1 > 1600:
            sections.append(current)
            current = ""
        current = f"{current}\n{paragraph}".strip()
    if current:
        sections.append(current)
    title = source.stem
    chunks = [
        {"chunk_id": f"{doc_id}:{i:04d}", "document_id": doc_id,
         "title": title, "section": i + 1, "start": None, "end": None,
         "text": section, "source_types": ["doc"]}
        for i, section in enumerate(sections)
    ]
    write_json(root / "meta.json", {"document_id": doc_id, "title": title, "filename": source.name})
    write_json(root / "sections.json", {"chunks": chunks})
    return read_json(root / "meta.json")


def list_documents(cfg):
    root = data_dir(cfg) / "documents"
    if not root.exists():
        return []
    return [read_json(p / "meta.json") for p in sorted(root.iterdir()) if (p / "meta.json").is_file()]
