import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.research import ClaimEvidence, EvidenceChunk, ExtractedClaim, Paper, ResearchJob, ResearchJobPaper, SourceDocument, VerificationResult
from app.schemas.research import CitationCheckStatus, ClaimType, DocumentStatus, JobPaperStatus, SemanticSupportStatus


def test_persists_complete_evidence_lineage(session) -> None:
    job = ResearchJob(topic="Reliable automated research-gap discovery")
    paper = Paper(title="Traceable Evidence Methods", doi="10.1000/example", authors=[{"name": "A. Researcher"}])
    link = ResearchJobPaper(research_job=job, paper=paper, status=JobPaperStatus.SCREENED, included=True, relevance_score=0.9)
    document = SourceDocument(paper=paper, source_url="https://example.org/paper.pdf", status=DocumentStatus.AVAILABLE)
    chunk = EvidenceChunk(source_document=document, page_number=4, section_label="Results", chunk_index=0, start_offset=0, end_offset=54, text="The method improved traceability in the reviewed workflow.")
    claim = ExtractedClaim(paper=paper, claim_type=ClaimType.RESULT, claim_text="The method improved workflow traceability.")
    evidence = ClaimEvidence(claim=claim, evidence_chunk=chunk, quotation_text="The method improved traceability in the reviewed workflow.", quote_start_offset=0, quote_end_offset=54, quotation_status=CitationCheckStatus.VALID, source_location_status=CitationCheckStatus.VALID)
    result = VerificationResult(claim=claim, semantic_status=SemanticSupportStatus.SUPPORTED, rationale="The quoted result directly supports the narrower claim.", verifier_version="phase-1-test")
    session.add_all([link, evidence, result])
    session.commit()

    persisted = session.get(ResearchJob, job.id)
    assert persisted is not None
    assert persisted.papers[0].paper.doi == "10.1000/example"
    assert persisted.papers[0].paper.source_documents[0].evidence_chunks[0].page_number == 4
    assert persisted.papers[0].paper.extracted_claims[0].evidence_links[0].quotation_status is CitationCheckStatus.VALID
    assert persisted.papers[0].paper.extracted_claims[0].verification_result.semantic_status is SemanticSupportStatus.SUPPORTED


def test_foreign_keys_and_unique_job_paper_constraint_are_enforced(session) -> None:
    with pytest.raises(IntegrityError):
        session.execute(text("INSERT INTO source_documents (id, paper_id, document_type, status, created_at, updated_at) VALUES ('bad-document', 'missing-paper', 'full_text_pdf', 'PENDING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        session.commit()
    session.rollback()

    job = ResearchJob(topic="A valid topic")
    paper = Paper(title="A valid paper", authors=[])
    session.add_all([job, paper])
    session.commit()
    session.add_all([ResearchJobPaper(research_job_id=job.id, paper_id=paper.id), ResearchJobPaper(research_job_id=job.id, paper_id=paper.id)])
    with pytest.raises(IntegrityError):
        session.commit()
