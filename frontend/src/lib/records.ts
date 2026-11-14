// Brew records as the hub returns them, and what the pages derive from them.
import { t } from "./i18n.svelte";

export type Point = [number, number];
export interface PlanPour { start_s: number; end_s: number; volume_ml: number; cumulative_ml: number; temperature_c: number; pause_s: number }
export interface Plan { pours: PlanPour[]; total_ml: number; pour_end_s: number }
export interface BrewSummary {
  id: string; started_at: string; ended_at: string | null; reason: string | null;
  duration_s: number | null; recipe_name: string | null; slug: string | null; water_ml: number | null;
}
export interface BrewRecord extends BrewSummary {
  hub_started: boolean;
  recipe: { name: string } | null;
  samples: { water: Point[]; weight: Point[] };
  events: [number, { cmd: number; [key: string]: unknown }][];
  plan: Plan | null;
}

export const pourStarts = (record: BrewRecord): number[] =>
  record.events.filter((e) => e[1].cmd === 40510).map((e) => e[0]);
export const recordName = (record: { recipe: { name: string } | null }): string =>
  record.recipe?.name ?? t("brew.at_machine");

// The planned water line: flat during pauses, rising during pours.
export function planLine(plan: Plan): Point[] {
  const points: Point[] = [[0, 0]];
  let before = 0;
  for (const pour of plan.pours) {
    points.push([pour.start_s, before], [pour.end_s, pour.cumulative_ml]);
    before = pour.cumulative_ml;
  }
  return points;
}

// The value of a series at a moment: on the line between the readings either
// side of it; before the first or after the last, that reading itself.
export function valueAt(points: Point[], time: number): number | null {
  if (!points.length) return null;
  let before = points[0];
  for (const point of points) {
    if (point[0] > time) {
      if (point[0] > before[0] && time >= before[0]) {
        return before[1] + ((point[1] - before[1]) * (time - before[0])) / (point[0] - before[0]);
      }
      return before[1];
    }
    before = point;
  }
  return before[1];
}
