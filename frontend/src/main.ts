import "./styles.css";
import { api } from "./api";
import type { Contour, Project } from "./types";
import { Preview } from "./preview";

const state = {
  projects: [] as { id: number; name: string; created_at: string;
    contour_count: number; invalid_count: number }[],
  current: null as Project | null,
  showTravel: true,
  showOrder: true,
  showInvalid: true,
  highlight: null as number | null,
};

let preview: Preview | null = null;

const $ = <T extends HTMLElement>(sel: string): T => {
  const el = document.querySelector<T>(sel);
  if (!el) throw new Error(`missing element ${sel}`);
  return el;
};

function h(tag: string, attrs: Record<string, string> = {},
          children: (Node | string)[] = []): HTMLElement {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else el.setAttribute(k, v);
  }
  for (const ch of children) el.append(typeof ch === "string" ? document.createTextNode(ch) : ch);
  return el;
}

// --------------------------------------------------------------------------- projects list
async function loadProjectList() {
  state.projects = await api.listProjects();
  const host = $("#project-list");
  host.innerHTML = "";
  if (!state.projects.length) {
    host.append(h("p", { class: "muted small" }, ["尚无项目，上传 SVG 开始"]));
    return;
  }
  for (const p of state.projects) {
    const row = h("div", { class: "project-row", tabindex: "0" });
    const active = state.current?.id === p.id;
    row.append(
      h("span", { class: "project-name" }, [p.name]),
      h("span", { class: "project-meta" },
        [`${p.contour_count} 轮廓` + (p.invalid_count ? ` · ${p.invalid_count} 异常` : "")]),
    );
    if (active) row.classList.add("active");
    row.addEventListener("click", () => openProject(p.id));
    const del = h("button", { class: "btn-del", title: "删除" }, ["✕"]);
    del.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!confirm(`删除项目「${p.name}」？`)) return;
      await api.deleteProject(p.id);
      if (state.current?.id === p.id) state.current = null;
      await loadProjectList();
      if (state.current) renderAll();
      else emptyDetail();
    });
    row.append(del);
    host.append(row);
  }
}

function emptyDetail() {
  $("#detail").innerHTML =
    '<div class="empty-hint"><h2>激光切割路径排程</h2>' +
    "<p>上传包含闭合轮廓的 SVG 文件，系统将：</p>" +
    "<ul><li>解析轮廓与嵌套（孔洞）关系</li>" +
    "<li>标记<b>自交</b>与<b>未闭合</b>轮廓</li>" +
    "<li>按<b>先内后外</b>约束求解最小空移的确定性切割顺序</li>" +
    "<li>提供路径预览与 G-code 导出</li></ul></div>";
  preview = null;
}

async function openProject(id: number) {
  state.current = await api.getProject(id);
  renderAll();
  await loadProjectList();
}

// --------------------------------------------------------------------------- rendering
function renderAll() {
  const p = state.current;
  if (!p) {
    emptyDetail();
    return;
  }
  const detail = $("#detail");
  detail.innerHTML = "";

  // header
  const header = h("div", { class: "detail-header" }, [
    h("h2", {}, [p.name]),
    h("div", { class: "header-actions" }, []),
  ]);
  const btnReschedule = h("button", { class: "btn" }, ["↻ 重新排程"]);
  btnReschedule.addEventListener("click", async () => {
    btnReschedule.setAttribute("disabled", "1");
    try {
      state.current = await api.reschedule(p.id);
      renderAll();
    } finally {
      btnReschedule.removeAttribute("disabled");
    }
  });
  const btnGcode = h("a", { class: "btn btn-primary",
    href: api.gcodeUrl(p.id), download: `${p.name}.gcode` }, ["⬇ 导出 G-code"]);
  header.querySelector(".header-actions")!.append(btnReschedule, btnGcode);
  detail.append(header);

  // body: preview + sidebar
  const body = h("div", { class: "detail-body" });
  const left = h("div", { class: "preview-pane" });
  const toolbar = h("div", { class: "preview-toolbar" });
  const toggles: [string, keyof typeof state][] = [
    ["空移路径", "showTravel"],
    ["切割序号", "showOrder"],
    ["异常轮廓", "showInvalid"],
  ];
  for (const [label, key] of toggles) {
    const lbl = h("label", { class: "chk" }, [
      Object.assign(document.createElement("input"), {
        type: "checkbox", checked: state[key] as boolean,
        onchange: (ev: Event) => {
          (state as Record<string, unknown>)[key] =
            (ev.target as HTMLInputElement).checked;
          preview?.update({
            showTravel: state.showTravel,
            showOrder: state.showOrder,
            showInvalid: state.showInvalid,
            highlightId: state.highlight,
          });
        },
      }),
      document.createTextNode(" " + label),
    ]);
    toolbar.append(lbl);
  }
  toolbar.append(h("span", { class: "muted small" },
    ["滚轮缩放 · 拖拽平移 · 双击复位"]));
  const host = h("div", { class: "preview-host" });
  left.append(toolbar, host);
  body.append(left);

  const right = h("div", { class: "side-pane" });
  renderStats(right, p);
  renderOrderTable(right, p);
  renderTree(right, p);
  renderSettings(right, p);
  body.append(right);
  detail.append(body);

  preview = new Preview(host, {
    showTravel: state.showTravel,
    showOrder: state.showOrder,
    showInvalid: state.showInvalid,
    highlightId: state.highlight,
    onSelect: (id) => {
      state.highlight = id;
      syncRowHighlight(id);
    },
  });
  preview.setProject(p);
}

function renderStats(host: HTMLElement, p: Project) {
  const s = p.schedule;
  const card = h("div", { class: "card" });
  card.append(h("h3", {}, ["统计"]));
  const grid = h("div", { class: "stat-grid" });
  const stat = (label: string, value: string, bad = false) =>
    h("div", { class: "stat" }, [
      h("div", { class: bad ? "stat-val bad" : "stat-val" }, [value]),
      h("div", { class: "stat-lbl" }, [label]),
    ]);
  const validN = p.contours.filter((c) => c.valid).length;
  grid.append(
    stat("有效轮廓", String(validN)),
    stat("异常轮廓", String(p.contours.length - validN),
      p.contours.length - validN > 0),
    stat("切割总长", `${(s?.total_cut ?? 0).toFixed(1)}`),
    stat("空移总长", `${(s?.total_travel ?? 0).toFixed(1)}`),
  );
  card.append(grid);
  host.append(card);
}

function renderOrderTable(host: HTMLElement, p: Project) {
  const card = h("div", { class: "card" });
  card.append(h("h3", {}, ["切割顺序（先内后外）"]));
  const table = h("table", { class: "order-table" });
  table.append(h("thead", {}, [h("tr", {}, [
    h("th", {}, ["#"]), h("th", {}, ["轮廓"]),
    h("th", {}, ["类型"]), h("th", {}, ["层级"]),
    h("th", {}, ["空移"]),
  ])]));
  const tbody = h("tbody", {});
  const byId = new Map(p.contours.map((c) => [c.id, c]));
  for (const cid of p.schedule?.order ?? []) {
    const c = byId.get(cid);
    if (!c) continue;
    const tr = h("tr", { "data-id": String(c.id) });
    tr.append(
      h("td", { class: "num" }, [String((c.cut_index ?? 0) + 1)]),
      h("td", {}, [`#${c.id}`]),
      h("td", {}, [c.kind]),
      h("td", { class: "num" }, [String(c.depth)]),
      h("td", { class: "num" }, [`${c.distance_from_previous.toFixed(1)}`]),
    );
    tr.addEventListener("mouseenter", () => {
      state.highlight = c.id;
      preview?.update({ highlightId: c.id,
        showTravel: state.showTravel, showOrder: state.showOrder,
        showInvalid: state.showInvalid, onSelect: () => {} });
    });
    tr.addEventListener("mouseleave", () => {
      state.highlight = null;
      preview?.update({ highlightId: null,
        showTravel: state.showTravel, showOrder: state.showOrder,
        showInvalid: state.showInvalid, onSelect: () => {} });
    });
    tbody.append(tr);
  }
  table.append(tbody);
  card.append(table);

  const invalids = p.contours.filter((c) => !c.valid);
  if (invalids.length) {
    const list = h("div", { class: "invalid-list" });
    for (const c of invalids) {
      list.append(h("div", { class: "invalid-item" }, [
        h("span", { class: "badge bad" }, [`#${c.id} ${c.kind}`]),
        h("span", { class: "muted small" }, [c.issues.join("，")]),
      ]));
    }
    card.append(h("div", { class: "warn-title" },
      ["⚠ 已排除的异常轮廓"]), list);
  }
  host.append(card);
}

function renderTree(host: HTMLElement, p: Project) {
  const card = h("div", { class: "card" });
  card.append(h("h3", {}, ["嵌套关系"]));
  const roots: Contour[] = p.contours.filter((c) => c.parent === null);
  const ul = h("ul", { class: "tree" });
  const byId = new Map(p.contours.map((c) => [c.id, c]));
  const build = (c: Contour): HTMLElement => {
    const li = h("li", { class: c.valid ? "" : "bad-node" });
    const label = h("span", { class: "tree-node" }, [
      `#${c.id} ${c.kind}` +
      (c.depth ? `（深 ${c.depth}）` : "") +
      (c.cut_index !== null ? ` → 切 ${c.cut_index + 1}` : " → 跳过"),
    ]);
    label.addEventListener("mouseenter", () => {
      state.highlight = c.id;
      preview?.update({ highlightId: c.id,
        showTravel: state.showTravel, showOrder: state.showOrder,
        showInvalid: state.showInvalid, onSelect: () => {} });
    });
    label.addEventListener("mouseleave", () => {
      state.highlight = null;
      preview?.update({ highlightId: null,
        showTravel: state.showTravel, showOrder: state.showOrder,
        showInvalid: state.showInvalid, onSelect: () => {} });
    });
    li.append(label);
    const kids = c.children
      .map((id) => byId.get(id))
      .filter((x): x is Contour => Boolean(x));
    if (kids.length) {
      const sub = h("ul", {});
      kids.sort((a, b) => a.id - b.id).forEach((k) => sub.append(build(k)));
      li.append(sub);
    }
    return li;
  };
  roots.sort((a, b) => a.id - b.id).forEach((r) => ul.append(build(r)));
  card.append(ul);
  host.append(card);
}

function renderSettings(host: HTMLElement, p: Project) {
  const card = h("div", { class: "card" });
  card.append(h("h3", {}, ["G-code 参数"]));
  const form = h("div", { class: "settings-form" });

  const numField = (key: keyof Project["settings"], label: string,
                    step: string): HTMLElement => {
    const wrap = h("label", { class: "field" });
    wrap.append(h("span", {}, [label]));
    const inp = document.createElement("input");
    inp.type = "number";
    inp.step = step;
    inp.value = String(p.settings[key]);
    inp.addEventListener("change", () =>
      saveSetting(p, { [key]: Number(inp.value) }));
    wrap.append(inp);
    return wrap;
  };

  form.append(
    numField("home_x", "起点 X", "0.1"),
    numField("home_y", "起点 Y", "0.1"),
    numField("laser_power", "激光功率 S (0-255)", "1"),
    numField("cut_speed", "切割进给 F", "10"),
    numField("travel_speed", "空移速度", "10"),
    numField("safe_z", "安全抬刀 Z", "0.1"),
  );

  const invWrap = h("label", { class: "field inline" });
  const invChk = document.createElement("input");
  invChk.type = "checkbox";
  invChk.checked = p.settings.invert_y;
  invChk.addEventListener("change", () =>
    saveSetting(p, { invert_y: invChk.checked }));
  invWrap.append(invChk, document.createTextNode(" 翻转 Y 轴（屏幕坐标→机床坐标）"));
  form.append(invWrap);

  const preLabel = h("label", { class: "field" }, [h("span", {}, ["G-code 前导指令"])]);
  const ta = document.createElement("textarea");
  ta.rows = 3;
  ta.value = p.settings.preamble;
  ta.placeholder = "例如：G0 Z5";
  ta.addEventListener("change", () =>
    saveSetting(p, { preamble: ta.value }));
  preLabel.append(ta);
  form.append(preLabel);
  card.append(form);
  host.append(card);
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;
async function saveSetting(p: Project, patch: Partial<Project["settings"]>) {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      state.current = await api.saveSettings(p.id, patch);
      preview?.setProject(state.current);
      // Refresh sidebar numbers without rebuilding inputs' focus.
      const statsHost = document.querySelector(".stat-grid");
      if (statsHost && state.current) {
        const card = statsHost.closest(".card");
        if (card) {
          const fresh = document.createElement("div");
          renderStats(fresh, state.current);
          card.replaceChildren(...fresh.firstElementChild!.childNodes);
        }
      }
    } catch (exc) {
      alert(`保存失败: ${(exc as Error).message}`);
    }
  }, 120);
}

function syncRowHighlight(id: number | null) {
  document.querySelectorAll(".order-table tbody tr").forEach((tr) => {
    tr.classList.toggle("hl", Number((tr as HTMLElement).dataset.id) === id);
  });
}

// --------------------------------------------------------------------------- upload
function bindUpload() {
  const input = $("#svg-file") as HTMLInputElement;
  const zone = $("#upload-zone");
  input.addEventListener("change", () => {
    if (input.files?.[0]) void upload(input.files[0]);
  });
  zone.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    zone.classList.add("drag");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
  zone.addEventListener("drop", (ev) => {
    ev.preventDefault();
    zone.classList.remove("drag");
    const f = ev.dataTransfer?.files[0];
    if (f) void upload(f);
  });
}

async function upload(file: File) {
  if (!file.name.toLowerCase().endsWith(".svg") && file.type !== "image/svg+xml") {
    if (!confirm("文件扩展名不是 .svg，仍尝试上传？")) return;
  }
  const zone = $("#upload-zone");
  zone.classList.add("busy");
  try {
    state.current = await api.uploadProject(file);
    await loadProjectList();
    renderAll();
  } catch (exc) {
    alert(`上传失败: ${(exc as Error).message}`);
  } finally {
    zone.classList.remove("busy");
    (document.getElementById("svg-file") as HTMLInputElement).value = "";
  }
}

// --------------------------------------------------------------------------- boot
void (async () => {
  bindUpload();
  emptyDetail();
  try {
    await loadProjectList();
    if (state.projects.length) await openProject(state.projects[0].id);
  } catch (exc) {
    console.error(exc);
  }
})();
