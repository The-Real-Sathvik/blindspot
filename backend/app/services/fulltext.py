"""Safe sequential full-text acquisition, PDF extraction, and evidence chunking."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import ipaddress
from pathlib import Path
import re
from typing import BinaryIO, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pymupdf as fitz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import EvidenceChunk, Paper, PaperExternalIdentifier, SourceDocument
from app.schemas.research import DocumentStatus


MAX_PDF_BYTES = 20 * 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = PROJECT_ROOT / "data" / "source_documents"


class FullTextError(RuntimeError):
    pass


class UnsafeUrlError(FullTextError):
    pass


class DownloadError(FullTextError):
    pass


class ExtractionError(FullTextError):
    pass


@dataclass(frozen=True)
class ResolvedPdf:
    url: str
    provenance: str


@dataclass(frozen=True)
class DownloadedPdf:
    local_path: Path
    content_hash: str
    size_bytes: int
    final_url: str


@dataclass(frozen=True)
class ExtractionResult:
    page_count: int
    character_count: int
    chunk_count: int


def resolve_pdf_url(paper: Paper) -> Optional[ResolvedPdf]:
    metadata = paper.open_access_metadata if isinstance(paper.open_access_metadata, dict) else {}
    oa_url = metadata.get("oa_url")
    if isinstance(oa_url, str) and oa_url:
        return ResolvedPdf(oa_url, "OpenAlex open-access URL metadata")
    for identifier in paper.external_identifiers:
        metadata = identifier.source_metadata if isinstance(identifier.source_metadata, dict) else {}
        pdf_url = metadata.get("pdf_url")
        if identifier.source_name == "arxiv" and isinstance(pdf_url, str) and pdf_url:
            return ResolvedPdf(pdf_url, "arXiv PDF-link metadata; not downloaded or verified")
    return None


def validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeUrlError("Only public HTTP(S) URLs without credentials are allowed.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
        raise UnsafeUrlError("Private, local, or reserved network destinations are not allowed.")


class PdfDownloader:
    def __init__(self, *, timeout_seconds: float = 30.0, max_bytes: int = MAX_PDF_BYTES, opener=urlopen) -> None:
        self._timeout, self._max_bytes, self._opener = timeout_seconds, max_bytes, opener

    def download(self, url: str, destination: Path) -> DownloadedPdf:
        validate_remote_url(url)
        request = Request(url, headers={"User-Agent": "Blindspot/0.1 (legal full-text metadata acquisition)"})
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                final_url = response.geturl()
                validate_remote_url(final_url)
                content_type = response.headers.get("Content-Type", "").casefold()
                declared_size = response.headers.get("Content-Length")
                if declared_size and int(declared_size) > self._max_bytes:
                    raise DownloadError("PDF exceeds configured size limit.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = sha256(); total = 0
                with destination.open("wb") as output:
                    while block := response.read(64 * 1024):
                        total += len(block)
                        if total > self._max_bytes:
                            output.close(); destination.unlink(missing_ok=True)
                            raise DownloadError("PDF exceeds configured size limit.")
                        digest.update(block); output.write(block)
        except (HTTPError, URLError, TimeoutError) as error:
            raise DownloadError("Full-text request failed.") from error
        if total < 5 or not destination.read_bytes()[:5] == b"%PDF-":
            destination.unlink(missing_ok=True)
            raise DownloadError("Response is not a valid PDF signature.")
        if "pdf" not in content_type and content_type:
            destination.unlink(missing_ok=True)
            raise DownloadError("Response content type is not PDF.")
        return DownloadedPdf(destination, digest.hexdigest(), total, final_url)


class FullTextService:
    def __init__(self, downloader: Optional[PdfDownloader] = None, *, chunk_size: int = 1200, overlap: int = 150) -> None:
        if chunk_size <= 0 or not 0 <= overlap < chunk_size:
            raise ValueError("Invalid chunk size or overlap.")
        self._downloader, self._chunk_size, self._overlap = downloader or PdfDownloader(), chunk_size, overlap

    def acquire_and_extract(self, session: Session, paper: Paper) -> tuple[SourceDocument, Optional[ExtractionResult]]:
        resolved = resolve_pdf_url(paper)
        if resolved is None:
            document = self._document(session, paper, None)
            document.status, document.error_info = DocumentStatus.UNAVAILABLE, "No legally accessible PDF URL in stored discovery metadata."
            session.flush(); return document, None
        document = self._document(session, paper, resolved.url)
        if document.status == DocumentStatus.AVAILABLE and document.evidence_chunks:
            return document, ExtractionResult(0, sum(len(chunk.text) for chunk in document.evidence_chunks), len(document.evidence_chunks))
        try:
            target = SOURCE_DIR / f"{document.id}.pdf"
            downloaded = self._downloader.download(resolved.url, target)
            document.local_path, document.content_hash, document.source_url = str(downloaded.local_path), downloaded.content_hash, downloaded.final_url
            document.document_type, document.status, document.error_info = "pdf", DocumentStatus.PENDING, None
            result = self._extract(document)
            document.status = DocumentStatus.AVAILABLE
            session.flush(); return document, result
        except (DownloadError, ExtractionError) as error:
            document.status = DocumentStatus.EXTRACTION_FAILED if isinstance(error, ExtractionError) else DocumentStatus.RETRIABLE_ERROR
            document.error_info = str(error)
            session.flush(); return document, None

    def _document(self, session: Session, paper: Paper, url: Optional[str]) -> SourceDocument:
        document = session.scalar(select(SourceDocument).where(SourceDocument.paper_id == paper.id, SourceDocument.source_url == url))
        if document is None:
            document = SourceDocument(paper=paper, source_url=url, document_type="pdf", status=DocumentStatus.PENDING)
            session.add(document); session.flush()
        return document

    def _extract(self, document: SourceDocument) -> ExtractionResult:
        if not document.local_path:
            raise ExtractionError("No local PDF path available.")
        try:
            pdf = fitz.open(document.local_path)
        except (fitz.FileDataError, OSError) as error:
            raise ExtractionError("PDF could not be opened.") from error
        try:
            chars = 0; index = 0
            document.evidence_chunks.clear()
            for page_index, page in enumerate(pdf, start=1):
                text = _normalise_text(page.get_text("text"))
                if not text:
                    continue
                chars += len(text)
                for start, end, part in _chunks(text, self._chunk_size, self._overlap):
                    document.evidence_chunks.append(EvidenceChunk(page_number=page_index, chunk_index=index, start_offset=start, end_offset=end, text=part))
                    index += 1
            if index == 0:
                raise ExtractionError("PDF contains no extractable text; it may be scanned or empty.")
            return ExtractionResult(pdf.page_count, chars, index)
        finally:
            pdf.close()


def _normalise_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _chunks(text: str, size: int, overlap: int):
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        value = text[start:end]
        leading = len(value) - len(value.lstrip())
        trailing = len(value) - len(value.rstrip())
        if value.strip():
            yield start + leading, end - trailing, value.strip()
        if end == len(text): break
        start = max(end - overlap, start + 1)
