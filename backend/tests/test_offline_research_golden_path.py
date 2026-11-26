"""SYNTHETIC TEST ONLY: real local PDF provenance, isolated DB, no cloud calls."""
import json
from pathlib import Path
import sqlite3

import pytest
import pymupdf as fitz
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.base import Base
from app.db.session import create_session_factory, create_sqlite_engine
from app.main import create_app
from app.models.research import ClaimEvidence, EvidenceChunk, ExtractedClaim, Paper, ResearchJob, ResearchJobPaper, SourceDocument
from app.schemas.research import AgentRole, DocumentStatus
from app.services.fulltext import _normalise_text
from app.services.gap_detection import prepare_packet, run_gap_detection
from app.services.model_config import BlindspotModelConfig
from app.services.ollama_client import InferenceMetrics
from app.services.web_claims import heldout_document_ids


class SyntheticClaimRunner:
    def __init__(self): self.calls=0; self.selected_ids=[]
    def generate(self, role, prompt, schema):
        assert role == AgentRole.CLAIM_EXTRACTION
        self.calls += 1
        spans=json.loads(prompt.rsplit('\n',1)[1])
        span=next(s for s in spans if not s['boundary_uncertain'] and 20 <= len(s['text']) <= 180)
        self.selected_ids.append(span['evidence_id'])
        return schema(claims=[{'claim_statement':'SYNTHETIC TEST ONLY: ' + span['text'],
            'claim_category':'methodology','evidence_ids':[span['evidence_id']],
            'uncertainty_or_missing_information':'Synthetic integration fixture, not a scientific finding.'}]), InferenceMetrics(0.0,10,None)


class SyntheticGapRunner:
    def __init__(self): self.calls=0
    def generate(self, role, prompt, schema):
        assert role == AgentRole.GAP_DETECTION
        self.calls+=1; packet=json.loads(prompt.rsplit('\n',1)[1])
        refs=[{'paper_id':x['paper_id'],'claim_id':x['claim_id'],'evidence_id':x['evidence_id'],'role':'supporting'} for x in packet if x['verification']=='supported']
        return schema(gaps=[{'statement':'SYNTHETIC TEST ONLY: a comparison may be missing.',
            'category':'underexplored_comparison','references':refs[:2],
            'explanation':'Synthetic fixture only.','counterevidence':'Not assessed.',
            'uncertainty':'Synthetic test; not scientific evidence.','validation_status':'not_yet_validated'}]), InferenceMetrics(0.0,10,None)


def test_real_pdf_offline_golden_path_isolated_from_real_database(tmp_path, monkeypatch):
    backend=Path(__file__).resolve().parents[1]; realdb=backend/'blindspot.db'
    if not realdb.is_file(): pytest.skip('Local development database is unavailable.')
    real=sqlite3.connect(f'file:{realdb.as_posix()}?mode=ro',uri=True); real.row_factory=sqlite3.Row
    ids=['0bb74217-f47b-40a9-b7cf-770c3ddf1f33','3da47b2e-d5dc-4dca-b5b2-f81cf15fcdf2']
    if set(ids) & heldout_document_ids(): pytest.fail('A held-out document cannot enter this test.')
    source=[]; job_id='6c8dc7b3-a995-438c-b48d-9b6258081c3e'
    try:
        original_job=real.execute('SELECT id,topic FROM research_jobs WHERE id=?',(job_id,)).fetchone()
        if original_job is None: pytest.skip('Local development research job is unavailable.')
        for doc_id in ids:
            row=real.execute('SELECT d.id,d.paper_id,d.local_path,d.source_url,d.content_hash,p.title FROM source_documents d JOIN papers p ON p.id=d.paper_id WHERE d.id=?',(doc_id,)).fetchone()
            if row is None or not Path(row['local_path']).is_file(): pytest.skip('Local development PDFs are unavailable.')
            assert real.execute('SELECT 1 FROM research_job_papers WHERE research_job_id=? AND paper_id=?',(job_id,row['paper_id'])).fetchone()
            chunks=real.execute('SELECT id,page_number,chunk_index,start_offset,end_offset,text FROM evidence_chunks WHERE source_document_id=? ORDER BY page_number,chunk_index',(doc_id,)).fetchall()
            from app.services.claim_extraction_v3 import generate_evidence_spans
            selected=None
            for item in chunks:
                probe=EvidenceChunk(id=item['id'],source_document_id=doc_id,page_number=item['page_number'],chunk_index=item['chunk_index'],start_offset=item['start_offset'],end_offset=item['end_offset'],text=item['text'])
                if any(not s.boundary_uncertain and 20<=len(s.text)<=180 for s in generate_evidence_spans(probe)):
                    selected=item; break
            assert selected is not None
            source.append((dict(row),dict(selected)))
    finally: real.close()
    engine=create_sqlite_engine('sqlite:///'+(tmp_path/'isolated.db').as_posix());Base.metadata.create_all(engine); factory=create_session_factory(engine)
    runner=SyntheticClaimRunner(); gap_runner=SyntheticGapRunner(); app=create_app(session_factory=factory,claim_runner_factory=lambda _assignment:runner,gap_runner_factory=lambda _assignment:gap_runner)
    monkeypatch.setattr('app.services.cloud_inference.urlopen',lambda *_a,**_k:pytest.fail('External inference must not run'))
    with factory() as session:
        job=ResearchJob(id=original_job['id'],topic=original_job['topic'])
        for row,chunk in source:
            paper=Paper(id=row['paper_id'],title=row['title'],authors=[],open_access_metadata={})
            doc=SourceDocument(id=row['id'],paper=paper,local_path=row['local_path'],source_url=row['source_url'],content_hash=row['content_hash'],document_type='pdf',status=DocumentStatus.AVAILABLE)
            evidence=EvidenceChunk(id=chunk['id'],source_document=doc,page_number=chunk['page_number'],chunk_index=chunk['chunk_index'],start_offset=chunk['start_offset'],end_offset=chunk['end_offset'],text=chunk['text'])
            session.add_all([ResearchJobPaper(research_job=job,paper=paper),evidence])
        session.commit()
    with TestClient(app,headers={'X-Blindspot-Request':'1'}) as api:
        for row,chunk in source:
            response=api.post(f'/api/jobs/{job_id}/claims/extract',json={'chunk_id':chunk['id'],'provider':'gemini','cloud_consent':True})
            assert response.status_code==200 and response.json()['accepted_count']==1
        claims=api.get(f'/api/jobs/{job_id}/claims').json()
        assert len(claims)==2 and all(c['statement'].startswith('SYNTHETIC TEST ONLY:') and c['semantic_status']=='unverified' for c in claims)
        with factory() as session:
            assert prepare_packet(session,job_id,allowed_chunk_ids=frozenset(c['id'] for _,c in source))==[]
            observed_ids=[]
            for claim in session.scalars(select(ExtractedClaim)):
                for link in claim.evidence_links:
                    chunk=link.evidence_chunk
                    assert chunk.source_document.paper_id==claim.paper_id
                    matching=[s for s in generate_evidence_spans(chunk) if s.chunk_start==link.quote_start_offset and s.chunk_end==link.quote_end_offset]
                    assert len(matching)==1
                    observed_ids.append(matching[0].evidence_id)
                    assert link.quotation_text==chunk.text[link.quote_start_offset:link.quote_end_offset]
                    with fitz.open(chunk.source_document.local_path) as pdf:
                        page=_normalise_text(pdf[chunk.page_number-1].get_text('text'))
                        assert page[chunk.start_offset+link.quote_start_offset:chunk.start_offset+link.quote_end_offset]==link.quotation_text
            assert sorted(observed_ids)==sorted(runner.selected_ids)
        allowed=frozenset(c['id'] for _,c in source)
        with factory() as session:
            assert run_gap_detection(session,job_id=job_id,allowed_chunk_ids=allowed,assignment=BlindspotModelConfig().gap_detection,runner=gap_runner).outcome=='insufficient_evidence'
        for index,claim in enumerate(claims):
            body={'semantic_status':'supported','reviewer':'SYNTHETIC TEST ONLY','rationale':'Synthetic mocked review, not a scientific assessment.','reviewed_evidence':True}
            assert api.post(f"/api/jobs/{job_id}/claims/{claim['id']}/semantic-review",json=body).status_code==200
            if index==0:
                with factory() as session:
                    assert run_gap_detection(session,job_id=job_id,allowed_chunk_ids=allowed,assignment=BlindspotModelConfig().gap_detection,runner=gap_runner).outcome=='insufficient_evidence'
        assert gap_runner.calls==0
        response=api.post(f'/api/jobs/{job_id}/gaps/detect',json={'cloud_consent':True})
        assert response.status_code==200 and response.json()['status']=='candidates' and response.json()['accepted_count']==1
        gaps=api.get(f'/api/jobs/{job_id}/gaps').json()
        assert len(gaps)==1 and gaps[0]['validation_status']=='not_yet_validated' and gaps[0]['statement'].startswith('SYNTHETIC TEST ONLY:')
        assert len({r['paper_id'] for r in gaps[0]['references']})==2
    assert runner.calls==2 and gap_runner.calls==1
    engine.dispose()
