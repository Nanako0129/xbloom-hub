// The brew that is running: its readings as they arrive, and what came before
// this page was looking (fetched from its record). As the current dashboard.
import { api, follow } from "./hub.svelte";
import { pourStarts, type BrewRecord, type Point } from "./records";

export const live = $state({
  id: null as string | null,
  water: [] as Point[],
  weight: [] as Point[],
  marks: [] as number[],
  lastPour: null as number | null,
});

const JOINED_LATE_S = 2;     // a brew this far along when first seen was running before the page

async function backfill(id: string): Promise<void> {
  let record: BrewRecord;
  try { record = await api<BrewRecord>("GET", `/api/brews/${encodeURIComponent(id)}`); } catch { return; }
  if (live.id !== id) return;
  const first = (points: { 0: number }[]) => (points.length ? points[0][0] : Infinity);
  live.water = record.samples.water.filter((p) => p[0] < first(live.water)).concat(live.water);
  live.weight = record.samples.weight.filter((p) => p[0] < first(live.weight)).concat(live.weight);
  live.marks = pourStarts(record).filter((x) => x < first(live.marks.map((m) => [m]))).concat(live.marks);
}

follow((next) => {
  const brew = next.brew;
  if (!brew) return;
  if (live.id !== brew.id) {
    // Joining a brew is not a pour starting: count from the pour it is on.
    Object.assign(live, { id: brew.id, water: [], weight: [], marks: [], lastPour: brew.pour });
    if (brew.elapsed_s > JOINED_LATE_S) backfill(brew.id);
  }
  if (next.water_ml !== null) live.water.push([brew.elapsed_s, next.water_ml]);
  if (next.weight_g !== null) live.weight.push([brew.elapsed_s, next.weight_g]);
  if (brew.pour !== live.lastPour) { live.lastPour = brew.pour; live.marks.push(brew.elapsed_s); }
});
