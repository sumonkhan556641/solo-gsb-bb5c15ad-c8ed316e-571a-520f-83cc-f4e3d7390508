"""G-code 导出（GRBL 兼容，激光模式）。

约定：SVG 1 用户单位 = 1 mm。
- G17/G21：XY 平面、毫米
- G90/G94：绝对坐标、每分钟进给
- G0 空移（激光关闭），G1 切割（激光开启，S 指定功率）
- 环形闭合轮廓最后用一段 G1 回到入刀点完成封口
"""
from __future__ import annotations

from dataclasses import dataclass

from .geometry import Contour
from .scheduler import PlanResult


@dataclass
class GcodeSettings:
    # 空移/切割进给（mm/min）
    rapid_feed: float = 3000.0
    cut_feed: float = 800.0
    # 激光功率 0..1000（GRBL S 值）
    power: int = 255
    # 结束后是否回到 home
    return_home: bool = True
    # 头注释前缀
    comment_prefix: str = ";"


def _f(v: float) -> str:
    return f"{v:.3f}"


def build_gcode(contours: list[Contour], plan: PlanResult,
                settings: GcodeSettings | None = None,
                meta_lines: tuple[str, ...] = ()) -> str:
    s = settings or GcodeSettings()
    cmt = s.comment_prefix
    by_id = {c.index: c for c in contours}
    lines: list[str] = []

    lines.append(f"{cmt} laser-cutting path export")
    for m in meta_lines:
        lines.append(f"{cmt} {m}")
    lines.append(f"{cmt} contours: {len(plan.cuts)} (skipped invalid: {len(plan.skipped)})")
    lines.append(f"{cmt} total air travel: {plan.total_air_travel:.3f} mm")
    lines.append(f"{cmt} total cut length: {plan.total_cut_length:.3f} mm")
    lines.append(f"{cmt} feed rapid={s.rapid_feed:.0f} cut={s.cut_feed:.0f} mm/min, "
                 f"power S={s.power}")
    lines.append("G17 G21 G90 G94")
    hx, hy = plan.home
    lines.append(f"G0 F{_f(s.rapid_feed)} X{_f(hx)} Y{_f(hy)}")
    lines.append("M5")

    for cut in plan.cuts:
        c = by_id[cut.contour_index]
        pts = c.points
        n = len(pts)
        # 入刀点在点列中的位置
        si = next(i for i, p in enumerate(pts)
                  if abs(p[0] - cut.start[0]) <= 1e-9 and abs(p[1] - cut.start[1]) <= 1e-9)
        order_ids = list(range(n)) if not cut.reversed else list(range(n - 1, -1, -1))
        # 旋转使入刀点为第 0 个
        if not cut.reversed:
            ordered = [pts[(si + k) % n] for k in range(n)]
        else:
            ordered = [pts[(si - k) % n] for k in range(n)]

        x0, y0 = cut.start
        lines.append(f"{cmt} --- contour #{cut.contour_index} {c.source} "
                     f"(order {cut.order}, depth {cut.depth}) ---")
        # 激光关闭空移到入刀点
        lines.append("M5")
        lines.append(f"G0 X{_f(x0)} Y{_f(y0)}")
        # 开光切割
        lines.append(f"M3 S{int(s.power)}")
        for x, y in ordered[1:]:
            lines.append(f"G1 F{_f(s.cut_feed)} X{_f(x)} Y{_f(y)}")
        # 封口回到入刀点
        lines.append(f"G1 X{_f(x0)} Y{_f(y0)}")
        lines.append("M5")

    if s.return_home:
        lines.append(f"{cmt} return home")
        lines.append(f"G0 F{_f(s.rapid_feed)} X{_f(hx)} Y{_f(hy)}")

    lines.append("M5")
    lines.append(f"{cmt} end")
    return "\n".join(lines) + "\n"
