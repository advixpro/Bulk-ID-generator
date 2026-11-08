"""Data preparation, card composition, and batch export."""
from collections import Counter, defaultdict
from datetime import date, datetime, time
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import math
import os
import re
import threading

from openpyxl import load_workbook
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, features
from pypdf import PdfReader, PdfWriter, Transformation
import pypdfium2 as pdfium
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
import xlsxwriter

PDFIUM_LOCK = threading.Lock()
TOKEN = re.compile(r"\{([^{}]+)\}")
SEQUENCE = re.compile(r"#+")
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}
WINDOWS_FONTS = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
FONTS = {
    "Arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
}


def display(cell):
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        fmt = cell.number_format.split(";")[0]
        if re.fullmatch(r"0+", fmt) and value >= 0 and int(value) == value:
            return str(int(value)).zfill(len(fmt))
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
    return str(value)


@lru_cache(maxsize=8)
def spreadsheet(path, stamp, sheet_name):
    with open(path, "rb") as source:
        book = load_workbook(source, read_only=True, data_only=True)
        try:
            sheet = book[sheet_name] if sheet_name else book.active
            cells = iter(sheet.iter_rows())
            headers = [display(cell).strip() for cell in next(cells, [])]
            columns = [name for name in headers if name]
            if not columns:
                raise ValueError("Put column names on the first row of the worksheet.")
            if len(set(columns)) != len(columns):
                raise ValueError("The worksheet has duplicate column headers.")
            rows = []
            for index, row in enumerate(cells, start=2):
                values = [display(cell) for cell in row]
                if any(values):
                    rows.append((index, dict(zip(headers, values))))
            return book.sheetnames, sheet.title, columns, rows
        finally:
            book.close()


def workbook(data):
    path = Path(data["excel"])
    stat = path.stat()
    return spreadsheet(str(path), (stat.st_mtime_ns, stat.st_size), data.get("sheet") or "")


def normalized(value):
    return value.strip().casefold()


def duplicate_keys(rows, column):
    groups = defaultdict(list)
    for row, values in rows:
        key = normalized(values.get(column, ""))
        if key:
            groups[key].append(row)
    return {key: indexes for key, indexes in groups.items() if len(indexes) > 1}


def photo(path):
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGBA")


@lru_cache(maxsize=16384)
def photo_hash(path, stamp, size):
    image = photo(path)
    digest = sha256(f"{image.width}x{image.height}:".encode())
    digest.update(image.tobytes())
    return digest.hexdigest()


def scan(folder):
    directory = Path(folder)
    groups = defaultdict(list)
    invalid = {}
    photos = {}
    for path in sorted(directory.iterdir(), key=lambda p: (p.name.casefold(), p.name)):
        if path.suffix.lower() not in PHOTO_EXTENSIONS or not path.is_file():
            continue
        try:
            stat = path.stat()
            digest = photo_hash(str(path), stat.st_mtime_ns, stat.st_size)
            groups[digest].append(path.name)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            invalid[path.name] = str(error)
        photos[path.name] = path
    duplicates = {
        sha256((digest + "\0" + "\0".join(names)).encode()).hexdigest(): names
        for digest, names in groups.items() if len(names) > 1
    }
    return photos, duplicates, invalid


def photo_choices(data, groups):
    omitted = set()
    notes = {}
    resolutions = data.get("resolutions", {})
    for key, names in groups.items():
        keep = resolutions.get(key)
        if not isinstance(keep, list) or not set(keep).issubset(names):
            raise ValueError("Resolve each identical-photo group before exporting.")
        omitted.update(set(names) - set(keep))
        for name in names:
            notes[name] = "Identical photo retained" if name in keep else "Identical photo omitted"
    return omitted, notes


def expand(pattern, values, sequence=None):
    if sequence is not None:
        pattern = SEQUENCE.sub(lambda match: str(sequence).zfill(len(match[0])), pattern)
    return TOKEN.sub(lambda match: values.get(match[1], ""), pattern)


def file_name(pattern, values, sequence):
    value = expand(pattern, values, sequence).strip()
    if value.lower().endswith(".pdf"):
        value = value[:-4]
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not value:
        raise ValueError("The filename pattern produced an empty name.")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", value.split(".")[0], re.I):
        value = "_" + value
    return value[:160].rstrip(" .") + ".pdf"


def reader(path):
    document = PdfReader(path)
    if document.is_encrypted and not document.decrypt(""):
        raise ValueError("The PDF is password protected. Select an unlocked copy.")
    return document


def prepared_template(path, pages):
    document = reader(path)
    form = document.root_object.get("/AcroForm")
    if form is None or not form.get_object().get("/Fields"):
        return document
    if form.get_object().get("/XFA"):
        raise ValueError("Save the XFA form as a regular PDF before using it as a template.")
    with PDFIUM_LOCK, pdfium.PdfDocument(path) as source:
        source.init_forms()
        for index in pages:
            page = source[index]
            try:
                page.flatten()
            finally:
                page.close()
        buffer = BytesIO()
        source.save(buffer)
    return PdfReader(buffer)


def render_pdf(source, page=0, limit=1150):
    with PDFIUM_LOCK, pdfium.PdfDocument(source) as document:
        document.init_forms()
        selected = document[page]
        try:
            width, height = selected.get_size()
            bitmap = selected.render(scale=limit / max(width, height), may_draw_forms=True)
            try:
                output = BytesIO()
                bitmap.to_pil().save(output, "PNG")
                return output.getvalue()
            finally:
                bitmap.close()
        finally:
            selected.close()


@lru_cache(maxsize=128)
def font_at(family, bold, italic, size):
    variants = FONTS[family]
    filename = variants[int(bool(bold)) + 2 * int(bool(italic))]
    return ImageFont.truetype(str(WINDOWS_FONTS / filename), size, layout_engine=ImageFont.Layout.RAQM)


def paint_text(canvas, field, values, box, units):
    content = " ".join(expand(field["expression"], values).split())
    if not content:
        return
    x, y, width, height = box
    pixels = 5 * units
    w, h = max(1, round(width * pixels)), max(1, round(height * pixels))
    if w * h > 35_000_000:
        raise ValueError("The text box is too large to render.")
    image = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(image)
    direction = None if field["direction"] == "auto" else field["direction"]
    low = max(1, round(float(field["minSize"]) * 5))
    high = max(low, round(float(field["size"]) * 5))
    best = None
    while low <= high:
        size = (low + high) // 2
        font = font_at(field["font"], field.get("bold"), field.get("italic"), size)
        left, top, right, bottom = draw.textbbox((0, 0), content, font=font, direction=direction)
        if right - left <= w and bottom - top <= h:
            best = (font, left, top, right, bottom)
            low = size + 1
        else:
            high = size - 1
    if best is None:
        raise ValueError(f"Text does not fit at the minimum size in {field['expression']!r}.")
    font, left, top, right, bottom = best
    slack = w - (right - left)
    offset = {"left": 0, "center": slack / 2, "right": slack}[field["align"]]
    draw.text((offset - left, (h - (bottom - top)) / 2 - top), content,
              font=font, direction=direction, fill=field["color"])
    canvas.drawImage(ImageReader(image), x, y, width, height, mask="auto")


def paint_photo(canvas, field, source, box, units):
    x, y, width, height = box
    w, h = max(1, round(width * 5 * units)), max(1, round(height * 5 * units))
    if w * h > 35_000_000:
        raise ValueError("The photo box is too large to render.")
    if field["fit"] == "fill":
        image = ImageOps.fit(source, (w, h), Image.Resampling.LANCZOS)
    else:
        image = Image.new("RGBA", (w, h))
        fitted = ImageOps.contain(source, (w, h), Image.Resampling.LANCZOS)
        image.paste(fitted, ((w - fitted.width) // 2, (h - fitted.height) // 2))
    radius = min(w / 2, h / 2, max(0, float(field.get("radius", 0))) * 5)
    if radius:
        mask = Image.new("L", (w, h))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
        image.putalpha(ImageChops.multiply(mask, image.getchannel("A")))
    canvas.drawImage(ImageReader(image), x, y, width, height, mask="auto")


def paint_cover(canvas, field, box):
    x, y, width, height = box
    start = HexColor(field["color"])
    canvas.saveState()
    path = canvas.beginPath()
    path.rect(x, y, width, height)
    if field.get("gradient"):
        canvas.clipPath(path, stroke=0, fill=0)
        end = HexColor(field["endColor"])
        if field.get("axis") == "vertical":
            canvas.linearGradient(x, y + height, x, y, (start, end))
        else:
            canvas.linearGradient(x, y, x + width, y, (start, end))
    else:
        canvas.setFillColor(start)
        canvas.drawPath(path, stroke=0, fill=1)
    canvas.restoreState()


def page_box(page):
    left = max(float(page.cropbox.left), float(page.mediabox.left))
    bottom = max(float(page.cropbox.bottom), float(page.mediabox.bottom))
    width = min(float(page.cropbox.right), float(page.mediabox.right)) - left
    height = min(float(page.cropbox.top), float(page.mediabox.top)) - bottom
    return left, bottom, width, height


def compose(template, layout, values, photo_path=None):
    writer = PdfWriter()
    image = photo(photo_path) if photo_path else None
    for side in layout["pages"]:
        page = writer.add_page(template.pages[side])
        # The preview uses the visible intersection of the media and crop boxes.
        if page.rotation:
            page.transfer_rotation_to_content()
        left, bottom, width, height = page_box(page)
        fields = [f for f in layout["fields"] if f["page"] == side]
        if not fields:
            continue
        overlay = BytesIO()
        canvas = Canvas(overlay, pagesize=(width, height))
        for field in sorted(fields, key=lambda item: item["kind"] != "cover"):
            x, y, w, h = (float(field[key]) for key in ("x", "y", "w", "h"))
            box = (x * width, (1 - y - h) * height, w * width, h * height)
            if field["kind"] == "cover":
                paint_cover(canvas, field, box)
            elif field["kind"] == "text":
                paint_text(canvas, field, values, box, page.user_unit)
            elif image is not None:
                paint_photo(canvas, field, image, box, page.user_unit)
        canvas.showPage()
        canvas.save()
        page.merge_transformed_page(PdfReader(overlay).pages[0], Transformation().translate(left, bottom))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def validate_layout(layout, template):
    pages = layout["pages"]
    if len(pages) not in (1, 2) or len(set(pages)) != len(pages) or any(
        not isinstance(p, int) or not 0 <= p < len(template.pages) for p in pages
    ):
        raise ValueError("Choose distinct front and back pages.")
    for field in layout["fields"]:
        x, y, w, h = (float(field[key]) for key in ("x", "y", "w", "h"))
        if field["page"] not in pages or not all(math.isfinite(n) for n in (x, y, w, h)):
            raise ValueError("A field has an invalid page or position.")
        if min(x, y) < 0 or min(w, h) <= 0 or max(x + w, y + h) > 1.000001:
            raise ValueError("Keep all fields inside their page.")
        if field["kind"] == "text" and not 1 <= float(field["minSize"]) <= float(field["size"]) <= 250:
            raise ValueError("Text sizes must be between 1 and 250 pt, with minimum at or below preferred size.")


def validate_columns(layout, columns, pattern="", photo_column=""):
    expressions = [field["expression"] for field in layout["fields"] if field["kind"] == "text"]
    missing = set(TOKEN.findall(pattern))
    for expression in expressions:
        missing.update(TOKEN.findall(expression))
    if photo_column:
        missing.add(photo_column)
    missing.difference_update(columns)
    if missing:
        raise ValueError("Missing Excel columns: " + ", ".join(sorted(missing)))


def export_pdf(folder, requested, contents):
    stem = Path(requested).stem
    suffix = 1
    while True:
        actual = requested if suffix == 1 else f"{stem}_{suffix}.pdf"
        path = folder / actual
        try:
            output = path.open("xb")
        except FileExistsError:
            suffix += 1
            continue
        try:
            with output:
                output.write(contents)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return actual


def run_batch(data, job):
    layout = data["layout"]
    template = reader(data["pdf"])
    validate_layout(layout, template)
    if not layout["fields"]:
        raise ValueError("Add at least one field to the layout.")
    _, _, columns, rows = workbook(data)
    column = data.get("photoColumn")
    has_photo = any(field["kind"] == "photo" for field in layout["fields"])
    if has_photo and (not column or not data.get("photos")):
        raise ValueError("Select a photo folder and matching column.")
    pattern = data["pattern"]
    if not pattern.strip():
        raise ValueError("Enter a filename pattern.")
    validate_columns(layout, columns, pattern, column)
    folder = Path(data["output"])
    if not folder.is_dir():
        raise ValueError("Select an existing output folder.")
    template = prepared_template(data["pdf"], layout["pages"])
    files, groups, invalid = scan(data["photos"]) if data.get("photos") else ({}, {}, {})
    omitted, decisions = photo_choices(data, groups)
    by_key = defaultdict(list)
    for name, path in files.items():
        by_key[normalized(path.stem)].append(name)
    duplicates = duplicate_keys(rows, column) if column else {}
    reports = []
    reasons = Counter()
    generated = 0
    renamed = 0
    job.update(total=len(rows), done=0)
    for sequence, (row, values) in enumerate(rows, start=int(data.get("start", 1))):
        requested = actual = ""
        key = normalized(values.get(column, ""))
        matched = by_key.get(key, [])
        usable = [name for name in matched if name not in omitted and name not in invalid]
        notes = [f"{name}: {decisions[name]}" for name in matched if name in decisions]
        issue = ""
        try:
            requested = file_name(pattern, values, sequence)
            if key in duplicates:
                raise ValueError("Duplicate photo matching value.")
            if has_photo:
                if not key:
                    raise ValueError("Missing photo matching value.")
                if len(usable) > 1:
                    raise ValueError("Multiple photos match the same value.")
                if not usable:
                    if any(name in invalid for name in matched):
                        raise ValueError("Matching photo could not be read.")
                    if matched:
                        raise ValueError("Matching photo was omitted.")
                    raise ValueError("Missing photo.")
            contents = compose(template, layout, values, files[usable[0]] if has_photo else None)
            actual = export_pdf(folder, requested, contents)
            generated += 1
            renamed += actual != requested
        except (ValueError, OSError) as error:
            issue = str(error)
            reasons["Text overflow" if issue.startswith("Text does not fit") else issue] += 1
        reports.append([row, values.get("Employee ID", ""), values.get(column, ""), ", ".join(matched),
                        "Generated" if actual else "Skipped", requested, actual, issue, " | ".join(notes)])
        job.update(done=len(reports), generated=generated)
    return {
        "total": len(rows), "generated": generated, "skipped": len(rows) - generated,
        "renamed": renamed, "reasons": dict(reasons),
    }, reports, [[name, note] for name, note in decisions.items()] + [
        [name, "Unreadable: " + reason] for name, reason in invalid.items()
    ]


def report_file(result, records, photos):
    stream = BytesIO()
    with xlsxwriter.Workbook(stream, {"in_memory": True, "strings_to_formulas": False,
                                      "strings_to_urls": False, "strings_to_numbers": False}) as book:
        header = book.add_format({"bold": True, "bg_color": "#193451", "font_color": "#FFFFFF"})
        summary = book.add_worksheet("Summary")
        summary.set_column(0, 0, 54)
        summary.write_row(0, 0, ["Outcome", "Count"], header)
        metrics = [(key.capitalize(), result[key]) for key in ("total", "generated", "skipped", "renamed")]
        for index, item in enumerate(metrics + list(result["reasons"].items()), 1):
            summary.write_row(index, 0, item)
        for name, titles, data, widths in [
            ("Records", ["Excel row", "Employee ID", "Match value", "Matching photos", "Status",
                         "Requested filename", "Actual filename", "Issue", "Photo decisions"],
             records, [12, 16, 20, 30, 14, 35, 35, 65, 65]),
            ("Photo checks", ["Photo filename", "Decision or issue"], photos, [32, 65]),
        ]:
            sheet = book.add_worksheet(name)
            sheet.write_row(0, 0, titles, header)
            sheet.freeze_panes(1, 0)
            sheet.autofilter(0, 0, len(data), len(titles) - 1)
            for index, width in enumerate(widths):
                sheet.set_column(index, index, width)
            for index, item in enumerate(data, 1):
                sheet.write_row(index, 0, item)
    return stream.getvalue()


if not features.check("raqm"):
    raise RuntimeError("Install a Pillow build with RAQM support for Arabic and mixed-direction text.")
