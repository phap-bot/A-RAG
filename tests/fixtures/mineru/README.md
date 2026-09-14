# MinerU integration fixtures

These small fixtures represent the direct JSON contract requested from the
local `mineru-api` endpoint:

- `content_list.json`: representative title, text, table, image, and equation
  blocks with MinerU's 0–1000 bounding-box coordinates.
- `sample.md`: generated Markdown companion output.
- `manifest.json`: expected backend, output names, and the recommended source
  matrix used by the integration policy.

The integration tests generate the input files in temporary directories so
the repository does not carry large binary Office/PDF/image samples. Before a
real deployment, add one representative born-digital PDF, scanned PDF, DOCX,
XLSX, PPTX, and image from the target domain and record the MinerU version and
backend used to produce their output.
