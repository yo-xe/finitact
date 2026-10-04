"""The part of a window UIA leaves uncovered, as one masked crop for OCR (ADR-0044).

numpy only: OpenCV is not a core dependency, and the morphology here is two box sums over a 4px grid.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Sequence

import numpy as np

if TYPE_CHECKING:
    from .screen_grounded_adapter import WindowFrame

Rect = tuple[int, int, int, int]

CELL = 4
# Narrower gaps between UIA elements hold borders and padding, not text an OCR line could read (probe, ADR-0044).
MIN_SIDE = 24
TILE = 32
# Gradient steps per tile below which the tile is flat colour; a glyph of body text alone exceeds it.
TILE_MIN_EDGES = 16
EDGE_THRESHOLD = 40


@dataclass(frozen=True)
class UncoveredCrop:
    frame: WindowFrame
    offset: tuple[int, int]
    share: float


def uncovered_crop(frame: WindowFrame, covered: Sequence[Rect]) -> UncoveredCrop | None:
    """The uncovered, non-blank area's bounding crop with everything else painted its median colour.

    None when nothing is left to read. Painting instead of cutting pieces keeps one extractor call, and identical
    uncovered pixels give an identical crop, so the extractor's pixel cache still hits.
    """

    rows, cols = -(-frame.height // CELL), -(-frame.width // CELL)
    grid = np.zeros((rows, cols), bool)
    for left, top, width, height in covered:
        grid[max(0, top) // CELL : -(-(top + height) // CELL), max(0, left) // CELL : -(-(left + width) // CELL)] = True
    side = MIN_SIDE // CELL
    opened = _dilate(_erode(~grid, side), side)
    if not opened.any():
        return None
    image = np.frombuffer(frame.pixels, np.uint8).reshape(frame.height, frame.width, 4)
    mask = np.kron(opened, np.ones((CELL, CELL), bool))[: frame.height, : frame.width]
    mask &= _busy_tiles(image, mask)
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask.any(axis=1))[0], np.nonzero(mask.any(axis=0))[0]
    top, bottom, left, right = int(ys[0]), int(ys[-1]) + 1, int(xs[0]), int(xs[-1]) + 1
    crop = image[top:bottom, left:right].copy()
    kept = mask[top:bottom, left:right]
    crop[~kept] = np.median(crop[kept], axis=0).astype(np.uint8)
    cropped = replace(frame, width=right - left, height=bottom - top, pixels=np.ascontiguousarray(crop).tobytes())
    return UncoveredCrop(cropped, (left, top), float(mask.mean()))


def _box_sum(grid: np.ndarray, side: int) -> np.ndarray:
    """Sum over every side x side window, indexed by the window's top-left cell."""

    integral = np.pad(grid.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return integral[side:, side:] - integral[:-side, side:] - integral[side:, :-side] + integral[:-side, :-side]


def _erode(grid: np.ndarray, side: int) -> np.ndarray:
    """Top-left cells of the side x side windows lying wholly inside ``grid``."""

    anchors = np.zeros_like(grid)
    if grid.shape[0] >= side and grid.shape[1] >= side:
        anchors[: grid.shape[0] - side + 1, : grid.shape[1] - side + 1] = _box_sum(grid, side) == side * side
    return anchors


def _dilate(anchors: np.ndarray, side: int) -> np.ndarray:
    padded = np.pad(anchors, ((side - 1, 0), (side - 1, 0)))
    return _box_sum(padded, side) > 0


def _busy_tiles(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    gray = image[:, :, :3].astype(np.int16).sum(axis=2) // 3
    edges = np.zeros(gray.shape, bool)
    edges[:, 1:] |= np.abs(np.diff(gray, axis=1)) > EDGE_THRESHOLD
    edges[1:, :] |= np.abs(np.diff(gray, axis=0)) > EDGE_THRESHOLD
    edges &= mask
    height, width = gray.shape
    rows, cols = -(-height // TILE), -(-width // TILE)
    padded = np.zeros((rows * TILE, cols * TILE), np.int32)
    padded[:height, :width] = edges
    busy = padded.reshape(rows, TILE, cols, TILE).sum(axis=(1, 3)) >= TILE_MIN_EDGES
    return np.kron(busy, np.ones((TILE, TILE), bool))[:height, :width]
