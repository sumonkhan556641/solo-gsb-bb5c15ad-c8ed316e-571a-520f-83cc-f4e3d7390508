import type { Project, ProjectSummary, Settings } from "./types";

const BASE = "/api";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + url, init);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore non-json error bodies */
    }
    throw new Error(detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  listProjects: () => req<ProjectSummary[]>("/projects"),

  getProject: (id: number) => req<Project>(`/projects/${id}`),

  uploadProject: (file: File, name?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    return req<Project>("/projects", { method: "POST", body: fd });
  },

  reschedule: (id: number) =>
    req<Project>(`/projects/${id}/schedule`, { method: "POST" }),

  saveSettings: (id: number, settings: Partial<Settings>) =>
    req<Project>(`/projects/${id}/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),

  deleteProject: (id: number) =>
    req<void>(`/projects/${id}`, { method: "DELETE" }),

  gcodeUrl: (id: number) => `${BASE}/projects/${id}/gcode`,
};
