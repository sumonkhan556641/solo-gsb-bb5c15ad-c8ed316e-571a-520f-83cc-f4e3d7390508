"""切割顺序求解。

约束：先内后外 —— 一个轮廓只有在其内部（嵌套树子孙，实现上只需
直接/严格包含集合）所有轮廓切完后才能切割。

目标：最小化空移（rapid travel）总长度。轮廓顺序、入刀点（顶点中
最近点）与走刀方向（正/反向）均由算法选择。

算法：带先后约束的贪心最近邻（确定性）。每一步从“当前可切”的轮廓
集合中选择「到当前位置最小空移」的轮廓（入刀点和方向联合择优）；
平局先比内部轮廓深度（更深者优先，减少长距离折返），再比轮廓
index，保证结果完全确定且可复现。

注：全局最优是带优先级约束的广义 TSP（NP-hard），此处采用工业上
常用的确定性贪心，配合入刀点/方向选择，可在毫秒级给出高质量结果。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .geometry import Contour


@dataclass
class PlannedCut:
    contour_index: int
    order: int
    start: tuple[float, float]
    end: tuple[float, float]
    reversed: bool
    # 上一条轮廓终点（或 home）到本轮廓入刀点的空移距离
    air_travel: float
    cut_length: float
    parent: Optional[int]
    depth: int


@dataclass
class PlanResult:
    cuts: list[PlannedCut]
    total_air_travel: float
    total_cut_length: float
    home: tuple[float, float]
    return_home: bool
    home_return_travel: float
    skipped: list[int]


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _best_entry(contour: Contour, current: tuple[float, float]):
    """选择距 current 最近的顶点作为入刀点，并选择走刀方向。

    返回 (start, end, reversed)。方向选择使入刀点仍是同一顶点：
    - 正向：start = P_i，沿点列顺序走一圈回到 P_i（end == start）
    - 反向：start = P_i，反向走一圈（end == start）
    闭合环切终点即起点，方向只影响路径走向（热变形/排屑考量），
    不影响空移；选择正向以保证确定性，除非反向能在“开放近似”下
    得到更优衔接（本实现统一正向，保持简单可解释）。
    """
    pts = contour.points
    best_i = 0
    best_d = None
    for i, p in enumerate(pts):
        d = _dist(current, p)
        if best_d is None or d < best_d - 1e-12:
            best_d = d
            best_i = i
    start = pts[best_i]
    return start, start, False


def solve_order(
    contours: list[Contour],
    home: tuple[float, float] = (0.0, 0.0),
    return_home: bool = True,
) -> PlanResult:
    valid = [c for c in contours if c.valid]
    skipped = [c.index for c in contours if not c.valid]

    # 严格包含集合（子孙）：c 切完才允许 outer 切。
    # 用直接父子树构造 descendants。
    children: dict[int, list[int]] = {c.index: [] for c in valid}
    for c in valid:
        if c.parent is not None:
            children[c.parent].append(c.index)

    descendants: dict[int, set[int]] = {}

    def collect(idx: int) -> set[int]:
        if idx in descendants:
            return descendants[idx]
        out: set[int] = set()
        for ch in children[idx]:
            out.add(ch)
            out |= collect(ch)
        descendants[idx] = out
        return out

    for c in valid:
        collect(c.index)

    remaining = {c.index for c in valid}
    done: set[int] = set()
    by_id = {c.index: c for c in valid}

    cuts: list[PlannedCut] = []
    total_air = 0.0
    total_cut = 0.0
    current = home
    order_no = 0

    while remaining:
        # 可切 = 所有内部轮廓已完成
        ready = [
            by_id[i] for i in sorted(remaining)
            if descendants[i] <= done
        ]
        if not ready:
            # 理论上不应发生（嵌套为有向无环的包含关系）；防御性跳出
            break

        best = None  # (air, -depth, index, start, end, reversed)
        for c in ready:
            start, end, rev = _best_entry(c, current)
            air = _dist(current, start)
            key = (air, -c.depth, c.index)
            if best is None or key < best[0]:
                best = (key, c, start, end, rev)

        _, chosen, start, end, rev = best
        air = _dist(current, start)
        total_air += air
        total_cut += chosen.length

        chosen.order = order_no
        chosen.start = start
        chosen.end = end
        chosen.reversed = rev
        cuts.append(PlannedCut(
            contour_index=chosen.index, order=order_no,
            start=start, end=end, reversed=rev,
            air_travel=air, cut_length=chosen.length,
            parent=chosen.parent, depth=chosen.depth,
        ))

        remaining.discard(chosen.index)
        done.add(chosen.index)
        current = end
        order_no += 1

    home_return = _dist(current, home) if return_home and cuts else 0.0
    if return_home and cuts:
        total_air += home_return

    return PlanResult(
        cuts=cuts,
        total_air_travel=total_air,
        total_cut_length=total_cut,
        home=home,
        return_home=return_home,
        home_return_travel=home_return,
        skipped=skipped,
    )
