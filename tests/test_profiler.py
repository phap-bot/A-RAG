"""Comprehensive Unit Tests for DocumentProfiler (profiler.py).

Tests cover:
- Extension-based FileCategory classification
- Feature detection heuristics (tables, images, code blocks)
- ExtractionStrategy selection logic
- SHA-256 fingerprint deduplication
- Language hint detection (Vietnamese vs English)
- PDF page estimation
- Edge cases (unknown extensions, empty files, binary files)
"""

import os
import tempfile

import pytest

from src.core.exceptions import IngestionError
from src.ingestion.parser.models import (
    ExtractionStrategy,
    FileCategory,
)
from src.ingestion.parser.profiler import DocumentProfiler


@pytest.fixture
def profiler():
    return DocumentProfiler()


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as d:
        yield d


def _write_file(tmp_dir: str, name: str, content: str | bytes) -> str:
    """Helper: write a file and return its path."""
    path = os.path.join(tmp_dir, name)
    mode = "wb" if isinstance(content, bytes) else "w"
    encoding = None if isinstance(content, bytes) else "utf-8"
    with open(path, mode, encoding=encoding) as f:
        f.write(content)
    return path


# =====================================================================
# 1. CATEGORY CLASSIFICATION
# =====================================================================

class TestCategoryClassification:
    def test_pdf_classified_as_document(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "spec.pdf", b"%PDF-1.4 fake pdf content")
        result = profiler.profile(path)
        assert result.category == FileCategory.DOCUMENT
        assert result.file_type == "pdf"

    def test_markdown_classified_as_document(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "readme.md", "# Hello World\n\nSome content.")
        result = profiler.profile(path)
        assert result.category == FileCategory.DOCUMENT
        assert result.file_type == "md"

    def test_xlsx_classified_as_spreadsheet(self, profiler, tmp_dir):
        # XLSX is a zip file, use minimal bytes
        path = _write_file(tmp_dir, "data.xlsx", b"PK\x03\x04 fake xlsx")
        result = profiler.profile(path)
        assert result.category == FileCategory.SPREADSHEET
        assert result.has_tables is True

    def test_csv_classified_as_spreadsheet(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "data.csv", "col1,col2,col3\na,b,c\nd,e,f\n")
        result = profiler.profile(path)
        assert result.category == FileCategory.SPREADSHEET

    def test_python_classified_as_code(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "app.py", "import os\n\ndef main():\n    pass\n")
        result = profiler.profile(path)
        assert result.category == FileCategory.CODE

    def test_image_classified_as_image(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "diagram.png", b"\x89PNG\r\n\x1a\n fake png")
        result = profiler.profile(path)
        assert result.category == FileCategory.IMAGE
        assert result.has_images is True

    def test_unknown_extension(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "mystery.xyz", "unknown content")
        result = profiler.profile(path)
        assert result.category == FileCategory.UNKNOWN

    def test_txt_classified_as_document(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "notes.txt", "Plain text notes.\n\nParagraph two.")
        result = profiler.profile(path)
        assert result.category == FileCategory.DOCUMENT

    def test_directory_path_is_rejected(self, profiler, tmp_dir):
        with pytest.raises(IngestionError):
            profiler.profile(tmp_dir)

    def test_extension_mismatch_uses_file_signature(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "image.txt", b"\x89PNG\r\n\x1a\n")
        result = profiler.profile(path)
        assert result.category == FileCategory.IMAGE
        assert result.fingerprint.mime_type == "image/png"


# =====================================================================
# 2. FEATURE DETECTION HEURISTICS
# =====================================================================

MARKDOWN_WITH_ALL_FEATURES = """# Architecture Spec

This document contains mixed content.

| Zone | Name | Protocol |
|---|---|---|
| Zone 1 | Ingestion | Celery |
| Zone 2 | Agentic | LangGraph |

![Workflow Diagram](assets/workflow.png)

```python
def retrieve(query: str) -> list:
    return search_knowledge_base(query)
```

Some concluding text.
"""


class TestFeatureDetection:
    def test_detect_tables_in_markdown(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "doc.md", MARKDOWN_WITH_ALL_FEATURES)
        result = profiler.profile(path)
        assert result.has_tables is True

    def test_detect_images_in_markdown(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "doc.md", MARKDOWN_WITH_ALL_FEATURES)
        result = profiler.profile(path)
        assert result.has_images is True

    def test_detect_code_blocks_in_markdown(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "doc.md", MARKDOWN_WITH_ALL_FEATURES)
        result = profiler.profile(path)
        assert result.has_code_blocks is True

    def test_plain_text_no_features(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "plain.txt", "This is just a plain paragraph.\nNothing special here.")
        result = profiler.profile(path)
        assert result.has_tables is False
        assert result.has_images is False
        assert result.has_code_blocks is False

    def test_csv_like_table_detection(self, profiler, tmp_dir):
        csv_content = "name,age,city\nAlice,30,Hanoi\nBob,25,HCMC\nCharlie,35,Danang\n"
        path = _write_file(tmp_dir, "data.txt", csv_content)
        result = profiler.profile(path)
        assert result.has_tables is True

    def test_html_table_detection(self, profiler, tmp_dir):
        html = "<html><body><table><tr><td>Cell</td></tr></table></body></html>"
        path = _write_file(tmp_dir, "page.txt", html)
        result = profiler.profile(path)
        assert result.has_tables is True


# =====================================================================
# 3. STRATEGY SELECTION LOGIC
# =====================================================================

class TestStrategySelection:
    def test_plain_markdown_no_features_gets_direct_text(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "simple.md", "# Title\n\nJust text content.\n")
        result = profiler.profile(path)
        assert result.strategy == ExtractionStrategy.DIRECT_TEXT

    def test_markdown_with_multi_features_gets_hybrid(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "complex.md", MARKDOWN_WITH_ALL_FEATURES)
        result = profiler.profile(path)
        assert result.strategy == ExtractionStrategy.HYBRID

    def test_markdown_with_only_table_gets_layout(self, profiler, tmp_dir):
        md = "# Report\n\n| Metric | Value |\n|---|---|\n| F1 | 0.92 |\n\nDone.\n"
        path = _write_file(tmp_dir, "table_only.md", md)
        result = profiler.profile(path)
        assert result.strategy == ExtractionStrategy.LAYOUT_ANALYSIS

    def test_spreadsheet_gets_table_extraction(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "data.xlsx", b"PK\x03\x04")
        result = profiler.profile(path)
        assert result.strategy == ExtractionStrategy.TABLE_EXTRACTION

    def test_image_gets_vlm_only(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "chart.png", b"\x89PNG")
        result = profiler.profile(path)
        assert result.strategy == ExtractionStrategy.VLM_ONLY

    def test_code_gets_code_parse(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "main.py", "import sys\nprint('hello')\n")
        result = profiler.profile(path)
        assert result.strategy == ExtractionStrategy.CODE_PARSE

    def test_pdf_gets_layout_analysis(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "doc.pdf", b"%PDF-1.7 minimal pdf")
        result = profiler.profile(path)
        assert result.strategy in (
            ExtractionStrategy.LAYOUT_ANALYSIS,
            ExtractionStrategy.HYBRID,
        )


# =====================================================================
# 4. FINGERPRINT & DEDUPLICATION
# =====================================================================

class TestFingerprint:
    def test_fingerprint_deterministic(self, profiler, tmp_dir):
        content = "Identical content for fingerprint test."
        path1 = _write_file(tmp_dir, "file_a.txt", content)
        path2 = _write_file(tmp_dir, "file_b.txt", content)
        r1 = profiler.profile(path1)
        r2 = profiler.profile(path2)
        assert r1.fingerprint.sha256 == r2.fingerprint.sha256
        assert r1.fingerprint.size_bytes == r2.fingerprint.size_bytes

    def test_fingerprint_different_content(self, profiler, tmp_dir):
        path1 = _write_file(tmp_dir, "a.txt", "Content A")
        path2 = _write_file(tmp_dir, "b.txt", "Content B")
        r1 = profiler.profile(path1)
        r2 = profiler.profile(path2)
        assert r1.fingerprint.sha256 != r2.fingerprint.sha256

    def test_fingerprint_has_valid_sha256(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "test.txt", "hello")
        result = profiler.profile(path)
        assert len(result.fingerprint.sha256) == 64
        assert all(c in "0123456789abcdef" for c in result.fingerprint.sha256)

    def test_fingerprint_size_bytes(self, profiler, tmp_dir):
        content = "Exactly 28 bytes of content."
        path = _write_file(tmp_dir, "size.txt", content)
        result = profiler.profile(path)
        assert result.fingerprint.size_bytes == len(content.encode("utf-8"))


# =====================================================================
# 5. LANGUAGE DETECTION
# =====================================================================

class TestLanguageDetection:
    def test_vietnamese_text_detected(self, profiler, tmp_dir):
        vi_text = (
            "Hệ thống tri thức doanh nghiệp sử dụng kiến trúc đa tác nhân "
            "với LangGraph để điều phối truy vấn và tổng hợp câu trả lời."
        )
        path = _write_file(tmp_dir, "vi_doc.md", f"# Giới thiệu\n\n{vi_text}\n")
        result = profiler.profile(path)
        assert result.language_hint == "vi"

    def test_english_text_detected(self, profiler, tmp_dir):
        en_text = "The enterprise knowledge base uses a multi-agent architecture for retrieval."
        path = _write_file(tmp_dir, "en_doc.md", f"# Introduction\n\n{en_text}\n")
        result = profiler.profile(path)
        assert result.language_hint == "en"

    def test_code_files_no_language_hint(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "app.py", "import os\nprint('hello')\n")
        result = profiler.profile(path)
        assert result.language_hint is None


# =====================================================================
# 6. ERROR HANDLING & EDGE CASES
# =====================================================================

class TestEdgeCases:
    def test_file_not_found_raises(self, profiler):
        with pytest.raises(IngestionError):
            profiler.profile("/nonexistent/path/file.pdf")

    def test_empty_file(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "empty.txt", "")
        result = profiler.profile(path)
        assert result.category == FileCategory.DOCUMENT
        assert result.fingerprint.size_bytes == 0

    def test_profiler_notes_populated(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "rich.md", MARKDOWN_WITH_ALL_FEATURES)
        result = profiler.profile(path)
        assert result.profiler_notes is not None
        assert "tables_detected" in result.profiler_notes
        assert "images_detected" in result.profiler_notes
        assert "code_blocks_detected" in result.profiler_notes


# =====================================================================
# 7. PAGE ESTIMATION
# =====================================================================

class TestPageEstimation:
    def test_markdown_page_estimate_uses_ceiling(self, profiler, tmp_dir):
        content = "\n".join(f"Line {i}" for i in range(51))
        path = _write_file(tmp_dir, "just_over_page.md", content)
        result = profiler.profile(path)
        assert result.estimated_pages == 2

    def test_long_markdown_multiple_pages(self, profiler, tmp_dir):
        # 200 lines → ~4 pages at 50 lines/page
        lines = ["Line " + str(i) for i in range(200)]
        content = "\n".join(lines)
        path = _write_file(tmp_dir, "long.md", content)
        result = profiler.profile(path)
        assert result.estimated_pages >= 3

    def test_short_text_one_page(self, profiler, tmp_dir):
        path = _write_file(tmp_dir, "short.txt", "One liner.")
        result = profiler.profile(path)
        assert result.estimated_pages == 1

    def test_pdf_page_estimate_uses_pdf_page_objects(self, profiler, tmp_dir):
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj <</Type /Catalog>> endobj\n"
            b"2 0 obj <</Type /Pages /Kids [] /Count 3>> endobj\n"
            b"3 0 obj <</Type /Page /Parent 2 0 R>> endobj\n"
            b"4 0 obj <</Type /Page /Parent 2 0 R>> endobj\n"
            b"5 0 obj <</Type /Page /Parent 2 0 R>> endobj\n"
        )
        path = _write_file(tmp_dir, "three_pages.pdf", pdf_bytes)
        result = profiler.profile(path)
        assert result.estimated_pages == 3
