"""Unit tests for the MinerU API boundary and output normalization."""

import json
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

from src.core.exceptions import MinerUError
from src.ingestion.parser.mineru_adapter import MinerUAdapter


class FakeMinerUClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def post(self, url, *, files, data, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "files": files,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if self.error:
            raise self.error
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=self.payload, request=request)


class RetryOnceMinerUClient(FakeMinerUClient):
    def __init__(self, payload):
        super().__init__(payload=payload)
        self.attempts = 0

    def post(self, url, *, files, data, headers, timeout):
        self.attempts += 1
        if self.attempts == 1:
            raise httpx.ConnectError("temporary MinerU outage")
        return super().post(
            url,
            files=files,
            data=data,
            headers=headers,
            timeout=timeout,
        )


def _payload(fixture_dir: Path) -> dict:
    return {
        "backend": "pipeline",
        "version": "3.0.9",
        "results": {
            "sample.pdf": {
                "md_content": (fixture_dir / "sample.md").read_text(encoding="utf-8"),
                "content_list": json.loads(
                    (fixture_dir / "content_list.json").read_text(encoding="utf-8")
                ),
            }
        },
    }


def test_mineru_api_response_maps_content_list_to_parsed_document(tmp_path):
    fixture_dir = Path(__file__).parent / "fixtures" / "mineru"
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"mineru fixture source")
    client = FakeMinerUClient(payload=_payload(fixture_dir))

    document = MinerUAdapter(client=client, max_retries=0).parse_file(str(source))

    assert document.file_type == "pdf"
    assert document.doc_metadata["engine"] == "mineru"
    assert document.doc_metadata["mineru_backend"] == "pipeline"
    assert document.elements[0].metadata.element_type == "header"
    assert document.elements[0].metadata.bounding_box.x1 == pytest.approx(0.1)
    assert document.elements[2].metadata.element_type == "table"
    assert document.elements[2].content.startswith("| Key | Value |")
    assert document.elements[3].vlm_caption == "A fixture image."
    form_data = client.calls[0]["data"]
    assert form_data["backend"] == "pipeline"
    assert form_data["return_md"] == "true"
    assert form_data["return_content_list"] == "true"
    assert form_data["response_format_zip"] == "false"


def test_pdf_mineru_failure_falls_back_to_native_pypdf(tmp_path):
    source = tmp_path / "fallback.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with source.open("wb") as file_handle:
        writer.write(file_handle)
    client = FakeMinerUClient(error=httpx.ConnectError("MinerU offline"))

    document = MinerUAdapter(client=client, max_retries=0).parse_file(str(source))

    assert document.file_type == "pdf"
    assert document.doc_metadata["engine"] == "pypdf_fallback"
    assert document.doc_metadata["mineru_backend"] == "pipeline"
    assert "MinerU API request failed" in document.doc_metadata["mineru_error"]


def test_transient_mineru_failure_is_retried(tmp_path):
    fixture_dir = Path(__file__).parent / "fixtures" / "mineru"
    source = tmp_path / "retry.pdf"
    source.write_bytes(b"mineru retry fixture")
    client = RetryOnceMinerUClient(payload=_payload(fixture_dir))

    document = MinerUAdapter(
        client=client,
        max_retries=1,
        retry_backoff_seconds=0,
    ).parse_file(str(source))

    assert client.attempts == 2
    assert document.doc_metadata["engine"] == "mineru"


@pytest.mark.parametrize("extension", ["docx", "png"])
def test_office_and_image_mineru_failure_is_fail_fast(tmp_path, extension):
    source = tmp_path / f"failed.{extension}"
    source.write_bytes(b"fixture")
    client = FakeMinerUClient(error=httpx.ConnectError("MinerU offline"))

    with pytest.raises(MinerUError, match="MinerU is required"):
        MinerUAdapter(client=client, max_retries=0).parse_file(str(source))
