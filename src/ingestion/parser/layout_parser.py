"""Document Layout Parser & Multi-Modal Structure Extractor.

ZONE 1: Ingestion Pipeline (Offline).
Classifies and segments documents into Headers, Paragraphs, Tables, Images, and Code blocks
while strictly preserving spatial and structural metadata.
"""

import hashlib
import os
import re
from typing import Any, Dict, List, Optional

from src.core.config import logger
from src.core.exceptions import ParsingError
from src.ingestion.parser.models import (
    BoundingBox,
    ElementMetadata,
    ElementType,
    ParsedDocument,
    ParsedElement,
    generate_doc_id,
    generate_element_id,
)


_MINERU_REQUIRED_EXTENSIONS = {
    "docx",
    "pptx",
    "xlsx",
    "png",
    "jpg",
    "jpeg",
    "jp2",
    "gif",
    "bmp",
    "webp",
    "svg",
    "tiff",
    "tif",
}


class DocumentLayoutParser:
    """Native parser for text-like formats and the PDF fallback path.

    MinerU-required Office and standalone image files are rejected here so a
    caller cannot accidentally bypass the orchestrator's fail-fast policy.
    """

    def __init__(self):
        logger.info("Initializing DocumentLayoutParser with layout analysis rules")

    def parse_file(self, file_path: str) -> ParsedDocument:
        """Parse a local document file into structured elements with rich metadata."""
        if not os.path.exists(file_path):
            raise ParsingError(f"File does not exist: {file_path}")
        if not os.path.isfile(file_path):
            raise ParsingError(f"Path is not a regular file: {file_path}")

        file_name = os.path.basename(file_path)
        ext = os.path.splitext(file_name)[1].lower().lstrip(".")

        logger.info(f"Parsing document '{file_name}' (type={ext})")

        with open(file_path, "rb") as f:
            content_bytes = f.read()

        doc_id = generate_doc_id(content_bytes)

        if ext in ["md", "markdown"]:
            text_content = content_bytes.decode("utf-8", errors="replace")
            return self.parse_markdown(text_content, file_name=file_name, doc_id=doc_id)
        elif ext in ["txt", "text", "log"]:
            text_content = content_bytes.decode("utf-8", errors="replace")
            return self.parse_text(text_content, file_name=file_name, doc_id=doc_id)
        elif ext in ["csv", "tsv"]:
            text_content = content_bytes.decode("utf-8", errors="replace")
            delimiter = "\t" if ext == "tsv" else ","
            return self.parse_csv(text_content, file_name=file_name, doc_id=doc_id, delimiter=delimiter)
        elif ext == "pdf":
            return self.parse_pdf(file_path, file_name=file_name, doc_id=doc_id)
        elif ext in [
            "py", "js", "ts", "java", "cpp", "c", "h", "go", "rs", "rb",
            "php", "sql", "sh", "bash", "yaml", "yml", "json", "xml", "html",
            "css", "toml", "ini", "cfg",
        ]:
            text_content = content_bytes.decode("utf-8", errors="replace")
            return self.parse_code(text_content, file_name=file_name, doc_id=doc_id, language=ext)
        elif ext in _MINERU_REQUIRED_EXTENSIONS:
            raise ParsingError(
                f"'.{ext}' requires the MinerU pipeline; native parser bypass is disabled"
            )
        else:
            raise ParsingError(
                f"Unsupported native parser format '.{ext or 'unknown'}'; "
                "profile the source and select an active parser tool"
            )

    def parse_markdown(
        self,
        markdown_text: str,
        file_name: str = "document.md",
        doc_id: Optional[str] = None,
    ) -> ParsedDocument:
        """Parse Markdown content into headers, tables, code blocks, images, and text.
        
        Tracks a running header hierarchy so every element inherits its enclosing
        section_path and parent_header for downstream contextual chunking.
        """
        doc_id = doc_id or hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()[:16]
        elements: List[ParsedElement] = []
        lines = markdown_text.splitlines()

        i = 0
        elem_idx = 0
        current_page = 1

        # --- Header hierarchy tracker ---
        # Maps header_level -> header_text. When a new H2 appears, all H3+ are cleared.
        header_stack: Dict[int, str] = {}
        current_parent_header: Optional[str] = None

        def _build_section_path() -> List[str]:
            """Build ordered breadcrumb from header_stack."""
            return [header_stack[lvl] for lvl in sorted(header_stack.keys())]

        def _update_header_stack(level: int, text: str) -> None:
            nonlocal current_parent_header
            # Clear all deeper headers
            for lvl in list(header_stack.keys()):
                if lvl >= level:
                    del header_stack[lvl]
            header_stack[level] = text
            current_parent_header = text

        while i < len(lines):
            line = lines[i]

            # 1. Page separator comment check e.g. <!-- page 2 -->
            page_match = re.match(r"<!--\s*page\s*(\d+)\s*-->", line, re.IGNORECASE)
            if page_match:
                current_page = int(page_match.group(1))
                i += 1
                continue

            stripped = line.strip()

            # Skip empty lines
            if not stripped:
                i += 1
                continue

            # 2. Code Block detection
            if stripped.startswith("```"):
                code_lang = stripped.lstrip("`").strip()
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    i += 1  # Skip closing backticks

                content = "\n".join(code_lines)
                element = ParsedElement(
                    element_id=generate_element_id(doc_id, elem_idx),
                    content=content,
                    raw_content=f"```{code_lang}\n{content}\n```",
                    metadata=ElementMetadata(
                        source_doc=file_name,
                        page_number=current_page,
                        element_index=elem_idx,
                        element_type="code",
                        confidence=1.0,
                        parent_header=current_parent_header,
                        section_path=_build_section_path(),
                        extra={"language": code_lang or "plaintext"},
                    ),
                )
                elements.append(element)
                elem_idx += 1
                continue

            # 3. Markdown Image Tag detection: ![alt](url)
            img_match = re.match(r"^!\[(.*?)\]\((.*?)\)$", stripped)
            if img_match:
                alt_text = img_match.group(1)
                img_path = img_match.group(2)
                element = ParsedElement(
                    element_id=generate_element_id(doc_id, elem_idx),
                    content=f"[IMAGE: {alt_text}]",
                    raw_content=stripped,
                    image_path=img_path,
                    metadata=ElementMetadata(
                        source_doc=file_name,
                        page_number=current_page,
                        element_index=elem_idx,
                        element_type="image",
                        confidence=1.0,
                        parent_header=current_parent_header,
                        section_path=_build_section_path(),
                        extra={"alt_text": alt_text, "image_source": img_path},
                    ),
                )
                elements.append(element)
                elem_idx += 1
                i += 1
                continue

            # 4. Markdown Table detection (| col1 | col2 |)
            if stripped.startswith("|") and stripped.endswith("|"):
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1

                table_content = "\n".join(table_lines)
                num_rows = len(table_lines)
                # Count columns by splitting first row
                cols = [c.strip() for c in table_lines[0].split("|")[1:-1]]
                num_cols = len(cols)

                element = ParsedElement(
                    element_id=generate_element_id(doc_id, elem_idx),
                    content=table_content,
                    raw_content=table_content,
                    metadata=ElementMetadata(
                        source_doc=file_name,
                        page_number=current_page,
                        element_index=elem_idx,
                        element_type="table",
                        confidence=1.0,
                        parent_header=current_parent_header,
                        section_path=_build_section_path(),
                        extra={
                            "row_count": num_rows,
                            "col_count": num_cols,
                            "headers": cols,
                        },
                    ),
                )
                elements.append(element)
                elem_idx += 1
                continue

            # 5. Header detection (# Header)
            header_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if header_match:
                level = len(header_match.group(1))
                header_text = header_match.group(2)
                _update_header_stack(level, header_text)

                element = ParsedElement(
                    element_id=generate_element_id(doc_id, elem_idx),
                    content=header_text,
                    raw_content=stripped,
                    metadata=ElementMetadata(
                        source_doc=file_name,
                        page_number=current_page,
                        element_index=elem_idx,
                        element_type="header",
                        confidence=1.0,
                        header_level=level,
                        parent_header=current_parent_header,
                        section_path=_build_section_path(),
                        extra={"header_level": level},
                    ),
                )
                elements.append(element)
                elem_idx += 1
                i += 1
                continue

            # 6. List element detection (- item, * item, 1. item)
            list_match = re.match(r"^(\*|-|\d+\.)\s+(.*)$", stripped)
            if list_match:
                list_lines = [stripped]
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip()
                    if re.match(r"^(\*|-|\d+\.)\s+(.*)$", next_line):
                        list_lines.append(next_line)
                        i += 1
                    elif next_line and not (next_line.startswith("#") or next_line.startswith("|") or next_line.startswith("```")):
                        # Continuation of list item
                        list_lines.append(next_line)
                        i += 1
                    else:
                        break

                list_content = "\n".join(list_lines)
                element = ParsedElement(
                    element_id=generate_element_id(doc_id, elem_idx),
                    content=list_content,
                    raw_content=list_content,
                    metadata=ElementMetadata(
                        source_doc=file_name,
                        page_number=current_page,
                        element_index=elem_idx,
                        element_type="list",
                        confidence=1.0,
                        parent_header=current_parent_header,
                        section_path=_build_section_path(),
                        extra={"item_count": len(list_lines)},
                    ),
                )
                elements.append(element)
                elem_idx += 1
                continue

            # 7. Standard Paragraph / Text element
            para_lines = [stripped]
            i += 1
            while i < len(lines):
                next_stripped = lines[i].strip()
                if not next_stripped:
                    break
                # If next line starts a new block type, break
                if (
                    next_stripped.startswith("#")
                    or next_stripped.startswith("```")
                    or next_stripped.startswith("![")
                    or (next_stripped.startswith("|") and next_stripped.endswith("|"))
                    or re.match(r"^(\*|-|\d+\.)\s+", next_stripped)
                ):
                    break
                para_lines.append(next_stripped)
                i += 1

            para_text = " ".join(para_lines)
            element = ParsedElement(
                element_id=generate_element_id(doc_id, elem_idx),
                content=para_text,
                raw_content="\n".join(para_lines),
                metadata=ElementMetadata(
                    source_doc=file_name,
                    page_number=current_page,
                    element_index=elem_idx,
                    element_type="text",
                    confidence=1.0,
                    parent_header=current_parent_header,
                    section_path=_build_section_path(),
                    extra={"word_count": len(para_text.split())},
                ),
            )
            elements.append(element)
            elem_idx += 1

        return ParsedDocument(
            document_id=doc_id,
            file_name=file_name,
            file_type="markdown",
            total_pages=current_page,
            elements=elements,
            doc_metadata={
                "element_count": len(elements),
                "has_tables": any(e.metadata.element_type == "table" for e in elements),
                "has_images": any(e.metadata.element_type == "image" for e in elements),
            },
        )

    def parse_text(
        self,
        text: str,
        file_name: str = "document.txt",
        doc_id: Optional[str] = None,
    ) -> ParsedDocument:
        """Parse raw text file into structured paragraph elements."""
        doc_id = doc_id or hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

        elements: List[ParsedElement] = []
        for idx, p in enumerate(paragraphs):
            element = ParsedElement(
                element_id=f"{doc_id}-elem-{idx:04d}",
                content=p,
                raw_content=p,
                metadata=ElementMetadata(
                    source_doc=file_name,
                    page_number=1,
                    element_index=idx,
                    element_type="text",
                    confidence=1.0,
                    extra={"word_count": len(p.split())},
                ),
            )
            elements.append(element)

        return ParsedDocument(
            document_id=doc_id,
            file_name=file_name,
            file_type="txt",
            total_pages=1,
            elements=elements,
            doc_metadata={"element_count": len(elements)},
        )

    def parse_code(
        self,
        code_text: str,
        file_name: str = "source.txt",
        doc_id: Optional[str] = None,
        language: Optional[str] = None,
    ) -> ParsedDocument:
        """Parse a source file as one typed code element with provenance."""
        file_type = os.path.splitext(file_name)[1].lower().lstrip(".") or "text"
        doc_id = doc_id or hashlib.sha256(code_text.encode("utf-8")).hexdigest()[:16]
        content = code_text or "[EMPTY CODE FILE]"
        element = ParsedElement(
            element_id=generate_element_id(doc_id, 0),
            content=content,
            raw_content=code_text,
            metadata=ElementMetadata(
                source_doc=file_name,
                page_number=1,
                element_index=0,
                element_type="code",
                confidence=1.0,
                extra={
                    "language": language or file_type,
                    "char_count": len(code_text),
                },
            ),
        )
        return ParsedDocument(
            document_id=doc_id,
            file_name=file_name,
            file_type=file_type,
            total_pages=1,
            elements=[element],
            doc_metadata={"element_count": 1, "language": language or file_type},
        )

    def parse_csv(
        self,
        csv_text: str,
        file_name: str = "data.csv",
        doc_id: Optional[str] = None,
        delimiter: str = ",",
    ) -> ParsedDocument:
        """Parse CSV or TSV spreadsheet data into structured table elements with rich metadata."""
        import csv
        import io

        doc_id = doc_id or hashlib.sha256(csv_text.encode("utf-8")).hexdigest()[:16]
        file_type = os.path.splitext(file_name)[1].lower().lstrip(".") or "csv"
        f = io.StringIO(csv_text.strip())
        reader = csv.reader(f, delimiter=delimiter)
        rows = [row for row in reader if any(cell.strip() for cell in row)]

        if not rows:
            return ParsedDocument(
                document_id=doc_id,
                file_name=file_name,
                file_type=file_type,
                total_pages=1,
                elements=[],
                doc_metadata={"element_count": 0, "has_tables": False, "has_images": False},
            )

        headers = [c.strip() for c in rows[0]]
        md_lines = ["| " + " | ".join(headers) + " |"]
        md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        for row in rows[1:]:
            cells = [c.strip() for c in row]
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            else:
                cells = cells[:len(headers)]
            md_lines.append("| " + " | ".join(cells) + " |")

        table_content = "\n".join(md_lines)
        element = ParsedElement(
            element_id=generate_element_id(doc_id, 0),
            content=table_content,
            raw_content=csv_text,
            metadata=ElementMetadata(
                source_doc=file_name,
                page_number=1,
                element_index=0,
                element_type="table",
                confidence=1.0,
                extra={
                    "row_count": len(rows),
                    "col_count": len(headers),
                    "headers": headers,
                    "sheet_name": "Sheet1",
                },
            ),
        )

        return ParsedDocument(
            document_id=doc_id,
            file_name=file_name,
            file_type=file_type,
            total_pages=1,
            elements=[element],
            doc_metadata={
                "element_count": 1,
                "has_tables": True,
                "has_images": False,
            },
        )

    def parse_pdf(
        self,
        file_path: str,
        file_name: Optional[str] = None,
        doc_id: Optional[str] = None,
    ) -> ParsedDocument:
        """Parse PDF document with page-by-page extraction and spatial metadata."""
        file_name = file_name or os.path.basename(file_path)
        doc_id = doc_id or hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:16]
        elements: List[ParsedElement] = []
        elem_idx = 0
        total_pages = 1

        try:
            import pypdf
            reader = pypdf.PdfReader(file_path)
            total_pages = len(reader.pages)

            for page_num, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                paragraphs = [p.strip() for p in re.split(r"\n\s*\n", page_text) if p.strip()]

                for p in paragraphs:
                    # Basic heading heuristic
                    is_header = len(p) < 60 and not p.endswith(".")
                    elem_type: ElementType = "header" if is_header else "text"

                    element = ParsedElement(
                        element_id=f"{doc_id}-elem-{elem_idx:04d}",
                        content=p,
                        raw_content=p,
                        metadata=ElementMetadata(
                            source_doc=file_name,
                            page_number=page_num,
                            element_index=elem_idx,
                            element_type=elem_type,
                            bounding_box=BoundingBox(x1=0.0, y1=0.0, x2=1.0, y2=1.0),
                            confidence=0.92,
                            extra={"char_count": len(p)},
                        ),
                    )
                    elements.append(element)
                    elem_idx += 1

                # Detect embedded images per page
                for img_idx, img_obj in enumerate(page.images):
                    img_name = getattr(img_obj, "name", f"page_{page_num}_img_{img_idx}.png")
                    element = ParsedElement(
                        element_id=f"{doc_id}-elem-{elem_idx:04d}",
                        content=f"[PDF IMAGE on Page {page_num}: {img_name}]",
                        image_path=img_name,
                        metadata=ElementMetadata(
                            source_doc=file_name,
                            page_number=page_num,
                            element_index=elem_idx,
                            element_type="image",
                            bounding_box=BoundingBox(x1=0.1, y1=0.1, x2=0.9, y2=0.9),
                            confidence=0.95,
                            extra={"image_index": img_idx, "name": img_name},
                        ),
                    )
                    elements.append(element)
                    elem_idx += 1

        except Exception as exc:
            logger.warning(f"pypdf extraction failed or not found ({exc}). Using text parsing fallback.")
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_text = f.read()
            fallback_document = self.parse_text(raw_text, file_name=file_name, doc_id=doc_id)
            fallback_document.file_type = "pdf"
            fallback_document.doc_metadata.update({"parser": "text_fallback"})
            return fallback_document

        return ParsedDocument(
            document_id=doc_id,
            file_name=file_name,
            file_type="pdf",
            total_pages=total_pages,
            elements=elements,
            doc_metadata={
                "element_count": len(elements),
                "total_pages": total_pages,
            },
        )


__all__ = ["DocumentLayoutParser"]
