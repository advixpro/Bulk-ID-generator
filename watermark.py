"""Find recognizable camera branding in photo edges with local OCR."""
from collections import defaultdict
import csv
from functools import lru_cache
from io import BytesIO, StringIO
import os
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image

BRANDING = re.compile(
    r"\b(?:REDMI|XIAOMI|POCO|ONEPLUS|REALME|OPPO|VIVO|HUAWEI|"
    r"SAMSUNG\s+GALAXY|SHOT\s+ON\s+(?:IPHONE|PIXEL|HONOR)|"
    r"(?:AI|AL)\s+(?:DUAL|TRIPLE|QUAD)\s+CAMERA)\b", re.I
)


@lru_cache(maxsize=1)
def ocr_executable():
    installed = shutil.which("tesseract")
    default = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tesseract.exe"
    return installed or (str(default) if default.is_file() else None)


def edge_crop(image):
    """Return the minimum edge trim needed to exclude recognized branding."""
    executable = ocr_executable()
    if not executable:
        raise RuntimeError("Tesseract OCR is unavailable. Install it with English language data and restart the app.")
    w, h = image.size
    band_w, band_h = max(1, round(w * .16)), max(1, round(h * .16))
    regions = {
        "top": image.crop((0, 0, w, band_h)),
        "bottom": image.crop((0, h - band_h, w, h)),
        "left": image.crop((0, 0, band_w, h)).transpose(Image.Transpose.ROTATE_270),
        "right": image.crop((w - band_w, 0, w, h)).transpose(Image.Transpose.ROTATE_90),
    }
    samples = []
    for edge, region in regions.items():
        width = min(1250, max(850, region.width))
        height = max(1, round(region.height * width / region.width))
        samples.append((edge, region.convert("L").resize((width, height), Image.Resampling.LANCZOS), region.height))
    atlas = Image.new("L", (max(sample.width for _, sample, _ in samples) + 48,
                            sum(sample.height + 36 for _, sample, _ in samples) + 24), 255)
    bands = {}
    offset = 24
    for edge, sample, original_height in samples:
        atlas.paste(sample, (24, offset))
        bands[edge] = (offset, sample.height, original_height)
        offset += sample.height + 36
    buffer = BytesIO()
    atlas.save(buffer, "PNG")
    try:
        completed = subprocess.run(
            [executable, "stdin", "stdout", "-l", "eng", "--psm", "11", "tsv"],
            input=buffer.getvalue(), capture_output=True, check=True, timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Camera branding could not be checked by Tesseract OCR.") from error
    lines = defaultdict(list)
    for row in csv.DictReader(StringIO(completed.stdout.decode("utf-8", errors="replace")), delimiter="\t"):
        if row["level"] != "5" or not row["text"].strip() or float(row["conf"]) < 35:
            continue
        top = int(row["top"])
        bottom = top + int(row["height"])
        for edge, (offset, height, original_height) in bands.items():
            if offset <= top and bottom <= offset + height:
                line = (edge, row["block_num"], row["par_num"], row["line_num"])
                lines[line].append((row["text"], (top - offset) * original_height / height,
                                    (bottom - offset) * original_height / height))
                break

    left, top, right, bottom = 0, 0, w, h
    for (edge, *_), words in lines.items():
        if not BRANDING.search(" ".join(word[0] for word in words)):
            continue
        padding = max(4, round((h if edge in ("top", "bottom") else w) * .012))
        if edge == "top":
            top = max(top, round(max(word[2] for word in words)) + padding)
        elif edge == "bottom":
            bottom = min(bottom, h - band_h + round(min(word[1] for word in words)) - padding)
        elif edge == "left":
            left = max(left, round(max(word[2] for word in words)) + padding)
        else:
            right = min(right, w - round(max(word[2] for word in words)) - padding)
    bounds = (left, top, right, bottom)
    return bounds if bounds != (0, 0, w, h) else None
