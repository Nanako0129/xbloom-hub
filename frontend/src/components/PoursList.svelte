<script lang="ts">
  import { t } from "../lib/i18n.svelte";
  import { clock } from "../lib/format";
  import type { Plan } from "../lib/records";
  import type { Pour } from "../lib/recipes";
  import Icon from "./Icon.svelte";

  // current: the pour being poured (a brew running). water: the water measured so
  // far, ml. starts: when each pour was seen to start, by pour index, seconds into the brew.
  let { plan, pours, current = null, water = null, starts = {} }:
    { plan: Plan; pours: Pour[]; current?: number | null; water?: number | null; starts?: Record<number, number> } = $props();

  // How far the water measured has come through a pour, 0 to 1: from the plan's
  // total before it to the total after it.
  const progress = (i: number): number => {
    if (water === null) return 0;
    const before = i > 0 ? plan.pours[i - 1].cumulative_ml : 0;
    const volume = plan.pours[i].volume_ml;
    return volume > 0 ? Math.min(1, Math.max(0, (water - before) / volume)) : 0;
  };
  // When a pour started against when the plan had it (both counted from the first pour); the first has nothing to differ from.
  const diff = (i: number): string | null => {
    if (i === 0 || starts[i] === undefined || starts[0] === undefined) return null;
    const seconds = Math.round(starts[i] - starts[0] - (plan.pours[i].start_s - plan.pours[0].start_s));
    return t("table.seconds", { sign: seconds >= 0 ? "+" : "−", s: Math.abs(seconds) });
  };
</script>

<ol class="pours">
  {#each plan.pours as p, i (i)}
    {@const done = current !== null && i < current}
    {@const now = current === i}
    <li data-now={now ? "true" : undefined} data-done={done ? "true" : undefined}>
      <div class="share num">{plan.total_ml ? Math.round((p.volume_ml / plan.total_ml) * 100) : 0}<small>%</small></div>
      <div class="detail num">
        <span>{p.volume_ml} ml</span><span>{p.temperature_c} °C</span>
        <span>{(Number(pours[i]?.flow_rate) || 0).toFixed(1)} ml/s</span>
        <span>{p.pause_s ? t("recipe.pause", { s: p.pause_s }) : t("recipe.no_pause")}</span>
        <span class="when">{t("recipe.when", { start: clock(p.start_s), end: clock(p.end_s), total: p.cumulative_ml })}</span>
      </div>
      {#if now || done}
        <div class="live">
          {#if done}
            <span class="done-mark" role="img" aria-label={t("pour.done")}><Icon name="check" /></span>
          {:else}
            <div class="bar" role="progressbar" aria-label={t("pour.progress")} aria-valuemin="0" aria-valuemax="100"
              aria-valuenow={Math.round(progress(i) * 100)}><i style:--p={progress(i)}></i></div>
          {/if}
          {#if diff(i)}<span class="diff num" title={t("pour.diff")}>{diff(i)}</span>{/if}
        </div>
      {/if}
    </li>
  {/each}
</ol>
