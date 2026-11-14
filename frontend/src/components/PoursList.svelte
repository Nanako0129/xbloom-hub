<script lang="ts">
  import { t } from "../lib/i18n.svelte";
  import { clock } from "../lib/format";
  import type { Plan } from "../lib/records";
  import type { Pour } from "../lib/recipes";

  let { plan, pours, current = null }: { plan: Plan; pours: Pour[]; current?: number | null } = $props();
</script>

<ol class="pours">
  {#each plan.pours as p, i (i)}
    <li data-now={current === i ? "true" : undefined} data-done={current !== null && i < current ? "true" : undefined}>
      <div class="share num">{plan.total_ml ? Math.round((p.volume_ml / plan.total_ml) * 100) : 0}<small>%</small></div>
      <div class="detail num">
        <span>{p.volume_ml} ml</span><span>{p.temperature_c} °C</span>
        <span>{(Number(pours[i]?.flow_rate) || 0).toFixed(1)} ml/s</span>
        <span>{p.pause_s ? t("recipe.pause", { s: p.pause_s }) : t("recipe.no_pause")}</span>
        <span class="when">{t("recipe.when", { start: clock(p.start_s), end: clock(p.end_s), total: p.cumulative_ml })}</span>
      </div>
    </li>
  {/each}
</ol>
