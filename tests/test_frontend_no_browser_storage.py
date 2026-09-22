"""The frontend must keep UI and auth state in React memory only."""

from pathlib import Path


WEB_SRC = Path(__file__).parents[1] / "web" / "src"


def test_frontend_has_no_browser_storage_or_legacy_storage_helper() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in WEB_SRC.rglob("*")
        if path.suffix in {".ts", ".tsx"}
    )
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert not (WEB_SRC / "sessionState.ts").exists()
