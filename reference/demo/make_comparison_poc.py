#!/usr/bin/env python3
"""
Build BEFORE / AFTER figures for client demos:
  left  = the identified X-ray as exported by the centre (viewer overlay + header + report, with fake PHI)
  right = the same X-ray after the pipeline (coded IDs only; image and finding unchanged)

Reads the pairs from output/ledger.csv, so run deid_pipeline.py first.
Finding markers are drawn for the demo only (the released DICOM pixels are untouched).
"""
import csv, textwrap
from pathlib import Path

import numpy as np
import pydicom
from PIL import Image, ImageDraw, ImageFont
from pydicom.pixels import apply_modality_lut, apply_voi_lut

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "comparison"
F = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FM = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
font = lambda p, s: ImageFont.truetype(p, s) if Path(p).exists() else ImageFont.load_default()

# Demo cases: inbox file -> finding markers in ORIGINAL pixel coordinates (x, y, radius) or boxes
CASES = {
    "CR_export_0923/IMG0001": {
        "title": "Chest X-ray (PA)",
        "finding": "Finding: multiple bilateral rounded pulmonary nodules",
        "circles": [(460, 1135, 115), (1468, 848, 95), (1418, 1152, 90), (1343, 653, 80)],
        "report": "CR_export_0923/report.txt",
    },
    "misc/leg.dcm": {
        "title": "Right leg X-ray (AP)",
        "finding": "Finding: non-ossifying fibroma, distal tibia",
        "boxes": [(760, 970, 950, 1250)],
        "report": "misc/ACC-60990.txt",
    },
}
NAVY, SLATE, RED, GREEN, AMBER = (28, 43, 58), (51, 65, 85), (176, 58, 46), (46, 125, 90), (255, 196, 61)


def render(ds, size=760):
    a = ds.pixel_array
    a = apply_voi_lut(apply_modality_lut(a, ds), ds) if "WindowCenter" in ds else a
    a = a.astype(np.float32)
    lo, hi = np.percentile(a, 1), np.percentile(a, 99)
    a = np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1) * 255
    if str(ds.PhotometricInterpretation) == "MONOCHROME1":
        a = 255 - a
    im = Image.fromarray(a.astype(np.uint8)).convert("RGB")
    scale = size / max(im.size)
    return im.resize((int(im.width * scale), int(im.height * scale))), scale


def draw_findings(im, scale, case):
    d = ImageDraw.Draw(im)
    for x, y, r in case.get("circles", []):
        x, y, r = x * scale, y * scale, r * scale
        d.ellipse([x - r, y - r, x + r, y + r], outline=AMBER, width=3)
    for x0, y0, x1, y1 in case.get("boxes", []):
        d.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale], outline=AMBER, width=3)


def overlay(im, corners):
    d = ImageDraw.Draw(im)
    f = font(F, 15)
    W, H = im.size
    for pos, lines in corners.items():
        for i, t in enumerate(lines):
            tw = d.textlength(t, font=f)
            x = 10 if "l" in pos else W - tw - 10
            y = 10 + i * 20 if "t" in pos else H - 10 - (len(lines) - i) * 20
            d.rectangle([x - 3, y - 1, x + tw + 3, y + 18], fill=(0, 0, 0))
            d.text((x, y), t, font=f, fill=(255, 255, 255))


def g(ds, kw, default="-"):
    v = ds.get(kw, None)
    return str(v) if v not in (None, "") else default


def fmt_date(s):
    return f"{s[6:8]}/{s[4:6]}/{s[0:4]}" if len(s) == 8 and s.isdigit() else s


def panel(ds, case, identified, report_text):
    img, scale = render(ds)
    draw_findings(img, scale, case)
    if identified:
        corners = {
            "tl": [g(ds, "PatientName").replace("^", " "), f"ID: {g(ds, 'PatientID')}",
                   f"DOB: {fmt_date(g(ds, 'PatientBirthDate'))}  Sex: {g(ds, 'PatientSex')}"],
            "tr": [g(ds, "InstitutionName"), f"Acc: {g(ds, 'AccessionNumber')}", f"Date: {fmt_date(g(ds, 'StudyDate'))}"],
            "bl": [f"Ref: {g(ds, 'ReferringPhysicianName').replace('^', ' ')}", f"Tel: {g(ds, 'PatientTelephoneNumbers')}"],
            "br": [f"{g(ds, 'Modality')}  {g(ds, 'ViewPosition', '')}", g(ds, "StudyDescription")],
        }
    else:
        corners = {
            "tl": [f"Patient: {g(ds, 'PatientID')}", f"Age: {g(ds, 'PatientAge')}  Sex: {g(ds, 'PatientSex')}"],
            "tr": [f"Study: {g(ds, 'AccessionNumber')}", f"Date (shifted): {fmt_date(g(ds, 'StudyDate'))}"],
            "bl": [f"Identity removed: {g(ds, 'PatientIdentityRemoved')}"],
            "br": [f"{g(ds, 'Modality')}  {g(ds, 'ViewPosition', '')}", g(ds, "StudyDescription")],
        }
    overlay(img, corners)

    private = sum(1 for el in ds if el.tag.is_private)
    rows = [
        ("Patient name", g(ds, "PatientName").replace("^", " ")),
        ("Patient ID / UHID", g(ds, "PatientID")),
        ("Date of birth", fmt_date(g(ds, "PatientBirthDate"))),
        ("Age / Sex", f"{g(ds, 'PatientAge', 'from DOB')} / {g(ds, 'PatientSex')}"),
        ("Address", g(ds, "PatientAddress")),
        ("Phone", g(ds, "PatientTelephoneNumbers")),
        ("Institution", g(ds, "InstitutionName")),
        ("Referring doctor", g(ds, "ReferringPhysicianName").replace("^", " ")),
        ("Accession no.", g(ds, "AccessionNumber")),
        ("Study date", fmt_date(g(ds, "StudyDate"))),
        ("Study UID", g(ds, "StudyInstanceUID")[:34] + "…"),
        ("Header fields / private", f"{len(ds)} / {private}"),
    ]
    return img, rows, report_text


def compose(case, before, after, out_png):
    W = 1640
    img_b, rows_b, rep_b = before
    img_a, rows_a, rep_a = after
    ih = max(img_b.height, img_a.height)
    H = 110 + ih + 30 + 12 * 26 + 40 + 20 * 19 + 60
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, W, 56], fill=NAVY)
    d.text((24, 14), f"{case['title']}: what de-identification changes", font=font(FB, 24), fill=(255, 255, 255))
    d.text((W - 24 - d.textlength(case["finding"], font=font(F, 17)), 19), case["finding"], font=font(F, 17), fill=AMBER)
    col = [20, W // 2 + 10]
    for i, (lab, colr) in enumerate([("BEFORE: identified, as exported by the centre", RED),
                                     ("AFTER: de-identified, as released to an AI buyer", GREEN)]):
        d.rectangle([col[i], 68, col[i] + 800, 100], fill=colr)
        d.text((col[i] + 12, 73), lab, font=font(FB, 18), fill=(255, 255, 255))
    y0 = 108
    for i, im in enumerate([img_b, img_a]):
        canvas.paste(Image.new("RGB", (800, ih), (0, 0, 0)), (col[i], y0))
        canvas.paste(im, (col[i] + (800 - im.width) // 2, y0 + (ih - im.height) // 2))
    y = y0 + ih + 20
    d.text((col[0], y), "DICOM header (key fields)", font=font(FB, 16), fill=SLATE)
    d.text((col[1], y), "DICOM header (key fields)", font=font(FB, 16), fill=SLATE)
    y += 28
    fm = font(FM, 14)
    for (k, vb), (_, va) in zip(rows_b, rows_a):
        changed = vb != va
        d.text((col[0], y), f"{k:<24}", font=fm, fill=SLATE)
        d.text((col[0] + 230, y), vb[:52], font=fm, fill=RED if changed else SLATE)
        d.text((col[1], y), f"{k:<24}", font=fm, fill=SLATE)
        d.text((col[1] + 230, y), va[:52], font=fm, fill=GREEN if changed else SLATE)
        y += 26
    y += 12
    d.text((col[0], y), "Report (first lines)", font=font(FB, 16), fill=SLATE)
    d.text((col[1], y), "Report (first lines)", font=font(FB, 16), fill=SLATE)
    y += 26
    for i, rep in enumerate([rep_b, rep_a]):
        lines = []
        for ln in rep.splitlines():
            lines += textwrap.wrap(ln, 88) or [""]
        for j, ln in enumerate(lines[:20]):
            d.text((col[i], y + j * 19), ln, font=font(FM, 13), fill=(31, 41, 51))
    d.text((20, H - 32), "Demo only: real public NEMA test radiographs carrying FAKE patient details; reports are fictional. "
           "Amber marks show the finding and are not written into the released image.", font=font(F, 13), fill=SLATE)
    canvas.save(out_png)
    return out_png


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / "output" / "ledger.csv") as f:
        ledger = {r["source"]: r for r in csv.DictReader(f)}
    made = []
    for src, case in CASES.items():
        row = ledger.get(src)
        if not row or not row["output"].startswith("dataset/"):
            print(f"skip {src}: not in ledger as released"); continue
        b = pydicom.dcmread(ROOT / "inbox" / src)
        a = pydicom.dcmread(ROOT / "output" / row["output"])
        rep_b = (ROOT / "inbox" / case["report"]).read_text()
        rep_a_path = ROOT / "output" / Path(row["output"]).parents[1] / "report.txt"
        rep_a = rep_a_path.read_text() if rep_a_path.exists() else "(no report)"
        name = Path(src).stem.lower()
        out = compose(case, panel(b, case, True, rep_b), panel(a, case, False, rep_a), OUT / f"before_after_{name}.png")
        made.append(out); print(out)
    return made


if __name__ == "__main__":
    main()
