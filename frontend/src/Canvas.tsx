import { useMemo, useState } from "react";
import type { Contour, JobResult } from "./types";

interface Props {
  job: JobResult;
}

// 顺序色板（HSL 均匀取色），无效轮廓单独用红/橙
function orderColor(t: number): string {
  return `hsl(${Math.round(t * 300)}, 75%, 45%)`;
}

function pointsToPath(points: [number, number][], closed: boolean): string {
  if (points.length === 0) return "";
  const head = `M ${points[0][0].toFixed(3)} ${points[0][1].toFixed(3)}`;
  const rest = points
    .slice(1)
    .map((p) => `L ${p[0].toFixed(3)} ${p[1].toFixed(3)}`)
    .join(" ");
  return head + (rest ? " " + rest : "") + (closed ? " Z" : "");
}

export default function Canvas({ job }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const [showAir, setShowAir] = useState(true);

  const { contours, cuts, home, stats } = job;
  const orderMap = useMemo(() => {
    const m = new Map<number, number>();
    cuts.forEach((c) => m.set(c.contour_index, c.order));
    return m;
  }, [cuts]);

  // 内容包围盒（含 home）
  const bounds = useMemo(() => {
    const boxes = contours.filter((c) => c.points.length).map((c) => c.bbox);
    if (boxes.length === 0) {
      return { minX: -10, minY: -10, w: 20, h: 20 };
    }
    let minX = Math.min(...boxes.map((b) => b[0]));
    let minY = Math.min(...boxes.map((b) => b[1]));
    let maxX = Math.max(...boxes.map((b) => b[2]));
    let maxY = Math.max(...boxes.map((b) => b[3]));
    minX = Math.min(minX, home[0]);
    minY = Math.min(minY, home[1]);
    maxX = Math.max(maxX, home[0]);
    maxY = Math.max(maxY, home[1]);
    const pad = Math.max((maxX - minX), (maxY - minY)) * 0.06 + 1;
    return {
      minX: minX - pad,
      minY: minY - pad,
      w: maxX - minX + pad * 2,
      h: maxY - minY + pad * 2,
    };
  }, [contours, home]);

  const airPath = useMemo(() => {
    if (!showAir || cuts.length === 0) return "";
    const pts: [number, number][] = [home, ...cuts.map((c) => c.start)];
    return pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p[0]} ${p[1]}`).join(" ");
  }, [cuts, home, showAir]);

  const renderContour = (c: Contour) => {
    const order = orderMap.get(c.index);
    const isHover = hover === c.index;
    let stroke = "#64748b";
    let dash = "";
    if (c.self_intersecting) {
      stroke = "#dc2626";
      dash = "6 3";
    } else if (!c.closed) {
      stroke = "#ea580c";
      dash = "2 2";
    } else if (!c.valid) {
      stroke = "#dc2626";
      dash = "6 3";
    } else if (order !== undefined) {
      stroke = orderColor(stats.valid_count <= 1 ? 0 : order / (stats.valid_count - 1));
    }
    return (
      <path
        key={c.index}
        d={pointsToPath(c.points, c.closed)}
        fill={isHover ? "rgba(37,99,235,0.10)" : "none"}
        stroke={stroke}
        strokeWidth={isHover ? 1.4 : 0.8}
        strokeDasharray={dash}
        strokeLinejoin="round"
        onMouseEnter={() => setHover(c.index)}
        onMouseLeave={() => setHover(null)}
        style={{ cursor: "pointer", vectorEffect: "non-scaling-stroke" }}
      />
    );
  };

  return (
    <div className="canvas-wrap">
      <div className="canvas-toolbar">
        <label>
          <input
            type="checkbox"
            checked={showAir}
            onChange={(e) => setShowAir(e.target.checked)}
          />{" "}
          显示空移路径
        </label>
        <span className="legend">
          <i className="dot" style={{ background: "#16a34a" }} /> 起点
          <i className="dot" style={{ background: "#2563eb" }} /> Home
          <i className="line-demo valid" /> 有效轮廓
          <i className="line-demo open" /> 未闭合
          <i className="line-demo self" /> 自交
        </span>
      </div>
      <svg
        className="canvas"
        viewBox={`${bounds.minX} ${bounds.minY} ${bounds.w} ${bounds.h}`}
        preserveAspectRatio="xMidYMid meet"
      >
        {/* SVG y 轴向下，直接按数据坐标显示（与文件一致） */}
        <rect
          x={bounds.minX}
          y={bounds.minY}
          width={bounds.w}
          height={bounds.h}
          fill="#0f172a"
        />
        {showAir && (
          <path
            d={airPath}
            fill="none"
            stroke="#94a3b8"
            strokeWidth={0.6}
            strokeDasharray="3 2"
            opacity={0.8}
            vectorEffect="non-scaling-stroke"
          />
        )}
        {contours.map(renderContour)}
        {/* 入刀点 + 顺序标号 */}
        {cuts.map((cut) => {
          return (
            <g key={`m-${cut.contour_index}`}>
              <circle
                cx={cut.start[0]}
                cy={cut.start[1]}
                r={Math.max(bounds.w, bounds.h) * 0.008}
                fill="#16a34a"
                stroke="#052e16"
                strokeWidth={0.3}
              />
              <text
                x={cut.start[0]}
                y={cut.start[1]}
                fontSize={Math.max(bounds.w, bounds.h) * 0.022}
                fill="#fff"
                textAnchor="middle"
                dy="0.35em"
                style={{ fontWeight: 700, pointerEvents: "none" }}
              >
                {cut.order + 1}
              </text>
            </g>
          );
        })}
        {/* Home 标记 */}
        <g>
          <rect
            x={home[0] - bounds.w * 0.012}
            y={home[1] - bounds.w * 0.012}
            width={bounds.w * 0.024}
            height={bounds.w * 0.024}
            fill="#2563eb"
            transform={`rotate(45 ${home[0]} ${home[1]})`}
          />
        </g>
      </svg>
      {hover !== null && (
        <div className="tooltip">
          {(() => {
            const c = contours.find((x) => x.index === hover);
            if (!c) return null;
            return (
              <>
                <b>
                  #{c.index} {c.source}
                </b>
                <br />
                {c.closed ? "闭合" : "未闭合"} · 深度 {c.depth} · 面积 {c.area.toFixed(2)} ·
                长度 {c.length.toFixed(2)}
                {c.order !== null && <> · 切割顺序 #{c.order + 1}</>}
                {c.issues.length > 0 && (
                  <>
                    <br />
                    <span style={{ color: "#fca5a5" }}>{c.issues.join("；")}</span>
                  </>
                )}
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
