# Reference prototype (tested)

This is the prototype that proves the de-identification rules. The new app reuses its logic but uses the ID and file-naming scheme in `docs/SPEC.md` §3.

## Run the end-to-end test
**bash**
```bash
cd reference
python3 -m venv .venv && source .venv/bin/activate
pip install pydicom numpy Pillow python-docx pylibjpeg pylibjpeg-libjpeg pylibjpeg-openjpeg \
            rapidocr-onnxruntime presidio-analyzer pypdfium2 reportlab
python -m spacy download en_core_web_sm
python test_end_to_end.py
```
**PowerShell**
```powershell
cd reference
py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install pydicom numpy Pillow python-docx pylibjpeg pylibjpeg-libjpeg pylibjpeg-openjpeg `
            rapidocr-onnxruntime presidio-analyzer pypdfium2 reportlab
python -m spacy download en_core_web_sm
python test_end_to_end.py
```
Expected result: `ALL CHECKS PASSED`. That means 4 records and 4 reports (txt, docx, text PDF, scanned PDF read by OCR), no identifiers in headers, reports, JSON, CSV or OCR of the pixels, and planted leaks caught by QA.

## Contents
- `deid_prototype/core.py`: allowlist, HMAC IDs, UID remap, dates and age, report rules, `read_report_ex()` (txt/docx/pdf)
- `deid_prototype/ocr_mask.py`: burned-in text masking
- `deid_prototype/text_ner.py`: Presidio name/place pass
- `deid_prototype/engine.py`: study-by-study loop (prototype output layout)
- `make_sample_data.py`, `samples/`: sample inbox (public NEMA test X-rays + fake Indian patient details). Demo and test only.
- `demo/make_comparison_poc.py`: before/after figure script from the batch PoC. It expects the old PoC layout and is ported in Phase 7.
