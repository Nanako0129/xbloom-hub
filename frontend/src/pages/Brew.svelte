<script lang="ts">
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { clock, when, endingText } from "../lib/format";
  import { eventMarks, planLine, pourStarts, recordName, type BrewRecord } from "../lib/records";
  import Chart, { type Line } from "../components/Chart.svelte";
  import Icon from "../components/Icon.svelte";

  const REPLAY_SPEED = 4;       // a replay runs at this many times real speed: a 3 minute brew in 45 s
  const SCRUB_STEP = 0.1;       // the scrubber's resolution, seconds
  // Under reduced motion nothing plays by itself: the scrubber alone moves the replay.
  const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

  let { id }: { id: string } = $props();
  let record = $state<BrewRecord | null>(null);
  let failed = $state("");

  $effect(() => {
    record = null; failed = "";
    let current = true;
    api<BrewRecord>("GET", `/api/brews/${encodeURIComponent(id)}`)
      .then((r) => { if (current) record = r; }).catch((error) => { if (current) failed = error.message; });
    return () => { current = false; };
  });

  const starts = $derived(record ? pourStarts(record) : []);
  const offset = $derived(starts.length ? starts[0] : 0);       // the plan's zero is the first pour
  const lines = $derived.by((): Line[] => {
    if (!record) return [];
    const out: Line[] = [];
    if (record.plan) out.push({ key: "plan", kind: "plan", label: t("chart.plan"),
      points: planLine(record.plan).map(([x, y]) => [x + offset, y]) });
    out.push({ key: "water", kind: "water", label: t("chart.water"), points: record.samples.water, gaps: record.gaps });
    out.push({ key: "coffee", kind: "coffee", label: t("chart.coffee"), points: record.samples.weight, gaps: record.gaps });
    return out;
  });
  const marks = $derived(record ? eventMarks(record) : []);

  // ---- replay: the curves redrawn up to a moment ------------------------------
  const span = $derived(record ? Math.max(record.duration_s ?? 0, ...record.samples.water.map((p) => p[0]),
    ...record.samples.weight.map((p) => p[0])) : 0);
  let replayAt = $state<number | null>(null);       // null: no replay, the whole brew is drawn
  let playing = $state(false);
  let frame = 0, lastFrame = 0;
  function tick(now: number) {
    replayAt = Math.min(span, (replayAt ?? 0) + ((now - lastFrame) / 1000) * REPLAY_SPEED);
    lastFrame = now;
    if (replayAt >= span) { playing = false; replayAt = null; return; }    // the end: the whole brew again
    frame = requestAnimationFrame(tick);
  }
  function play() {
    if (replayAt === null || replayAt >= span) replayAt = 0;
    playing = true;
    lastFrame = performance.now();
    frame = requestAnimationFrame(tick);
  }
  function pause() { playing = false; cancelAnimationFrame(frame); }
  $effect(() => { id; replayAt = null; return pause; });      // another brew, or leaving the page: stop

  const last = (points: [number, number][]) => (points.length ? Math.round(points[points.length - 1][1]) : null);
  const rows = $derived(record?.plan ? record.plan.pours.map((p, i) => {
    const actual = starts[i] === undefined ? null : starts[i] - offset;
    const diff = actual === null ? null : Math.round(actual - p.start_s);
    return { n: i + 1, planned: clock(p.start_s), actual: actual === null ? "-" : clock(actual),
             diff: diff === null ? "-" : t("table.seconds", { sign: diff >= 0 ? "+" : "−", s: Math.abs(diff) }) };
  }) : []);
</script>

<p><a href="#/history">{t("history.all")}</a></p>
{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !record}
  <p class="muted">{t("loading")}</p>
{:else}
  <h1>{recordName(record)}</h1>
  <div class="cols">
    <div>
      <Chart {lines} {marks} upto={replayAt}>
        <div class="replay">
          {#if !reduced}
            <button type="button" class="quiet play" aria-label={playing ? t("replay.pause") : t("replay.play")}
              onclick={() => (playing ? pause() : play())}><span class="icon"><Icon name={playing ? "pause" : "play"} /></span></button>
          {/if}
          <input type="range" class="scrub" min="0" max={span} step={SCRUB_STEP} value={replayAt ?? span}
            aria-label={t("replay.scrub")} oninput={(event) => { const at = Number(event.currentTarget.value); replayAt = at >= span ? null : at; }} />
          <span class="num muted">{clock(replayAt ?? span)} / {clock(span)}</span>
        </div>
      </Chart>
    </div>
    <div>
      <section class="card">
        <dl class="facts">
          <dt>{t("record.time")}</dt><dd class="num">{when(record.started_at)}</dd>
          <dt>{t("record.result")}</dt><dd>{endingText(record.reason)}</dd>
          <dt>{t("record.duration")}</dt><dd class="num">{record.duration_s == null ? "-" : clock(record.duration_s)}</dd>
          <dt>{t("record.water")}</dt><dd class="num">{last(record.samples.water) === null ? "-" : `${last(record.samples.water)} ml`}</dd>
          <dt>{t("record.coffee")}</dt><dd class="num">{last(record.samples.weight) === null ? "-" : `${last(record.samples.weight)} g`}</dd>
          <dt>{t("record.started")}</dt><dd>{record.hub_started ? t("record.started_here") : t("record.started_machine")}</dd>
        </dl>
      </section>
      {#if rows.length}
        <section class="card">
          <table>
            <thead><tr><th>{t("table.pour")}</th><th>{t("table.planned")}</th><th>{t("table.actual")}</th><th>{t("table.diff")}</th></tr></thead>
            <tbody>
              {#each rows as row (row.n)}
                <tr><td>{t("table.pour_n", { n: row.n })}</td><td class="num">{row.planned}</td><td class="num">{row.actual}</td><td class="num">{row.diff}</td></tr>
              {/each}
            </tbody>
          </table>
          <p class="muted">{t("table.note")}</p>
        </section>
      {/if}
    </div>
  </div>
{/if}
