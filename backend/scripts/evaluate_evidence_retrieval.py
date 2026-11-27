"""Offline Pass A retrieval experiment over saved Blindspot evidence.

Compares the evidence packet Claim Extraction V3 would receive per analyzed
paper under two chunk-selection policies:

  baseline   current orchestrator: first chunk in page order with an admissible span
  retrieval  evidence_retrieval.rank_chunks order (topic BM25 + facets + section prior)

Both arms use the production span path (ensure_processed_source ->
select_evidence_spans with the live V3 Gemini assignment). The database is
opened read-only; no model, network, or write operation is performed.

Usage (PowerShell, from D:\\Blindspot\\backend):
  ..\\.venv\\Scripts\\python.exe scripts\\evaluate_evidence_retrieval.py
  ..\\.venv\\Scripts\\python.exe scripts\\evaluate_evidence_retrieval.py --score <labelled.csv>

Outputs (git-ignored) go to data/reports/retrieval_eval/:
  summary.json          automatic proxy metrics (NOT independent of the scorer; see notes)
  annotation_blind.csv  shuffled spans from both arms, arm hidden, for human labelling
  annotation_key.json   row -> arm mapping; do not open while labelling
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.models.research  # noqa: F401,E402
from app.models.research import EvidenceChunk, Paper, ResearchJob, ResearchJobPaper, SourceDocument  # noqa: E402
from app.services.claim_extraction_v3 import NoAdmissiblePassage, PROMPT_VERSION, select_evidence_spans  # noqa: E402
from app.services.evidence_retrieval import RETRIEVAL_VERSION, infer_sections, rank_chunks, span_facets, tokenize  # noqa: E402
from app.services.model_config import AgentModelAssignment, InferenceProvider  # noqa: E402
from app.services.web_claims import ensure_processed_source, heldout_document_ids  # noqa: E402

OUT = BACKEND.parent / "data" / "reports" / "retrieval_eval"
LABELS = ("background", "method", "dataset", "result", "limitation", "future_work", "noise")
SUBSTANTIVE = frozenset({"method", "dataset", "result", "limitation", "future_work"})
SEED = 20261127

# The live V3 assignment from app/main.py; spans are size-admitted against it.
ASSIGNMENT = AgentModelAssignment(provider=InferenceProvider.GEMINI, model_tag="gemini-3.5-flash-lite",
                                  output_tokens=512, gemini_response_mode="plain_text",
                                  prompt_template_version=PROMPT_VERSION)


def production_spans(chunk, document):
    page = ensure_processed_source(chunk, document)
    return select_evidence_spans(chunk, page, ASSIGNMENT, plain_text_contract=True)


def first_admissible(ordered_chunks, document):
    """Mirror the orchestrator loop: the first chunk V3 would not reject."""
    for chunk in ordered_chunks:
        try:
            return chunk, production_spans(chunk, document)
        except NoAdmissiblePassage:
            continue
    return None, []


def units(session: Session):
    heldout = heldout_document_ids()
    links = session.scalars(select(ResearchJobPaper).where(ResearchJobPaper.analysis_selected.is_(True))
                            .order_by(ResearchJobPaper.research_job_id, ResearchJobPaper.paper_id)).all()
    for link in links:
        document = session.scalar(select(SourceDocument).where(SourceDocument.paper_id == link.paper_id)
                                  .order_by(SourceDocument.created_at, SourceDocument.id).limit(1))
        if document is None or document.id in heldout:
            continue
        chunks = session.scalars(select(EvidenceChunk).where(EvidenceChunk.source_document_id == document.id)
                                 .order_by(EvidenceChunk.page_number, EvidenceChunk.chunk_index, EvidenceChunk.id)).all()
        if chunks:
            yield session.get(ResearchJob, link.research_job_id), session.get(Paper, link.paper_id), document, chunks


def arm_metrics(records, topic_tokens_by_unit):
    spans = [(r["unit"], s) for r in records for s in r["spans"]]
    n = len(spans) or 1
    facets = [span_facets(s) for _, s in spans]
    return {
        "units_with_spans": sum(bool(r["spans"]) for r in records),
        "spans": len(spans),
        "page1_selection_rate": round(sum(r["page"] == 1 for r in records if r["spans"]) / max(1, sum(bool(r["spans"]) for r in records)), 3),
        "section_of_selected_chunk": dict(Counter(r["section"] for r in records if r["spans"])),
        "spans_with_substantive_cue": round(sum(bool(f & {"result", "limitation", "future_work", "dataset"}) for f in facets) / n, 3),
        "spans_with_result_cue": round(sum("result" in f for f in facets) / n, 3),
        "spans_with_limitation_or_future_cue": round(sum(bool(f & {"limitation", "future_work"}) for f in facets) / n, 3),
        "spans_with_topic_term": round(sum(bool(set(tokenize(s)) & topic_tokens_by_unit[u]) for u, s in spans) / n, 3),
    }


def run(args) -> int:
    engine = create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true")
    OUT.mkdir(parents=True, exist_ok=True)
    arms = {"baseline": [], "retrieval": []}
    skipped = []
    topic_tokens = {}
    with Session(engine) as session:
        for job, paper, document, chunks in units(session):
            unit = f"{job.id}:{paper.id}"
            topic_tokens[unit] = set(tokenize(job.topic))
            sections = infer_sections(chunks)
            by_id = {c.id: c for c in chunks}
            try:
                base_chunk, base_spans = first_admissible(chunks, document)
                ranked = rank_chunks(chunks, job.topic)
                ret_chunk, ret_spans = first_admissible([by_id[r.chunk_id] for r in ranked], document)
            except Exception as error:  # provenance mismatch / missing PDF: record, never drop silently
                skipped.append({"unit": unit, "paper": paper.title, "reason": getattr(error, "detail", None) or error.__class__.__name__})
                continue
            for arm, chunk, spans in (("baseline", base_chunk, base_spans), ("retrieval", ret_chunk, ret_spans)):
                arms[arm].append({
                    "unit": unit, "topic": job.topic, "paper": paper.title,
                    "chunk_id": chunk.id if chunk else None, "page": chunk.page_number if chunk else None,
                    "section": next((r.section for r in ranked if chunk and r.chunk_id == chunk.id), sections[chunk.id].start if chunk else None),
                    "spans": [s.text for s in spans], "evidence_ids": [s.evidence_id for s in spans],
                })
    same = sum(b["chunk_id"] == r["chunk_id"] for b, r in zip(arms["baseline"], arms["retrieval"]))
    summary = {
        "retrieval_version": RETRIEVAL_VERSION, "units": len(arms["baseline"]), "skipped": skipped,
        "identical_chunk_choice": same,
        "baseline": arm_metrics(arms["baseline"], topic_tokens),
        "retrieval": arm_metrics(arms["retrieval"], topic_tokens),
        "note": ("Cue/section metrics use the same lexicons the retrieval scorer optimises, so they are "
                 "descriptive only. The objective comparison is the blinded human labelling in annotation_blind.csv."),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "selections.json").write_text(json.dumps(arms, indent=2, ensure_ascii=False), encoding="utf-8")

    # Blinded annotation sheet: pool both arms per unit, deduplicate identical spans.
    rows, key = {}, {}
    for arm, records in arms.items():
        for record in records:
            for evidence_id, text in zip(record["evidence_ids"], record["spans"]):
                rid = f"{record['unit']}:{evidence_id}"
                rows.setdefault(rid, {"topic": record["topic"], "paper": record["paper"], "text": text})
                key.setdefault(rid, []).append(arm)
    order = sorted(rows)
    random.Random(SEED).shuffle(order)
    with (OUT / "annotation_blind.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["row", "topic", "paper", "span_text", f"label ({'|'.join(LABELS)})", "topic_relevant (y|n)"])
        for index, rid in enumerate(order, start=1):
            writer.writerow([index, rows[rid]["topic"], rows[rid]["paper"], rows[rid]["text"], "", ""])
    (OUT / "annotation_key.json").write_text(json.dumps({str(i): {"id": rid, "arms": key[rid], "unit": rid.rsplit(":", 1)[0]}
                                                        for i, rid in enumerate(order, start=1)}, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "skipped"} | {"skipped": len(skipped)}, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUT}")
    return 0


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact binomial sign test, ties excluded."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


# Pre-registered pass rule (fixed before labelling):
#   substantive-and-relevant share improves by >= 20 percentage points, AND
#   topic-relevant share drops by no more than 5 percentage points.
# The per-paper sign test is informational only.
PASS_MIN_GAIN = Fraction(20, 100)
PASS_MAX_RELEVANCE_DROP = Fraction(5, 100)
REPORTED_LABELS = ("result", "limitation", "future_work")
ARMS = ("baseline", "retrieval")


def evaluate_labels(rows, key) -> dict:
    """Score labelled rows ``(row_id, label, topic_relevant)`` against the answer key.

    Shares are over labelled sentences. A sentence selected by both methods for
    the same job-paper appears once in the sheet and counts for both methods.

    Paired comparison is once per distinct paper: when a paper was analysed in
    several jobs, all its labelled sentences from those jobs are pooled per
    method before computing that paper's substantive-and-relevant share.
    Papers lacking labelled sentences for either method are left out of the
    pairing and counted. Any unlabelled/invalid row makes the verdict
    ``incomplete`` rather than silently dropping evidence.
    """
    per_paper = defaultdict(lambda: {arm: [] for arm in ARMS})
    jobs_per_paper = defaultdict(set)
    invalid = 0
    for row_id, label, relevant in rows:
        label, relevant = label.strip().lower(), relevant.strip().lower()
        if label not in LABELS or relevant not in {"y", "n"} or row_id not in key:
            invalid += 1
            continue
        entry = key[row_id]
        job_id, paper_id = entry["unit"].split(":", 1)
        jobs_per_paper[paper_id].add(job_id)
        for arm in entry["arms"]:
            per_paper[paper_id][arm].append((label, relevant == "y"))

    def share(items, test):
        return Fraction(sum(1 for item in items if test(item)), len(items)) if items else None

    good = lambda item: item[0] in SUBSTANTIVE and item[1]
    arms, exact = {}, {}
    for arm in ARMS:
        items = [item for paper in per_paper.values() for item in paper[arm]]
        exact[arm] = {"good": share(items, good), "relevant": share(items, lambda item: item[1])}
        arms[arm] = {
            "sentences": len(items),
            "substantive_and_relevant_share": _pct(exact[arm]["good"]),
            "topic_relevant_share": _pct(exact[arm]["relevant"]),
            **{f"{name}_share": _pct(share(items, lambda item, n=name: item[0] == n)) for name in REPORTED_LABELS},
            "label_counts": dict(Counter(item[0] for item in items)),
        }

    wins = losses = ties = unpaired = 0
    for paper in per_paper.values():
        if not paper["baseline"] or not paper["retrieval"]:
            unpaired += 1
            continue
        b, r = share(paper["baseline"], good), share(paper["retrieval"], good)
        wins += r > b; losses += r < b; ties += r == b

    verdict, gain, drop = "incomplete", None, None
    if exact["baseline"]["good"] is not None and exact["retrieval"]["good"] is not None:
        gain = exact["retrieval"]["good"] - exact["baseline"]["good"]
        drop = exact["baseline"]["relevant"] - exact["retrieval"]["relevant"]
        if not invalid:
            verdict = "PASS" if gain >= PASS_MIN_GAIN and drop <= PASS_MAX_RELEVANCE_DROP else "FAIL"
    return {
        "verdict": verdict,
        "rule": "substantive-and-relevant gain >= 20 pp AND topic-relevance drop <= 5 pp (sign test informational)",
        "gain_pp": _pp(gain), "topic_relevance_drop_pp": _pp(drop),
        "invalid_or_unlabelled_rows": invalid,
        "arms": arms,
        "per_distinct_paper": {"papers": len(per_paper), "retrieval_better": wins, "baseline_better": losses,
                               "tied": ties, "unpaired": unpaired,
                               "papers_pooled_across_jobs": sum(len(j) > 1 for j in jobs_per_paper.values()),
                               "sign_test_p_informational": round(sign_test(wins, losses), 4)},
    }


def _pct(value):
    return None if value is None else round(float(value), 3)


def _pp(value):
    return None if value is None else round(float(value) * 100, 1)


def score(args) -> int:
    key = json.loads((OUT / "annotation_key.json").read_text(encoding="utf-8"))
    with Path(args.score).open(encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        next(reader)
        rows = [(row[0], row[4], row[5]) if len(row) >= 6 else (row[0] if row else "", "", "") for row in reader]
    print(json.dumps(evaluate_labels(rows, key), indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(BACKEND / "blindspot.db"))
    parser.add_argument("--score", help="Labelled copy of annotation_blind.csv")
    arguments = parser.parse_args()
    raise SystemExit(score(arguments) if arguments.score else run(arguments))
