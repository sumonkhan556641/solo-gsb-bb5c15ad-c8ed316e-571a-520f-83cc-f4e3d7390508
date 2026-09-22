import type { JobResult, JobSummary, SolveSettings } from "./types";

export async function uploadAndSolve(
  file: File,
  settings: SolveSettings,
  name?: string
): Promise<JobResult> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("settings", JSON.stringify(settings));
  if (name) fd.append("name", name);

  const res = await fetch("/api/jobs", { method: "POST", body: fd });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as JobResult;
}

export async function listJobs(): Promise<JobSummary[]> {
  const res = await fetch("/api/jobs");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getJob(id: number): Promise<JobResult> {
  const res = await fetch(`/api/jobs/${id}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function gcodeUrl(id: number): string {
  return `/api/jobs/${id}/gcode`;
}
