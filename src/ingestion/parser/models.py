"""Strict Pydantic JSON Data Contracts for the Agentic Ingestion Pipeline.

ZONE 1: Ingestion Pipeline (Offline).

This module defines the SINGLE SOURCE OF TRUTH for all data schemas flowing through
the parser/ directory. Every stage of the pipeline produces typed Pydantic models:

  File Input
    ↓
  ProfilerResult       (profiler.py output)
    ↓
  ParsedElement[]      (layout_parser.py + skills.py output)
    ↓
  ParsedDocument       (md_to_json.py validated output)
    ↓
  ContentChunk[]       (chunking/ input — THE FINAL CONTRACT)

RULE: Raw strings and Markdown are FORBIDDEN from leaving parser/.
      Only validated Pydantic instances may cross the parser/ boundary.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# =====================================================================
# 1. ENUMERATIONS & TYPE ALIASES
# =====================================================================

class FileCategory(str, Enum):
    """Top-level file classification determined by the Profiler."""
    DOCUMENT = "document"          # PDF, DOCX, TXT, MD
    SPREADSHEET = "spreadsheet"    # XLSX, CSV
    PRESENTATION = "presentation"  # PPTX
    IMAGE = "image"                # PNG, JPG, SVG (standalone)
    CODE = "code"                  # .py, .js, .java, etc.
    UNKNOWN = "unknown"


class ExtractionStrategy(str, Enum):
    """Extraction pipeline strategy selected by the Profiler."""
    LAYOUT_ANALYSIS = "layout_analysis"    # PDF with complex layouts, mixed text/table/image
    DIRECT_TEXT = "direct_text"            # Plain text, Markdown — no layout needed
    TABLE_EXTRACTION = "table_extraction"  # Spreadsheets — row/col oriented
    VLM_ONLY = "vlm_only"                 # Standalone images — VLM captioning only
    CODE_PARSE = "code_parse"             # Source code — AST/syntax aware parsing
    HYBRID = "hybrid"                      # Multi-strategy (e.g. PDF with code blocks & charts)


ElementType = Literal["header", "text", "table", "image", "code", "list", "chart", "equation"]

ModalityType = Literal["text", "table", "image", "chart", "code"]


# =====================================================================
# 2. PROFILER OUTPUT MODELS
# =====================================================================

class FileFingerprint(BaseModel):
    """Immutable identity digest of a source file. Prevents reprocessing duplicates."""

    sha256: str = Field(..., description="Full SHA-256 hex digest of file bytes")
    size_bytes: int = Field(..., ge=0, description="File size in bytes")
    mime_type: Optional[str] = Field(default=None, description="Detected MIME type (e.g. application/pdf)")


class ProfilerResult(BaseModel):
    """Output of profiler.py — determines HOW to parse a file before any extraction begins.
    
    The Profiler inspects the file and produces:
    - Classification (document, spreadsheet, image, code)
    - Recommended extraction strategy
    - Feature flags (has_tables, has_images, etc.) for downstream skill selection
    """

    file_name: str = Field(..., description="Original filename with extension")
    file_type: str = Field(..., description="Lowercase extension without dot (pdf, md, xlsx, ...)")
    category: FileCategory = Field(..., description="Top-level file classification")
    strategy: ExtractionStrategy = Field(..., description="Recommended extraction pipeline")
    fingerprint: FileFingerprint = Field(..., description="Content-based identity for deduplication")

    # Feature flags — signals to skill dispatcher
    has_tables: bool = Field(default=False, description="File likely contains tabular data")
    has_images: bool = Field(default=False, description="File likely contains embedded images/charts")
    has_code_blocks: bool = Field(default=False, description="File likely contains source code snippets")
    estimated_pages: int = Field(default=1, ge=1, description="Estimated page count")
    language_hint: Optional[str] = Field(default=None, description="Detected natural language (e.g. 'vi', 'en')")

    # Profiler diagnostics
    profiler_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Profiler classification confidence")
    profiler_notes: Optional[str] = Field(default=None, description="Free-text notes from profiler heuristics")


# =====================================================================
# 3. LAYOUT PARSER & SKILLS OUTPUT MODELS (Existing + Extended)
# =====================================================================

class BoundingBox(BaseModel):
    """Normalized bounding coordinates of a layout element on a page.
    
    Values are normalized to [0.0, 1.0] relative to page dimensions.
    (0,0) = top-left corner, (1,1) = bottom-right corner.
    """

    x1: float = Field(..., ge=0.0, le=1.0, description="Top-left X (normalized)")
    y1: float = Field(..., ge=0.0, le=1.0, description="Top-left Y (normalized)")
    x2: float = Field(..., ge=0.0, le=1.0, description="Bottom-right X (normalized)")
    y2: float = Field(..., ge=0.0, le=1.0, description="Bottom-right Y (normalized)")

    @model_validator(mode="after")
    def validate_box_ordering(self) -> "BoundingBox":
        if self.x2 < self.x1:
            raise ValueError(f"x2 ({self.x2}) must be >= x1 ({self.x1})")
        if self.y2 < self.y1:
            raise ValueError(f"y2 ({self.y2}) must be >= y1 ({self.y1})")
        return self

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)


class ElementMetadata(BaseModel):
    """Rich, structured metadata preserved for each extracted element.
    
    This is the provenance record — it tracks WHERE in the source document
    each piece of content originated, what type it is, and how confident
    the classifier was.
    """

    source_doc: str = Field(..., description="Source document filename or path")
    page_number: int = Field(default=1, ge=1, description="1-indexed page number in source")
    element_index: int = Field(..., ge=0, description="Sequential index within document (reading order)")
    element_type: ElementType = Field(..., description="Classified layout element type")
    bounding_box: Optional[BoundingBox] = Field(default=None, description="Spatial coordinates (if available)")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Layout classifier confidence score")

    # Hierarchical context
    parent_header: Optional[str] = Field(default=None, description="Nearest preceding header text (for context inheritance)")
    header_level: Optional[int] = Field(default=None, ge=1, le=6, description="If element_type=header, the heading level (1-6)")
    section_path: List[str] = Field(default_factory=list, description="Breadcrumb path of enclosing headers, e.g. ['Chapter 1', 'Section 1.2']")

    # Format-specific details
    extra: Dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific metadata. Tables: {row_count, col_count, headers}. "
                    "Code: {language}. Lists: {item_count}. Images: {alt_text, image_source}."
    )


class ParsedElement(BaseModel):
    """An individual structural unit extracted by Layout Analysis or a Skill.

    This is the ATOMIC content unit of the pipeline. Each instance represents
    exactly one block: a paragraph, a table, an image, a code block, or a header.
    """

    element_id: str = Field(..., description="Globally unique element ID: {doc_id}-elem-{index:04d}")
    content: str = Field(..., min_length=1, description="Extracted text, Markdown table, or code snippet")
    raw_content: Optional[str] = Field(default=None, description="Original unparsed string before normalization")
    metadata: ElementMetadata = Field(..., description="Full provenance and classification metadata")

    # Multi-modal hooks
    image_path: Optional[str] = Field(default=None, description="Filesystem path or URI to extracted image asset")
    vlm_caption: Optional[str] = Field(default=None, description="VLM-generated technical description (populated by skills.py)")


class ParsedDocument(BaseModel):
    """Complete structured representation of a document after parsing.

    This is the validated JSON output of the parser/ module.
    The chunking/ module ONLY accepts this model as input.
    """

    document_id: str = Field(..., description="Content-based unique ID (SHA-256 prefix or UUID)")
    file_name: str = Field(..., description="Original filename with extension")
    file_type: str = Field(..., description="Lowercase file extension (pdf, md, docx, txt, xlsx)")
    total_pages: int = Field(default=1, ge=1, description="Total page count in source document")
    elements: List[ParsedElement] = Field(default_factory=list, description="Ordered sequence of parsed elements")

    # Document-level metadata
    doc_metadata: Dict[str, Any] = Field(default_factory=dict, description="File-level metadata (author, size, timestamps)")

    # Profiler provenance
    profiler_result: Optional[ProfilerResult] = Field(default=None, description="Profiler classification that drove parsing")

    # Aggregation statistics (computed automatically)
    stats: Dict[str, Any] = Field(default_factory=dict, description="Computed statistics: element counts by type, modality distribution")

    @model_validator(mode="after")
    def compute_stats(self) -> "ParsedDocument":
        """Auto-compute document statistics after construction."""
        type_counts: Dict[str, int] = {}
        has_tables = False
        has_images = False
        has_code = False

        for elem in self.elements:
            etype = elem.metadata.element_type
            type_counts[etype] = type_counts.get(etype, 0) + 1
            if etype == "table":
                has_tables = True
            elif etype in ("image", "chart"):
                has_images = True
            elif etype == "code":
                has_code = True

        self.stats = {
            "total_elements": len(self.elements),
            "type_counts": type_counts,
            "has_tables": has_tables,
            "has_images": has_images,
            "has_code": has_code,
        }
        return self


# =====================================================================
# 4. FINAL CHUNK CONTRACT (Output to chunking/ module)
# =====================================================================

class ChunkMetadata(BaseModel):
    """Metadata carried by each chunk into the Vector DB and Knowledge Graph.
    
    This preserves full lineage: which document, which page, which element(s),
    what modality, and context breadcrumb headers.
    """

    document_id: str = Field(..., description="Parent document ID")
    source_doc: str = Field(..., description="Original filename")
    page_numbers: List[int] = Field(default_factory=list, description="Page(s) this chunk spans")
    element_ids: List[str] = Field(default_factory=list, description="Source element IDs composing this chunk")
    modality: ModalityType = Field(default="text", description="Content modality of the chunk")
    section_path: List[str] = Field(default_factory=list, description="Header breadcrumb trail for contextual hierarchy")
    parent_header: Optional[str] = Field(default=None, description="Nearest enclosing header text")

    # Retrieval optimization hints
    has_table: bool = Field(default=False, description="Chunk contains tabular content")
    has_image: bool = Field(default=False, description="Chunk references an image or chart")
    language: Optional[str] = Field(default=None, description="Programming language if code, or natural language hint")


class ContentChunk(BaseModel):
    """The FINAL normalized data unit that crosses the parser/ boundary into chunking/.

    THIS IS THE CONTRACT. The chunking/ module, embedding/ module, and downstream
    Vector DB / Neo4j storage ONLY accept this model.
    
    Each ContentChunk is:
    - Self-contained (has its own text + metadata)
    - Traceable (links back to exact source elements and pages)
    - Modality-tagged (text vs table vs image caption vs code)
    - Ready for embedding (content field is clean, normalized text)
    """

    chunk_id: str = Field(
        default_factory=lambda: f"chunk-{uuid4().hex[:12]}",
        description="Globally unique chunk identifier"
    )
    content: str = Field(..., min_length=1, description="Normalized text content ready for embedding")
    metadata: ChunkMetadata = Field(..., description="Full lineage and provenance metadata")

    # Multi-modal supplementary fields
    table_markdown: Optional[str] = Field(default=None, description="Original Markdown table (preserved intact for downstream rendering)")
    vlm_caption: Optional[str] = Field(default=None, description="VLM technical description of image/chart")
    code_snippet: Optional[str] = Field(default=None, description="Raw code block with language tag")

    # Embedding slot (populated by embedding/ module)
    embedding: Optional[List[float]] = Field(default=None, exclude=True, description="Vector embedding (excluded from JSON serialization)")

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Chunk creation timestamp")

    @property
    def token_estimate(self) -> int:
        """Rough token count estimate (word_count * 1.3)."""
        return int(len(self.content.split()) * 1.3)


# =====================================================================
# 5. UTILITY FUNCTIONS
# =====================================================================

def generate_doc_id(content_bytes: bytes) -> str:
    """Generate a deterministic 16-char document ID from raw file bytes."""
    return hashlib.sha256(content_bytes).hexdigest()[:16]


def generate_element_id(doc_id: str, index: int) -> str:
    """Generate a standardized element ID following {doc_id}-elem-{index:04d} format."""
    return f"{doc_id}-elem-{index:04d}"


# =====================================================================
# EXPORTS
# =====================================================================

__all__ = [
    # Enums & Types
    "FileCategory",
    "ExtractionStrategy",
    "ElementType",
    "ModalityType",
    # Profiler
    "FileFingerprint",
    "ProfilerResult",
    # Layout & Skills
    "BoundingBox",
    "ElementMetadata",
    "ParsedElement",
    "ParsedDocument",
    # Final Chunk Contract
    "ChunkMetadata",
    "ContentChunk",
    # Utilities
    "generate_doc_id",
    "generate_element_id",
]
