<script lang="ts">
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { clock, when, endingText } from "../lib/format";
  import { planLine, pourStarts, recordName, type BrewRecord } from "../lib/records";
  import Chart, { type Line } from "../components/Chart.svelte";

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
    out.push({ key: "water", kind: "water", label: t("chart.water"), points: record.samples.water });
    out.push({ key: "coffee", kind: "coffee", label: t("chart.coffee"), points: record.samples.weight });
    return out;
  });
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
    <div><Chart {lines} marks={starts} /></div>
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
