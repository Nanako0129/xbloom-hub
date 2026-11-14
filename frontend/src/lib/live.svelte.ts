// The brew that is running: its readings as they arrive, and what came before
// this page was looking (fetched from its record). As the current dashboard.
import { api, follow } from "./hub.svelte";
import { pourStarts, startsByIndex, type BrewRecord, type Point } from "./records";

export const live = $state({
  id: null as string | null,
  water: [] as Point[],
  weight: [] as Point[],
  marks: [] as number[],
  startAt: {} as Record<number, number>,     // when each pour (by its index) was seen to start, seconds into the brew
  lastPour: null as number | null,
  lastSeen: null as number | null,           // the brew time of the last report this page got
  gaps: [] as [number, number][],            // spans the hub itself heard nothing (its link was down)
});
// Reports come several times a second; a longer silence means this page missed
// some (a phone put the page in the background, the stream reconnected).
const MISSED_S = 3;
// The record's times are to 0.01 s, the stream's to 0.1 s: what the page heard
// within this of the record's end is the same reading, not a new one.
const SEAM_S = 0.5;

const JOINED_LATE_S = 2;     // a brew this far along when first seen was running before the page

// The hub's record of the running brew is the whole truth up to now: take it,
// and keep only what this page heard after the record's last reading.
async function backfill(id: string): Promise<void> {
  let record: BrewRecord;
  try { record = await api<BrewRecord>("GET", `/api/brews/${encodeURIComponent(id)}`); } catch { return; }
  if (live.id !== id) return;
  const after = (end: number | undefined) => (time: number) => time > (end ?? -Infinity) + SEAM_S;
  const merged = (recorded: Point[], heard: Point[]) =>
    recorded.concat(heard.filter((p) => after(recorded.at(-1)?.[0])(p[0])));
  live.water = merged(record.samples.water, live.water);
  live.weight = merged(record.samples.weight, live.weight);
  live.startAt = { ...live.startAt, ...startsByIndex(record) };
  const starts = pourStarts(record);
  live.marks = starts.concat(live.marks.filter(after(starts.at(-1))));
  live.gaps = record.gaps ?? [];
}

follow((next) => {
  const brew = next.brew;
  if (!brew) return;
  if (live.id !== brew.id) {
    // Joining a brew is not a pour starting: count from the pour it is on.
    Object.assign(live, { id: brew.id, water: [], weight: [], marks: [], startAt: {}, lastPour: brew.pour,
                          lastSeen: null, gaps: [] });
    if (brew.elapsed_s > JOINED_LATE_S) backfill(brew.id);
  } else if ((live.lastSeen !== null && brew.elapsed_s - live.lastSeen > MISSED_S)
             || (brew.gaps ?? 0) !== live.gaps.length) {
    backfill(brew.id);          // this page missed reports, or the hub's link had a gap: take the record
  }
  live.lastSeen = brew.elapsed_s;
  if (next.water_ml !== null) live.water.push([brew.elapsed_s, next.water_ml]);
  if (next.weight_g !== null) live.weight.push([brew.elapsed_s, next.weight_g]);
  // A pour starting is a new pour index; a report without one is not a start.
  if (brew.pour !== null && brew.pour !== undefined && brew.pour !== live.lastPour) {
    live.lastPour = brew.pour;
    live.marks.push(brew.elapsed_s);
    live.startAt[brew.pour] = brew.elapsed_s;
  }
});
