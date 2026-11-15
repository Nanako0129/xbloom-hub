// Brew records as the hub returns them, and what the pages derive from them.
import { t } from "./i18n.svelte";

export type Point = [number, number];
export interface PlanPour { start_s: number; end_s: number; volume_ml: number; cumulative_ml: number; temperature_c: number; pause_s: number }
export interface Plan { pours: PlanPour[]; total_ml: number; pour_end_s: number }
export interface BrewSummary {
  id: string; started_at: string; ended_at: string | null; reason: string | null;
  duration_s: number | null; recipe_name: string | null; slug: string | null; water_ml: number | null;
  spark?: Point[];            // the water series thinned to at most 24 points, for the history list
}
export interface BrewRecord extends BrewSummary {
  hub_started: boolean;
  recipe: { name: string } | null;
  samples: { water: Point[]; weight: Point[] };
  events: [number, { cmd: number; [key: string]: unknown }][];
  plan: Plan | null;
  rezeroed?: Point[];         // [seconds, grams carried over] for each scale reset the hub saw
  gaps?: [number, number][];  // [from, to] seconds the hub's link was down while the brew ran
}

export const pourStarts = (record: BrewRecord): number[] =>
  record.events.filter((e) => e[1].cmd === 40510).map((e) => e[0]);

// What happened during a brew, as marks on its chart: a pour starting (cmd
// 40510) or ending (40511), the scale being re-zeroed. `label` is short, for the
// chart; `title` says it in full, for the reading under it.
export interface Mark { t: number; kind: "start" | "end" | "rezero" | "grind"; label: string; title: string }
// Grinding as the machine reports it (2026-11-15): 40506 when the grinder
// starts (with activity 34), 40507 when it stops, before the first pour.
const GRIND_START = 40506, GRIND_END = 40507;

// [pour index, time]: numbered by the machine's own pour index, so a missed
// start does not renumber the ones after it.
export const startMarks = (starts: [number, number][]): Mark[] =>
  starts.map(([index, time]) => ({ t: time, kind: "start", label: String(index + 1), title: t("mark.start", { n: index + 1 }) }));
export const pourEnds = (record: BrewRecord): number[] =>
  record.events.filter((e) => e[1].cmd === 40511).map((e) => e[0]);
export function eventMarks(record: BrewRecord): Mark[] {
  // An end belongs to the pour last seen starting.
  const starts: [number, number][] = [];
  const ends: Mark[] = [];
  let current = -1;
  for (const [time, event] of record.events) {
    if (event.cmd === 40510) {
      current = typeof event.pour_index === "number" ? event.pour_index : current + 1;
      starts.push([current, time]);
    } else if (event.cmd === 40511) {
      const n = Math.max(current, 0) + 1;
      ends.push({ t: time, kind: "end", label: String(n), title: t("mark.end", { n }) });
    }
  }
  const grind: Mark[] = record.events
    .filter((e) => e[1].cmd === GRIND_START || e[1].cmd === GRIND_END)
    .map(([time, event]) => ({ t: time, kind: "grind", label: event.cmd === GRIND_START ? "▶" : "■",
                               title: t(event.cmd === GRIND_START ? "mark.grind_start" : "mark.grind_end") }));
  const zeroes: Mark[] = (record.rezeroed ?? []).map(([time, grams]) =>
    ({ t: time, kind: "rezero", label: "0", title: t("mark.rezero", { g: Math.round(grams) }) }));
  return [...grind, ...startMarks(starts), ...ends, ...zeroes].sort((a, b) => a.t - b.t);
}

// The pour starts by pour index (the machine's own number), for a brew that
// is running: a page that joined late may not know the first ones.
export const startsByIndex = (record: BrewRecord): Record<number, number> =>
  Object.fromEntries(record.events.filter((e) => e[1].cmd === 40510 && typeof e[1].pour_index === "number")
    .map((e) => [e[1].pour_index as number, e[0]]));

export const recordName = (record: { recipe: { name: string } | null }): string =>
  record.recipe?.name || t("brew.at_machine");

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
