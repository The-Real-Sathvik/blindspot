"""Validate human-entered Phase 4B.3A.1 review decisions without changing them."""
from pathlib import Path
import argparse
import json
import os
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.db.session import create_session_factory, create_sqlite_engine
from app.services.annotation_review import (
    AnnotationReviewPackage,
    annotation_dataset_hash,
    review_status_counts,
    validate_review_package,
)
from app.services.claim_extraction_evaluation import load_reference_annotations, validate_reference_provenance


ANNOTATIONS = BACKEND_ROOT / "tests" / "fixtures" / "claim_extraction_reference_annotations.json"
STATUS = BACKEND_ROOT.parent / "data" / "reports" / "phase_4b_3a_1_annotation_review" / "review_status.json"
DATASET_VERSION = "phase-4b-3a-v1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a versioned human review package.")
    parser.add_argument("--annotations", type=Path, default=ANNOTATIONS)
    parser.add_argument("--status", type=Path, default=STATUS)
    parser.add_argument("--dataset-version", default=DATASET_VERSION)
    args = parser.parse_args()
    annotations = load_reference_annotations(args.annotations)
    package = AnnotationReviewPackage.model_validate(json.loads(args.status.read_text(encoding="utf-8")))
    validate_review_package(package, annotations, dataset_version=args.dataset_version, dataset_hash=annotation_dataset_hash(args.annotations))
    engine = create_sqlite_engine("sqlite:///blindspot.db")
    session = create_session_factory(engine)()
    try:
        provenance = validate_reference_provenance(session, annotations)
    finally:
        session.close(); engine.dispose()
    if provenance.errors:
        raise RuntimeError("source provenance failed: " + "; ".join(provenance.errors))
    print(review_status_counts(package))
    print(f"provenance valid: {provenance.valid_count}/{len(annotations)}")


if __name__ == "__main__":
    main()
