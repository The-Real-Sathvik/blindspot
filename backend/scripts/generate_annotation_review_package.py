"""Generate the local Phase 4B.3A.1 review packet; it never changes labels."""
from pathlib import Path
import argparse
import os
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.db.session import create_session_factory, create_sqlite_engine
from app.services.annotation_review import write_review_package
from app.services.claim_extraction_evaluation import load_reference_annotations, validate_reference_provenance


ANNOTATIONS = BACKEND_ROOT / "tests" / "fixtures" / "claim_extraction_reference_annotations.json"
OUTPUT = BACKEND_ROOT.parent / "data" / "reports" / "phase_4b_3a_1_annotation_review"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new, versioned review package.")
    parser.add_argument("--annotations", type=Path, default=ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--dataset-version", default="phase-4b-3a-v1")
    args = parser.parse_args()
    engine = create_sqlite_engine("sqlite:///blindspot.db")
    session = create_session_factory(engine)()
    try:
        annotations = load_reference_annotations(args.annotations)
        report = validate_reference_provenance(session, annotations)
        if report.errors:
            raise RuntimeError("Cannot generate review package with invalid provenance: " + "; ".join(report.errors))
        markdown, status = write_review_package(session, annotations, annotation_path=args.annotations, output_dir=args.output, dataset_version=args.dataset_version)
        print(f"wrote {markdown}")
        print(f"wrote {status}")
    finally:
        session.close(); engine.dispose()


if __name__ == "__main__":
    main()
