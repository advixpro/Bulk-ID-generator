"""Loopback-only HTTP server for the Windows browser editor."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser

from PIL import Image
from engine import (FONTS, WINDOWS_FONTS, compose, duplicate_keys, file_name, normalized, page_box, photo,
                    prepared_template, reader, render_pdf, report_file, run_batch, scan, validate_columns,
                    validate_layout, workbook)

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = Path(os.environ["LOCALAPPDATA"]) / "ID Card Studio" if "LOCALAPPDATA" in os.environ else ROOT / ".id-card-data"
DATA = Path(os.environ.get("ID_CARD_STUDIO_DATA", str(DEFAULT_DATA)))
SETUPS = DATA / "setups"
KEY = secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.Lock()
PICKER_LOCK = threading.Lock()
ASSETS = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
          "/studio.js": ("studio.js", "text/javascript")}


def png(data):
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def setup_path(identifier):
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid setup ID.")
    return SETUPS / identifier


def read_setup(path):
    setup = json.loads((path / "setup.json").read_text(encoding="utf-8"))
    setup.update(id=path.name, pdf=str(path / setup["pdfFile"]))
    return setup


def choose(kind):
    with PICKER_LOCK:
        process = subprocess.run([sys.executable, __file__, "--pick", kind],
                                 capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(process.stdout)


def native_picker(kind):
    import tkinter as tk
    from tkinter import filedialog
    window = tk.Tk()
    window.withdraw()
    window.attributes("-topmost", True)
    try:
        if kind in ("photos", "output"):
            path = filedialog.askdirectory(parent=window, mustexist=True,
                                           title="Choose photo folder" if kind == "photos" else "Choose output folder")
        else:
            title, pattern = ("PDF template", "*.pdf") if kind == "pdf" else ("Excel workbook", "*.xlsx")
            path = filedialog.askopenfilename(parent=window, title="Choose " + title,
                                              filetypes=[(title, pattern)])
        print(json.dumps(path))
    finally:
        window.destroy()


def job_worker(data, job):
    try:
        result, records, photos = run_batch(data, job)
        report = report_file(result, records, photos)
        job.update(result=result, report=report, status="complete")
    except Exception as error:
        job.update(error=str(error), status="failed")


def dispatch(action, data):
    if action == "pick":
        return {"path": choose(data["kind"])}
    if action == "template":
        document = reader(data["pdf"])
        return {"count": len(document.pages), "fonts": [
            name for name, variants in FONTS.items() if all((WINDOWS_FONTS / f).is_file() for f in variants)
        ]}
    if action == "page":
        index = int(data["page"])
        image = png(render_pdf(data["pdf"], index, 240 if data.get("thumb") else 1150))
        if data.get("thumb"):
            return {"image": image}
        page = reader(data["pdf"]).pages[index]
        _, _, width, height = page_box(page)
        if page.rotation % 180:
            width, height = height, width
        return {"image": image, "width": width * page.user_unit, "height": height * page.user_unit}
    if action == "excel":
        sheets, sheet, columns, rows = workbook(data)
        return {"sheets": sheets, "sheet": sheet, "columns": columns, "total": len(rows),
                "samples": [{"row": row, "values": {key: values.get(key, "") for key in columns}}
                            for row, values in rows[:20]]}
    if action == "duplicates":
        return duplicate_keys(workbook(data)[3], data["photoColumn"])
    if action == "photos":
        files, groups, invalid = scan(data["photos"])
        matches = {}
        if data.get("excel") and data.get("photoColumn"):
            for row, values in workbook(data)[3]:
                key = normalized(values.get(data["photoColumn"], ""))
                if key:
                    matches.setdefault(key, []).append(row)
        return {"count": len(files), "invalid": invalid, "groups": [
            {"key": key, "files": [{"name": name, "rows": matches.get(normalized(Path(name).stem), [])}
                                   for name in names]} for key, names in groups.items()
        ]}
    if action == "thumbnail":
        name = data["name"]
        if Path(name).name != name:
            raise ValueError("Invalid photo filename.")
        image = photo(Path(data["photos"]) / name)
        image.thumbnail((110, 110), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, "PNG")
        return {"image": png(output.getvalue())}
    if action == "preview":
        document = reader(data["pdf"])
        layout = data["layout"]
        validate_layout(layout, document)
        columns, rows = workbook(data)[2:] if data.get("excel") else ([], [])
        sample = int(data.get("sample", 0))
        values = rows[sample][1] if rows else {}
        path = None
        notice = ""
        if data.get("excel"):
            validate_columns(layout, columns, data["pattern"], data.get("photoColumn"))
        if data.get("photos") and data.get("photoColumn"):
            key = normalized(values.get(data["photoColumn"], ""))
            allowed = []
            omitted = set()
            if key:
                files, groups, invalid = scan(data["photos"])
                for group, names in groups.items():
                    if group in data.get("resolutions", {}):
                        omitted.update(set(names) - set(data["resolutions"][group]))
                allowed = [path for name, path in files.items()
                           if normalized(path.stem) == key and name not in invalid and name not in omitted]
            if len(allowed) == 1:
                path = allowed[0]
            elif rows:
                notice = "No unique usable photo for this sample."
        finished = compose(prepared_template(data["pdf"], layout["pages"]), layout, values, path)
        return {"images": [png(render_pdf(finished, side)) for side in range(len(layout["pages"]))],
                "filename": file_name(data["pattern"], values, int(data.get("start", 1)) + sample) if rows else "",
                "notice": notice}
    if action == "list":
        SETUPS.mkdir(parents=True, exist_ok=True)
        result = [read_setup(p) for p in SETUPS.iterdir() if (p / "setup.json").is_file()]
        return sorted([{"id": s["id"], "name": s["name"]} for s in result], key=lambda s: s["name"].casefold())
    if action == "open":
        return read_setup(setup_path(data["id"]))
    if action == "save":
        name = data["name"].strip()
        if not name:
            raise ValueError("Enter a setup name.")
        validate_layout(data["layout"], reader(data["pdf"]))
        with LOCK:
            path = setup_path(data.get("id") or secrets.token_hex(16))
            path.mkdir(parents=True, exist_ok=True)
            source = Path(data["pdf"])
            pdf_file = source.name if source.parent.resolve() == path.resolve() else secrets.token_hex(12) + ".pdf"
            target = path / pdf_file
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            setup = {key: data[key] for key in ("layout", "pattern", "start", "photoColumn", "sheet")}
            setup.update(name=name, pdfFile=pdf_file, templateName=data.get("templateName") or source.name)
            temporary = path / "setup.tmp"
            temporary.write_text(json.dumps(setup, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path / "setup.json")
        return read_setup(path)
    if action == "export":
        with LOCK:
            if any(job["status"] == "running" for job in JOBS.values()):
                raise ValueError("An export is already running.")
            if len(JOBS) >= 8:
                del JOBS[next(iter(JOBS))]
            identifier = secrets.token_hex(16)
            job = JOBS[identifier] = {"status": "running", "total": 0, "done": 0, "generated": 0}
            threading.Thread(target=job_worker, args=(data, job), daemon=True).start()
        return {"id": identifier}
    if action == "job":
        return {key: value for key, value in JOBS[data["id"]].copy().items() if key != "report"}
    raise ValueError("Unknown action.")


class Handler(BaseHTTPRequestHandler):
    def local_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}",
                                            f"localhost:{self.server.server_port}"}

    def reply(self, body, content_type, status=200, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def json(self, body, status=200):
        self.reply(json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def do_GET(self):
        if not self.local_host():
            return self.json({"error": "Open this app from its local address."}, 403)
        if self.path in ASSETS:
            name, mime = ASSETS[self.path]
            payload = (ROOT / "web" / name).read_bytes()
            return self.reply(payload.replace(b"__SESSION_KEY__", KEY.encode()) if self.path == "/" else payload,
                              mime + "; charset=utf-8")
        self.json({"error": "Page not found."}, 404)

    def do_POST(self):
        if not self.local_host() or self.headers.get("X-Studio-Key") != KEY:
            return self.json({"error": "Reload the local app and try again."}, 403)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 4_000_000:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(length))
            action = self.path.removeprefix("/api/")
            if action == "report":
                return self.reply(JOBS[data["id"]]["report"],
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  filename="ID Card Export Report.xlsx")
            return self.json(dispatch(action, data))
        except (KeyError, IndexError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            return self.json({"error": str(error)}, 400)
        except Exception as error:
            self.log_error("%s: %s", type(error).__name__, error)
            return self.json({"error": str(error) or type(error).__name__}, 400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--pick", choices=["pdf", "excel", "photos", "output"])
    args = parser.parse_args()
    if args.pick:
        native_picker(args.pick)
    else:
        with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
            address = f"http://127.0.0.1:{server.server_port}"
            print(f"ID Card Studio is running at {address}", flush=True)
            if args.open:
                threading.Thread(target=webbrowser.open, args=(address,), daemon=True).start()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
