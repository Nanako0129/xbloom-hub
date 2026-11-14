<script lang="ts">
  import { t } from "../lib/i18n.svelte";
  import { clock } from "../lib/format";
  import { heat, type LibraryEntry } from "../lib/recipes";

  let { entry }: { entry: LibraryEntry } = $props();
  const GAP = 0.8;     // between two pours in the bar, in percent of its width
  const bars = $derived.by(() => {
    if (!entry.valid || !entry.pours || !entry.total_ml) return [];
    let x = 0;
    return entry.pours.map((pour) => {
      const width = (pour.volume_ml / (entry.total_ml as number)) * 100;
      const bar = { x: x + GAP / 2, width: Math.max(0.5, width - GAP), kind: heat(pour.temperature_c) };
      x += width;
      return bar;
    });
  });
</script>

{#if entry.valid}
  <a class="recipe" href="#/recipes/{encodeURIComponent(entry.slug)}">
    <div class="name">{entry.name || entry.slug}</div>
    <div class="line num">{t("library.line", { dose: entry.dose_g ?? "", ratio: entry.ratio ?? "", total: entry.total_ml ?? "", time: clock(entry.pour_end_s) })}</div>
    <svg class="bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
      {#each bars as bar, i (i)}<rect x={bar.x} y="0" width={bar.width} height="8" rx="2" class={bar.kind} />{/each}
    </svg>
  </a>
{:else}
  <div class="recipe">
    <div class="name">{entry.name || entry.slug}</div>
    <div class="line num">{t("library.invalid")}</div>
  </div>
{/if}
