"""MinerU API adapter and strict ``ParsedDocument`` normalizer.

The adapter intentionally isolates the external MinerU contract from the
project contract. MinerU may return a direct JSON response or a ZIP archive;
both representations are normalized into the same Pydantic models used by the
rest of Zone 1.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from html.parser import HTMLParser
import json
import mimetypes
import os
from pathlib import Path
import time
from typing import Any
from zipfile import ZipFile
from io import BytesIO

import httpx

from src.core.config import logger, settings
from src.core.exceptions import MinerUError
from src.ingestion.parser.layout_parser import DocumentLayoutParser
from src.ingestion.parser.models import (
    BoundingBox,
    ElementMetadata,
    ParsedDocument,
    ParsedElement,
    generate_doc_id,
    generate_element_id,
)


MINERU_SUPPORTED_EXTENSIONS = frozenset(
    {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "png",
        "jpg",
        "jpeg",
        "jp2",
        "webp",
        "gif",
        "bmp",
    }
)

MINERU_OFFICE_IMAGE_EXTENSIONS = frozenset(
    {
        "docx",
        "pptx",
        "xlsx",
        "png",
        "jpg",
        "jpeg",
        "jp2",
        "webp",
        "gif",
        "bmp",
    }
)

_TYPE_MAP = {
    "title": "header",
    "doc_title": "header",
    "section_header": "header",
    "header": "header",
    "text": "text",
    "paragraph": "text",
    "table": "table",
    "table_body": "table",
    "image": "image",
    "image_body": "image",
    "chart": "chart",
    "chart_body": "chart",
    "code": "code",
    "code_body": "code",
    "algorithm": "code",
    "list": "list",
    "equation": "equation",
    "interline_equation": "equation",
    "inline_equation": "equation",
    "image_caption": "text",
    "table_caption": "text",
    "chart_caption": "text",
}


class _MinerUTableHTMLParser(HTMLParser):
    """Extract basic row/cell text from MinerU's HTML table_body output."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._current_row is not None:
            value = " ".join("".join(self._current_cell or []).split())
            self._current_row.append(value)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


def _html_table_to_markdown(value: str) -> str:
    """Convert MinerU's HTML table body to a conservative Markdown table."""
    if "<table" not in value.lower():
        return value.strip()
    parser = _MinerUTableHTMLParser()
    parser.feed(value)
    if not parser.rows:
        return value.strip()

    width = max(len(row) for row in parser.rows)
    rows = [row + [""] * (width - len(row)) for row in parser.rows]
    markdown_rows = ["| " + " | ".join(rows[0]) + " |"]
    markdown_rows.append("| " + " | ".join(["---"] * width) + " |")
    markdown_rows.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(markdown_rows)


class MinerUAdapter:
    """Call the self-hosted MinerU API and normalize its output.

    The default request uses the synchronous ``/file_parse`` endpoint with
    ``backend=pipeline``, ``return_md=true``,
    ``return_content_list=true``, and ``response_format_zip=false``. A PDF
    service failure falls back to the native pypdf parser. Office and image
    failures are raised immediately by design.

    Args:
        base_url: MinerU API base URL, normally ``http://localhost:8000``.
        api_key: Optional bearer token.
        timeout_seconds: Total HTTP request timeout.
        max_retries: Retry count for transport/5xx failures.
        retry_backoff_seconds: Delay multiplier between retries.
        client: Optional injected httpx-compatible client for tests.
    """

    endpoint = "/file_parse"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
        client: Any | None = None,
    ) -> None:
        self.base_url = (base_url or settings.mineru_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.mineru_api_key
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.mineru_timeout_seconds
        )
        self.max_retries = (
            max_retries if max_retries is not None else settings.mineru_max_retries
        )
        self.retry_backoff_seconds = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else settings.mineru_retry_backoff_seconds
        )
        self._client = client

    def parse_file(
        self,
        file_path: str,
        backend: str | None = None,
    ) -> ParsedDocument:
        """Parse one supported file through MinerU and return ``ParsedDocument``.

        PDFs use the native parser only when the MinerU request or response
        fails. Office and image files never silently fall back to text or a
        weaker parser.
        """
        path = Path(file_path)
        if not path.is_file():
            raise MinerUError(
                f"MinerU source file does not exist: {file_path}",
                details={"file_path": file_path},
            )

        extension = path.suffix.lower().lstrip(".")
        if extension not in MINERU_SUPPORTED_EXTENSIONS:
            raise MinerUError(
                f"MinerU API does not support '.{extension}' in this integration",
                details={"file_path": file_path, "extension": extension},
            )

        selected_backend = backend or settings.mineru_backend
        if not selected_backend.strip():
            raise MinerUError("MinerU backend cannot be empty")

        try:
            response = self._post_file(path, selected_backend)
            return self._parse_response(
                response=response,
                file_path=path,
                backend=selected_backend,
            )
        except MinerUError as exc:
            return self._handle_mineru_failure(
                path=path,
                extension=extension,
                backend=selected_backend,
                error=exc,
            )
        except Exception as exc:
            # A malformed response/archive is still a MinerU boundary failure.
            # Convert it here so PDF fallback and Office/Image fail-fast remain
            # deterministic instead of leaking implementation exceptions.
            boundary_error = MinerUError(
                f"MinerU response normalization failed: {exc}",
                details={
                    "file_path": str(path),
                    "extension": extension,
                    "backend": selected_backend,
                },
            )
            return self._handle_mineru_failure(
                path=path,
                extension=extension,
                backend=selected_backend,
                error=boundary_error,
            )

    def parse_json_result(
        self,
        payload: Mapping[str, Any] | list[Any],
        *,
        file_name: str = "document.pdf",
        source_bytes: bytes | None = None,
        backend: str | None = None,
    ) -> ParsedDocument:
        """Normalize a complete MinerU Web/JSON export into ``ParsedDocument``.

        MinerU Web commonly returns ``{"pdf_info": [{"preproc_blocks": [...]}]}``
        while the API may return ``content_list`` or a ``results`` envelope.
        This method accepts all three without making Web JSON the downstream
        contract. The original block structure is retained in element metadata.

        ``source_bytes`` should contain the original PDF bytes when available;
        otherwise the canonical JSON payload is used for a deterministic ID.
        """
        selected_backend = backend or settings.mineru_backend
        if isinstance(payload, list):
            result: Mapping[str, Any] = {"content_list": payload}
        elif isinstance(payload, Mapping) and "pdf_info" in payload:
            result = {
                "content_list": payload.get("pdf_info"),
                "version": payload.get("version") or payload.get("_version_name"),
            }
        else:
            result = self._select_result(payload, file_name)
        identity = source_bytes or json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._normalize_result(
            result=result,
            file_path=Path(file_name),
            backend=selected_backend,
            version=result.get("version"),
            source_bytes=identity,
        )

    def _handle_mineru_failure(
        self,
        path: Path,
        extension: str,
        backend: str,
        error: MinerUError,
    ) -> ParsedDocument:
        """Apply the explicit fallback policy after any MinerU boundary error."""
        if extension == "pdf":
            logger.warning(
                f"MinerU failed for PDF '{path.name}'; using native pypdf fallback: {error}"
            )
            try:
                document = DocumentLayoutParser().parse_file(str(path))
            except Exception as fallback_error:
                raise MinerUError(
                    f"MinerU failed and PDF pypdf fallback failed: {fallback_error}",
                    details={
                        "file_path": str(path),
                        "extension": extension,
                        "backend": backend,
                        "fallback": "pypdf",
                        "mineru_error": error.to_dict(),
                    },
                ) from fallback_error
            document.doc_metadata.update(
                {
                    "engine": "pypdf_fallback",
                    "mineru_backend": backend,
                    "mineru_error": str(error),
                    "mineru_error_details": error.details,
                }
            )
            return document

        # Explicit fail-fast policy for Office/Image sources.
        raise MinerUError(
            f"MinerU is required for '.{extension}' and failed: {error}",
            details={
                "file_path": str(path),
                "extension": extension,
                "backend": backend,
                "fallback": "none",
                "policy": "fail_fast",
                "cause": error.to_dict(),
            },
        ) from error

    def _post_file(self, path: Path, backend: str) -> httpx.Response:
        """Submit the multipart request with bounded transient retries."""
        url = f"{self.base_url}{self.endpoint}"
        form_data = self._build_form_data(backend)
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with path.open("rb") as file_handle:
                    files = {
                        "files": (
                            path.name,
                            file_handle,
                            mimetypes.guess_type(path.name)[0]
                            or "application/octet-stream",
                        )
                    }
                    if self._client is None:
                        with httpx.Client(
                            timeout=self.timeout_seconds,
                            follow_redirects=True,
                        ) as client:
                            response = client.post(
                                url,
                                files=files,
                                data=form_data,
                                headers=headers,
                            )
                    else:
                        response = self._client.post(
                            url,
                            files=files,
                            data=form_data,
                            headers=headers,
                            timeout=self.timeout_seconds,
                        )
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep_before_retry(attempt)
                    continue
                raise MinerUError(
                    f"MinerU API request failed after {attempt + 1} attempts: {exc}",
                    details={
                        "endpoint": url,
                        "attempts": attempt + 1,
                        "backend": backend,
                    },
                ) from exc

            if self._is_retryable_status(response.status_code) and attempt < self.max_retries:
                last_error = RuntimeError(
                    f"MinerU server error: {response.status_code}"
                )
                self._sleep_before_retry(attempt)
                continue

            if response.status_code >= 400:
                raise self._response_error(response, backend)
            return response

        # The loop always returns or raises; this protects static analyzers.
        raise MinerUError(
            f"MinerU API request failed: {last_error or 'unknown error'}",
            details={"endpoint": url, "backend": backend},
        )

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        """Identify transient HTTP statuses safe to retry for file parsing."""
        return status_code in {408, 425, 429} or status_code >= 500

    def _build_form_data(self, backend: str) -> dict[str, str]:
        """Build the recommended MinerU API output request."""
        return {
            "backend": backend,
            "parse_method": settings.mineru_parse_method,
            "return_md": "true",
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_content_list": "true",
            "return_images": "false",
            "response_format_zip": "false",
            "return_original_file": "false",
        }

    def _sleep_before_retry(self, attempt: int) -> None:
        """Apply bounded exponential backoff between transient attempts."""
        delay = self.retry_backoff_seconds * (2**attempt)
        if delay > 0:
            time.sleep(delay)

    def _response_error(self, response: httpx.Response, backend: str) -> MinerUError:
        """Convert an HTTP failure into a redacted structured error."""
        try:
            detail: Any = response.json()
        except ValueError:
            detail = response.text[:1000]
        return MinerUError(
            f"MinerU API returned HTTP {response.status_code}",
            details={
                "status_code": response.status_code,
                "backend": backend,
                "response": detail,
            },
        )

    def _parse_response(
        self,
        response: httpx.Response,
        file_path: Path,
        backend: str,
    ) -> ParsedDocument:
        """Parse direct JSON, inline ZIP, or ZIP URL API responses."""
        if self._is_zip_response(response):
            return self._parse_zip_bytes(
                response.content,
                file_path=file_path,
                backend=backend,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise MinerUError(
                "MinerU API returned neither JSON nor a ZIP archive",
                details={"content_type": response.headers.get("content-type")},
            ) from exc

        zip_url = self._find_zip_url(payload)
        if zip_url:
            zip_response = self._get_url(zip_url)
            if not self._is_zip_response(zip_response):
                raise MinerUError(
                    "MinerU ZIP result URL did not return a ZIP archive",
                    details={"url": zip_url},
                )
            return self._parse_zip_bytes(
                zip_response.content,
                file_path=file_path,
                backend=backend,
            )

        result = self._select_result(payload, file_path.name)
        version = payload.get("version") if isinstance(payload, Mapping) else None
        if version is None:
            version = result.get("version") or result.get("_version_name")
        return self._normalize_result(
            result=result,
            file_path=file_path,
            backend=backend,
            version=version,
        )

    @staticmethod
    def _is_zip_response(response: httpx.Response) -> bool:
        content_type = response.headers.get("content-type", "").lower()
        return "zip" in content_type or response.content[:2] == b"PK"

    def _get_url(self, url: str) -> httpx.Response:
        """Download a remote ZIP result using the configured HTTP client."""
        headers = {"Accept": "application/zip"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            if self._client is None:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    response = client.get(url, headers=headers)
            else:
                response = self._client.get(
                    url,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
        except httpx.HTTPError as exc:
            raise MinerUError(
                f"MinerU result download failed: {exc}", details={"url": url}
            ) from exc
        if response.status_code >= 400:
            raise MinerUError(
                f"MinerU result download returned HTTP {response.status_code}",
                details={"url": url, "status_code": response.status_code},
            )
        return response

    @staticmethod
    def _find_zip_url(payload: Any) -> str | None:
        if not isinstance(payload, Mapping):
            return None
        for key in ("full_zip_url", "zip_url", "result_url"):
            value = payload.get(key)
            if isinstance(value, str):
                clean_value = value.split("?", 1)[0].split("#", 1)[0].lower()
                if clean_value.endswith((".zip", "/zip")):
                    return value
        for container_key in ("data", "result"):
            nested = payload.get(container_key)
            if isinstance(nested, Mapping):
                found = MinerUAdapter._find_zip_url(nested)
                if found:
                    return found
        return None

    @staticmethod
    def _select_result(payload: Any, file_name: str) -> Mapping[str, Any]:
        """Select the per-file result from MinerU's response envelope."""
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, Mapping):
                    continue
                item_name = item.get("file_name") or item.get("filename")
                if item_name == file_name or any(
                    key in item for key in ("content_list", "md_content", "markdown")
                ):
                    return item
            raise MinerUError(
                "MinerU response list did not include content_list or Markdown",
                details={"items": len(payload)},
            )
        if not isinstance(payload, Mapping):
            raise MinerUError("MinerU response payload must be a JSON object")
        if payload.get("error"):
            raise MinerUError(
                f"MinerU API reported an error: {payload['error']}",
                details={"response": payload.get("error")},
            )

        # MinerU Web/CLI export shape: one page wrapper per item in
        # ``pdf_info``, containing ``preproc_blocks``.
        if isinstance(payload.get("pdf_info"), list):
            return {"content_list": payload["pdf_info"], "version": payload.get("version")}

        for key in ("results", "data", "result"):
            container = payload.get(key)
            if isinstance(container, list):
                selected = MinerUAdapter._select_result(container, file_name)
                if selected:
                    return selected
            if isinstance(container, Mapping):
                if file_name in container and isinstance(container[file_name], Mapping):
                    return container[file_name]
                if any(
                    candidate in container
                    for candidate in ("content_list", "md_content", "markdown")
                ):
                    return container
                for value in container.values():
                    if isinstance(value, Mapping) and (
                        "content_list" in value
                        or "md_content" in value
                        or "markdown" in value
                    ):
                        return value
        if any(key in payload for key in ("content_list", "md_content", "markdown")):
            return payload
        raise MinerUError(
            "MinerU response did not include content_list or Markdown",
            details={"keys": list(payload.keys())},
        )

    def _parse_zip_bytes(
        self,
        content: bytes,
        file_path: Path,
        backend: str,
    ) -> ParsedDocument:
        """Safely extract a MinerU result ZIP and normalize its artifacts."""
        doc_id = generate_doc_id(file_path.read_bytes())
        artifact_root = Path(settings.mineru_artifact_dir) / doc_id
        artifact_root.mkdir(parents=True, exist_ok=True)
        self._safe_extract_zip(content, artifact_root)

        content_list_path = self._find_artifact(artifact_root, "content_list.json")
        markdown_path = self._find_markdown(artifact_root)
        if content_list_path is None and markdown_path is None:
            raise MinerUError(
                "MinerU ZIP did not contain content_list.json or Markdown",
                details={"artifact_root": str(artifact_root)},
            )

        content_list: Any = None
        if content_list_path is not None:
            content_list = self._read_json_file(content_list_path)
        markdown = (
            markdown_path.read_text(encoding="utf-8", errors="replace")
            if markdown_path is not None
            else None
        )
        return self._normalize_result(
            result={
                "content_list": content_list,
                "md_content": markdown,
            },
            file_path=file_path,
            backend=backend,
            version=None,
            artifact_root=artifact_root,
        )

    @staticmethod
    def _safe_extract_zip(content: bytes, target_root: Path) -> None:
        """Extract ZIP members without allowing path traversal."""
        resolved_root = target_root.resolve()
        with ZipFile(BytesIO(content)) as archive:
            for member in archive.infolist():
                member_path = (target_root / member.filename).resolve()
                if not member_path.is_relative_to(resolved_root):
                    raise MinerUError(
                        "MinerU ZIP contains an unsafe path",
                        details={"member": member.filename},
                    )
            archive.extractall(target_root)

    @staticmethod
    def _find_artifact(root: Path, file_name: str) -> Path | None:
        candidates = list(root.rglob(f"*_{file_name}")) + list(root.rglob(file_name))
        return candidates[0] if candidates else None

    @staticmethod
    def _find_markdown(root: Path) -> Path | None:
        candidates = [
            path
            for path in root.rglob("*.md")
            if not path.name.endswith(("_layout.md", "_span.md"))
        ]
        return candidates[0] if candidates else None

    @staticmethod
    def _read_json_file(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MinerUError(
                f"Cannot read MinerU JSON artifact: {path}",
                details={"path": str(path)},
            ) from exc

    def _normalize_result(
        self,
        result: Mapping[str, Any],
        file_path: Path,
        backend: str,
        version: Any = None,
        artifact_root: Path | None = None,
        source_bytes: bytes | None = None,
    ) -> ParsedDocument:
        """Map MinerU content blocks or Markdown to project models."""
        content_list = self._coerce_json(result.get("content_list"))
        markdown = self._coerce_markdown(
            result.get("md_content") or result.get("markdown") or result.get("md")
        )
        identity = source_bytes if source_bytes is not None else file_path.read_bytes()
        doc_id = generate_doc_id(identity)
        elements = self._normalize_blocks(
            content_list=content_list,
            file_name=file_path.name,
            doc_id=doc_id,
            backend=backend,
            version=version,
            artifact_root=artifact_root,
        )

        if not elements and markdown:
            document = DocumentLayoutParser().parse_markdown(
                markdown,
                file_name=file_path.name,
                doc_id=doc_id,
            )
        elif elements:
            total_pages = self._total_pages(result, elements)
            document = ParsedDocument(
                document_id=doc_id,
                file_name=file_path.name,
                file_type=file_path.suffix.lower().lstrip("."),
                total_pages=total_pages,
                elements=elements,
                doc_metadata={},
            )
        else:
            raise MinerUError(
                "MinerU response contained no usable content blocks or Markdown",
                details={"file_path": str(file_path), "backend": backend},
            )

        document.doc_metadata.update(
            {
                "engine": "mineru",
                "mineru_backend": backend,
                "mineru_version": version,
                "mineru_output": ["content_list.json", "markdown"],
            }
        )
        if artifact_root is not None:
            document.doc_metadata["mineru_artifact_root"] = str(artifact_root)
        return document

    @staticmethod
    def _coerce_json(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        if isinstance(value, Mapping) and "content_list" in value:
            return value["content_list"]
        return value

    @staticmethod
    def _coerce_markdown(value: Any) -> str | None:
        return value if isinstance(value, str) and value.strip() else None

    def _normalize_blocks(
        self,
        content_list: Any,
        file_name: str,
        doc_id: str,
        backend: str,
        version: Any,
        artifact_root: Path | None,
    ) -> list[ParsedElement]:
        if not isinstance(content_list, list):
            return []

        elements: list[ParsedElement] = []
        current_header: str | None = None
        section_path: list[str] = []
        header_stack: dict[int, str] = {}
        for block, page_hint in self._iter_blocks(content_list):
            if not isinstance(block, Mapping):
                continue
            original_type = str(block.get("type", "text")).lower()
            if original_type.startswith("discarded"):
                continue
            element_type = _TYPE_MAP.get(original_type, "text")
            if original_type == "text" and isinstance(block.get("text_level"), int):
                if block["text_level"] > 0:
                    element_type = "header"
            content = self._block_content(block, original_type, file_name)
            image_path = self._block_image_path(block, artifact_root)
            if not content:
                if element_type in {"image", "chart"}:
                    content = f"[IMAGE: {Path(image_path).name if image_path else file_name}]"
                else:
                    continue

            page_number = self._page_number(block, page_hint)
            metadata_extra = {
                "mineru_type": original_type,
                "mineru_backend": backend,
                "mineru_block": dict(block),
            }
            for key in ("sub_type", "text_level", "index", "angle", "level"):
                if key in block:
                    metadata_extra[key] = block[key]

            header_level = self._header_level(block, element_type)
            element_parent_header = section_path[-1] if section_path else None
            element_section_path = list(section_path)
            if element_type == "header":
                level = header_level or 1
                for old_level in list(header_stack):
                    if old_level >= level:
                        del header_stack[old_level]
                header_stack[level] = content
                element_section_path = [
                    header_stack[key] for key in sorted(header_stack)
                ]

            element = ParsedElement(
                element_id=generate_element_id(doc_id, len(elements)),
                content=content,
                raw_content=self._raw_block_content(block),
                image_path=image_path,
                vlm_caption=self._block_caption(block),
                metadata=ElementMetadata(
                    source_doc=file_name,
                    page_number=page_number,
                    element_index=len(elements),
                    element_type=element_type,
                    bounding_box=self._bounding_box(block.get("bbox")),
                    confidence=self._confidence(block),
                    parent_header=element_parent_header,
                    header_level=header_level,
                    section_path=element_section_path,
                    extra=metadata_extra,
                ),
            )
            elements.append(element)

            if element_type == "header":
                current_header = content
                section_path = element_section_path
        return elements

    @staticmethod
    def _iter_blocks(content_list: list[Any]) -> Iterator[tuple[Any, int | None]]:
        """Flatten direct blocks, ``blocks`` and Web ``preproc_blocks`` pages."""
        for index, item in enumerate(content_list):
            page_hint: int | None = None
            if isinstance(item, Mapping):
                if isinstance(item.get("page_idx"), int):
                    page_hint = item["page_idx"]
                elif isinstance(item.get("preproc_blocks"), list):
                    # ``pdf_info`` page wrappers often omit page_idx; their
                    # position in the array is the zero-based page hint.
                    page_hint = index
            yield from MinerUAdapter._iter_block_item(item, page_hint)

    @staticmethod
    def _iter_block_item(item: Any, page_hint: int | None) -> Iterator[tuple[Any, int | None]]:
        """Recursively flatten one MinerU wrapper without losing page identity."""
        if isinstance(item, list):
            for nested in item:
                yield from MinerUAdapter._iter_block_item(nested, page_hint)
            return
        if not isinstance(item, Mapping):
            return
        item_page = item.get("page_idx") if isinstance(item.get("page_idx"), int) else page_hint
        for key in ("preproc_blocks", "blocks"):
            nested_blocks = item.get(key)
            if isinstance(nested_blocks, list):
                for nested in nested_blocks:
                    yield from MinerUAdapter._iter_block_item(nested, item_page)
                return
        yield item, item_page

    @staticmethod
    def _block_content(block: Mapping[str, Any], original_type: str, file_name: str) -> str:
        keys = [
            "content",
            "text",
            "value",
            "title",
            "code_body",
            "latex",
            "equation",
            "table_body",
            "html",
            "list_items",
            "caption",
        ]
        if original_type in {"image", "image_body", "chart", "chart_body"}:
            keys = ["content", "text", "caption", "image_caption", "chart_caption"]
        for candidate in MinerUAdapter._nested_mappings(block):
            candidate_type = str(candidate.get("type", original_type)).lower()
            candidate_keys = keys
            if candidate_type in {"image", "image_body"}:
                candidate_keys = ["content", "text", "caption", "image_caption"]
            for key in candidate_keys:
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    content = value.strip()
                    if candidate_type in {"table", "table_body"} and key in {"table_body", "html"}:
                        return _html_table_to_markdown(content)
                    return content
                if isinstance(value, list) and value:
                    values = [str(item).strip() for item in value if str(item).strip()]
                    if values:
                        return "\n".join(values)
        return ""

    @staticmethod
    def _nested_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
        """Yield a block and all nested JSON mappings in document order."""
        if isinstance(value, Mapping):
            yield value
            for child in value.values():
                yield from MinerUAdapter._nested_mappings(child)
        elif isinstance(value, list):
            for child in value:
                yield from MinerUAdapter._nested_mappings(child)

    @staticmethod
    def _raw_block_content(block: Mapping[str, Any]) -> str | None:
        for candidate in MinerUAdapter._nested_mappings(block):
            for key in ("content", "text", "code_body", "table_body", "html"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return None

    @staticmethod
    def _block_image_path(block: Mapping[str, Any], artifact_root: Path | None) -> str | None:
        value: Any = None
        for candidate_block in MinerUAdapter._nested_mappings(block):
            value = candidate_block.get("img_path") or candidate_block.get("image_path")
            if isinstance(value, str) and value:
                break
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        if artifact_root is not None and not path.is_absolute():
            candidate = (artifact_root / path).resolve()
            if candidate.is_file():
                return str(candidate)
        return value

    @staticmethod
    def _block_caption(block: Mapping[str, Any]) -> str | None:
        for candidate_block in MinerUAdapter._nested_mappings(block):
            for key in ("image_caption", "chart_caption", "caption"):
                value = candidate_block.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, list):
                    captions = [str(item).strip() for item in value if str(item).strip()]
                    if captions:
                        return " ".join(captions)
        return None

    @staticmethod
    def _page_number(block: Mapping[str, Any], page_hint: int | None) -> int:
        value = block.get("page_number")
        if isinstance(value, int) and value >= 1:
            return value
        value = block.get("page_idx", page_hint)
        if isinstance(value, int):
            return value + 1
        return 1

    @staticmethod
    def _header_level(block: Mapping[str, Any], element_type: str) -> int | None:
        """Normalize MinerU ``level``/``text_level`` to the project range."""
        if element_type != "header":
            return None
        value = block.get("level", block.get("text_level", 1))
        try:
            return min(6, max(1, int(value)))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _confidence(block: Mapping[str, Any]) -> float:
        value: Any = block.get("score", block.get("confidence"))
        if value is None:
            for candidate in MinerUAdapter._nested_mappings(block):
                value = candidate.get("score", candidate.get("confidence"))
                if value is not None:
                    break
        if value is None:
            value = 1.0
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _bounding_box(value: Any) -> BoundingBox | None:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            coordinates = [float(item) for item in value]
        except (TypeError, ValueError):
            return None
        if max(abs(item) for item in coordinates) > 1.0:
            coordinates = [item / 1000.0 for item in coordinates]
        x1, y1, x2, y2 = coordinates
        x1, x2 = sorted((min(1.0, max(0.0, x1)), min(1.0, max(0.0, x2))))
        y1, y2 = sorted((min(1.0, max(0.0, y1)), min(1.0, max(0.0, y2))))
        return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)

    @staticmethod
    def _total_pages(result: Mapping[str, Any], elements: list[ParsedElement]) -> int:
        for key in ("total_pages", "page_count", "pages"):
            value = result.get(key)
            if isinstance(value, int) and value >= 1:
                return value
        return max((element.metadata.page_number for element in elements), default=1)


__all__ = [
    "MINERU_OFFICE_IMAGE_EXTENSIONS",
    "MINERU_SUPPORTED_EXTENSIONS",
    "MinerUAdapter",
]
