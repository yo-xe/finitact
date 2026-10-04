"""Group OCR text lines that sit on one uniform-colour panel into a single fill target (ADR-0029).

A fill on a text crop told the provider "a 61x20 word", so it preferred BLOCKED over the editor
body (BUG-0031). The panel is only geometry: a uniform background is not proof that the area is
editable, so a block is offered beside, never instead of, the click candidates and verify still
judges the outcome.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Sequence

import numpy as np

Rect = tuple[int, int, int, int]

CELL = 4
COLOR_TOLERANCE = 3
# Frame-hugging areas (menu bars, window chrome) are not panels of their own.
EDGE_MARGIN_CELLS = 3
MIN_FILL_RATIO = 0.5
# A panel must be clearly taller than one line, or tab strips and single-line boxes would absorb their text.
MIN_HEIGHT_LINES = 4
# A crop whose ring is split between panels has no unambiguous owner (consult 20260925-0949 point 1).
MIN_RING_SHARE = 0.5
MAX_RIVAL_SHARE = 0.25
# Keeps the delivery point off glyphs the OCR crop cut tightly and off panel borders.
TEXT_CLEARANCE = 2


@dataclass(frozen=True)
class PanelBlock:
    rect: Rect
    delivery_point: tuple[int, int]
    members: tuple[int, ...]


def panel_blocks(
    pixels: np.ndarray, rects: Sequence[Rect], eligible: Sequence[bool] | None = None
) -> list[PanelBlock]:
    """``pixels`` is HxWx3+ (any channel order); ``members`` index ``rects``, top-to-bottom."""

    img = pixels[:, :, :3].astype(np.int16)
    h, w, _ = img.shape
    gh, gw = h // CELL, w // CELL
    if gh < 2 * EDGE_MARGIN_CELLS or gw < 2 * EDGE_MARGIN_CELLS:
        return []
    cells = img[: gh * CELL, : gw * CELL].reshape(gh, CELL, gw, CELL, 3)
    lo, hi = cells.min(axis=(1, 3)), cells.max(axis=(1, 3))
    uniform = (hi - lo).max(axis=2) <= COLOR_TOLERANCE
    # Flooding checks cells one by one; Python scalars avoid a whole-grid comparison per component.
    lo_flat = lo.reshape(-1, 3).tolist()
    hi_flat = hi.reshape(-1, 3).tolist()
    uniform_flat = uniform.ravel().tolist()
    labels = [0] * (gh * gw)
    components: list[np.ndarray] = [np.zeros(0)]
    panel_ok: dict[tuple[int, int], bool] = {}

    def label_at(flat: int) -> int:
        if not uniform_flat[flat]:
            return 0
        if not labels[flat]:
            components.append(_flood(lo_flat, hi_flat, uniform_flat, labels, flat, len(components), gh, gw))
        return labels[flat]

    owner: dict[int, list[int]] = {}
    for index, (x, y, rw, rh) in enumerate(rects):
        if eligible is not None and not eligible[index]:
            continue
        gx0, gy0 = max(x // CELL - 1, 0), max(y // CELL - 1, 0)
        gx1, gy1 = min((x + rw - 1) // CELL + 2, gw), min((y + rh - 1) // CELL + 2, gh)
        ring = [
            gy * gw + gx
            for gy in range(gy0, gy1)
            for gx in range(gx0, gx1)
            if gy in (gy0, gy1 - 1) or gx in (gx0, gx1 - 1)
        ]
        if not ring:
            continue
        counts: dict[int, int] = {}
        for flat in ring:
            found = label_at(flat)
            if found:
                counts[found] = counts.get(found, 0) + 1
        if not counts:
            continue
        ranked = sorted(counts.values(), reverse=True)
        if ranked[0] < MIN_RING_SHARE * len(ring) or (len(ranked) > 1 and ranked[1] >= MAX_RIVAL_SHARE * len(ring)):
            continue
        component = max(counts, key=counts.get)
        box = _cell_box(components[component])
        if (component, rh) not in panel_ok:
            panel_ok[component, rh] = _is_panel(box, components[component], gh, gw, rh)
        if not panel_ok[component, rh]:
            continue
        bx, by, bw, bh = box
        # Grid rounding: the crop may overhang the cell box by less than one cell.
        if x < bx - CELL or y < by - CELL or x + rw > bx + bw + CELL or y + rh > by + bh + CELL:
            continue
        owner.setdefault(component, []).append(index)
    blocks = []
    for component, members in owner.items():
        members.sort(key=lambda index: (rects[index][1], rects[index][0]))
        # Below the last line, at the left edge of the widest line: the widest line is the likeliest
        # body text, where gutters (VSCode line numbers) are narrow.
        widest = max(members, key=lambda index: rects[index][2])
        last = rects[members[-1]]
        target = (rects[widest][0] + CELL, last[1] + last[3] + last[3] // 2)
        point = _delivery_point(components[component], rects, target)
        if point is not None:
            blocks.append(PanelBlock(_cell_box(components[component]), point, tuple(members)))
    return sorted(blocks, key=lambda block: (block.rect[1], block.rect[0]))


def _flood(lo, hi, uniform, labels, start: int, value: int, gh: int, gw: int) -> np.ndarray:
    seed = lo[start]
    mask = np.zeros(gh * gw, bool)
    labels[start] = value
    queue = deque([start])
    visited = [start]
    while queue:
        flat = queue.popleft()
        y, x = divmod(flat, gw)
        for near in (flat - gw if y else -1, flat + gw if y + 1 < gh else -1, flat - 1 if x else -1, flat + 1 if x + 1 < gw else -1):
            if near < 0 or labels[near] or not uniform[near]:
                continue
            a, b = lo[near], hi[near]
            if (
                abs(a[0] - seed[0]) <= COLOR_TOLERANCE and abs(a[1] - seed[1]) <= COLOR_TOLERANCE
                and abs(a[2] - seed[2]) <= COLOR_TOLERANCE and abs(b[0] - seed[0]) <= COLOR_TOLERANCE
                and abs(b[1] - seed[1]) <= COLOR_TOLERANCE and abs(b[2] - seed[2]) <= COLOR_TOLERANCE
            ):
                labels[near] = value
                visited.append(near)
                queue.append(near)
    mask[visited] = True
    return mask.reshape(gh, gw)


def _cell_box(mask: np.ndarray) -> Rect:
    ys, xs = np.nonzero(mask)
    return int(xs.min()) * CELL, int(ys.min()) * CELL, int(xs.max() - xs.min() + 1) * CELL, int(ys.max() - ys.min() + 1) * CELL


def _is_panel(box: Rect, mask: np.ndarray, gh: int, gw: int, line_height: int) -> bool:
    bx, by, bw, bh = (value // CELL for value in box)
    if by < EDGE_MARGIN_CELLS or bx < EDGE_MARGIN_CELLS or by + bh > gh - EDGE_MARGIN_CELLS or bx + bw > gw - EDGE_MARGIN_CELLS:
        return False
    return mask.sum() >= MIN_FILL_RATIO * bw * bh and box[3] >= MIN_HEIGHT_LINES * line_height


def _delivery_point(mask: np.ndarray, rects: Sequence[Rect], target: tuple[int, int]) -> tuple[int, int] | None:
    """The panel cell nearest ``target`` whose neighbours are panel too and that touches no text.

    No centre fallback: a panel without such a cell may be covered by controls the OCR did not read.
    """

    gh, gw = mask.shape
    inner = mask.copy()
    inner[1:, :] &= mask[:-1, :]
    inner[:-1, :] &= mask[1:, :]
    inner[:, 1:] &= mask[:, :-1]
    inner[:, :-1] &= mask[:, 1:]
    inner[0, :] = inner[-1, :] = False
    inner[:, 0] = inner[:, -1] = False
    for x, y, rw, rh in rects:
        gx0 = max((x - TEXT_CLEARANCE) // CELL, 0)
        gy0 = max((y - TEXT_CLEARANCE) // CELL, 0)
        gx1 = min((x + rw + TEXT_CLEARANCE - 1) // CELL + 1, gw)
        gy1 = min((y + rh + TEXT_CLEARANCE - 1) // CELL + 1, gh)
        inner[gy0:gy1, gx0:gx1] = False
    ys, xs = np.nonzero(inner)
    if ys.size == 0:
        return None
    target_x, target_y = target
    centres_x, centres_y = xs * CELL + CELL // 2, ys * CELL + CELL // 2
    best = int(np.argmin((centres_x - target_x) ** 2 + (centres_y - target_y) ** 2))
    return int(centres_x[best]), int(centres_y[best])
