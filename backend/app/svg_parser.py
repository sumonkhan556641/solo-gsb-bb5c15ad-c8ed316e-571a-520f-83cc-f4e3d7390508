"""SVG importer.

Parses ``path`` / ``rect`` / ``circle`` / ``ellipse`` / ``line`` / ``polyline``
/ ``polygon`` elements (including ``transform`` lists), flattens curves into
polylines and returns contours in document order.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import geometry as g
from .geometry import IDENTITY, Mat3, Point, apply_mat, mat_mul

COMMAND_TOKENS = set("MLCSTQAHVZmlcstqahvz")
TOKEN_RE = re.compile(
    r"[MLCSTQAHVZmlcstqahvz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
)
TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

# Elements whose subtree defines reusable / non-rendered content.
SKIP_SUBTREE = {"defs", "symbol", "clipPath", "mask", "marker", "pattern",
                "style", "title", "desc", "metadata"}


@dataclass
class RawContour:
    element_id: int
    kind: str                       # path | rect | circle | ...
    points: list[Point] = field(default_factory=list)
    closed: bool = False


class SVGParseError(ValueError):
    pass


# --------------------------------------------------------------------------- path data
def parse_path_data(d: str) -> list[tuple[list[Point], bool]]:
    """Parse a path ``d`` attribute into a list of (points, closed) subpaths."""
    tokens = TOKEN_RE.findall(d)
    subpaths: list[tuple[list[Point], bool]] = []

    cur: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    pts: list[Point] = []
    closed = False
    cmd = ""
    prev_cmd = ""
    prev_cubic_ctrl: Point | None = None
    prev_quad_ctrl: Point | None = None
    has_subpath = False

    ti = 0

    def nums(n: int) -> list[float]:
        nonlocal ti
        vals: list[float] = []
        while len(vals) < n:
            if ti >= len(tokens) or tokens[ti] in COMMAND_TOKENS:
                raise SVGParseError(
                    f"path command {cmd!r} expects {n} numbers, got {len(vals)}")
            vals.append(float(tokens[ti]))
            ti += 1
        return vals

    def begin_subpath(p: Point) -> None:
        nonlocal pts, closed, has_subpath, start
        if has_subpath:
            subpaths.append((pts, closed))
        pts, closed = [p], False
        start, has_subpath = p, True

    while ti < len(tokens):
        tok = tokens[ti]
        if tok in COMMAND_TOKENS:
            cmd = tok
            ti += 1
        # else: implicit repeat (lineto after moveto handled below)

        rel = cmd.islower()
        upper = cmd.upper()

        def ab(x: float, y: float) -> Point:
            return (cur[0] + x, cur[1] + y) if rel else (x, y)

        if upper == "M":
            vals = nums(2)
            cur = ab(vals[0], vals[1])
            begin_subpath(cur)
            cmd = "l" if rel else "L"   # subsequent pairs are implicit lineto
        elif upper == "L":
            vals = nums(2)
            cur = ab(vals[0], vals[1])
            pts.append(cur)
        elif upper == "H":
            x = nums(1)[0]
            cur = (cur[0] + x, cur[1]) if rel else (x, cur[1])
            pts.append(cur)
        elif upper == "V":
            y = nums(1)[0]
            cur = (cur[0], cur[1] + y) if rel else (cur[0], y)
            pts.append(cur)
        elif upper == "C":
            v = nums(6)
            p1, p2 = ab(v[0], v[1]), ab(v[2], v[3])
            end = ab(v[4], v[5])
            pts.extend(g.flatten_cubic(cur, p1, p2, end))
            prev_cubic_ctrl, cur = p2, end
        elif upper == "S":
            v = nums(4)
            if prev_cmd.upper() in ("C", "S") and prev_cubic_ctrl is not None:
                p1 = (2 * cur[0] - prev_cubic_ctrl[0],
                      2 * cur[1] - prev_cubic_ctrl[1])
            else:
                p1 = cur
            p2, end = ab(v[0], v[1]), ab(v[2], v[3])
            pts.extend(g.flatten_cubic(cur, p1, p2, end))
            prev_cubic_ctrl, cur = p2, end
        elif upper == "Q":
            v = nums(4)
            p1, end = ab(v[0], v[1]), ab(v[2], v[3])
            pts.extend(g.flatten_quad(cur, p1, end))
            prev_quad_ctrl, cur = p1, end
        elif upper == "T":
            v = nums(2)
            if prev_cmd.upper() in ("Q", "T") and prev_quad_ctrl is not None:
                p1 = (2 * cur[0] - prev_quad_ctrl[0],
                      2 * cur[1] - prev_quad_ctrl[1])
            else:
                p1 = cur
            end = ab(v[0], v[1])
            pts.extend(g.flatten_quad(cur, p1, end))
            prev_quad_ctrl, cur = p1, end
        elif upper == "A":
            v = nums(7)
            end = ab(v[5], v[6])
            pts.extend(g.flatten_arc(cur, v[0], v[1], v[2],
                                     int(v[3]), int(v[4]), end))
            cur = end
        elif upper == "Z":
            closed = True
            if pts:
                cur = pts[0]
                if pts[-1] != start:
                    pts.append(start)
        else:  # pragma: no cover - TOKEN_RE guarantees known command
            raise SVGParseError(f"unsupported path command {cmd!r}")

        if upper not in ("C", "S"):
            prev_cubic_ctrl = None
        if upper not in ("Q", "T"):
            prev_quad_ctrl = None
        prev_cmd = upper

    if has_subpath:
        subpaths.append((pts, closed))
    return subpaths


# --------------------------------------------------------------------------- transforms
def parse_transform(text: str | None) -> Mat3:
    if not text:
        return IDENTITY
    m: Mat3 = IDENTITY
    for name, raw_args in TRANSFORM_RE.findall(text):
        args = [float(x) for x in NUM_RE.findall(raw_args)]
        if name == "matrix" and len(args) == 6:
            t = (args[0], args[1], args[2], args[3], args[4], args[5])
        elif name == "translate":
            tx = args[0]
            ty = args[1] if len(args) > 1 else 0.0
            t = (1, 0, 0, 1, tx, ty)
        elif name == "scale":
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            t = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate":
            import math
            ang = math.radians(args[0])
            cos_a, sin_a = math.cos(ang), math.sin(ang)
            rot: Mat3 = (cos_a, sin_a, -sin_a, cos_a, 0, 0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                t = mat_mul((1, 0, 0, 1, cx, cy),
                            mat_mul(rot, (1, 0, 0, 1, -cx, -cy)))
            else:
                t = rot
        elif name == "skewX":
            import math
            t = (1, 0, math.tan(math.radians(args[0])), 1, 0, 0)
        elif name == "skewY":
            import math
            t = (1, math.tan(math.radians(args[0])), 0, 1, 0, 0)
        else:
            continue
        m = mat_mul(t, m)
    return m


# --------------------------------------------------------------------------- shapes
def _shape_to_path(tag: str, attrs: dict[str, str]) -> list[tuple[list[Point], bool]]:
    def f(key: str, default: float = 0.0) -> float:
        v = attrs.get(key)
        return float(v) if v is not None else default

    if tag == "path":
        return parse_path_data(attrs.get("d", ""))
    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        if w <= 0 or h <= 0:
            return []
        rx_attr, ry_attr = attrs.get("rx"), attrs.get("ry")
        if rx_attr is None and ry_attr is None:
            rx = ry = 0.0
        else:
            rx = float(rx_attr) if rx_attr is not None else float(ry_attr or 0)
            ry = float(ry_attr) if ry_attr is not None else rx
            rx = min(rx, abs(w) / 2.0)
            ry = min(ry, abs(h) / 2.0)
        if rx == 0 or ry == 0:
            d = (f"M{x},{y} H{x+w} V{y+h} H{x} Z")
        else:
            d = (
                f"M{x+rx},{y} "
                f"H{x+w-rx} A{rx},{ry} 0 0 1 {x+w},{y+ry} "
                f"V{y+h-ry} A{rx},{ry} 0 0 1 {x+w-rx},{y+h} "
                f"H{x+rx} A{rx},{ry} 0 0 1 {x},{y+h-ry} "
                f"V{y+ry} A{rx},{ry} 0 0 1 {x+rx},{y} Z"
            )
        return parse_path_data(d)
    if tag == "circle":
        cx, cy, r = f("cx"), f("cy"), f("r")
        if r <= 0:
            return []
        return parse_path_data(
            f"M{cx-r},{cy} A{r},{r} 0 1 0 {cx+r},{cy} "
            f"A{r},{r} 0 1 0 {cx-r},{cy} Z")
    if tag == "ellipse":
        cx, cy, rx, ry = f("cx"), f("cy"), f("rx"), f("ry")
        if rx <= 0 or ry <= 0:
            return []
        return parse_path_data(
            f"M{cx-rx},{cy} A{rx},{ry} 0 1 0 {cx+rx},{cy} "
            f"A{rx},{ry} 0 1 0 {cx-rx},{cy} Z")
    if tag == "line":
        x1, y1, x2, y2 = f("x1"), f("y1"), f("x2"), f("y2")
        return [([(x1, y1), (x2, y2)], False)]
    if tag in ("polyline", "polygon"):
        nums = [float(x) for x in NUM_RE.findall(attrs.get("points", ""))]
        pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        if len(pts) < 2:
            return []
        return [(pts, tag == "polygon")]
    return []


RENDERABLE = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_svg(text: str) -> tuple[list[RawContour], tuple[float, float, float, float] | None]:
    """Parse an SVG document. Returns contours and the declared viewBox or None."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SVGParseError(f"invalid XML: {exc}") from exc
    if _localname(root.tag) != "svg":
        raise SVGParseError("root element is not <svg>")

    view_box: tuple[float, float, float, float] | None = None
    vb = root.get("viewBox")
    if vb:
        parts = [float(x) for x in re.split(r"[\s,]+", vb.strip()) if x]
        if len(parts) == 4:
            view_box = (parts[0], parts[1], parts[2], parts[3])

    contours: list[RawContour] = []
    next_id = 0

    def walk(el: ET.Element, matrix: Mat3) -> None:
        nonlocal next_id
        for child in el:
            tag = _localname(child.tag)
            if tag in SKIP_SUBTREE:
                continue
            child_matrix = mat_mul(parse_transform(child.get("transform")), matrix)
            if tag in RENDERABLE:
                try:
                    subpaths = _shape_to_path(tag, child.attrib)
                except SVGParseError:
                    raise
                for pts, closed in subpaths:
                    transformed = [apply_mat(child_matrix, p) for p in pts]
                    contours.append(RawContour(next_id, tag, transformed, closed))
                    next_id += 1
            walk(child, child_matrix)

    walk(root, parse_transform(root.get("transform")))
    if not contours:
        raise SVGParseError("no renderable contours found in SVG")
    return contours, view_box
