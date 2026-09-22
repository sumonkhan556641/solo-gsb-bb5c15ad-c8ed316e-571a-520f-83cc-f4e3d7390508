export interface Contour {
  id: number;
  kind: string;
  points: [number, number][];
  closed: boolean;
  valid: boolean;
  issues: string[];
  intersections: [number, number][];
  parent: number | null;
  depth: number;
  children: number[];
  cut_index: number | null;
  distance_from_previous: number;
}

export interface Segment {
  type: "travel" | "cut";
  cut_index: number | null;
  contour_id: number | null;
  from?: [number, number];
  to?: [number, number];
  points?: [number, number][];
  length: number;
}

export interface Schedule {
  order: number[];
  segments: Segment[];
  total_travel: number;
  total_cut: number;
  invalid_count: number;
}

export interface Settings {
  home_x: number;
  home_y: number;
  invert_y: boolean;
  laser_power: number;
  travel_speed: number;
  cut_speed: number;
  safe_z: number;
  preamble: string;
}

export interface Project {
  id: number;
  name: string;
  created_at: string;
  view_box: [number, number, number, number] | null;
  settings: Settings;
  contours: Contour[];
  schedule: Schedule | null;
}

export interface ProjectSummary {
  id: number;
  name: string;
  created_at: string;
  contour_count: number;
  invalid_count: number;
}
