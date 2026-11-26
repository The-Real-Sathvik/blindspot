from app.models.research import EvidenceChunk, Paper, SourceDocument
from app.services.annotation_quality_review import SemanticAssessment, assess_annotations
from app.services.claim_extraction_evaluation import ReferenceAnnotation


def annotation_for(chunk, paper, document):
    quote = "95% accuracy"
    return ReferenceAnnotation(
        reference_id="quality-case", paper_id=paper.id, document_title=paper.title,
        source_document_id=document.id, evidence_chunk_id=chunk.id, pdf_page_number=1,
        chunk_start_offset=0, expected_claim_meaning="The source reports accuracy.", claim_category="result",
        supporting_quotation=quote, chunk_quote_start_offset=0, chunk_quote_end_offset=len(quote),
        page_quote_start_offset=0, page_quote_end_offset=len(quote), review_status="draft", dataset_split="development",
    )


def test_independent_quality_review_checks_source_and_flags_invalid_quote(session) -> None:
    paper = Paper(title="Quality source", authors=[], open_access_metadata={})
    document = SourceDocument(paper=paper, document_type="pdf")
    chunk = EvidenceChunk(source_document=document, page_number=1, chunk_index=0, start_offset=0, end_offset=12, text="95% accuracy")
    session.add_all([paper, document, chunk]); session.commit()
    valid = annotation_for(chunk, paper, document)
    finding = assess_annotations(session, [valid])[0]
    assert finding.deterministic_valid and finding.assessment is SemanticAssessment.LIKELY_SUPPORTED
    invalid = valid.model_copy(update={"supporting_quotation": "94% accuracy"})
    finding = assess_annotations(session, [invalid])[0]
    assert not finding.deterministic_valid and finding.assessment is SemanticAssessment.POTENTIALLY_MISLEADING
