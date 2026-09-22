import type { Contour, Project, Segment } from "./types";

const SVG_NS = "http://www.w3.org/2000/svg";

export interface PreviewOptions {
  showTravel: boolean;
  showOrder: boolean;
  showInvalid: boolean;
  highlightId: number | null;
  onSelect: (id: number | null) => void;
}

const VALID_FILL = "rgba(64, 156, 255, 0.08)";
const VALID_STROKE = "#409cff";
const INVALID_STROKE = "#e5484d";
const INVALID_FILL = "rgba(229, 72, 77, 0.08)";
const TRAVEL_STROKE = "#f5a623";
const CUT_ACTIVE = "#30d158";
const HIGHLIGHT = "#ffd60a";

/** Deterministic HSL color spread across the cutting sequence. */
function cutColor(i: number, n: number): string {
  if (n <= 1) return CUT_ACTIVE;
  return `hsl(${Math.round((i / (n - 1)) * 200)}, 80%, 50%)`;
}

export class Preview {
  private root: SVGSVGElement;
  private g!: SVGGElement;
  private project: Project | null = null;
  private opts: PreviewOptions;
  private vb = { x: 0, y: 0, w: 100, h: 100 };
  private scale = 1;

  constructor(host: HTMLElement, opts: PreviewOptions) {
    this.opts = opts;
    this.root = document.createElementNS(SVG_NS, "svg");
    this.root.setAttribute("class", "preview-svg");
    host.innerHTML = "";
    host.appendChild(this.root);

    const bg = document.createElementNS(SVG_NS, "rect");
    bg.setAttribute("class", "preview-bg");
    bg.setAttribute("x", "-100000");
    bg.setAttribute("y", "-100000");
    bg.setAttribute("width", "200000");
    bg.setAttribute("height", "200000");
    this.root.appendChild(bg);

    this.g = document.createElementNS(SVG_NS, "g");
    this.root.appendChild(this.g);

    this.bindPanZoom(host);
  }

  // ------------------------------------------------------------------ pan/zoom
  private bindPanZoom(host: HTMLElement) {
    this.root.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
      this.zoomAt(ev.offsetX, ev.offsetY, factor);
    }, { passive: false });

    let panning = false;
    let sx = 0;
    let sy = 0;
    let vx = 0;
    let vy = 0;
    this.root.addEventListener("mousedown", (ev) => {
      panning = true;
      sx = ev.clientX;
      sy = ev.clientY;
      vx = this.vb.x;
      vy = this.vb.y;
      this.root.style.cursor = "grabbing";
    });
    window.addEventListener("mousemove", (ev) => {
      if (!panning) return;
      const dx = (ev.clientX - sx) / this.scale;
      const dy = (ev.clientY - sy) / this.scale;
      this.setViewBox(vx - dx, vy - dy, this.vb.w, this.vb.h);
    });
    window.addEventListener("mouseup", () => {
      panning = false;
      this.root.style.cursor = "grab";
    });
    host.addEventListener("dblclick", () => this.fit());
    this.root.style.cursor = "grab";
  }

  private zoomAt(px: number, py: number, factor: number) {
    const rect = this.root.getBoundingClientRect();
    const wx = this.vb.x + (px - rect.left) / this.scale;
    const wy = this.vb.y + (py - rect.top) / this.scale;
    const nw = this.vb.w / factor;
    const nh = this.vb.h / factor;
    this.setViewBox(wx - (px - rect.left) / (this.scale * factor),
      wy - (py - rect.top) / (this.scale * factor), nw, nh);
  }

  private setViewBox(x: number, y: number, w: number, h: number) {
    this.vb = { x, y, w, h };
    this.root.setAttribute("viewBox", `${x} ${y} ${w} ${h}`);
  }

  fit() {
    if (!this.project) return;
    const pts: [number, number][] = [];
    for (const c of this.project.contours) pts.push(...c.points);
    if (!pts.length) return;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const [x, y] of pts) {
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
    const pad = Math.max(maxX - minX, maxY - minY) * 0.08 || 10;
    this.vb = {
      x: minX - pad,
      y: minY - pad,
      w: maxX - minX + pad * 2,
      h: maxY - minY + pad * 2,
    };
    this.applyViewBox();
  }

  private applyViewBox() {
    this.root.setAttribute("viewBox",
      `${this.vb.x} ${this.vb.y} ${this.vb.w} ${this.vb.h}`);
    requestAnimationFrame(() => {
      const rect = this.root.getBoundingClientRect();
      this.scale = rect.width / this.vb.w;
    });
  }

  // ------------------------------------------------------------------ render
  setProject(project: Project) {
    this.project = project;
    this.vb = project.view_box
      ? { x: project.view_box[0], y: project.view_box[1],
          w: project.view_box[2], h: project.view_box[3] }
      : this.vb;
    this.fit();
    this.render();
  }

  update(opts: Partial<PreviewOptions>) {
    this.opts = { ...this.opts, ...opts };
    this.render();
  }

  private render() {
    if (!this.project) return;
    const p = this.project;
    while (this.g.firstChild) this.g.removeChild(this.g.firstChild);

    const cutOf = new Map<number, number>();
    p.contours.forEach((c) => {
      if (c.cut_index !== null) cutOf.set(c.id, c.cut_index);
    });
    const n = Math.max(p.contours.length, 1);

    if (this.opts.showTravel && p.schedule) {
      this.renderTravel(p.schedule.segments);
    }

    for (const c of p.contours) {
      if (!c.valid && !this.opts.showInvalid) continue;
      this.renderContour(c, cutOf.get(c.id) ?? null, n);
    }

    if (this.opts.showOrder) this.renderOrderLabels(cutOf);
    this.renderHome(p.settings.home_x, p.settings.home_y);
  }

  private pointsAttr(pts: [number, number][]): string {
    return pts.map(([x, y]) => `${round(x)},${round(y)}`).join(" ");
  }

  private renderContour(c: Contour, cutIndex: number | null, n: number) {
    const group = document.createElementNS(SVG_NS, "g");
    group.setAttribute("class", "contour");

    const poly = document.createElementNS(SVG_NS, "polygon");
    if (!c.closed) {
      poly.removeAttribute("points");
      const pl = document.createElementNS(SVG_NS, "polyline");
      pl.setAttribute("points", this.pointsAttr(c.points));
      this.styleContour(pl, c, cutIndex, n);
      group.appendChild(pl);
    } else {
      poly.setAttribute("points", this.pointsAttr(c.points));
      this.styleContour(poly, c, cutIndex, n);
      group.appendChild(poly);
    }

    group.addEventListener("mouseenter", () => {
      this.opts.onSelect(c.id);
      this.highlight(c.id);
    });
    group.addEventListener("mouseleave", () => {
      this.opts.onSelect(null);
      this.highlight(null);
    });
    group.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.opts.onSelect(c.id === this.opts.highlightId ? null : c.id);
      this.highlight(this.opts.highlightId);
    });
    this.g.appendChild(group);

    for (const [x, y] of c.intersections) {
      const m = document.createElementNS(SVG_NS, "circle");
      m.setAttribute("cx", String(x));
      m.setAttribute("cy", String(y));
      m.setAttribute("r", String(this.markerR()));
      m.setAttribute("class", "intersection-marker");
      this.g.appendChild(m);
    }
  }

  private styleContour(el: SVGPolygonElement | SVGPolylineElement,
                       c: Contour, cutIndex: number | null, n: number) {
    const highlighted = c.id === this.opts.highlightId;
    el.setAttribute("fill", c.valid ? VALID_FILL : INVALID_FILL);
    el.setAttribute("stroke", !c.valid
      ? INVALID_STROKE
      : highlighted
        ? HIGHLIGHT
        : cutIndex !== null
          ? cutColor(cutIndex, n)
          : VALID_STROKE);
    el.setAttribute("stroke-width", String(this.strokeW(highlighted)));
    el.setAttribute("stroke-linejoin", "round");
    if (!c.closed) el.setAttribute("stroke-dasharray", String(this.strokeW(true) * 3));
  }

  private renderTravel(segments: Segment[]) {
    for (const s of segments) {
      if (s.type !== "travel" || !s.from || !s.to) continue;
      const line = document.createElementNS(SVG_NS, "line");
      line.setAttribute("x1", String(s.from[0]));
      line.setAttribute("y1", String(s.from[1]));
      line.setAttribute("x2", String(s.to[0]));
      line.setAttribute("y2", String(s.to[1]));
      line.setAttribute("stroke", TRAVEL_STROKE);
      line.setAttribute("stroke-width", String(this.strokeW(false) * 0.7));
      line.setAttribute("stroke-dasharray", String(this.strokeW(true) * 2.5));
      line.setAttribute("class", "travel-line");
      this.g.appendChild(line);
    }
  }

  private renderOrderLabels(cutOf: Map<number, number>) {
    if (!this.project?.schedule) return;
    // Entry point of each contour = first point of its "cut" segment,
    // which is the nearest-vertex rotation chosen by the scheduler.
    const entry = new Map<number, [number, number]>();
    for (const s of this.project.schedule.segments) {
      if (s.type === "cut" && s.contour_id !== null && s.points?.length) {
        entry.set(s.contour_id, s.points[0]);
      }
    }
    for (const c of this.project.contours) {
      const idx = cutOf.get(c.id);
      if (idx === undefined) continue;
      const start = entry.get(c.id);
      if (!start) continue;
      const gEl = document.createElementNS(SVG_NS, "g");
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("cx", String(start[0]));
      circle.setAttribute("cy", String(start[1]));
      circle.setAttribute("r", String(this.markerR() * 1.4));
      circle.setAttribute("class", "order-badge");
      const text = document.createElementNS(SVG_NS, "text");
      text.setAttribute("x", String(start[0]));
      text.setAttribute("y", String(start[1]));
      text.setAttribute("class", "order-text");
      text.setAttribute("font-size", String(this.markerR() * 2.2));
      text.textContent = String(idx + 1);
      gEl.append(circle, text);
      this.g.appendChild(gEl);
    }
  }

  private renderHome(x: number, y: number) {
    const gEl = document.createElementNS(SVG_NS, "g");
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", String(x));
    c.setAttribute("cy", String(y));
    c.setAttribute("r", String(this.markerR()));
    c.setAttribute("class", "home-marker");
    const t = document.createElementNS(SVG_NS, "text");
    t.setAttribute("x", String(x + this.markerR() * 1.4));
    t.setAttribute("y", String(y - this.markerR() * 1.2));
    t.setAttribute("font-size", String(this.markerR() * 2.4));
    t.setAttribute("class", "home-text");
    t.textContent = "HOME";
    gEl.append(c, t);
    this.g.appendChild(gEl);
  }

  private highlight(_id: number | null) {
    // Full re-render keeps styling simple; contours count is modest.
    this.render();
  }

  private strokeW(bold: boolean): number {
    const base = this.vb.w / 420;
    return round(Math.max(base * (bold ? 2.2 : 1.1), this.vb.w / 4000));
  }

  private markerR(): number {
    return Math.max(this.vb.w / 160, 0.8);
  }
}

function round(v: number): number {
  return Math.round(v * 10000) / 10000;
}
