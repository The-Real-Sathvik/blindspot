from pathlib import Path

import pymupdf as fitz

from app.models.research import Paper, PaperExternalIdentifier
from app.schemas.research import DocumentStatus
from app.services.fulltext import DownloadError, FullTextService, PdfDownloader, UnsafeUrlError, validate_remote_url


class Response:
    def __init__(self, data: bytes, url: str = "https://example.org/paper.pdf", content_type: str = "application/pdf") -> None:
        self.data, self.position, self.url = data, 0, url
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
    def read(self, amount=-1):
        if amount < 0: amount = len(self.data) - self.position
        value = self.data[self.position:self.position + amount]; self.position += len(value); return value
    def geturl(self): return self.url
    def __enter__(self): return self
    def __exit__(self, *_): return False


def pdf_bytes() -> bytes:
    document = fitz.open()
    for text in ("Page one fake news detection evidence.", "Page two social media misinformation evidence."):
        page = document.new_page(); page.insert_text((72, 72), text)
    data = document.tobytes(); document.close(); return data


def test_downloader_validates_pdf_size_and_destination(tmp_path: Path) -> None:
    data = pdf_bytes()
    downloader = PdfDownloader(opener=lambda *_args, **_kwargs: Response(data))
    result = downloader.download("https://example.org/paper.pdf", tmp_path / "safe.pdf")
    assert result.size_bytes == len(data) and result.local_path.exists()
    with __import__('pytest').raises(UnsafeUrlError): validate_remote_url("http://127.0.0.1/private.pdf")
    with __import__('pytest').raises(DownloadError): PdfDownloader(max_bytes=10, opener=lambda *_args, **_kwargs: Response(data)).download("https://example.org/paper.pdf", tmp_path / "large.pdf")
    with __import__('pytest').raises(DownloadError): PdfDownloader(opener=lambda *_args, **_kwargs: Response(b"not pdf", content_type="text/html")).download("https://example.org/paper.pdf", tmp_path / "bad.pdf")


def test_acquisition_extraction_chunks_and_repeat_idempotency(session, tmp_path: Path, monkeypatch) -> None:
    import app.services.fulltext as fulltext
    monkeypatch.setattr(fulltext, "SOURCE_DIR", tmp_path)
    paper = Paper(title="Paper", authors=[], open_access_metadata={"oa_url": "https://example.org/paper.pdf"})
    session.add(paper); session.commit()
    service = FullTextService(PdfDownloader(opener=lambda *_args, **_kwargs: Response(pdf_bytes())), chunk_size=20, overlap=5)
    document, result = service.acquire_and_extract(session, paper); session.commit()
    assert result and result.page_count == 2 and result.chunk_count > 2
    assert document.status is DocumentStatus.AVAILABLE
    assert {chunk.page_number for chunk in document.evidence_chunks} == {1, 2}
    assert all(chunk.text == chunk.text.strip() and chunk.end_offset >= chunk.start_offset for chunk in document.evidence_chunks)
    document_again, again = service.acquire_and_extract(session, paper)
    assert document_again.id == document.id and again and again.chunk_count == result.chunk_count


def test_missing_url_and_empty_pdf_failure_are_persisted(session, tmp_path: Path, monkeypatch) -> None:
    import app.services.fulltext as fulltext
    monkeypatch.setattr(fulltext, "SOURCE_DIR", tmp_path)
    no_url = Paper(title="No URL", authors=[], open_access_metadata={})
    session.add(no_url); session.commit()
    document, result = FullTextService().acquire_and_extract(session, no_url)
    assert result is None and document.status is DocumentStatus.UNAVAILABLE
    empty = fitz.open(); empty.new_page(); data = empty.tobytes(); empty.close()
    paper = Paper(title="Empty", authors=[], open_access_metadata={"oa_url": "https://example.org/empty.pdf"})
    session.add(paper); session.commit()
    document, result = FullTextService(PdfDownloader(opener=lambda *_args, **_kwargs: Response(data))).acquire_and_extract(session, paper)
    assert result is None and document.status is DocumentStatus.EXTRACTION_FAILED
