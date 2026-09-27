# -*- coding: utf-8 -*-
"""
Example: Basic document extraction and pipeline execution.
"""

import sys
from pathlib import Path

# Add src to path if running directly from repo
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from scan_reader import LegalDocPlatformFacade


def main():
    facade = LegalDocPlatformFacade()
    print("🚀 Initialized ScanReader Facade")
    print(f"Supported document plugins: {facade.registry.ids()}")

    # Example: classify a document
    dummy_scan = REPO_ROOT / "data" / "ground_truth" / "sample.pdf"
    if dummy_scan.exists():
        doc_type, conf, method = facade.classify_document(str(dummy_scan))
        print(f"Detected type: {doc_type} (conf: {conf:.2f}, method: {method})")
    else:
        print("💡 Place sample scans into data/ to test direct classification.")


if __name__ == "__main__":
    main()
