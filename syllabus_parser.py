import sys
import io
import json
import re
from pathlib import Path

import fitz
import pytesseract
from PIL import Image

# Windows Tesseract path configuration
import platform
if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

OCR_DPI = 300

# Bigger number = fewer, taller strips.
# Smaller number = more, shorter strips.
STRIP_HEIGHT_PX = 220

# Tesseract config:
# --psm 6 = assume a single uniform block of text
# preserve_interword_spaces helps keep spacing more stable
TESS_CONFIG = r"--oem 3 --psm 6 -c preserve_interword_spaces=1"


def clean_line(line: str) -> str:
    line = line.replace("\x00", " ")
    line = re.sub(r"[ \t]+", " ", line)
    return line.strip()


def normalize_lines(lines):
    cleaned = [clean_line(line) for line in lines]
    return [line for line in cleaned if line]


def render_page(page, dpi=OCR_DPI):
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def ocr_strip_words(strip_img):
    """
    OCR one horizontal strip and return words with positions.
    """
    data = pytesseract.image_to_data(
        strip_img,
        lang="eng",
        config=TESS_CONFIG,
        output_type=pytesseract.Output.DICT,
    )

    words = []
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        conf = data["conf"][i]

        try:
            conf_val = float(conf)
        except Exception:
            conf_val = -1

        if not text or conf_val < 0:
            continue

        words.append({
            "text": text,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
            "conf": conf_val,
            "line_num": data["line_num"][i],
            "block_num": data["block_num"][i],
            "par_num": data["par_num"][i],
        })

    return words


def words_to_lines(words, y_tolerance=18):
    """
    Build row-like lines by grouping words with similar y positions,
    then sorting left-to-right.
    """
    if not words:
        return []

    words = sorted(words, key=lambda w: (w["top"], w["left"]))

    rows = []
    current_row = [words[0]]
    current_y = words[0]["top"]

    for w in words[1:]:
        if abs(w["top"] - current_y) <= y_tolerance:
            current_row.append(w)
        else:
            rows.append(current_row)
            current_row = [w]
            current_y = w["top"]

    rows.append(current_row)

    lines = []
    for row in rows:
        row_sorted = sorted(row, key=lambda w: w["left"])
        line = " ".join(w["text"] for w in row_sorted)
        line = clean_line(line)
        if line:
            lines.append(line)

    return lines


def ocr_page_by_strips(page, dpi=OCR_DPI, strip_height=STRIP_HEIGHT_PX):
    """
    OCR a page strip-by-strip from top to bottom.
    This often works better for schedule tables laid out in rows.
    """
    img = render_page(page, dpi=dpi)
    width, height = img.size

    all_lines = []

    for y0 in range(0, height, strip_height):
        y1 = min(y0 + strip_height, height)
        strip = img.crop((0, y0, width, y1))

        words = ocr_strip_words(strip)
        lines = words_to_lines(words)

        all_lines.extend(lines)

    return normalize_lines(all_lines)


def extract_pdf_rows_first(pdf_path):
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"File not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    pages = []

    for i, page in enumerate(doc, start=1):
        lines = ocr_page_by_strips(page)

        pages.append({
            "page": i,
            "method": "ocr_row_strips",
            "lines": lines,
            "text": "\n".join(lines),
        })

    doc.close()

    full_text = "\n\n".join(
        f"--- Page {p['page']} ---\n{p['text']}"
        for p in pages
    )

    return {
        "source_file": str(pdf_path),
        "total_pages": len(pages),
        "pages": pages,
        "full_text": full_text,
    }


def parse_to_file(input_pdf: str, output_json: str) -> str:
    """
    This is the new callable function for main.py.
    It runs the extraction and saves the JSON file, returning the file path.
    """
    print(f"Starting strip-by-strip OCR on {input_pdf}...")
    result = extract_pdf_rows_first(input_pdf)

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Saved OCR output to {output_json}")
    return output_json


# this if for testing this particular script if we need it
if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python pdf_parser.py <input_pdf> <output_json>")
        sys.exit(1)

    parse_to_file(sys.argv[1], sys.argv[2])