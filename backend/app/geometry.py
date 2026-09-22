"""Pure geometry helpers: points, affine matrices, curve flattening,
self-intersection detection and point-in-polygon tests.

All contours are represented as ``list[tuple[float, float]]``.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Iterable

Point = tuple[float, float]
Mat3 = tuple[float, float, float, float, float, float]  # a b c d e f (SVG 2x3 matrix)

IDENTITY: Mat3 = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# --------------------------------------------------------------------------- matrices
def mat_mul(m1: Mat3, m2: Mat3) -> Mat3:
    """Return m1 applied *after* m2 (m1 ∘ m2), matching SVG transform lists."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply_mat(m: Mat3, p: Point) -> Point:
    a, b, c, d, e, f = m
    x, y = p
    return (a * x + c * y + e, b * x + d * y + f)


# --------------------------------------------------------------------------- curves
def _dist(p: Point, q: Point) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _cubic_point(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    mt = 1.0 - t
    x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
    y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
    return (x, y)


def flatten_cubic(p0: Point, p1: Point, p2: Point, p3: Point,
                  steps: int = 16, include_start: bool = False) -> list[Point]:
    pts = [p0] if include_start else []
    for i in range(1, steps + 1):
        pts.append(_cubic_point(p0, p1, p2, p3, i / steps))
    return pts


def _quad_point(p0: Point, p1: Point, p2: Point, t: float) -> Point:
    mt = 1.0 - t
    return (
        mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0],
        mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1],
    )


def flatten_quad(p0: Point, p1: Point, p2: Point, steps: int = 12,
                 include_start: bool = False) -> list[Point]:
    pts = [p0] if include_start else []
    for i in range(1, steps + 1):
        pts.append(_quad_point(p0, p1, p2, i / steps))
    return pts


def flatten_arc(start: Point, rx_: float, ry_: float, phi_deg: float,
                large_arc: int, sweep: int, end: Point,
                steps: int = 24, include_start: bool = False) -> list[Point]:
    """Endpoint-to-centerpoint parameterization per the SVG 1.1 appendix F.6."""
    x1, y1 = start
    x2, y2 = end
    rx, ry = abs(rx_), abs(ry_)
    if rx < 1e-12 or ry < 1e-12:
        return [end]
    phi = math.radians(phi_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # Radii correction (F.6.6)
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s

    rxs, rys = rx * rx, ry * ry
    x1ps, y1ps = x1p * x1p, y1p * y1p
    radical = max(0.0, (rxs * rys - rxs * y1ps - rys * x1ps) /
                  (rxs * y1ps + rys * x1ps + 1e-18))
    coef = (math.sqrt(radical) * -1.0 if large_arc == sweep else math.sqrt(radical))
    cxp = coef * (rx * y1p / ry)
    cyp = coef * -(ry * x1p / rx)

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = max(-1.0, min(1.0, (ux * vx + uy * vy) /
                            (math.hypot(ux, uy) * math.hypot(vx, vy) + 1e-18)))
        a = math.acos(dot)
        return -a if ux * vy - uy * vx < 0 else a

    theta1 = angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle((x1p - cxp) / rx, (y1p - cyp) / ry,
                   (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    dtheta %= 2.0 * math.pi
    if sweep == 0 and dtheta > 0:
        dtheta -= 2.0 * math.pi
    if sweep == 1 and dtheta < 0:
        dtheta += 2.0 * math.pi

    pts = [start] if include_start else []
    for i in range(1, steps + 1):
        th = theta1 + dtheta * (i / steps)
        ex = rx * math.cos(th)
        ey = ry * math.sin(th)
        pts.append((cx + cos_phi * ex - sin_phi * ey,
                    cy + sin_phi * ex + cos_phi * ey))
    return pts


# --------------------------------------------------------------------------- polygon math
def path_length(pts: Sequence[Point], closed: bool = False) -> float:
    if len(pts) < 2:
        return 0.0
    total = sum(_dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))
    if closed:
        total += _dist(pts[-1], pts[0])
    return total


def signed_area2(pts: Sequence[Point]) -> float:
    """Twice the signed area (positive for CCW in standard coordinates)."""
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return s


def bbox(pts: Iterable[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------- intersections
def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _on_segment(p: Point, a: Point, b: Point, tol: float) -> bool:
    px, py = p
    ax, ay = a
    bx, by = b
    if abs(_cross(bx - ax, by - ay, px - ax, py - ay)) > tol:
        return False
    return (min(ax, bx) - tol <= px <= max(ax, bx) + tol and
            min(ay, by) - tol <= py <= max(ay, by) + tol)


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Return True if segment ab and segment cd cross or touch (T-junction)."""
    tol = 1e-9 * (1.0 + max(math.fabs(v) for v in
                            (*a, *b, *c, *d)))
    # Exact shared endpoints (consecutive segments) are not an intersection;
    # callers already skip adjacent pairs.
    if (max(abs(a[0] - c[0]), abs(a[1] - c[1])) < tol or
            max(abs(a[0] - d[0]), abs(a[1] - d[1])) < tol or
            max(abs(b[0] - c[0]), abs(b[1] - c[1])) < tol or
            max(abs(b[0] - d[0]), abs(b[1] - d[1])) < tol):
        return False
    p1 = _cross(b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1])
    p2 = _cross(b[0] - a[0], b[1] - a[1], d[0] - a[0], d[1] - a[1])
    p3 = _cross(d[0] - c[0], d[1] - c[1], a[0] - c[0], a[1] - c[1])
    p4 = _cross(d[0] - c[0], d[1] - c[1], b[0] - c[0], b[1] - c[1])
    if ((p1 > tol and p2 < -tol) or (p1 < -tol and p2 > tol)) and \
       ((p3 > tol and p4 < -tol) or (p3 < -tol and p4 > tol)):
        return True
    # T-junction: an endpoint lying strictly inside the other segment.
    if abs(p1) <= tol and _on_segment(c, a, b, tol):
        return True
    if abs(p2) <= tol and _on_segment(d, a, b, tol):
        return True
    if abs(p3) <= tol and _on_segment(a, c, d, tol):
        return True
    if abs(p4) <= tol and _on_segment(b, c, d, tol):
        return True
    return False


def find_self_intersections(pts: Sequence[Point], closed: bool) -> list[Point]:
    """Detect crossings/T-junctions between non-adjacent edges via a grid index."""
    n = len(pts)
    edge_count = n if closed else n - 1
    if edge_count < 3:
        return []

    minx, miny, maxx, maxy = bbox(pts)
    span = max(maxx - minx, maxy - miny, 1e-9)
    # Grid cells ~ average edge length, capped to a sane number of columns.
    total_len = path_length(pts, closed)
    cell = max(total_len / max(edge_count, 1), span / 1024.0, 1e-9)
    ncol = max(1, min(256, int(math.ceil(span / cell)) + 1))
    cell = span / ncol

    def cells_of(i: int) -> set[int]:
        p, q = pts[i], pts[(i + 1) % n]
        c0x = int((p[0] - minx) / cell)
        c0y = int((p[1] - miny) / cell)
        c1x = int((q[0] - minx) / cell)
        c1y = int((q[1] - miny) / cell)
        out: set[int] = set()
        steps_n = max(abs(c1x - c0x), abs(c1y - c0y), 0)
        if steps_n > 64:  # pathological long edge: fall back to brute force
            return set()
        for k in range(steps_n + 1):
            t = k / steps_n if steps_n else 0.0
            cx = int((p[0] + (q[0] - p[0]) * t - minx) / cell)
            cy = int((p[1] + (q[1] - p[1]) * t - miny) / cell)
            out.add(min(cx, ncol - 1) * ncol + min(cy, ncol - 1))
        return out

    cells_edges: list[set[int]] = []
    buckets: dict[int, list[int]] = {}
    fallback = False
    for i in range(edge_count):
        cs = cells_of(i)
        if not cs:
            fallback = True
            break
        cells_edges.append(cs)
        for cell_id in cs:
            buckets.setdefault(cell_id, []).append(i)

    hits: list[Point] = []
    seen_pairs: set[tuple[int, int]] = set()

    def adjacent(i: int, j: int) -> bool:
        if abs(i - j) == 1:
            return True
        return closed and {i, j} == {0, edge_count - 1}

    def check_pair(i: int, j: int) -> None:
        lo, hi = (i, j) if i < j else (j, i)
        if (lo, hi) in seen_pairs:
            return
        seen_pairs.add((lo, hi))
        a, b = pts[lo], pts[(lo + 1) % n]
        c, d = pts[hi], pts[(hi + 1) % n]
        if segments_intersect(a, b, c, d):
            # Approximate intersection point by the crossing location.
            ip = _intersection_point(a, b, c, d)
            if ip is not None:
                hits.append(ip)

    if fallback:
        for i in range(edge_count):
            for j in range(i + 1, edge_count):
                if not adjacent(i, j):
                    check_pair(i, j)
    else:
        for edge_ids in buckets.values():
            if len(edge_ids) < 2:
                continue
            for x in range(len(edge_ids)):
                for y in range(x + 1, len(edge_ids)):
                    i, j = edge_ids[x], edge_ids[y]
                    if not adjacent(i, j):
                        check_pair(i, j)
    return hits


def _intersection_point(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    x1, y1 = a
    x2, y2 = b
    x3, y3 = c
    x4, y4 = d
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-18:
        # Collinear overlap: report midpoint
        return ((x1 + x3) / 2.0, (y1 + y3) / 2.0)
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


# --------------------------------------------------------------------------- point in polygon
def point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    """Boundary is considered inside (ray casting + boundary check)."""
    x, y = p
    n = len(poly)
    inside = False
    j = n - 1
    tol = 1e-9
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if abs((xj - xi) * (y - yi) - (yj - yi) * (x - xi)) <= tol and \
                min(xi, xj) - tol <= x <= max(xi, xj) + tol and \
                min(yi, yj) - tol <= y <= max(yi, yj) + tol:
            return True
        if (yi > y) != (yj > y):
            x_inter = (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi
            if x_inter > x:
                inside = not inside
        j = i
    return inside
