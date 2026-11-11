"""Local face framing and person masking for an ID card photo."""
from functools import cached_property
from pathlib import Path
from threading import Lock

from PIL import Image, ImageChops, ImageOps
from watermark import edge_crop

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None

MODELS = Path(__file__).resolve().parent / "models"
OFF_WHITE = "#f7f6f2"
# The reference cards put the detected face at 57–61% of the photo height,
# with roughly 4% clear space above the hair or head covering.
TARGET_FACE_HEIGHT = 0.59
TARGET_HEADROOM = 0.04
MODEL_LOCK = Lock()
_detector = _segmenter = None


def clamp(value, low, high):
    return max(low, min(high, value))


def detect_faces(image):
    global _detector
    sample = image.convert("RGB")
    sample.thumbnail((640, 640), Image.Resampling.LANCZOS)
    bgr = cv2.cvtColor(np.asarray(sample), cv2.COLOR_RGB2BGR)
    with MODEL_LOCK:
        if _detector is None:
            _detector = cv2.FaceDetectorYN.create(str(MODELS / "yunet.onnx"), "", sample.size, 0.75, 0.3, 200)
        _detector.setInputSize(sample.size)
        _, faces = _detector.detect(bgr)
    if faces is None:
        return []
    sx, sy = image.width / sample.width, image.height / sample.height
    return [(x * sx, y * sy, w * sx, h * sy, confidence)
            for x, y, w, h, *_, confidence in faces]


def head_top(image, face, mask):
    x, y, w, h = face
    estimated = max(0, y - 0.42 * h)
    if mask is None:
        return estimated
    sx, sy = mask.width / image.width, mask.height / image.height
    left = max(0, round((x + .15 * w) * sx))
    right = min(mask.width, round((x + .85 * w) * sx))
    top = max(0, round((y - .70 * h) * sy))
    bottom = min(mask.height, round(y * sy))
    if right <= left or bottom <= top:
        return estimated
    head = np.asarray(mask.crop((left, top, right, bottom)))
    rows = np.flatnonzero((head >= 96).mean(axis=1) >= .20)
    if not rows.size:
        return estimated
    # Keep a little extra room for soft edges and the low-resolution mask.
    return max(0, (top + rows[0] - 2) / sy)


def frame_box(image, face, size, mask=None):
    x, y, w, h = face
    ratio = size[0] / size[1]
    left, top = max(0, x - 0.20 * w), head_top(image, face, mask)
    right, bottom = min(image.width, x + 1.20 * w), min(image.height, y + 1.08 * h)
    max_height = min(image.height, image.width / ratio)
    min_height = max(bottom - top, (right - left) / ratio)
    if min_height > max_height:
        return None
    height = min(max_height, max(h / TARGET_FACE_HEIGHT, min_height))
    width = min(image.width, height * ratio)
    cx = x + w / 2
    start_x = clamp(cx - width / 2, max(0, right - width), min(left, image.width - width))
    start_y = clamp(top - TARGET_HEADROOM * height, max(0, bottom - height), min(top, image.height - height))
    return start_x, start_y, min(image.width, start_x + width), min(image.height, start_y + height)


def detail_ok(image, face, box, size):
    x, y, w, h = face
    if min(w, h) < 64 or size[1] > 1.8 * (box[3] - box[1]):
        return False
    sample = image.crop((round(x), round(y), round(x + w), round(y + h))).convert("L")
    sample.thumbnail((96, 96), Image.Resampling.LANCZOS)
    return cv2.Laplacian(np.asarray(sample), cv2.CV_64F).var() >= 20


def person_mask(image, face):
    global _segmenter
    rgb = np.asarray(image.convert("RGB").resize((192, 192), Image.Resampling.BILINEAR))
    blob = cv2.dnn.blobFromImage(rgb, 1 / 127.5, (192, 192), (127.5,) * 3)
    with MODEL_LOCK:
        if _segmenter is None:
            _segmenter = cv2.dnn.readNet(str(MODELS / "pphumanseg.onnx"))
        _segmenter.setInput(blob)
        scores = _segmenter.forward()[0]
    # Use the foreground component containing the face, rejecting uncertain masks.
    foreground = scores[1]
    if scores.min() < 0 or scores.max() > 1 or not np.allclose(scores.sum(axis=0), 1, atol=0.02):
        foreground = 1 / (1 + np.exp(np.clip(scores[0] - scores[1], -60, 60)))
    _, labels = cv2.connectedComponents(np.uint8(foreground >= 0.50))
    x, y, w, h = face
    center_x = clamp(round((x + w / 2) * 192 / image.width), 0, 191)
    center_y = clamp(round((y + h / 2) * 192 / image.height), 0, 191)
    half_w = max(1, round(w * 48 / image.width))
    half_h = max(1, round(h * 48 / image.height))
    core = np.s_[max(0, center_y-half_h):center_y+half_h+1, max(0, center_x-half_w):center_x+half_w+1]
    component = labels[center_y, center_x]
    if not component or np.mean((labels[core] == component) & (foreground[core] >= 0.80)) < 0.88:
        return None
    subject = labels == component
    if subject.mean() < 0.025 or (foreground < 0.20).mean() < 0.025:
        return None
    alpha = np.clip((foreground - 0.15) / 0.75, 0, 1)
    nearby = cv2.dilate(subject.astype(np.uint8), np.ones((3, 3), np.uint8))
    return Image.fromarray(np.uint8(alpha * nearby * 255))


class Portrait:
    def __init__(self, image):
        self.image = image

    @cached_property
    def face(self):
        if cv2 is None:
            return None, "Local photo processing is unavailable. Restart with the app launcher."
        try:
            faces = detect_faces(self.image)
        except (cv2.error, OSError):
            return None, "Face detection could not run."
        if len(faces) > 1:
            return None, "Multiple faces detected."
        if not faces or faces[0][4] < 0.86:
            return None, "No clear face detected."
        return faces[0][:4], ""

    @cached_property
    def mask(self):
        face, issue = self.face
        if issue:
            return None
        try:
            return person_mask(self.image, face)
        except (cv2.error, OSError):
            return None

    @cached_property
    def without_watermark(self):
        try:
            bounds = edge_crop(self.image)
        except RuntimeError as error:
            return self, str(error)
        if bounds is None:
            return self, ""
        face, issue = self.face
        if issue:
            return self, "Camera branding detected, but a safe crop could not be verified. " + issue
        x, y, w, h = face
        left, top, right, bottom = bounds
        if (left > max(0, x - .20 * w) or top > max(0, y - .42 * h)
                or right < min(self.image.width, x + 1.20 * w)
                or bottom < min(self.image.height, y + 1.035 * h)):
            return self, "Camera branding detected, but cropping could cut into the head."
        cropped = Portrait(self.image.crop(bounds))
        cropped.face = ((x - left, y - top, w, h), "")
        return cropped, ""

    def render(self, field, size):
        source, issue = self.without_watermark if field.get("cropWatermark") else (self, "")
        image, warnings = source.render_framed(field, size)
        if issue:
            warnings.insert(0, "Crop camera watermarks: " + issue)
        return image, warnings

    def render_framed(self, field, size):
        source = self.image
        warnings = []
        crop = None
        if field.get("autoFraming"):
            face, issue = self.face
            if not issue:
                crop = frame_box(source, face, size, self.mask)
                if crop is None:
                    issue = "The full head could not fit the photo frame."
                elif not detail_ok(source, face, crop, size):
                    issue = "The face is too small or soft for a clear automatic crop."
                    crop = None
            if issue:
                warnings.append("Auto Framing: " + issue)
        background = field.get("offWhite")
        mask = self.mask if background else None
        if background and mask is None:
            warnings.append("Off-white background: Could not reliably separate the person from the background.")
        if crop is None and field["fit"] == "fill":
            ratio = size[0] / size[1]
            w, h = source.size
            if w / h > ratio:
                margin = (w - h * ratio) / 2
                crop = (margin, 0, w - margin, h)
            else:
                margin = (h - w / ratio) / 2
                crop = (0, margin, w, h - margin)
        if crop is not None:
            fitted = source.resize(size, Image.Resampling.LANCZOS, box=crop)
        else:
            fitted = ImageOps.contain(source, size, Image.Resampling.LANCZOS)
            crop = (0, 0, source.width, source.height)
        if mask is not None:
            mask_box = tuple(value * 192 / source.size[i % 2] for i, value in enumerate(crop))
            alpha = mask.resize(fitted.size, Image.Resampling.BILINEAR, box=mask_box)
            fitted.putalpha(ImageChops.multiply(alpha, fitted.getchannel("A")))
        result = Image.new("RGBA", size, OFF_WHITE if mask is not None else (0, 0, 0, 0))
        result.alpha_composite(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
        return result, warnings
