// 与后端 API 对应的类型定义

export interface SolveSettings {
  home_x: number;
  home_y: number;
  return_home: boolean;
  flip_y: boolean;
  curve_tolerance: number;
  rapid_feed: number;
  cut_feed: number;
  power: number;
}

export interface Contour {
  index: number;
  source: string;
  points: [number, number][];
  closed: boolean;
  valid: boolean;
  self_intersecting: boolean;
  issues: string[];
  area: number;
  length: number;
  bbox: [number, number, number, number];
  parent: number | null;
  depth: number;
  order: number | null;
  start: [number, number] | null;
  end: [number, number] | null;
  reversed: boolean;
}

export interface Cut {
  contour_index: number;
  order: number;
  start: [number, number];
  end: [number, number];
  reversed: boolean;
  air_travel: number;
  cut_length: number;
  parent: number | null;
  depth: number;
}

export interface Stats {
  contour_count: number;
  valid_count: number;
  invalid_count: number;
  total_air_travel: number;
  total_cut_length: number;
  home_return_travel: number;
  skipped: number[];
}

export interface Meta {
  viewbox: [number, number, number, number];
  width: number;
  height: number;
  element_count: number;
}

export interface JobResult {
  id: number;
  name: string;
  meta: Meta;
  contours: Contour[];
  cuts: Cut[];
  stats: Stats;
  home: [number, number];
}

export interface JobSummary {
  id: number;
  name: string;
  filename: string;
  status: string;
  error: string | null;
  created_at: string;
  solved_at: string | null;
}

export const DEFAULT_SETTINGS: SolveSettings = {
  home_x: 0,
  home_y: 0,
  return_home: true,
  flip_y: false,
  curve_tolerance: 0.5,
  rapid_feed: 3000,
  cut_feed: 800,
  power: 255,
};
