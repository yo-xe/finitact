"""Stage 1 candidate-proposal extractors for the screen-grounded adapter (EXP-0001).

Each extractor turns raw pixels into a *bounded*, evidence-tagged ``VisualRegion`` list. A
decision provider only ever sees these regions' ids, never a coordinate it invented itself —
that boundary is what keeps this different from windows-mcp's ``Click(x, y)``, which lets a
provider write directly into the shared OS input queue at any pixel it names (the failure mode
behind the accidental terminal input logged in STATUS.md, Phase 4).
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import subprocess
import tempfile
import threading
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Sequence

from .contracts import EDGE_CONTOUR_SOURCE
from .screen_grounded_adapter import VisualCandidateExtractor, VisualRegion, WindowFrame


def warm_up_native_extractor_runtime() -> None:
    """Load NumPy's and Pillow's native cores before a Windows MCP stdio reader thread exists.

    Importing a module is not a sufficient probe: the exact first native operation each extractor
    performs must run before ``FastMCP.run()`` (BUG-0010 for NumPy; Pillow's ``_imaging`` hangs the
    same way -- observed live when the NumPy-only warm-up still hung inside
    ``EdgeRectangleRegionExtractor.extract`` at its ``Image.frombuffer(...).convert("L")`` call).
    """

    import numpy as np
    from PIL import Image

    sample = np.asarray([[1, 2], [3, 4]], dtype=np.int16)
    np.abs(np.diff(sample, axis=1, prepend=sample[:, :1]))
    Image.frombuffer("RGBA", (1, 1), b"\x00\x00\x00\x00", "raw", "BGRA", 0, 1).convert("L")


class TesseractTextRegionExtractor:
    """OCR-based Stage 1 extractor: recognized words become evidence-backed rects.

    Finds anything with legible text (labels, list items, menu entries). Misses icon-only
    controls with no text, by design; that gap is what ``EdgeRectangleRegionExtractor`` covers.
    """

    def __init__(
        self,
        *,
        min_confidence: float = 0.5,
        languages: str = "eng",
        max_word_gap_ratio: float = 0.8,
        min_long_side: int = 600,
        max_upscale: int = 4,
    ) -> None:
        self.min_confidence = min_confidence
        self.max_word_gap_ratio = max_word_gap_ratio
        self.languages = languages
        self.min_long_side = min_long_side
        self.max_upscale = max_upscale

    def upscale_factor(self, width: int, height: int) -> int:
        """Tesseract returns no words at all for a ~200px popup at 1x (BUG-0018); full windows
        already read well at 1x and would only get slower, so only small frames are enlarged."""

        return max(1, min(self.max_upscale, math.ceil(self.min_long_side / max(width, height, 1))))

    def extract(self, frame: WindowFrame) -> Sequence[VisualRegion]:
        from PIL import Image

        image = Image.frombuffer("RGBA", (frame.width, frame.height), frame.pixels, "raw", "BGRA", 0, 1)
        scale = self.upscale_factor(frame.width, frame.height)
        if scale > 1:
            # Grayscale before resampling found FULL HD on the BUG-0018 popup where RGB did not; at
            # 1x it lowered Phase G full-window recall, so it is part of the small-frame path only.
            image = image.convert("L").resize((frame.width * scale, frame.height * scale), Image.LANCZOS)
        with tempfile.TemporaryDirectory() as tmp_dir:
            png_path = Path(tmp_dir) / "frame.png"
            image.save(png_path)
            out_base = Path(tmp_dir) / "out"
            # Options must precede the config name: after "tsv", "-l eng" is read as config files.
            completed = subprocess.run(
                ["tesseract", str(png_path), str(out_base), "-l", self.languages, "tsv"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"tesseract failed: {detail}")
            tsv_text = out_base.with_suffix(".tsv").read_text(encoding="utf-8")
        return _with_stable_ids(self._regions_from_tsv(tsv_text, scale))

    def _regions_from_tsv(self, tsv_text: str, scale: int = 1):
        lines = tsv_text.splitlines()
        if not lines:
            return
        header = lines[0].split("\t")
        words_by_line: dict[tuple[str, str, str], list[tuple[int, str, float, tuple[int, int, int, int]]]] = {}
        for line in lines[1:]:
            fields = line.split("\t")
            if len(fields) != len(header):
                continue
            row = dict(zip(header, fields))
            text = row.get("text", "").strip()
            if not text:
                continue
            try:
                conf = float(row["conf"])
                rect = _unscale_rect(
                    (int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"])), scale
                )
                word_num = int(row["word_num"])
            except (KeyError, ValueError):
                continue
            if conf < 0 or rect[2] <= 0 or rect[3] <= 0:
                continue
            confidence = max(0.0, min(1.0, conf / 100))
            if confidence < self.min_confidence:
                continue
            line_key = (row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
            words_by_line.setdefault(line_key, []).append((word_num, text, conf, rect))

        for line_words in words_by_line.values():
            line_words.sort(key=lambda w: w[0])
            for words in self._split_at_wide_gaps(line_words):
                yield self._region_from_words(words)

    def _split_at_wide_gaps(self, words):
        return _split_at_wide_gaps(words, self.max_word_gap_ratio)

    def _region_from_words(self, words) -> VisualRegion:
        min_conf = min(w[2] for w in words)
        return _region_from_words(
            words,
            " ".join(w[1] for w in words),
            max(0.0, min(1.0, min_conf / 100)),
            f"tesseract_line:conf={min_conf:.0f},words={len(words)}",
        )


def _split_at_wide_gaps(words, max_word_gap_ratio: float):
    """An OCR "line" spans a whole toolbar row on full-window captures, fusing separate buttons into
    one candidate (Phase G calibration). Inter-word spacing inside one label stays well under the
    glyph height; the gap between adjacent controls does not."""

    heights = sorted(w[3][3] for w in words)
    limit = heights[len(heights) // 2] * max_word_gap_ratio
    segment = [words[0]]
    for word in words[1:]:
        prev = segment[-1][3]
        if word[3][0] - (prev[0] + prev[2]) > limit:
            yield segment
            segment = []
        segment.append(word)
    yield segment


def _region_from_words(words, text: str, confidence: float, evidence: str) -> VisualRegion:
    rects = [w[3] for w in words]
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    rect = (left, top, right - left, bottom - top)
    # Placeholder until _with_stable_ids numbers the whole frame's regions.
    return VisualRegion(id="ocr", label=text, rect=rect, confidence=confidence, evidence=evidence)


def _with_stable_ids(regions) -> tuple[VisualRegion, ...]:
    """Name each OCR region by its label and reading-order rank among same-label regions.

    OCR boxes on unchanged pixels move 1-3px when anything else in the frame changes, so an id
    over the exact rect churned candidates a repaint never touched (ocr-engine-comparison.md). A
    fixed position grid still splits a jittering box at cell edges; ranking has no boundary.
    Same-label boxes whose centers lie within half a box height share a row ranked by x, because
    two values on one row (Blender's ``0 m``) swap their y order under the same jitter. Exact
    position is still enforced by the adapter's rect and anchor checks before a click."""

    regions = tuple(regions)
    by_label: dict[str, list[int]] = {}
    for index, region in enumerate(regions):
        by_label.setdefault(region.label, []).append(index)
    ids = [""] * len(regions)
    for label, indexes in by_label.items():
        rows: list[tuple[float, int, list[int]]] = []
        for index in sorted(indexes, key=lambda i: _center_y(regions[i].rect)):
            rect = regions[index].rect
            if rows and _center_y(rect) - rows[-1][0] < min(rect[3], rows[-1][1]) / 2:
                rows[-1][2].append(index)
            else:
                rows.append((_center_y(rect), rect[3], [index]))
        ordered = [i for row in rows for i in sorted(row[2], key=lambda i: _center_x(regions[i].rect))]
        for rank, index in enumerate(ordered):
            key = f"{label}\0{rank}".encode()
            ids[index] = f"ocr_{hashlib.sha256(key).hexdigest()[:12]}"
    return tuple(dataclasses.replace(region, id=ids[i]) for i, region in enumerate(regions))


def _center_x(rect) -> float:
    return rect[0] + rect[2] / 2


def _center_y(rect) -> float:
    return rect[1] + rect[3] / 2


def _join_ocr_words(texts) -> str:
    """Windows OCR splits CJK text into one word per character; a space between two non-ASCII
    words would turn a Japanese label into spaced-out characters the provider cannot match."""

    out = texts[0]
    for prev, text in zip(texts, texts[1:]):
        out += text if (not prev[-1].isascii() and not text[0].isascii()) else " " + text
    return out


def _is_icon_glyph(text: str) -> bool:
    return not any(unicodedata.category(ch)[0] in "LN" for ch in text)


def _strip_icon_glyphs(words):
    """PP-OCR reads a tab's or panel's icon as a symbol word (``#``, ``✓``, ``>``) glued to the label
    (``# Scene``); the provider matches labels by their visible words, so edge glyph words are dropped
    while a real word remains. Icon-only controls are the edge extractor's job."""

    start, end = 0, len(words)
    while start < end and _is_icon_glyph(words[start][1]):
        start += 1
    while end > start and _is_icon_glyph(words[end - 1][1]):
        end -= 1
    return words[start:end] if start < end else words


def _split_at_separators(words, gray, *, contrast: int = 12, coverage: float = 0.9):
    """Adjacent toggle buttons (Unity's ``Error Pause`` | ``Editor``) sit closer than the gap limit
    and share one OCR line; what separates them is a 1px divider running the full text height,
    which glyph strokes never do, so a gap column that differs from the gap's background on nearly
    every row splits the line."""

    import numpy as np

    segment = [words[0]]
    for word in words[1:]:
        prev = segment[-1][3]
        left, right = prev[0] + prev[2], word[3][0]
        top = max(0, min(prev[1], word[3][1]) - 2)
        bottom = min(gray.shape[0], max(prev[1] + prev[3], word[3][1] + word[3][3]) + 2)
        patch = gray[top:bottom, max(0, left):max(0, right)]
        if patch.size and patch.shape[0] > 2:
            differs = np.abs(patch - np.median(patch)) > contrast
            if (differs.mean(axis=0) >= coverage).any():
                yield segment
                segment = []
        segment.append(word)
    yield segment


class WindowsOcrTextRegionExtractor:
    """Stage 1 OCR via Windows.Media.Ocr (ADR-0015 comparison candidate).

    The recognizer languages are the OS profile's installed OCR packs, not a parameter we can
    ship; the engine returns no confidence, so regions carry 1.0 and the evidence says so.
    """

    def __init__(self, *, max_word_gap_ratio: float = 0.8, min_long_side: int = 0, max_upscale: int = 4) -> None:
        self.max_word_gap_ratio = max_word_gap_ratio
        self.min_long_side = min_long_side
        self.max_upscale = max_upscale
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from winrt.windows.media.ocr import OcrEngine

            engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                raise RuntimeError("Windows OCR: no recognizer language installed for the user profile")
            self._engine = engine
        return self._engine

    def extract(self, frame: WindowFrame) -> Sequence[VisualRegion]:
        from PIL import Image
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine

        engine = self._get_engine()
        scale = max(1, min(self.max_upscale, math.ceil(self.min_long_side / max(frame.width, frame.height, 1))))
        if max(frame.width, frame.height) * scale > OcrEngine.max_image_dimension:
            raise RuntimeError(f"Windows OCR: frame {frame.width}x{frame.height} exceeds max_image_dimension")
        width, height, pixels = frame.width, frame.height, frame.pixels
        if scale > 1:
            image = Image.frombuffer("RGBA", (width, height), pixels, "raw", "RGBA", 0, 1)
            width, height = width * scale, height * scale
            pixels = image.resize((width, height), Image.LANCZOS).tobytes()
        bitmap = SoftwareBitmap.create_copy_from_buffer(bytearray(pixels), BitmapPixelFormat.BGRA8, width, height)
        # IAsyncOperation.get() blocks; asyncio.run would fail inside the MCP server's running loop.
        result = engine.recognize_async(bitmap).get()
        regions = []
        for line in result.lines:
            words = []
            for index, word in enumerate(line.words):
                box = word.bounding_rect
                rect = _unscale_rect(
                    (int(box.x), int(box.y), max(1, round(box.width)), max(1, round(box.height))), scale
                )
                if word.text.strip():
                    words.append((index, word.text.strip(), None, rect))
            if not words:
                continue
            for segment in _split_at_wide_gaps(words, self.max_word_gap_ratio):
                text = _join_ocr_words([w[1] for w in segment])
                evidence = f"windows_ocr_line:no_confidence,words={len(segment)}"
                regions.append(_region_from_words(segment, text, 1.0, evidence))
        return _with_stable_ids(regions)


class PaddleOcrTextRegionExtractor:
    """Stage 1 OCR via PP-OCR on ONNX Runtime (RapidOCR), the portable candidate of
    ADR-0015 addendum 1: one model covers Japanese and English on every OS, with confidences.
    PP-OCRv6 takes ``small``/``medium`` (``tiny`` drops Japanese); v5 takes ``mobile``/``server``.
    ``inference_engine`` is RapidOCR's backend name (``onnxruntime`` or ``openvino``)."""

    def __init__(
        self,
        *,
        ocr_version: str = "PP-OCRv5",
        model_type: str = "mobile",
        inference_engine: str = "onnxruntime",
        min_confidence: float = 0.5,
        max_word_gap_ratio: float = 0.8,
        min_long_side: int = 0,
        max_upscale: int = 4,
    ) -> None:
        self.ocr_version = ocr_version
        self.model_type = model_type
        self.inference_engine = inference_engine
        self.min_confidence = min_confidence
        self.max_word_gap_ratio = max_word_gap_ratio
        self.min_long_side = min_long_side
        self.max_upscale = max_upscale
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR

            version, model_type = OCRVersion(self.ocr_version), ModelType(self.model_type)
            engine = EngineType(self.inference_engine)
            self._engine = RapidOCR(
                params={
                    "Det.engine_type": engine,
                    "Cls.engine_type": engine,
                    "Rec.engine_type": engine,
                    "Det.ocr_version": version,
                    "Rec.ocr_version": version,
                    "Det.model_type": model_type,
                    "Rec.model_type": model_type,
                }
            )
            if hasattr(self._engine, "text_rec"):
                self._engine.text_rec = CachingTextRecognizer(self._engine.text_rec)
        return self._engine

    def extract(self, frame: WindowFrame) -> Sequence[VisualRegion]:
        import numpy as np
        from PIL import Image

        image = Image.frombuffer("RGBA", (frame.width, frame.height), frame.pixels, "raw", "BGRA", 0, 1)
        gray = np.asarray(image.convert("L"), dtype=np.int16)
        scale = max(1, min(self.max_upscale, math.ceil(self.min_long_side / max(frame.width, frame.height, 1))))
        if scale > 1:
            image = image.resize((frame.width * scale, frame.height * scale), Image.LANCZOS)
        bgr = _pad_for_detection(np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1]))
        result = self._get_engine()(bgr, return_word_box=True)
        tag = f"{self.ocr_version.replace('-', '').lower()}_{self.model_type}_line"
        regions = []
        for line_words in result.word_results or ():
            if line_words and isinstance(line_words[0], str):
                continue  # RapidOCR's no-detection sentinel is a flat ('', 1.0, None), not a line of words
            words = []
            for index, (text, conf, quad) in enumerate(line_words):
                text = text.strip()
                if not text or conf < self.min_confidence:
                    continue
                xs, ys = [pt[0] for pt in quad], [pt[1] for pt in quad]
                left, top = int(min(xs)), int(min(ys))
                rect = (left, top, max(1, math.ceil(max(xs)) - left), max(1, math.ceil(max(ys)) - top))
                words.append((index, text, float(conf), _unscale_rect(rect, scale)))
            if not words:
                continue
            for gap_segment in _split_at_wide_gaps(words, self.max_word_gap_ratio):
                for segment in _split_at_separators(gap_segment, gray):
                    segment = _strip_icon_glyphs(segment)
                    min_conf = min(w[2] for w in segment)
                    text = _join_ocr_words([w[1] for w in segment])
                    evidence = f"{tag}:conf={min_conf:.2f},words={len(segment)}"
                    regions.append(_region_from_words(segment, text, min_conf, evidence))
        return _with_stable_ids(regions)


# RapidOCR's detector scales the short side up to Det.limit_side_len (736, limit_type "min").
_DET_LIMIT_SIDE = 736
_MAX_DET_UPSCALE = 4


def _pad_for_detection(bgr):
    """A thin popup (Unity's 300x39 tooltip) was scaled 19x for detection, 0.8s for three words
    (docs/plans/uncertain-direct-pick.md); padding the short side caps that at 4x. Padding goes
    right/bottom in the border colour, so detected coordinates stay frame coordinates."""

    import numpy as np

    height, width = bgr.shape[:2]
    floor = min(max(height, width), math.ceil(_DET_LIMIT_SIDE / _MAX_DET_UPSCALE))
    if min(height, width) >= floor:
        return bgr
    border = np.concatenate((bgr[0], bgr[-1], bgr[:, 0], bgr[:, -1]))
    colour = np.median(border, axis=0).astype(bgr.dtype)
    padded = np.empty((max(height, floor), max(width, floor), 3), dtype=bgr.dtype)
    padded[:] = colour
    padded[:height, :width] = bgr
    return padded


class CachingTextRecognizer:
    """Reuse recognition per text-line crop: after a click only the lines whose pixels changed need
    recognizing (a Unity dropdown opening changes 25 of 65 lines, docs/plans/uncertain-direct-pick.md),
    and recognition is most of PP-OCR's time. The key is the crop's pixels, so a hit can only return
    what recognizing that same crop produced."""

    def __init__(self, recognizer, *, capacity: int = 4096) -> None:
        self.recognizer = recognizer
        self.capacity = capacity
        self._cache: OrderedDict[tuple[bytes, bool], tuple] = OrderedDict()
        self._output_type = None

    def __call__(self, args):
        import numpy as np

        started = time.perf_counter()
        images = [args.img] if isinstance(args.img, np.ndarray) else list(args.img)
        keys = [
            (hashlib.sha256(repr(image.shape).encode() + image.tobytes()).digest(), bool(args.return_word_box))
            for image in images
        ]
        unique: dict[tuple[bytes, bool], int] = {}
        for index, key in enumerate(keys):
            if key not in self._cache:
                unique.setdefault(key, index)
        if unique:
            fresh = self.recognizer(dataclasses.replace(args, img=[images[index] for index in unique.values()]))
            self._output_type = type(fresh)
            for position, key in enumerate(unique):
                self._cache[key] = (fresh.txts[position], fresh.scores[position], fresh.word_results[position])
        results = []
        for key in keys:
            self._cache.move_to_end(key)
            results.append(self._cache[key])
        while len(self._cache) > self.capacity:
            self._cache.popitem(last=False)
        txts, scores, words = (tuple(column) for column in zip(*results)) if results else ((), (), ())
        # Any cached entry implies an earlier miss, so the output type is known even on an all-hit call.
        return self._output_type(images, txts, list(scores), words, time.perf_counter() - started)


def default_ocr_extractor() -> PaddleOcrTextRegionExtractor:
    """ADR-0016: PP-OCRv6 medium, on OpenVINO where it is installed (x64) and ONNX Runtime elsewhere."""

    try:
        import openvino  # noqa: F401
    except ImportError:
        engine = "onnxruntime"
    else:
        engine = "openvino"
    return PaddleOcrTextRegionExtractor(ocr_version="PP-OCRv6", model_type="medium", inference_engine=engine)


def _unscale_rect(rect: tuple[int, int, int, int], scale: int) -> tuple[int, int, int, int]:
    """Map a rect from the enlarged OCR image back to frame pixels, rounding outward so the
    region never shrinks off the glyphs it was recognized from."""

    if scale == 1:
        return rect
    left, top = rect[0] // scale, rect[1] // scale
    right, bottom = -(-(rect[0] + rect[2]) // scale), -(-(rect[1] + rect[3]) // scale)
    return (left, top, right - left, bottom - top)


class CompositeRegionExtractor:
    """Pool several extractors' regions (EXP-0001: OCR and edge detection are complementary,
    neither alone covers icon-only controls and text-only labels). Each extractor keeps its own
    id namespace and evidence, so pooling never blurs which extractor backs a given candidate.

    A ``supplementary`` region centred inside a primary region adds no click target but pushed
    screen observations over the single-request choice limit, so it is dropped (EXP-0003).
    """

    def __init__(
        self,
        extractors: Sequence[VisualCandidateExtractor],
        *,
        supplementary: Sequence[VisualCandidateExtractor] = (),
    ) -> None:
        if not extractors:
            raise ValueError("at least one extractor is required")
        self.extractors = tuple(extractors)
        self.supplementary = tuple(supplementary)

    def extract(self, frame: WindowFrame) -> Sequence[VisualRegion]:
        regions: list[VisualRegion] = []
        for extractor in self.extractors:
            regions.extend(extractor.extract(frame))
        primary = tuple(regions)
        for extractor in self.supplementary:
            regions.extend(region for region in extractor.extract(frame) if not _centred_in_any(region, primary))
        return tuple(regions)


class CachingRegionExtractor:
    """Share one extractor per process and reuse its regions for pixel-identical frames.

    Building the OCR engine per run reloaded the model on every run's first observation, and
    consecutive runs re-OCR the same unchanged frame (post-observation, then the next run's
    observation). Extraction depends only on the pixels, so identical frames reuse the result.
    """

    def __init__(self, extractor: VisualCandidateExtractor, *, capacity: int = 8) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.extractor = extractor
        self.capacity = capacity
        self._cache: OrderedDict[tuple[int, int, str], tuple[VisualRegion, ...]] = OrderedDict()
        self._lock = threading.Lock()

    def extract(self, frame: WindowFrame) -> Sequence[VisualRegion]:
        key = (frame.width, frame.height, frame.fingerprint)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            regions = tuple(self.extractor.extract(frame))
            self._cache[key] = regions
            if len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
            return regions


def _centred_in_any(region: VisualRegion, others: Sequence[VisualRegion], margin: int = 2) -> bool:
    x, y, w, h = region.rect
    cx, cy = x + w / 2, y + h / 2
    return any(
        ox - margin <= cx <= ox + ow + margin and oy - margin <= cy <= oy + oh + margin
        for ox, oy, ow, oh in (other.rect for other in others)
    )


class EdgeRectangleRegionExtractor:
    """CV-based Stage 1 extractor: bordered/boxed regions independent of text.

    Catches icon-only controls (checkboxes, button borders) that ``TesseractTextRegionExtractor``
    cannot see. Plain numpy connected-components over an edge-magnitude mask; a real deployment
    should use OpenCV for speed on full-desktop frames (this prototype was only run against a
    single ~200x250px popup capture, see EXP-0001).
    """

    def __init__(
        self,
        *,
        min_area: int = 40,
        max_area_ratio: float = 0.5,
        edge_threshold: int = 40,
        gap_span_ratio: float = 0.5,
        min_gap_rows: int = 3,
    ) -> None:
        self.min_area = min_area
        self.max_area_ratio = max_area_ratio
        self.edge_threshold = edge_threshold
        self.gap_span_ratio = gap_span_ratio
        self.min_gap_rows = min_gap_rows

    def extract(self, frame: WindowFrame) -> Sequence[VisualRegion]:
        import numpy as np
        from PIL import Image

        image = Image.frombuffer("RGBA", (frame.width, frame.height), frame.pixels, "raw", "BGRA", 0, 1).convert("L")
        gray = np.asarray(image, dtype=np.int16)
        gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
        gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
        edges = (gx + gy) > self.edge_threshold

        max_area = frame.width * frame.height * self.max_area_ratio
        regions = []
        for left, top, width, height, area, submask in _connected_components(edges):
            for band_left, band_top, band_width, band_height, band_area in self._split_component(
                left, top, submask
            ):
                if band_area < self.min_area or band_width * band_height > max_area:
                    continue
                aspect = band_width / band_height if band_height else 0.0
                if not (0.2 <= aspect <= 5.0):
                    continue
                rect = (band_left, band_top, band_width, band_height)
                region_id = f"edge_{hashlib.sha256(str(rect).encode()).hexdigest()[:12]}"
                confidence = max(0.0, min(1.0, band_area / (band_width * band_height)))
                regions.append(
                    VisualRegion(
                        id=region_id,
                        label=f"boxed_region_{band_width}x{band_height}",
                        rect=rect,
                        confidence=confidence,
                        evidence=f"{EDGE_CONTOUR_SOURCE}:pixels={band_area}",
                    )
                )
        return tuple(regions)

    def _split_component(self, left: int, top: int, submask):
        """Split a connected component into row bands at gap rows whose edges cluster narrowly.

        Flood fill merges stacked controls that touch through a shared border (e.g. a list
        container's wall connecting two checkbox rows, EXP-0001's known gap) into one blob.
        Row *pixel count* can't tell a gap row (just the connecting wall) from a real control's
        interior row (also just its two side strokes) -- both can have equally few edge pixels.
        What differs is *span*: a control's own left+right features sit near its full width
        apart, while a mere pass-through connector's pixels cluster in one narrow column run.
        """

        import numpy as np

        height, width = submask.shape
        has_edge = submask.any(axis=1)
        first_col = np.argmax(submask, axis=1)
        last_col = width - 1 - np.argmax(submask[:, ::-1], axis=1)
        span = np.where(has_edge, last_col - first_col + 1, 0)
        is_gap = span <= max(2, width * self.gap_span_ratio)

        bands: list[tuple[int, int]] = []
        y = 0
        while y < height:
            if is_gap[y]:
                y += 1
                continue
            start = y
            while y < height and not is_gap[y]:
                y += 1
            bands.append((start, y))

        merged: list[tuple[int, int]] = []
        for start, end in bands:
            if merged and start - merged[-1][1] < self.min_gap_rows:
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        # A single surviving band means no stacked controls were found; an L-shaped button border
        # (one side stroke + bottom edge) would otherwise shrink to its 1-row bottom edge and be
        # dropped by the aspect filter (Phase G: Unity Play button lost).
        if len(merged) < 2:
            merged = [(0, height)]

        results = []
        for start, end in merged:
            band_mask = submask[start:end, :]
            col_counts = band_mask.sum(axis=0)
            cols_with_edges = np.nonzero(col_counts)[0]
            if cols_with_edges.size == 0:
                continue
            col_min, col_max = int(cols_with_edges.min()), int(cols_with_edges.max())
            band_area = int(band_mask[:, col_min : col_max + 1].sum())
            results.append(
                (left + col_min, top + start, col_max - col_min + 1, end - start, band_area)
            )
        return results


def _connected_components(mask):
    """4-connectivity flood fill; returns (left, top, width, height, pixel_count, submask) per
    component. ``submask`` is the component's own pixels, local to its bounding box -- it lets
    callers look for internal gaps instead of just trusting the bbox as one solid region."""

    import numpy as np

    height, width = mask.shape
    visited = [[False] * width for _ in range(height)]
    for y in range(height):
        row = mask[y]
        for x in range(width):
            if not row[x] or visited[y][x]:
                continue
            stack = [(y, x)]
            visited[y][x] = True
            coords = [(y, x)]
            min_x = max_x = x
            min_y = max_y = y
            while stack:
                cy, cx = stack.pop()
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny][nx]:
                        visited[ny][nx] = True
                        stack.append((ny, nx))
                        coords.append((ny, nx))
            comp_width, comp_height = max_x - min_x + 1, max_y - min_y + 1
            submask = np.zeros((comp_height, comp_width), dtype=bool)
            for cy, cx in coords:
                submask[cy - min_y, cx - min_x] = True
            yield (min_x, min_y, comp_width, comp_height, len(coords), submask)
