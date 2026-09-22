"""SVG 轮廓解析与几何分析。

- 基于 svgelements 将 rect/circle/ellipse/polygon/polyline/path 等元素
  统一为子路径，并把弧/贝塞尔曲线按弧长容差离散成多边形点列；
- 标记未闭合（open）与自交（self-intersecting）轮廓；
- 计算嵌套（包含）关系，直接父轮廓 = 包含该轮廓的面积最小者。

SVG 用户单位按 1 单位 = 1 mm 处理（G-code 常见假设）。
"""
from __future__ import annotations

import io
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

import svgelements as se

# 坐标重合 / 自交判断的绝对容差（mm）
COINC_EPS = 1e-6
# 单条曲线最大采样点数（防止极端路径爆炸）
MAX_SAMPLES_PER_CURVE = 4096


@dataclass
class Contour:
    """一条闭合或未闭合的轮廓（已离散为点列，闭合时首末点不重复）。"""

    index: int
    source: str
    points: list[tuple[float, float]]
    closed: bool
    area: float = 0.0
    length: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    self_intersecting: bool = False
    issues: list[str] = field(default_factory=list)
    # 嵌套关系：直接外层轮廓的 index（None = 最外层）
    parent: Optional[int] = None
    depth: int = 0
    # ---- 排程求解结果 ----
    order: Optional[int] = None  # 切割先后序号，从 0 开始
    start: Optional[tuple[float, float]] = None
    end: Optional[tuple[float, float]] = None
    reversed: bool = False

    @property
    def valid(self) -> bool:
        """可参与嵌套求解与切割的轮廓。"""
        return (
            self.closed
            and not self.self_intersecting
            and len(self.points) >= 3
            and self.area > 0.0
        )


@dataclass
class SvgMeta:
    viewbox: tuple[float, float, float, float]
    width: float
    height: float
    element_count: int


# ---------------------------------------------------------------- 采样

def _point_line_distance(p, a, b) -> float:
    """点 P 到线段 AB 的距离。"""
    px, py = p; ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy)


def _adaptive(seg, t0: float, t1: float, p0, p1, tol: float,
              depth: int, out: list) -> None:
    """按弦高（弧中点到弦的距离）递归细分，直到 <= tol 或达到深度上限。"""
    tm = 0.5 * (t0 + t1)
    pm = seg.point(tm)
    pm = (float(pm.x), float(pm.y))
    if depth <= 0 or _point_line_distance(pm, p0, p1) <= tol:
        out.append(p1)
        return
    _adaptive(seg, t0, tm, p0, pm, tol, depth - 1, out)
    _adaptive(seg, tm, t1, pm, p1, tol, depth - 1, out)


def _sample_curve(seg, tolerance: float) -> list[tuple[float, float]]:
    """自适应采样弧/贝塞尔曲线，返回包含起点与终点的点列。"""
    p0 = seg.point(0.0)
    p1 = seg.point(1.0)
    p0 = (float(p0.x), float(p0.y))
    p1 = (float(p1.x), float(p1.y))
    if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) <= COINC_EPS:
        return [p0]
    out: list[tuple[float, float]] = [p0]
    _adaptive(seg, 0.0, 1.0, p0, p1, tolerance, 16, out)
    if len(out) > MAX_SAMPLES_PER_CURVE:  # 极端曲线的兜底
        step = len(out) / MAX_SAMPLES_PER_CURVE
        out = [out[int(i * step)] for i in range(MAX_SAMPLES_PER_CURVE)] + [out[-1]]
    return out


def _sample_segment(seg, tolerance: float) -> list[tuple[float, float]]:
    if isinstance(seg, se.Move):
        return []
    # Close 与 Line 都是直线段，仅保留端点，避免无谓的点膨胀
    if isinstance(seg, (se.Line, se.Close)):
        return [(float(seg.start.x), float(seg.start.y)),
                (float(seg.end.x), float(seg.end.y))]
    # Arc / QuadraticBezier / CubicBezier / 其它曲线
    return _sample_curve(seg, tolerance)


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > COINC_EPS or abs(p[1] - out[-1][1]) > COINC_EPS:
            out.append(p)
    return out


def _subpath_points(subpath, tolerance: float) -> tuple[list[tuple[float, float]], bool]:
    segs = list(subpath.segments())
    closed = any(isinstance(s, se.Close) for s in segs)
    move = next((s for s in segs if isinstance(s, se.Move)), None)
    pts: list[tuple[float, float]] = []
    if move is not None:
        pts.append((float(move.end.x), float(move.end.y)))
    for s in segs:
        if isinstance(s, se.Move):
            continue
        sampled = _sample_segment(s, tolerance)
        # 每段跳过与前段重合的起点
        for p in sampled[1:]:
            pts.append(p)
    pts = _dedupe(pts)
    # 闭合轮廓去掉与起点重合的末点（闭合边隐式存在）
    if closed and len(pts) > 1:
        a, b = pts[0], pts[-1]
        if abs(a[0] - b[0]) <= COINC_EPS and abs(a[1] - b[1]) <= COINC_EPS:
            pts.pop()
    return pts, closed


# ---------------------------------------------------------------- 几何量

def polygon_area(points: list[tuple[float, float]]) -> float:
    s = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def polygon_length(points: list[tuple[float, float]], closed: bool) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    n = len(points)
    m = n if closed else n - 1
    for i in range(m):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def polygon_bbox(points: list[tuple[float, float]]):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------- 自交检测

def _cross(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_segment(ax, ay, bx, by, px, py, eps: float) -> bool:
    """点 P 是否落在线段 AB 上（含端点）。"""
    if abs(_cross(ax, ay, bx, by, px, py)) > eps:
        return False
    return (min(ax, bx) - eps <= px <= max(ax, bx) + eps
            and min(ay, by) - eps <= py <= max(ay, by) + eps)


def edges_intersect(a, b, c, d, eps: float = 1e-7) -> bool:
    """线段 AB 与 CD 是否相交（含端点接触与共线重叠）。"""
    ax, ay = a; bx, by = b; cx, cy = c; dx, dy = d
    t1 = _cross(ax, ay, bx, by, cx, cy)
    t2 = _cross(ax, ay, bx, by, dx, dy)
    t3 = _cross(cx, cy, dx, dy, ax, ay)
    t4 = _cross(cx, cy, dx, dy, bx, by)
    if ((t1 > eps and t2 < -eps) or (t1 < -eps and t2 > eps)) and \
       ((t3 > eps and t4 < -eps) or (t3 < -eps and t4 > eps)):
        return True
    # 端点接触 / 共线重叠
    if _on_segment(ax, ay, bx, by, cx, cy, eps):
        return True
    if _on_segment(ax, ay, bx, by, dx, dy, eps):
        return True
    if _on_segment(cx, cy, dx, dy, ax, ay, eps):
        return True
    if _on_segment(cx, cy, dx, dy, bx, by, eps):
        return True
    return False


def has_self_intersection(points: list[tuple[float, float]], closed: bool,
                          cell_size: float) -> bool:
    """用空间网格分桶检测非相邻边之间的相交（O(n)~O(n^2) 之间，典型 O(n)）。"""
    n = len(points)
    edge_count = n if closed else n - 1
    if edge_count < (3 if closed else 2):
        return False

    def edge_vertices(i):
        return points[i], points[(i + 1) % n]

    def adjacent(i: int, j: int) -> bool:
        if abs(i - j) == 1:
            return True
        if closed and min(i, j) == 0 and max(i, j) == edge_count - 1:
            return True
        return False

    def cells_of(x1, y1, x2, y2):
        c0x = int(math.floor(min(x1, x2) / cell_size))
        c1x = int(math.floor(max(x1, x2) / cell_size))
        c0y = int(math.floor(min(y1, y2) / cell_size))
        c1y = int(math.floor(max(y1, y2) / cell_size))
        for cx in range(c0x, c1x + 1):
            for cy in range(c0y, c1y + 1):
                yield (cx, cy)

    buckets: dict[tuple[int, int], list[int]] = {}
    for i in range(edge_count):
        (x1, y1), (x2, y2) = edge_vertices(i)
        touched = set(cells_of(x1, y1, x2, y2))
        for cell in touched:
            for j in buckets.get(cell, ()):  # 仅与此前放入的边比较
                if adjacent(i, j):
                    continue
                a, b = edge_vertices(i)
                c, d = edge_vertices(j)
                if edges_intersect(a, b, c, d):
                    return True
        for cell in touched:
            buckets.setdefault(cell, []).append(i)
    return False


# ---------------------------------------------------------------- 点包含

def point_in_polygon(px: float, py: float,
                     points: list[tuple[float, float]], eps: float = 1e-7) -> bool:
    """射线法，边界上的点视为在内部。"""
    n = len(points)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if _on_segment(xi, yi, xj, yj, px, py, eps):
            return True
        if (yi > py) != (yj > py):
            xint = (xj - xi) * (py - yi) / (yj - yi) + xi
            if xint > px:
                inside = not inside
        j = i
    return inside


def bbox_contains(outer, inner, eps: float = 1e-7) -> bool:
    ox1, oy1, ox2, oy2 = outer
    ix1, iy1, ix2, iy2 = inner
    return (ix1 >= ox1 - eps and iy1 >= oy1 - eps
            and ix2 <= ox2 + eps and iy2 <= oy2 + eps)


def contour_contains(outer: Contour, inner: Contour) -> bool:
    if outer.index == inner.index or not outer.valid:
        return False
    if not bbox_contains(outer.bbox, inner.bbox):
        return False
    # 面积兜底：包含者面积必须更大
    if outer.area <= inner.area + 1e-9:
        return False
    return all(point_in_polygon(x, y, outer.points) for x, y in inner.points)


# ---------------------------------------------------------------- 解析

def _flip_points(points, height: float):
    return [(x, height - y) for x, y in points]


def _normalize_svg(content: bytes) -> bytes:
    """剥离根 <svg> 的 width/height 属性。

    Inkscape / Illustrator 常导出 width="200mm" 等物理单位，svgelements
    会按 96 PPI 把坐标整体缩放（1mm≈3.78 用户单位）。本工具约定
    「viewBox 中的 1 用户单位 = 1 mm」，因此强制以 viewBox 为唯一尺度，
    避免隐式 PPI 缩放。
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return content  # 交给 svgelements 产出更具体的解析错误
    for attr in ("width", "height"):
        if attr in root.attrib:
            del root.attrib[attr]
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


def parse_svg(content: bytes, tolerance: float = 0.5,
              flip_y: bool = False) -> tuple[list[Contour], SvgMeta]:
    """解析 SVG 字节内容，返回轮廓列表与画布元信息。

    抛 ValueError 表示文件无法解析或不含任何路径。
    """
    tolerance = max(tolerance, 1e-3)
    content = _normalize_svg(content)

    try:
        svg = se.SVG.parse(io.BytesIO(content))
    except Exception as exc:  # svgelements / ElementTree 解析错误
        raise ValueError(f"SVG 解析失败: {exc}") from exc

    shapes = [el for el in svg.elements() if isinstance(el, se.Shape)]
    if not shapes:
        raise ValueError("SVG 中未找到任何可切割图形元素")

    contours: list[Contour] = []
    idx = 0
    for el in shapes:
        tag = type(el).__name__
        xml_el = getattr(el, "element", None)
        source = f"<{xml_el.tag}>" if xml_el is not None else f"<{tag}>"
        try:
            path = se.Path(el)
            subpaths = list(path.as_subpaths())
        except Exception as exc:
            contours.append(Contour(
                index=idx, source=source, points=[], closed=False,
                issues=[f"元素无法转换为路径: {exc}"]))
            idx += 1
            continue
        for k, sp in enumerate(subpaths):
            pts, closed = _subpath_points(sp, tolerance)
            src = source if len(subpaths) == 1 else f"{source}#{k}"
            c = Contour(index=idx, source=src, points=pts, closed=closed)
            if not closed:
                c.issues.append("轮廓未闭合（缺少 Z 闭合段）")
            if closed and len(pts) < 3:
                c.issues.append("退化轮廓：顶点少于 3 个")
            if pts:
                c.length = polygon_length(pts, closed)
                c.bbox = polygon_bbox(pts)
                if closed and len(pts) >= 3:
                    c.area = polygon_area(pts)
                    if c.area <= 1e-9:
                        c.issues.append("退化轮廓：面积为 0")
                    cell = max(8.0 * tolerance, 1e-6)
                    if has_self_intersection(pts, True, cell):
                        c.self_intersecting = True
                        c.issues.append("轮廓自交")
            else:
                c.issues.append("空轮廓")
            contours.append(c)
            idx += 1

    # viewBox / 画布尺寸（用于 Y 轴翻转）
    vb = getattr(svg, "viewbox", None)
    if vb is not None:
        meta_vb = (float(vb.x), float(vb.y), float(vb.width), float(vb.height))
    else:
        all_b = [c.bbox for c in contours if c.points]
        x1 = min(b[0] for b in all_b); y1 = min(b[1] for b in all_b)
        x2 = max(b[2] for b in all_b); y2 = max(b[3] for b in all_b)
        meta_vb = (x1, y1, x2 - x1, y2 - y1)

    if flip_y:
        h = meta_vb[3]
        for c in contours:
            if c.points:
                c.points = _flip_points(c.points, h)
                c.bbox = polygon_bbox(c.points)

    meta = SvgMeta(viewbox=meta_vb, width=meta_vb[2], height=meta_vb[3],
                   element_count=len(shapes))
    return contours, meta


def build_nesting(contours: list[Contour]) -> None:
    """计算直接父轮廓与嵌套深度（仅有效轮廓参与）。"""
    valid = [c for c in contours if c.valid]
    for inner in valid:
        containers = [o for o in valid if contour_contains(o, inner)]
        if containers:
            # 直接父 = 面积最小的包含者（并列时取 index 较小者，保证确定性）
            containers.sort(key=lambda o: (o.area, o.index))
            inner.parent = containers[0].index

    def depth_of(c: Contour) -> int:
        d = 0
        seen = set()
        cur = c
        while cur.parent is not None:
            if cur.index in seen:  # 防御性：不应出现环
                break
            seen.add(cur.index)
            cur = contours[cur.parent]
            d += 1
        return d

    for c in valid:
        c.depth = depth_of(c)
