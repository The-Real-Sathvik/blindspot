"""Generate a non-approving AI-assisted quality report; no model inference occurs."""
from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.db.session import create_session_factory, create_sqlite_engine
from app.services.annotation_quality_review import assess_annotations, finding_dicts
from app.services.claim_extraction_evaluation import load_reference_annotations


ANNOTATIONS = BACKEND_ROOT / "tests" / "fixtures" / "claim_extraction_reference_annotations_phase_4b_3a_v2.json"
OUTPUT = BACKEND_ROOT.parent / "data" / "reports" / "phase_4b_3a_v2_quality_review"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    annotations = load_reference_annotations(ANNOTATIONS)
    engine = create_sqlite_engine("sqlite:///blindspot.db")
    session = create_session_factory(engine)()
    try:
        findings = assess_annotations(session, annotations)
    finally:
        session.close(); engine.dispose()
    grouped: dict[str, list] = defaultdict(list)
    for finding in findings: grouped[finding.assessment.value].append(finding)
    (OUTPUT / "quality_review.json").write_text(json.dumps({"dataset_version": "phase-4b-3a-v2", "inference_used": False, "findings": finding_dicts(findings)}, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# AI-assisted annotation quality review — v2",
        "", "This report uses deterministic source checks plus transparent heuristic triage. **No Ollama or other model inference was used.** It is not human approval, semantic verification, or evidence of scientific truth.",
        "", "## Deterministic results", "", f"- Records reviewed: {len(findings)}", f"- Source/provenance checks passed: {sum(f.deterministic_valid for f in findings)}/{len(findings)}", "- Held-out records were reviewed only for dataset maintenance; none was exposed to extraction prompts or tuning.",
    ]
    for status in ("needs_human_review", "possibly_incomplete", "potentially_misleading", "likely_supported"):
        rows = grouped[status]
        lines.extend(["", f"## {status.replace('_', ' ').title()} ({len(rows)})", ""])
        for finding in rows:
            annotation = next(item for item in annotations if item.reference_id == finding.annotation_id)
            lines.extend([
                f"### {finding.annotation_id}",
                f"- Partition: `{annotation.dataset_split.value}`; category: `{annotation.claim_category.value if annotation.claim_category else 'abstention'}`",
                f"- Exact quotation: {annotation.supporting_quotation or '[abstention]'}",
                f"- Checks: {'passed' if finding.deterministic_valid else 'failed'}; numeric values: {', '.join(finding.numeric_values) or 'none'}",
                f"- Assessment reason: {' '.join(finding.reasons)}", "",
            ])
    (OUTPUT / "AI_ASSISTED_QUALITY_REVIEW.md").write_text("\n".join(lines), encoding="utf-8")
    print(OUTPUT / "AI_ASSISTED_QUALITY_REVIEW.md")


if __name__ == "__main__":
    main()
