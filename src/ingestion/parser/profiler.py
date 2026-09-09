"""Agentic File Profiler — Pre-Parse Intelligence for the Ingestion Pipeline.

ZONE 1: Ingestion Pipeline (Offline).
Directory Lock: src/ingestion/parser/

The Profiler is the FIRST agent in the parsing chain. It inspects a raw file
BEFORE any content extraction begins and produces a ProfilerResult that tells
downstream agents (layout_parser, skills, md_to_json) exactly HOW to process it.

Decision Logic:
  1. Read file bytes → compute SHA-256 fingerprint (deduplication gate).
  2. Classify by extension + MIME sniffing → FileCategory.
  3. Peek into content (header bytes, magic numbers, text heuristics) → feature flags.
  4. Select ExtractionStrategy based on category + detected features.

The Profiler NEVER extracts content. It only classifies and signals.
"""

import hashlib
import mimetypes
import os
import re
from typing import Optional

from src.core.config import logger
from src.core.exceptions import IngestionError
from src.ingestion.parser.models import (
    ExtractionStrategy,
    FileCategory,
    FileFingerprint,
    ProfilerResult,
)


# =====================================================================
# Extension → Category mapping
# =====================================================================

_EXT_TO_CATEGORY: dict[str, FileCategory] = {
    # Documents
    "pdf": FileCategory.DOCUMENT,
    "md": FileCategory.DOCUMENT,
    "markdown": FileCategory.DOCUMENT,
    "txt": FileCategory.DOCUMENT,
    "text": FileCategory.DOCUMENT,
    "log": FileCategory.DOCUMENT,
    "docx": FileCategory.DOCUMENT,
    "doc": FileCategory.DOCUMENT,
    "rtf": FileCategory.DOCUMENT,
    "odt": FileCategory.DOCUMENT,
    # Spreadsheets
    "xlsx": FileCategory.SPREADSHEET,
    "xls": FileCategory.SPREADSHEET,
    "csv": FileCategory.SPREADSHEET,
    "tsv": FileCategory.SPREADSHEET,
    "ods": FileCategory.SPREADSHEET,
    # Presentations
    "pptx": FileCategory.PRESENTATION,
    "ppt": FileCategory.PRESENTATION,
    "odp": FileCategory.PRESENTATION,
    # Images
    "png": FileCategory.IMAGE,
    "jpg": FileCategory.IMAGE,
    "jpeg": FileCategory.IMAGE,
    "gif": FileCategory.IMAGE,
    "bmp": FileCategory.IMAGE,
    "svg": FileCategory.IMAGE,
    "webp": FileCategory.IMAGE,
    "tiff": FileCategory.IMAGE,
    "tif": FileCategory.IMAGE,
    # Code
    "py": FileCategory.CODE,
    "js": FileCategory.CODE,
    "ts": FileCategory.CODE,
    "java": FileCategory.CODE,
    "cpp": FileCategory.CODE,
    "c": FileCategory.CODE,
    "h": FileCategory.CODE,
    "go": FileCategory.CODE,
    "rs": FileCategory.CODE,
    "rb": FileCategory.CODE,
    "php": FileCategory.CODE,
    "sql": FileCategory.CODE,
    "sh": FileCategory.CODE,
    "bash": FileCategory.CODE,
    "yaml": FileCategory.CODE,
    "yml": FileCategory.CODE,
    "json": FileCategory.CODE,
    "xml": FileCategory.CODE,
    "html": FileCategory.CODE,
    "css": FileCategory.CODE,
    "toml": FileCategory.CODE,
    "ini": FileCategory.CODE,
    "cfg": FileCategory.CODE,
}

# Strategy selection rules: (category, feature_flags) → strategy
_CATEGORY_TO_BASE_STRATEGY: dict[FileCategory, ExtractionStrategy] = {
    FileCategory.DOCUMENT: ExtractionStrategy.LAYOUT_ANALYSIS,
    FileCategory.SPREADSHEET: ExtractionStrategy.TABLE_EXTRACTION,
    FileCategory.PRESENTATION: ExtractionStrategy.LAYOUT_ANALYSIS,
    FileCategory.IMAGE: ExtractionStrategy.VLM_ONLY,
    FileCategory.CODE: ExtractionStrategy.CODE_PARSE,
    FileCategory.UNKNOWN: ExtractionStrategy.DIRECT_TEXT,
}


class DocumentProfiler:
    """Agentic file classifier that determines extraction strategy before parsing.
    
    Usage:
        profiler = DocumentProfiler()
        result = profiler.profile("path/to/document.pdf")
        # result.strategy → ExtractionStrategy.LAYOUT_ANALYSIS
        # result.has_tables → True
    """

    def __init__(self, text_peek_bytes: int = 8192):
        """Initialize profiler.
        
        Args:
            text_peek_bytes: Number of bytes to read from text files for heuristic analysis.
        """
        self._text_peek_bytes = text_peek_bytes
        logger.info("DocumentProfiler initialized")

    def profile(self, file_path: str) -> ProfilerResult:
        """Profile a file and produce classification + strategy + feature flags.
        
        Args:
            file_path: Absolute or relative path to the source file.
            
        Returns:
            ProfilerResult with classification, strategy, and feature flags.
            
        Raises:
            IngestionError: If file doesn't exist or is unreadable.
        """
        if not os.path.exists(file_path):
            raise IngestionError(
                f"File not found: {file_path}",
                details={"file_path": file_path},
            )
        if not os.path.isfile(file_path):
            raise IngestionError(
                f"Path is not a regular file: {file_path}",
                details={"file_path": file_path},
            )

        file_name = os.path.basename(file_path)
        ext = os.path.splitext(file_name)[1].lower().lstrip(".")

        logger.info(f"Profiling file: '{file_name}' (ext={ext})")

        # Step 1: Read bytes and compute fingerprint
        content_bytes = self._read_file_bytes(file_path)
        fingerprint = self._compute_fingerprint(content_bytes, file_path)

        # Step 2: Classify by extension
        category = self._classify_category(ext, content_bytes)

        # Step 3: Detect features via content heuristics
        features = self._detect_features(
            content_bytes=content_bytes,
            ext=ext,
            category=category,
            file_path=file_path,
        )

        # Step 4: Select extraction strategy
        strategy = self._select_strategy(category, features, ext)

        # Step 5: Detect language hint
        language_hint = self._detect_language(content_bytes, ext)

        # Build profiler notes
        notes_parts = []
        if features["has_tables"]:
            notes_parts.append("tables_detected")
        if features["has_images"]:
            notes_parts.append("images_detected")
        if features["has_code_blocks"]:
            notes_parts.append("code_blocks_detected")
        if strategy != _CATEGORY_TO_BASE_STRATEGY.get(category):
            notes_parts.append(f"strategy_overridden_from_{_CATEGORY_TO_BASE_STRATEGY.get(category, 'unknown')}")

        result = ProfilerResult(
            file_name=file_name,
            file_type=ext or "unknown",
            category=category,
            strategy=strategy,
            fingerprint=fingerprint,
            has_tables=features["has_tables"],
            has_images=features["has_images"],
            has_code_blocks=features["has_code_blocks"],
            estimated_pages=features["estimated_pages"],
            language_hint=language_hint,
            profiler_confidence=features["confidence"],
            profiler_notes="; ".join(notes_parts) if notes_parts else None,
        )

        logger.info(
            f"Profiler result: category={result.category.value}, "
            f"strategy={result.strategy.value}, "
            f"tables={result.has_tables}, images={result.has_images}, "
            f"code={result.has_code_blocks}, pages≈{result.estimated_pages}"
        )

        return result

    # -----------------------------------------------------------------
    # Private implementation methods
    # -----------------------------------------------------------------

    def _read_file_bytes(self, file_path: str) -> bytes:
        """Read raw file bytes. Raises IngestionError on I/O failure."""
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except OSError as exc:
            raise IngestionError(
                f"Cannot read file: {file_path}",
                details={"error": str(exc)},
            ) from exc

    def _compute_fingerprint(self, content_bytes: bytes, file_path: str) -> FileFingerprint:
        """Compute SHA-256 hash, file size, and MIME type."""
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        mime_type = self._sniff_mime_type(content_bytes) or mimetypes.guess_type(file_path)[0]
        return FileFingerprint(
            sha256=sha256,
            size_bytes=len(content_bytes),
            mime_type=mime_type,
        )

    def _classify_category(self, ext: str, content_bytes: bytes = b"") -> FileCategory:
        """Map file extension to category, preferring a known file signature."""
        signature_mime = self._sniff_mime_type(content_bytes)
        if signature_mime:
            signature_category = self._MIME_TO_CATEGORY.get(signature_mime)
            if signature_category is not None:
                return signature_category

        category = _EXT_TO_CATEGORY.get(ext, FileCategory.UNKNOWN)
        if category == FileCategory.UNKNOWN:
            logger.warning(f"Unknown file extension: '.{ext}'. Defaulting to UNKNOWN.")
        return category

    _MIME_TO_CATEGORY: dict[str, FileCategory] = {
        "application/pdf": FileCategory.DOCUMENT,
        "image/png": FileCategory.IMAGE,
        "image/jpeg": FileCategory.IMAGE,
        "image/gif": FileCategory.IMAGE,
        "image/bmp": FileCategory.IMAGE,
        "image/webp": FileCategory.IMAGE,
        "image/tiff": FileCategory.IMAGE,
    }

    @staticmethod
    def _sniff_mime_type(content_bytes: bytes) -> Optional[str]:
        """Identify common binary formats from magic bytes."""
        signatures = (
            (b"%PDF-", "application/pdf"),
            (b"\x89PNG\r\n\x1a\n", "image/png"),
            (b"\xff\xd8\xff", "image/jpeg"),
            (b"GIF87a", "image/gif"),
            (b"GIF89a", "image/gif"),
            (b"BM", "image/bmp"),
            (b"RIFF", "image/webp"),
            (b"II*\x00", "image/tiff"),
            (b"MM\x00*", "image/tiff"),
        )
        for signature, mime_type in signatures:
            if content_bytes.startswith(signature):
                return mime_type
        return None

    def _detect_features(
        self,
        content_bytes: bytes,
        ext: str,
        category: FileCategory,
        file_path: str,
    ) -> dict:
        """Heuristic content inspection to detect tables, images, code blocks.
        
        Returns dict with keys: has_tables, has_images, has_code_blocks,
                                 estimated_pages, confidence.
        """
        has_tables = False
        has_images = False
        has_code_blocks = False
        estimated_pages = 1
        confidence = 1.0

        if category in (FileCategory.DOCUMENT, FileCategory.CODE, FileCategory.UNKNOWN):
            # Attempt to decode as text for heuristic scanning
            try:
                text_peek = content_bytes[:self._text_peek_bytes].decode("utf-8", errors="replace")
            except Exception:
                text_peek = ""

            if text_peek:
                # Normalize all newline variations to \n for cross-platform regex consistency
                text_peek = text_peek.replace("\r\n", "\n").replace("\r", "\n")
                has_tables = self._detect_tables_in_text(text_peek)
                has_images = self._detect_images_in_text(text_peek)
                has_code_blocks = self._detect_code_in_text(text_peek)

        if category == FileCategory.SPREADSHEET:
            has_tables = True  # Spreadsheets are inherently tabular
            confidence = 1.0

        if category == FileCategory.IMAGE:
            has_images = True
            confidence = 1.0

        if ext == "pdf":
            estimated_pages = self._estimate_pdf_pages(file_path, content_bytes)
            # PDFs often have images; flag if PDF is large enough
            if len(content_bytes) > 100_000:
                has_images = True
                confidence = 0.85  # Heuristic — not confirmed until layout analysis

        elif ext in ("md", "markdown", "txt"):
            # Estimate pages by line count (≈50 lines per page)
            try:
                line_count = content_bytes.count(b"\n") + (1 if content_bytes else 0)
                estimated_pages = max(1, (line_count + 49) // 50)
            except Exception:
                estimated_pages = 1

        return {
            "has_tables": has_tables,
            "has_images": has_images,
            "has_code_blocks": has_code_blocks,
            "estimated_pages": estimated_pages,
            "confidence": confidence,
        }

    def _select_strategy(
        self,
        category: FileCategory,
        features: dict,
        ext: str,
    ) -> ExtractionStrategy:
        """Select the optimal extraction strategy based on classification + features.
        
        Decision logic:
        - Plain text/markdown with NO tables/images/code → DIRECT_TEXT (fastest)
        - Plain text/markdown WITH tables+images → HYBRID
        - PDF → always LAYOUT_ANALYSIS (needs spatial decomposition)
        - Spreadsheets → TABLE_EXTRACTION
        - Images → VLM_ONLY
        - Code files → CODE_PARSE
        """
        base_strategy = _CATEGORY_TO_BASE_STRATEGY.get(category, ExtractionStrategy.DIRECT_TEXT)

        # Override: simple text files with no special content → DIRECT_TEXT
        if ext in ("md", "markdown", "txt", "text", "log"):
            feature_count = sum([
                features["has_tables"],
                features["has_images"],
                features["has_code_blocks"],
            ])
            if feature_count == 0:
                return ExtractionStrategy.DIRECT_TEXT
            elif feature_count >= 2:
                return ExtractionStrategy.HYBRID
            else:
                return ExtractionStrategy.LAYOUT_ANALYSIS

        # Override: DOCX/PDF with both tables and images → HYBRID
        if ext in ("pdf", "docx") and features["has_tables"] and features["has_images"]:
            return ExtractionStrategy.HYBRID

        return base_strategy

    def _detect_tables_in_text(self, text: str) -> bool:
        """Heuristic: detect Markdown tables or CSV-like patterns."""
        # Markdown table: lines with | separators
        md_table = re.search(r"^\|.+\|\s*$", text, re.MULTILINE)
        if md_table:
            return True
        # CSV-like: multiple comma-separated values across lines
        csv_lines = re.findall(r"^.+,.+,.+$", text, re.MULTILINE)
        if len(csv_lines) >= 3:
            return True
        # HTML table tags
        if "<table" in text.lower() or "<tr" in text.lower():
            return True
        return False

    def _detect_images_in_text(self, text: str) -> bool:
        """Heuristic: detect image references in Markdown or HTML."""
        # Markdown image: ![alt](path)
        if re.search(r"!\[.*?\]\(.*?\)", text):
            return True
        # HTML img tag
        if "<img " in text.lower():
            return True
        # Base64 data URIs
        if "data:image/" in text:
            return True
        return False

    def _detect_code_in_text(self, text: str) -> bool:
        """Heuristic: detect fenced code blocks or language-like patterns."""
        # Fenced code block: ``` or ~~~
        if re.search(r"^```", text, re.MULTILINE):
            return True
        if re.search(r"^~~~", text, re.MULTILINE):
            return True
        # Common code patterns (import, def, class, function)
        code_patterns = re.findall(
            r"^(?:import |from |def |class |function |const |let |var |public )",
            text,
            re.MULTILINE,
        )
        if len(code_patterns) >= 3:
            return True
        return False

    def _detect_language(self, content_bytes: bytes, ext: str) -> Optional[str]:
        """Best-effort natural language detection from first kilobyte of text.
        
        Uses simple heuristic: check for Vietnamese diacritical marks vs ASCII-only.
        """
        if ext in ("py", "js", "ts", "java", "cpp", "go", "rs", "sql", "sh",
                    "json", "xml", "html", "css", "yaml", "yml", "toml"):
            return None  # Code files — language hint not applicable

        try:
            text_sample = content_bytes[:2048].decode("utf-8", errors="replace")
        except Exception:
            return None

        # Vietnamese diacritical marks
        vi_pattern = re.compile(r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", re.IGNORECASE)
        vi_count = len(vi_pattern.findall(text_sample))

        if vi_count > 10:
            return "vi"
        elif len(text_sample.strip()) > 50:
            return "en"  # Default assumption for non-Vietnamese text
        return None

    def _estimate_pdf_pages(self, file_path: str, content_bytes: bytes) -> int:
        """Read PDF page count, falling back to a conservative byte heuristic."""
        try:
            import pypdf

            page_count = len(pypdf.PdfReader(file_path).pages)
            if page_count > 0:
                return page_count
        except Exception:
            pass

        try:
            # Quick heuristic: count /Type /Page (not /Pages) in raw PDF stream
            page_count = content_bytes.count(b"/Type /Page")
            # Subtract /Type /Pages (the parent node)
            pages_node_count = content_bytes.count(b"/Type /Pages")
            estimated = page_count - pages_node_count
            return max(1, estimated)
        except Exception:
            return 1


__all__ = ["DocumentProfiler"]
