import { useEffect, useRef, useState } from "react";
import { gcodeUrl, listJobs, uploadAndSolve } from "./api";
import Canvas from "./Canvas";
import {
  DEFAULT_SETTINGS,
  type JobResult,
  type JobSummary,
  type SolveSettings,
} from "./types";

function NumberField({
  label,
  value,
  step,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        value={value}
        step={step ?? 0.1}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

export default function App() {
  const [settings, setSettings] = useState<SolveSettings>(DEFAULT_SETTINGS);
  const [job, setJob] = useState<JobResult | null>(null);
  const [history, setHistory] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listJobs().then(setHistory).catch(() => undefined);
  }, []);

  const patch = (p: Partial<SolveSettings>) =>
    setSettings((s) => ({ ...s, ...p }));

  const handleFile = async (file: File | undefined | null) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const result = await uploadAndSolve(file, settings);
      setJob(result);
      setHistory(await listJobs());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const invalidContours = job
    ? job.contours.filter((c) => !c.valid)
    : [];

  return (
    <div className="app">
      <header>
        <h1>激光切割路径排程</h1>
        <span className="subtitle">
          SVG 闭合轮廓导入 · 嵌套解析 · 先内后外 · 最小空移 · G-code 导出
        </span>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <section>
            <h2>1. 导入 SVG</h2>
            <div
              className={`dropzone${dragOver ? " over" : ""}`}
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                void handleFile(e.dataTransfer.files?.[0]);
              }}
            >
              {loading ? "求解中…" : "点击或拖拽 .svg 文件到此处"}
              <input
                ref={fileRef}
                type="file"
                accept=".svg,image/svg+xml"
                hidden
                onChange={(e) => void handleFile(e.target.files?.[0])}
              />
            </div>
            {error && <div className="error">{error}</div>}
          </section>

          <section>
            <h2>2. 排程 / 机床设置</h2>
            <div className="grid2">
              <NumberField
                label="Home X (mm)"
                value={settings.home_x}
                onChange={(v) => patch({ home_x: v })}
              />
              <NumberField
                label="Home Y (mm)"
                value={settings.home_y}
                onChange={(v) => patch({ home_y: v })}
              />
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={settings.return_home}
                onChange={(e) => patch({ return_home: e.target.checked })}
              />
              完成后返回 Home
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settings.flip_y}
                onChange={(e) => patch({ flip_y: e.target.checked })}
              />
              Y 轴翻转（适配机床上位机坐标系）
            </label>
            <NumberField
              label="曲线离散容差 (mm)"
              value={settings.curve_tolerance}
              step={0.1}
              onChange={(v) => patch({ curve_tolerance: v })}
            />
            <div className="grid2">
              <NumberField
                label="空移 F (mm/min)"
                value={settings.rapid_feed}
                step={50}
                onChange={(v) => patch({ rapid_feed: v })}
              />
              <NumberField
                label="切割 F (mm/min)"
                value={settings.cut_feed}
                step={10}
                onChange={(v) => patch({ cut_feed: v })}
              />
            </div>
            <NumberField
              label="激光功率 S (0-1000)"
              value={settings.power}
              step={1}
              onChange={(v) => patch({ power: v })}
            />
            <p className="hint">
              修改设置后重新导入同一 SVG 即可重新求解；嵌套约束保证内孔先切、
              外轮廓后切，顺序/入刀点/空移确定且可复现。
            </p>
          </section>

          {job && (
            <section>
              <h2>3. 导出</h2>
              <a className="btn primary" href={gcodeUrl(job.id)}>
                ⬇ 下载 G-code (.gcode)
              </a>
              <div className="stats">
                <div>
                  <b>{job.stats.contour_count}</b>
                  <span>轮廓总数</span>
                </div>
                <div>
                  <b className="ok">{job.stats.valid_count}</b>
                  <span>可切割</span>
                </div>
                <div>
                  <b className={job.stats.invalid_count ? "bad" : "ok"}>
                    {job.stats.invalid_count}
                  </b>
                  <span>异常</span>
                </div>
                <div>
                  <b>{job.stats.total_air_travel.toFixed(1)}</b>
                  <span>空移 mm</span>
                </div>
                <div>
                  <b>{job.stats.total_cut_length.toFixed(1)}</b>
                  <span>切割长度 mm</span>
                </div>
              </div>
            </section>
          )}

          {history.length > 0 && (
            <section>
              <h2>历史作业</h2>
              <ul className="jobs">
                {history.map((j) => (
                  <li key={j.id}>
                    <span className="job-name">
                      #{j.id} {j.name}
                    </span>
                    <span className={`status ${j.status}`}>{j.status}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>

        <main className="main">
          {job ? (
            <>
              <Canvas job={job} />
              <section className="contours">
                <h2>
                  轮廓清单（{job.contours.length}）
                  {invalidContours.length > 0 && (
                    <span className="warn">
                      {invalidContours.length} 条异常已标红并从排程中剔除
                    </span>
                  )}
                </h2>
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>来源</th>
                      <th>状态</th>
                      <th>嵌套</th>
                      <th>顺序</th>
                      <th>面积</th>
                      <th>长度</th>
                      <th>空移至入刀点</th>
                      <th>问题</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.contours.map((c) => {
                      const cut = job.cuts.find(
                        (k) => k.contour_index === c.index
                      );
                      return (
                        <tr key={c.index} className={c.valid ? "" : "row-bad"}>
                          <td>{c.index}</td>
                          <td title={c.source}>{c.source}</td>
                          <td>
                            {c.self_intersecting
                              ? "自交"
                              : !c.closed
                                ? "未闭合"
                                : "闭合"}
                          </td>
                          <td>
                            {c.parent === null ? "外层" : `在 #${c.parent} 内`}
                            <small> (深 {c.depth})</small>
                          </td>
                          <td>{cut ? cut.order + 1 : "—"}</td>
                          <td>{c.area.toFixed(2)}</td>
                          <td>{c.length.toFixed(2)}</td>
                          <td>{cut ? cut.air_travel.toFixed(2) : "—"}</td>
                          <td className="issues">{c.issues.join("；") || ""}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </section>
            </>
          ) : (
            <div className="empty">
              <div className="empty-card">
                <h2>激光切割路径排程工具</h2>
                <ol>
                  <li>
                    导入含闭合轮廓的 SVG（rect / circle / path / polygon
                    等，支持曲线与分组变换）。
                  </li>
                  <li>
                    自动解析嵌套关系，未闭合与自交轮廓在画布中标红提示。
                  </li>
                  <li>
                    按“先内后外”约束求解最小空移的确定性切割顺序，预览空移、
                    入刀点与次序。
                  </li>
                  <li>一键导出 GRBL 兼容 G-code。</li>
                </ol>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
