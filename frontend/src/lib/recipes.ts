// Recipes as the hub stores and plans them.
import type { Plan } from "./records";

export interface Pour {
  volume_ml: number | null; temperature_c: number | null; pause_s: number | null; pattern: number;
  flow_rate: number | null; agitate_before: number; agitate_after: number;
}
export interface Meta { bean?: string; roaster?: string; process?: string; roast?: string; notes?: string; source?: string }
export interface Recipe {
  name: string; cup_type: number; dose_g: number | null; ratio: string; grind: boolean;
  grind_size: number | null; grinder_speed_rpm: number | null; pours: Pour[]; meta?: Meta;
}
export interface RecipeInfo { recipe: Recipe; as_sent: { dose_g: number; ratio: string; total_ml: number; pour_volumes_ml: number[] }; plan: Plan }
export interface LibraryEntry {
  slug: string; name: string; valid: boolean;
  dose_g?: number; ratio?: string; total_ml?: number; pour_end_s?: number;
  pours?: { volume_ml: number; temperature_c: number }[];
}

// The hub's plan(), for a draft that has not been saved: volume / flow for
// each pour, then its pause; zero is the first pour. A preview only: what is
// saved is planned by the hub, which also checks the numbers.
export function previewPlan(pours: Pour[]): Plan {
  let clock = 0, total = 0;
  const out = pours.map((pour) => {
    const volume = Number(pour.volume_ml) || 0, flow = Number(pour.flow_rate) || 0, pause = Number(pour.pause_s) || 0;
    const duration = flow > 0 ? volume / flow : 0;
    total += volume;
    const row = { start_s: clock, end_s: clock + duration, volume_ml: volume, cumulative_ml: total,
                  temperature_c: Number(pour.temperature_c) || 0, pause_s: pause };
    clock += duration + pause;
    return row;
  });
  return { pours: out, total_ml: total, pour_end_s: out.length ? out[out.length - 1].end_s : 0 };
}

// The temperature band a pour is drawn in, as on the current dashboard.
export const heat = (celsius: number) => (celsius >= 92 ? "hot" : celsius >= 80 ? "warm" : "cool");

// Only an https address becomes a link; anything else stays text.
export const isLink = (source: string | undefined) => Boolean(source && /^https:\/\//.test(source));
