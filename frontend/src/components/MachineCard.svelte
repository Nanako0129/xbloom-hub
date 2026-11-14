<script lang="ts">
  import { hub } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { stateName, cupText, tankText, reasonText } from "../lib/words";

  const s = $derived(hub.state);
  const detail = $derived(s ? (s.state === "offline" ? reasonText(s.error) : (s.brew?.recipe_name ?? s.recipe?.name ?? "")) : "");
</script>

{#if s}
  <div class="state"><span class="lamp" data-state={s.state}></span>{stateName(s.state)}</div>
  {#if detail}<div class="muted">{detail}</div>{/if}
  <dl class="facts">
    <dt>{t("cup")}</dt><dd>{cupText(s.cup)}</dd>
    <dt>{t("tank")}</dt><dd>{tankText(s.water_enough)}</dd>
    <dt>{t("console.firmware")}</dt><dd class="num">{s.fw_version ?? "-"}</dd>
  </dl>
{/if}
