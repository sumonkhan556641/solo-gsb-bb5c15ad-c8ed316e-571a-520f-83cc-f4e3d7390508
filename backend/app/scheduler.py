"""Cutting-order solver.

Rules
-----
* Open or self-intersecting contours are excluded from cutting and reported
  as invalid (the UI shows them as warnings).
* Valid closed contours form a nesting forest: contour B is inside A when one
  of B's points lies in A and A is the smallest such enclosing contour.
* Constraint: every inner contour (hole) is cut before the contour that
  surrounds it ("先内后外").
* Objective: minimum total empty travel.  At each step only contours whose
  enclosing parent is still uncut and whose children are already cut are
  "ready"; from the ready set we greedily pick the contour nearest the
  current head position.  Ties are broken by contour index, so the result is
  fully deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geometry as g
from .geometry import Point, bbox, point_in_polygon, signed_area2


@dataclass
class ContourInput:
    id: int
    kind: str
    points: list[Point]
    closed: bool
    self_intersections: list[Point]


@dataclass
class AnalysedContour:
    id: int
    kind: str
    points: list[Point]
    closed: bool
    self_intersections: list[Point]
    valid: bool
    issues: list[str] = field(default_factory=list)
    area: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0)
    parent: int | None = None
    depth: int = 0
    children: list[int] = field(default_factory=list)
    cut_index: int | None = None
    distance_from_previous: float = 0.0
    start_point: Point | None = None


@dataclass
class ScheduleResult:
    contours: list[AnalysedContour]
    order: list[int]
    segments: list[dict]
    total_travel: float
    total_cut: float
    invalid_count: int


CLOSE_TOL = 1e-6
MIN_POINTS = 3
DEGEN_AREA_TOL = 1e-9


def analyse_contours(raw: list[ContourInput], global_scale: float = 1.0) -> list[AnalysedContour]:
    analysed: list[AnalysedContour] = []
    tol = CLOSE_TOL * max(1.0, global_scale)

    for c in raw:
        issues: list[str] = []
        pts = c.points
        # Normalise a nearly-closed open polyline: Z-less paths whose last
        # point coincides with the first are still open by declaration.
        if not c.closed and len(pts) >= 2 and \
                math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) <= tol:
            # Still considered open but close it geometrically for area math.
            pass
        if not c.closed:
            issues.append("not closed")
        if c.self_intersections:
            issues.append(f"self-intersecting ({len(c.self_intersections)} crossing)")
        if len(pts) < MIN_POINTS:
            issues.append("too few points")

        if c.closed and pts and pts[-1] != pts[0]:
            pts_calc = pts + [pts[0]]
        else:
            pts_calc = pts
        area = abs(signed_area2(pts)) / 2.0 if c.closed and len(pts) >= 3 else 0.0
        if c.closed and area <= DEGEN_AREA_TOL * max(1.0, global_scale) ** 2:
            issues.append("zero-area contour")

        valid = not issues
        analysed.append(AnalysedContour(
            id=c.id, kind=c.kind, points=pts, closed=c.closed,
            self_intersections=c.self_intersections, valid=valid,
            issues=issues, area=area,
            bbox=bbox(pts) if pts else (0, 0, 0, 0),
        ))

    _build_nesting(analysed)
    return analysed


def _bbox_contains(outer: tuple[float, float, float, float],
                   inner: tuple[float, float, float, float], tol: float) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and
            inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def _build_nesting(contours: list[AnalysedContour]) -> None:
    valid = [c for c in contours if c.valid]
    if not valid:
        return
    span = 0.0
    for c in valid:
        span = max(span, c.bbox[2] - c.bbox[0], c.bbox[3] - c.bbox[1])
    tol = 1e-7 * max(span, 1.0)

    for c in valid:
        parent: int | None = None
        # Primary test: a representative point of c lies inside the candidate.
        representative = c.points[0]
        for p in valid:
            if p.id == c.id or p.area <= c.area + tol:
                continue
            if not _bbox_contains(p.bbox, c.bbox, tol):
                continue
            if point_in_polygon(representative, p.points):
                if parent is None or p.area < contours[parent].area:
                    parent = p.id
        # Fallback: bbox containment only (degenerate touching geometry).
        if parent is None:
            for p in valid:
                if p.id == c.id or p.area <= c.area + tol:
                    continue
                if _bbox_contains(p.bbox, c.bbox, tol):
                    if parent is None or p.area < contours[parent].area:
                        parent = p.id
        c.parent = parent
        if parent is not None:
            contours[parent].children.append(c.id)

    # Depth via iterative traversal.
    for c in valid:
        d, node = 0, c
        seen: set[int] = set()
        while node.parent is not None and node.parent not in seen:
            seen.add(node.parent)
            d += 1
            node = contours[node.parent]
        c.depth = d


def _nearest_point_on_contour(pos: Point, c: AnalysedContour) -> tuple[Point, int]:
    """Closest contour vertex to ``pos``; returns (point, vertex index)."""
    best_i, best_d2 = 0, math.inf
    for i, p in enumerate(c.points):
        d2 = (p[0] - pos[0]) ** 2 + (p[1] - pos[1]) ** 2
        if d2 < best_d2:
            best_d2, best_i = d2, i
    return c.points[best_i], best_i


def _rotated(points: list[Point], start_i: int) -> list[Point]:
    """Rotate the vertex list so cutting begins at ``start_i``."""
    if not points:
        return points
    return points[start_i:] + points[:start_i]


def solve(raw: list[ContourInput], home: Point = (0.0, 0.0)) -> ScheduleResult:
    scale = 1.0
    if raw:
        all_b = bbox([p for c in raw for p in c.points])
        scale = max(all_b[2] - all_b[0], all_b[3] - all_b[1], 1.0)
    contours = analyse_contours(raw, scale)
    by_id = {c.id: c for c in contours}

    valid = [c for c in contours if c.valid]
    total_cut = sum(g.path_length(c.points, closed=True) for c in valid)

    remaining_children: dict[int, set[int]] = {
        c.id: set(c.children) for c in valid
    }
    # A contour is ready when *it* has no uncut parent dependency in the sense
    # that it must be cut before its parent: readiness = all its children cut.
    ready = {c.id for c in valid if not c.children}
    order: list[int] = []
    segments: list[dict] = []

    head = home
    total_travel = 0.0
    cut_no = 0

    while ready:
        # Choose nearest ready contour; deterministic tie-break by id.
        best_id = min(
            ready,
            key=lambda cid: (
                (_nearest_point_on_contour(head, by_id[cid])[0][0] - head[0]) ** 2
                + (_nearest_point_on_contour(head, by_id[cid])[0][1] - head[1]) ** 2,
                cid,
            ),
        )
        ready.remove(best_id)
        c = by_id[best_id]
        start_pt, start_i = _nearest_point_on_contour(head, c)
        travel = math.hypot(start_pt[0] - head[0], start_pt[1] - head[1])
        total_travel += travel

        c.cut_index = cut_no
        c.distance_from_previous = travel
        c.start_point = start_pt
        order.append(best_id)

        oriented = _rotated(c.points, start_i)
        closed_path = oriented + [oriented[0]] if c.closed and oriented else oriented
        segments.append({
            "type": "travel",
            "cut_index": cut_no,
            "contour_id": best_id,
            "from": [head[0], head[1]],
            "to": [start_pt[0], start_pt[1]],
            "length": travel,
        })
        segments.append({
            "type": "cut",
            "cut_index": cut_no,
            "contour_id": best_id,
            "points": [[p[0], p[1]] for p in closed_path],
            "length": g.path_length(c.points, closed=True),
        })

        # A closed loop ends where it started.
        head = start_pt if c.closed else (oriented[-1] if oriented else start_pt)
        cut_no += 1

        # Parent may become ready once this child is cut.
        par = c.parent
        if par is not None:
            remaining_children[par].discard(best_id)
            if not remaining_children[par] and by_id[par].cut_index is None \
                    and par not in ready and par not in order:
                ready.add(par)

    # Defensive: ready-set can only stall on a cycle, which nesting forbids.
    assert cut_no == len(valid), "nesting cycle detected"

    # Final return to home.
    back = math.hypot(home[0] - head[0], home[1] - head[1])
    total_travel += back
    if order:
        segments.append({
            "type": "travel",
            "cut_index": None,
            "contour_id": None,
            "from": [head[0], head[1]],
            "to": [home[0], home[1]],
            "length": back,
        })

    return ScheduleResult(
        contours=contours,
        order=order,
        segments=segments,
        total_travel=total_travel,
        total_cut=total_cut,
        invalid_count=len(contours) - len(valid),
    )
