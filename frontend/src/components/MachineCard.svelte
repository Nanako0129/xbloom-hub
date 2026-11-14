<script lang="ts">
  import { hub } from "../lib/hub.svelte";
  import { stateLabel, stateDetail, cupText, tankText, unheard } from "../lib/words";
  import { t } from "../lib/i18n.svelte";
  import { act, work, release, acquire, ask } from "../lib/actions.svelte";

  const s = $derived(hub.state);
</script>

{#if s && !hub.reachable}
  <!-- What the hub last said is no longer known to be true. -->
  <div class="state"><span class="lamp" data-state="offline"></span>{t("status.no_hub")}</div>
{:else if s}
  <div class="state"><span class="lamp" data-state={unheard(s) ? "unheard" : s.state}></span>{stateLabel(s)}</div>
  {#if stateDetail(s)}<div class="muted">{stateDetail(s)}</div>{/if}
  <dl class="facts">
    <dt>{t("cup")}</dt><dd>{cupText(s.cup)}</dd>
    <dt>{t("tank")}</dt><dd>{tankText(s.water_enough)}</dd>
    <dt>{t("console.firmware")}</dt><dd class="num">{s.fw_version ?? "-"}</dd>
  </dl>
  {#if s.state === "released"}
    <button class="quiet small" disabled={work.busy} onclick={() => act(acquire)}>{t("action.acquire")}</button>
  {:else}
    <button class="quiet small" disabled={work.busy || s.state === "brewing" || s.state === "armed"}
      onclick={async () => { if (await ask({ title: t("release.title"), body: t("release.body"), yes: t("action.release") })) act(release); }}>
      {t("action.release")}</button>
  {/if}
{/if}
