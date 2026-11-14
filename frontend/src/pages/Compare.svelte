<script lang="ts">
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { when } from "../lib/format";
  import { pourStarts, recordName, type BrewRecord, type Point } from "../lib/records";
  import Chart, { type Line } from "../components/Chart.svelte";

  let { ids }: { ids: string[] } = $props();
  let records = $state<BrewRecord[] | null>(null);
  let failed = $state("");

  $effect(() => {
    records = null; failed = "";
    if (ids.length < 2) { location.hash = "#/history"; return; }
    Promise.all(ids.slice(0, 2).map((id) => api<BrewRecord>("GET", `/api/brews/${encodeURIComponent(id)}`)))
      .then((list) => { records = list; }).catch((error) => { failed = error.message; });
  });

  // Both brews aligned on their first pour.
  const shifted = (record: BrewRecord, key: "water" | "weight"): Point[] => {
    const s = pourStarts(record)[0] ?? 0;
    return record.samples[key].map(([x, y]): Point => [x - s, y]).filter(([x]) => x >= 0);
  };
  const name = (record: BrewRecord) => t("compare.name", { name: recordName(record), when: when(record.started_at) });
  const lines = $derived.by((): Line[] => {
    if (!records) return [];
    const [a, b] = records;
    return [
      { key: "a-water", kind: "water", label: t("chart.water"), points: shifted(a, "water") },
      { key: "a-coffee", kind: "coffee", label: t("chart.coffee"), points: shifted(a, "weight") },
      { key: "b-water", kind: "water", label: t("chart.other", { name: t("chart.water") }), points: shifted(b, "water"), dashed: true },
      { key: "b-coffee", kind: "coffee", label: t("chart.other", { name: t("chart.coffee") }), points: shifted(b, "weight"), dashed: true },
    ];
  });
</script>

<p><a href="#/history">{t("history.all")}</a></p>
<h1>{t("compare.title")}</h1>
{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !records}
  <p class="muted">{t("loading")}</p>
{:else}
  <div class="cols">
    <div><Chart {lines} /></div>
    <div>
      <section class="card">
        <dl class="facts long">
          <dt>{t("compare.solid")}</dt><dd>{name(records[0])}</dd>
          <dt>{t("compare.dashed")}</dt><dd>{name(records[1])}</dd>
        </dl>
        <p class="muted">{t("compare.note")}</p>
      </section>
    </div>
  </div>
{/if}
