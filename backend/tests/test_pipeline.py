"""核心算法单元测试：解析、自交检测、嵌套与排程、G-code。"""
import math
import os
import tempfile

import pytest

from app.geometry import (
    contour_contains, has_self_intersection, parse_svg, build_nesting,
    point_in_polygon, polygon_area,
)
from app.scheduler import solve_order
from app.gcode import GcodeSettings, build_gcode


SQUARE_WITH_HOLE = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect x="10" y="10" width="80" height="80"/>
<circle cx="50" cy="50" r="10"/>
</svg>'''

OPEN_PATH = b'''<svg xmlns="http://www.w3.org/2000/svg">
<polyline points="0,0 50,0 50,50"/>
</svg>'''

SELF_CROSS = b'''<svg xmlns="http://www.w3.org/2000/svg">
<path d="M0,0 L100,100 L100,0 L0,100 Z"/>
</svg>'''


def test_parse_nested_square_circle():
    contours, meta = parse_svg(SQUARE_WITH_HOLE)
    assert len(contours) == 2
    outer, inner = contours
    assert outer.closed and inner.closed
    assert all(c.valid for c in contours)
    assert outer.area == pytest.approx(6400, rel=1e-3)
    assert inner.area == pytest.approx(math.pi * 100, rel=5e-2)
    build_nesting(contours)
    assert inner.parent == outer.index
    assert outer.parent is None
    assert inner.depth == 1


def test_open_path_flagged():
    contours, _ = parse_svg(OPEN_PATH)
    assert len(contours) == 1
    c = contours[0]
    assert not c.closed
    assert not c.valid
    assert any("未闭合" in i for i in c.issues)


def test_self_intersection_flagged():
    contours, _ = parse_svg(SELF_CROSS)
    assert len(contours) == 1
    c = contours[0]
    assert c.self_intersecting
    assert not c.valid
    assert any("自交" in i for i in c.issues)


def test_has_self_intersection_unit():
    assert has_self_intersection([(0, 0), (10, 10), (10, 0), (0, 10)], True, 1.0)
    assert not has_self_intersection([(0, 0), (10, 0), (10, 10), (0, 10)], True, 1.0)
    # 两个三角形共边不算自交（相邻边）
    assert not has_self_intersection([(0, 0), (5, 0), (5, 5), (0, 5)], True, 1.0)


def test_point_in_polygon():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon(5, 5, sq)
    assert not point_in_polygon(15, 5, sq)
    assert point_in_polygon(0, 5, sq)  # 边界


def test_inner_first_order():
    contours, _ = parse_svg(SQUARE_WITH_HOLE)
    build_nesting(contours)
    plan = solve_order(contours, home=(0, 0))
    order = [cut.contour_index for cut in plan.cuts]
    # 内圆必须先于外方
    assert order[0] == 1 and order[1] == 0
    assert plan.total_air_travel > 0
    assert plan.total_cut_length > 0
    g = build_gcode(contours, plan, GcodeSettings())
    assert "M3 S255" in g
    assert "G21" in g
    assert g.count("M5") >= 2
    # 内圆先出现
    assert g.index("contour #1") < g.index("contour #0")


def test_separate_contours_nearest_first():
    # 两个独立方块，home 位于右侧方块附近 -> 右侧应先切
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg">
    <rect x="0" y="0" width="10" height="10"/>
    <rect x="90" y="0" width="10" height="10"/>
    </svg>'''
    contours, _ = parse_svg(svg)
    build_nesting(contours)
    plan = solve_order(contours, home=(100, 0))
    assert plan.cuts[0].contour_index == 1
    # 空移距离确定性
    plan2 = solve_order(contours, home=(100, 0))
    assert [c.contour_index for c in plan.cuts] == [c.contour_index for c in plan2.cuts]


def test_flip_y():
    c1, _ = parse_svg(SQUARE_WITH_HOLE, flip_y=False)
    c2, _ = parse_svg(SQUARE_WITH_HOLE, flip_y=True)
    # 翻转后 y 坐标互补（viewBox 高 100）
    assert c1[0].points[0][1] + c2[0].points[0][1] == pytest.approx(100, abs=1e-6)


def test_bad_svg():
    with pytest.raises(ValueError):
        parse_svg(b"not an svg")
    with pytest.raises(ValueError):
        parse_svg(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')


def test_mm_width_not_scaled():
    # width="200mm" 会导致 svgelements 按 96PPI 缩放；归一化后坐标应仍以
    # viewBox 用户单位（mm）给出
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm"
        viewBox="0 0 200 100"><rect x="0" y="0" width="200" height="100"/></svg>'''
    contours, meta = parse_svg(svg)
    assert meta.width == 200 and meta.height == 100
    assert contours[0].bbox == pytest.approx((0, 0, 200, 100), abs=1e-6)
    assert contours[0].area == pytest.approx(20000, rel=1e-6)
