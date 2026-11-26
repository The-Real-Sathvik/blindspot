"""Create the reviewed-correction candidate dataset without changing v1."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.db.session import create_session_factory, create_sqlite_engine
from app.models.research import EvidenceChunk


V1 = BACKEND_ROOT / "tests" / "fixtures" / "claim_extraction_reference_annotations.json"
V2 = BACKEND_ROOT / "tests" / "fixtures" / "claim_extraction_reference_annotations_phase_4b_3a_v2.json"
CHANGELOG = BACKEND_ROOT / "tests" / "fixtures" / "claim_extraction_reference_annotations_phase_4b_3a_v2_changes.json"


def quote_from(text: str, start: str, end: str | None = None) -> str:
    first = text.index(start)
    last = len(text) if end is None else text.index(end, first)
    return text[first:last].rstrip()


def main() -> None:
    if V2.exists() or CHANGELOG.exists():
        raise FileExistsError("v2 dataset already exists; do not overwrite a versioned reference dataset")
    values = json.loads(V1.read_text(encoding="utf-8"))
    engine = create_sqlite_engine("sqlite:///blindspot.db")
    session = create_session_factory(engine)()
    changes: list[dict] = []
    try:
        chunks = {value["reference_id"]: session.get(EvidenceChunk, value["evidence_chunk_id"]) for value in values}
        for value in values:
            before = dict(value); identifier = value["reference_id"]; chunk = chunks[identifier]
            if chunk is None:
                raise RuntimeError(f"missing source chunk for {identifier}")
            text = chunk.text
            correction = CORRECTIONS.get(identifier)
            if correction:
                quotation = correction["quotation"](text)
                start = text.index(quotation); end = start + len(quotation)
                value.update({
                    "expected_claim_meaning": correction["meaning"],
                    "claim_category": correction["category"],
                    "supporting_quotation": quotation,
                    "chunk_quote_start_offset": start,
                    "chunk_quote_end_offset": end,
                    "page_quote_start_offset": chunk.start_offset + start,
                    "page_quote_end_offset": chunk.start_offset + end,
                    "uncertainty_or_missing_information": correction["uncertainty"],
                    "review_note": "v2 correction candidate: " + correction["reason"],
                })
            elif not value.get("expected_abstention"):
                start, end = value["chunk_quote_start_offset"], value["chunk_quote_end_offset"]
                value["page_quote_start_offset"] = chunk.start_offset + start
                value["page_quote_end_offset"] = chunk.start_offset + end
                if identifier == "covid-dataset-size":
                    value["uncertainty_or_missing_information"] = "10,700 is one dataset-size quantity; the surrounding text identifies the data source and split."
                    value["review_note"] = "v2 unchanged scientific label; page-relative offsets added and numerical quantity clarified."
            if value != before:
                changes.append({"annotation_id": identifier, "changed_fields": sorted(key for key in value if value.get(key) != before.get(key)), "reason": value.get("review_note")})
        V2.write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        CHANGELOG.write_text(json.dumps({"dataset_version": "phase-4b-3a-v2", "base_dataset": V1.name, "changes": changes}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    finally:
        session.close(); engine.dispose()


CORRECTIONS = {
    "numeric-social-media-engagement": {
        "meaning": "The chapter cites an external statistic that tweets with images received higher engagement; it is not an original experiment reported by this chapter.", "category": "result",
        "quotation": lambda t: t[t.index("For instance,"):t.index("images7.") + len("images7.")],
        "uncertainty": "The statistic is attributed through citation [7]; this annotation does not establish the cited study's design or population.", "reason": "Clarifies cited-statistic attribution and preserves all three percentages."},
    "objective-comprehensive-review": {
        "meaning": "The authors state that they present a comprehensive review of visual content in fake news.", "category": "objective",
        "quotation": lambda t: quote_from(t, "To\nfurther facilitate", "\n5 https"), "uncertainty": "The review scope is limited to what the authors state in this chapter.", "reason": "Uses the complete objective statement."},
    "limitation-specific-manipulations": {
        "meaning": "The authors state that some features apply only to specific manipulation types, which are unknown in practice.", "category": "limitation",
        "quotation": lambda t: t[t.index("Note that"):t.index("practice.") + len("practice.")], "uncertainty": "This is an author-stated limitation of the discussed features, not an independently verified general conclusion.", "reason": "Completes the stated limitation."},
    "hettransformer-novel-model": {
        "meaning": "The authors introduce a novel Transformer-based HetTransformer model for fake-news detection on social networks.", "category": "contribution",
        "quotation": lambda t: quote_from(t, "In this paper,", ", which utilizes"), "uncertainty": "The quotation establishes the proposed model but not its empirical effectiveness.", "reason": "Names the introduced model and task."},
    "hettransformer-three-datasets": {
        "meaning": "The authors evaluate the model on three real-world datasets.", "category": "methodology",
        "quotation": lambda t: quote_from(t, "Experiments on three real-world datasets", "\ndemonstrate"), "uncertainty": "This label records experimental scope; performance is represented separately.", "reason": "Changes experiment-scope label from result to methodology."},
    "hettransformer-prior-method-limitation": {
        "meaning": "The authors state that prior methods ignore global structural representations in the social network.", "category": "limitation",
        "quotation": lambda t: quote_from(t, "A major", "\nThe news"), "uncertainty": "This is the authors' characterization of prior methods.", "reason": "Completes the prior-method limitation."},
    "hettransformer-model-method": {
        "meaning": "The authors propose the HetTransformer model.", "category": "methodology",
        "quotation": lambda t: "we\npropose HetTransformer model.", "uncertainty": "The quotation omits implementation details.", "reason": "Uses a complete proposal statement."},
    "hettransformer-experiment-method": {
        "meaning": "The authors conduct experiments on three real-world datasets.", "category": "methodology",
        "quotation": lambda t: t[t.index("We conduct"):t.index("datasets.", t.index("We conduct")) + len("datasets.")], "uncertainty": "The quotation does not identify the experimental protocol details.", "reason": "Completes stated experimental scope."},
    "hettransformer-superior-performance": {
        "meaning": "The authors report that HetTransformer outperforms state-of-the-art baselines on the fake-news detection task.", "category": "result",
        "quotation": lambda t: quote_from(t, "proposed HetTransformer", "\nThe rest"), "uncertainty": "No numerical metric is stated in this quotation.", "reason": "Retains model and comparison context."},
    "hettransformer-generalizability-limitation": {
        "meaning": "The authors state that dataset-specific hand-crafted rules induce low generalizability.", "category": "limitation",
        "quotation": lambda t: quote_from(t, "These traditional methods", "\nIn order"), "uncertainty": "This is the authors' characterization of traditional methods.", "reason": "Completes cause and limitation."},
    "covid-methodology-objective": {
        "meaning": "The authors report a methodology to analyze reliability of COVID-19 social-media information.", "category": "methodology",
        "quotation": lambda t: quote_from(t, "For our analysis", " Our best"), "uncertainty": "The quotation states the methodology aim; detailed implementation occurs elsewhere.", "reason": "Uses the complete methodology statement."},
    "covid-architecture-objective": {
        "meaning": "The paper aims to develop an efficient COVID-19 fake-news detection architecture.", "category": "objective",
        "quotation": lambda t: quote_from(t, "this paper looks", " Initially,"), "uncertainty": "This is an objective statement, not a completed performance result.", "reason": "Completes the stated architecture objective."},
    "covid-ensemble-method": {
        "meaning": "The ensemble averages softmax values from BERT, ALBERT, and XLNet.", "category": "methodology",
        "quotation": lambda t: t[t.index("Our ensemble model"):t.index("each model.", t.index("Our ensemble model")) + len("each model.")], "uncertainty": "The quotation does not provide all training details.", "reason": "Completes the ensemble calculation description."},
    "covid-ensemble-accuracy": {
        "meaning": "Table 3 reports ensemble-model accuracy of 0.985512; its F1 score is 0.985514.", "category": "result",
        "quotation": lambda t: quote_from(t, "Model Type", "\nTable 3:"), "uncertainty": "Metric association follows the table's printed Precision, Recall, Accuracy, and F1-Score column order.", "reason": "Corrects the unspecified value to accuracy and preserves the distinct F1 value."},
    "covid-best-f1": {
        "meaning": "The authors report the ensemble has the best test-set F1 score, 0.9855.", "category": "result",
        "quotation": lambda t: quote_from(t, "An ensemble", " Our transformer"), "uncertainty": "The comparison applies to the reported test set.", "reason": "Uses the complete F1 result sentence."},
    "covid-future-contextual-embeddings": {
        "meaning": "The authors intend to explore FLAIR and ELMo contextual embeddings in future work.", "category": "future_direction",
        "quotation": lambda t: quote_from(t, "We\nintend", "\nReferences"), "uncertainty": "This is a stated future direction, not a completed method.", "reason": "Completes the explicit future-work statement."},
    "mining-synthetic-text-problem": {
        "meaning": "The authors identify differentiating machine-generated synthetic text by generating model as an important problem.", "category": "research_problem",
        "quotation": lambda t: quote_from(t, "One important", " One can"), "uncertainty": "This is a stated research problem in a review context.", "reason": "Replaces an insufficient fragment with the complete problem statement."},
    "mining-trifn-accuracy": {
        "meaning": "The chapter reports that TriFN can achieve 0.87 accuracy for detecting disinformation.", "category": "result",
        "quotation": lambda t: "TriFN can achieve 0.87\naccuracy for detecting disinformation.", "uncertainty": "This is a review's report of a cited method result, not a new experiment by this chapter.", "reason": "Preserves metric, task, and cited-work context."},
    "mining-defend-performance": {
        "meaning": "The chapter reports that dEFEND achieves approximately 0.9 accuracy and approximately 0.92 F1.", "category": "result",
        "quotation": lambda t: quote_from(t, "Experiments\nshow", "\nEarly warning"), "uncertainty": "This is a review's report of dEFEND performance; approximate symbols are retained in the quotation.", "reason": "Adds the reported accuracy and F1 values."},
    "mining-labeled-data-limitation": {
        "meaning": "The authors identify limited labeled data as a bottleneck for supervised-learning systems.", "category": "limitation",
        "quotation": lambda t: quote_from(t, "Limited labeled", " In the Chapter"), "uncertainty": "The limitation is stated in an overview of a chapter topic.", "reason": "Completes the limitation statement."},
    "mining-future-directions": {
        "meaning": "The authors introduce representative future directions for the field.", "category": "future_direction",
        "quotation": lambda t: "representative future directions as follows.", "uncertainty": "Specific directions appear after this introductory statement.", "reason": "Preserves explicit future-direction framing."},
    "mining-cross-topic-limitation": {
        "meaning": "The authors state that cross-topic performance is limited by overfitting small labeled samples or lack of supervision.", "category": "limitation",
        "quotation": lambda t: t[t.index("However,"):t.index("information.", t.index("However,")) + len("information.")], "uncertainty": "This limitation is stated for the described cross-topic setting.", "reason": "Completes cause and scope of the limitation."},
}


if __name__ == "__main__":
    main()
