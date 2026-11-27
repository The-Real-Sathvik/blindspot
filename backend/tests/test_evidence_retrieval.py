import importlib.util
from pathlib import Path

from app.models.research import EvidenceChunk
from app.services.claim_extraction_v3 import select_evidence_spans
from app.services.evidence_retrieval import (bm25_scores, candidate_spans, infer_sections, rank_chunks,
                                             span_facets, tokenize)
from app.services.model_config import AgentModelAssignment


def chunk(index, text, page=None):
    return EvidenceChunk(id=f"c{index}", source_document_id="doc", page_number=page or index + 1,
                         chunk_index=index, start_offset=0, end_offset=len(text), text=text)


# Leading/trailing fragments mimic chunk edges, which V3 always discards.
FRONT = chunk(0, "Title line\nAbstract\nfrag. Fake news spreads quickly on social media platforms today. "
                 "Many people read news on social networks every day. tail")
METHOD = chunk(1, "frag.\n3 Method\nWe propose a transformer model for fake news detection on social media. "
                  "The model is trained on the PolitiFact dataset with standard settings. tail")
# V3 discards a sentence glued to a numbered heading line ("5 Results\nWe ..."
# starts with a digit), so the scored spans are the following sentences.
RESULTS = chunk(2, "frag.\n5 Results\nWe evaluate on two public benchmarks. Our model achieves 97.5% accuracy on PolitiFact and outperforms all baselines. "
                   "However, performance drops on PHEME because labels are limited and noisy. tail")
REFS = chunk(3, "frag.\nReferences\nSmith and Jones wrote about fake news detection in social media. "
                "Another detection paper studied social media posts for fake news. tail")
APPENDIX = chunk(4, "frag.\nDiscussion\nThe appendix model achieves 99.9% accuracy and outperforms baselines on fake news. "
                    "However, this limited appendix experiment does not generalise to social media. tail")
CHECKLIST = chunk(5, "frag.\nNeurIPS Paper Checklist\nAnswer: [Yes] Justification: We discuss limitations of fake news models. "
                     "Guidelines: The answer NA means that the paper has no limitations section. tail")
DOC = [FRONT, METHOD, RESULTS, REFS, APPENDIX, CHECKLIST]


def test_sections_carry_forward_and_back_matter_is_marked():
    sections = infer_sections(DOC)
    assert sections["c0"].start == "front_matter" and "abstract" in sections["c0"].present
    assert sections["c1"].present == ("abstract", "method")
    assert sections["c2"].present == ("method", "results")
    assert sections["c3"].present == ("results", "references")
    # A "Discussion" heading after the reference list is appendix/back matter.
    assert sections["c4"].present == ("references", "back_matter")
    assert sections["c5"].present[-1] == "checklist"


def test_heading_detection_requires_short_standalone_line():
    long_line = chunk(0, "The results of this long sentence are not a heading because it keeps going for many words.")
    numbered = chunk(1, "IV. Experimental Setup\nText.")
    sections = infer_sections([long_line, numbered])
    assert sections["c0"].present == ("front_matter",)
    assert sections["c1"].present[-1] == "experiments"


def test_results_and_limitations_outrank_front_matter_and_noise_is_excluded():
    ranked = rank_chunks(DOC, "Fake news detection on social media")
    order = [r.chunk_id for r in ranked]
    assert order[0] == "c2"  # results + limitation in the main text
    assert "c3" not in order and "c5" not in order  # references and checklist excluded
    assert order.index("c2") < order.index("c4")  # main text beats appendix
    assert order.index("c1") < order.index("c0")
    top = ranked[0]
    assert top.section == "results" and {"result", "limitation"} <= top.facets
    assert ranked == rank_chunks(list(reversed(DOC)), "Fake news detection on social media")  # deterministic


def test_ranking_never_mutates_provenance():
    before = [(c.id, c.text, c.page_number, c.start_offset, c.end_offset, c.chunk_index) for c in DOC]
    rank_chunks(DOC, "fake news")
    assert before == [(c.id, c.text, c.page_number, c.start_offset, c.end_offset, c.chunk_index) for c in DOC]


def test_chunks_without_admissible_spans_are_omitted():
    assert rank_chunks([chunk(0, "only a fragment without sentence end")], "fragment") == []
    assert rank_chunks([], "anything") == []


def test_candidate_spans_match_production_v3_selection():
    assignment = AgentModelAssignment(provider="gemini", model_tag="gemini-3.5-flash-lite",
                                      output_tokens=512, gemini_response_mode="plain_text")
    for item in DOC:
        expected = select_evidence_spans(item, item.text, assignment, plain_text_contract=True)
        assert [s.evidence_id for s in candidate_spans(item)] == [s.evidence_id for s in expected]


def test_tokenize_bm25_and_facets():
    # "studies" and "based" are deliberate topic stopwords.
    assert tokenize("Studies of Networks using the Transformer-based models") == ["network", "transformer", "model"]
    assert tokenize("Taxonomies of strategies") == ["taxonomy", "strategy"]
    scores = bm25_scores(["fake", "news"], [["fake", "news", "model"], ["image", "model"], []])
    assert scores[0] > 0 and scores[1] == 0 and scores[2] == 0
    assert span_facets("Accuracy improved to 91.2% but the dataset is limited.") >= {"result", "limitation", "dataset"}
    assert span_facets("Fake news is a social problem.") == frozenset()


def _evaluation_module():
    spec = importlib.util.spec_from_file_location(
        "evaluate_evidence_retrieval", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_evidence_retrieval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sheet(arm_rows):
    """Synthetic key/labels: arm_rows is [(unit, arms, label, relevant)]."""
    key = {str(i): {"id": f"{unit}:ev{i}", "arms": list(arms), "unit": unit} for i, (unit, arms, _, _) in enumerate(arm_rows, 1)}
    rows = [(str(i), label, relevant) for i, (_, _, label, relevant) in enumerate(arm_rows, 1)]
    return rows, key


def _arm(n_good, n_bg_relevant, n_bg_irrelevant, arm, unit="J1:P"):
    return ([(unit, [arm], "result", "y")] * n_good + [(unit, [arm], "background", "y")] * n_bg_relevant
            + [(unit, [arm], "noise", "n")] * n_bg_irrelevant)


def test_scorer_pass_rule_boundaries():
    evaluate = _evaluation_module().evaluate_labels
    baseline = _arm(4, 16, 0, "baseline")  # 20% good, 100% relevant
    # Exactly +20 pp and -5 pp relevance passes.
    report = evaluate(*_sheet(baseline + _arm(8, 11, 1, "retrieval")))
    assert report["verdict"] == "PASS" and report["gain_pp"] == 20.0 and report["topic_relevance_drop_pp"] == 5.0
    assert report["arms"]["retrieval"]["result_share"] == 0.4
    assert {"limitation_share", "future_work_share"} <= set(report["arms"]["baseline"])
    # Relevance drop of 10 pp fails despite the gain.
    assert evaluate(*_sheet(baseline + _arm(8, 10, 2, "retrieval")))["verdict"] == "FAIL"
    # Gain of 15 pp fails.
    assert evaluate(*_sheet(baseline + _arm(7, 13, 0, "retrieval")))["verdict"] == "FAIL"


def test_scorer_pools_papers_across_jobs_and_counts_shared_sentences_for_both():
    evaluate = _evaluation_module().evaluate_labels
    rows = (_arm(1, 0, 0, "baseline", "J1:P") + _arm(0, 1, 0, "retrieval", "J1:P")        # J1: baseline better
            + _arm(0, 3, 0, "baseline", "J2:P") + _arm(3, 0, 0, "retrieval", "J2:P")      # J2: retrieval better
            + [("J1:Q", ["baseline", "retrieval"], "limitation", "y")])                     # shared sentence
    report = evaluate(*_sheet(rows))
    paired = report["per_distinct_paper"]
    # P pooled: baseline 1/4 vs retrieval 3/4 -> one win, not one win + one loss.
    assert (paired["papers"], paired["retrieval_better"], paired["baseline_better"], paired["tied"]) == (2, 1, 0, 1)
    assert paired["papers_pooled_across_jobs"] == 1
    assert report["arms"]["baseline"]["sentences"] == 5 and report["arms"]["retrieval"]["sentences"] == 5


def test_scorer_marks_incomplete_labelling():
    evaluate = _evaluation_module().evaluate_labels
    rows, key = _sheet(_arm(4, 16, 0, "baseline") + _arm(20, 0, 0, "retrieval"))
    rows[0] = (rows[0][0], "", "")
    report = evaluate(rows, key)
    assert report["verdict"] == "incomplete" and report["invalid_or_unlabelled_rows"] == 1


def test_evaluation_sign_test():
    module = _evaluation_module()
    assert module.sign_test(0, 0) == 1.0
    assert module.sign_test(8, 0) == 2 / 256
    assert module.sign_test(3, 3) == 1.0
