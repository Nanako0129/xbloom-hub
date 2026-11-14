<script lang="ts">
  import { hub } from "../lib/hub.svelte";
  import { stateName, stateDetail, cupText, tankText } from "../lib/words";
  import { t } from "../lib/i18n.svelte";

  const s = $derived(hub.state);
</script>

{#if s && !hub.reachable}
  <!-- What the hub last said is no longer known to be true. -->
  <div class="state"><span class="lamp" data-state="offline"></span>{t("status.no_hub")}</div>
{:else if s}
  <div class="state"><span class="lamp" data-state={s.state}></span>{stateName(s.state)}</div>
  {#if stateDetail(s)}<div class="muted">{stateDetail(s)}</div>{/if}
  <dl class="facts">
    <dt>{t("cup")}</dt><dd>{cupText(s.cup)}</dd>
    <dt>{t("tank")}</dt><dd>{tankText(s.water_enough)}</dd>
    <dt>{t("console.firmware")}</dt><dd class="num">{s.fw_version ?? "-"}</dd>
  </dl>
{/if}
