"""Quick offline checks for geometry, SVG parsing and scheduling (stdlib only)."""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.svg_parser import parse_svg
from app.geometry import find_self_intersections, point_in_polygon, signed_area2
from app.scheduler import ContourInput, solve
from app.gcode import render_gcode

SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
  <g transform="translate(0,0)">
    <rect x="10" y="10" width="100" height="100"/>
    <circle cx="60" cy="60" r="20"/>
    <circle cx="60" cy="60" r="8"/>
  </g>
  <!-- bowtie: self-intersecting quadrilateral -->
  <polygon points="150,10 190,90 150,90 190,10"/>
  <!-- open polyline -->
  <polyline points="150,150 170,160 190,150"/>
  <path d="M30,150 h40 v40 h-40 z M45,165 h10 v10 h-10 z"/>
</svg>
"""


def approx(a, b, eps=1e-6):
    return abs(a - b) <= eps


def main() -> int:
    raw, vb = parse_svg(SVG.decode())
    assert vb == (0, 0, 200, 200), vb
    print("contours:", [(c.element_id, c.kind, len(c.points), c.closed) for c in raw])
    assert len(raw) == 7, len(raw)

    inputs = []
    for rc in raw:
        hits = find_self_intersections(rc.points, closed=rc.closed)
        inputs.append(ContourInput(rc.element_id, rc.kind, rc.points, rc.closed, hits))
        print(rc.element_id, rc.kind, "closed" if rc.closed else "OPEN",
              "hits:", len(hits))

    # bowtie id 3 must self-intersect; none of the others should
    assert len(inputs[3].self_intersections) >= 1, "bowtie not detected"
    for i in (0, 1, 2, 5, 6):
        assert not inputs[i].self_intersections, f"false positive on {i}"

    res = solve(inputs, home=(0, 0))
    for c in res.contours:
        print("id", c.id, "valid", c.valid, c.issues,
              "parent", c.parent, "depth", c.depth, "cut#", c.cut_index)

    # nesting: circle r20 inside rect; circle r8 inside r20
    by = {c.id: c for c in res.contours}
    assert by[1].parent == 0, by[1].parent
    assert by[2].parent == 1, by[2].parent
    assert by[1].depth == 1 and by[2].depth == 2
    # compound path: inner square inside outer square
    assert by[6].parent == 5, (by[6].parent, )

    # invalid: open polyline + bowtie
    assert res.invalid_count == 2, res.invalid_count
    valid_cuts = [c for c in res.contours if c.valid]
    assert len(res.order) == len(valid_cuts) == 5

    # inside-first: inner circle (id2) before middle circle (id1) before rect (id0)
    pos = {cid: i for i, cid in enumerate(res.order)}
    assert pos[2] < pos[1] < pos[0]
    assert pos[6] < pos[5]

    # point_in_polygon checks
    sq = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert point_in_polygon((5, 5), sq)
    assert not point_in_polygon((15, 5), sq)
    assert point_in_polygon((0, 5), sq)  # boundary

    # travel is the real path length; sanity: finite and positive
    assert math.isfinite(res.total_travel) and res.total_travel > 0
    assert res.total_cut > 0

    code = render_gcode(res, project_name="t")
    assert "G21" in code and "M4 S255" in code and "$H" in code
    assert "Laser" in code
    print("G-code lines:", len(code.splitlines()))
    print("travel", round(res.total_travel, 2), "cut", round(res.total_cut, 2))

    # arc flattening closes circle near-perfectly: rect bbox of circle
    cx = by[1]
    xs = [p[0] for p in cx.points]; ys = [p[1] for p in cx.points]
    assert approx(max(xs) - 60, 20, 1e-6) and approx(60 - min(xs), 20, 1e-6)

    # determinism: same input -> same order
    res2 = solve(inputs, home=(0, 0))
    assert res2.order == res.order

    print("ALL CORE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
