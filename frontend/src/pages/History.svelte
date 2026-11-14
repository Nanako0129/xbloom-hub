<script lang="ts" module>
  // The brews ticked for comparing stay ticked while the page is away.
  let kept: string[] = [];
</script>

<script lang="ts">
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { clock, when, endingText } from "../lib/format";
  import type { BrewSummary } from "../lib/records";

  let brews = $state<BrewSummary[] | null>(null);
  let failed = $state("");
  let picked = $state<string[]>(kept);
  $effect(() => { kept = picked; });

  api<BrewSummary[]>("GET", "/api/brews").then((list) => { brews = list; }).catch((error) => { failed = error.message; });

  function toggle(id: string, on: boolean) {
    picked = on ? [...picked.filter((x) => x !== id), id].slice(-2) : picked.filter((x) => x !== id);
  }
</script>

<h1>{t("history.title")}</h1>
{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !brews}
  <p class="muted">{t("loading")}</p>
{:else if !brews.length}
  <p class="muted">{t("history.empty")}</p>
{:else}
  <ul class="list">
    {#each brews as b (b.id)}
      <li class="pick">
        <input type="checkbox" aria-label={t("history.pick")} checked={picked.includes(b.id)}
          onchange={(event) => toggle(b.id, event.currentTarget.checked)} />
        <a href="#/history/{encodeURIComponent(b.id)}">
          <span class="title">{b.recipe_name || t("brew.at_machine")}</span>
          <span class="sub num">{[when(b.started_at), `${b.water_ml == null ? "-" : Math.round(b.water_ml)} ml`,
            b.duration_s == null ? "" : clock(b.duration_s), endingText(b.reason)].filter(Boolean).join("   ")}</span>
        </a>
      </li>
    {/each}
  </ul>
  {#if brews.length > 1}
    <div class="actions">
      <a class="pill" class:disabled={picked.length !== 2} aria-disabled={picked.length !== 2}
        href={picked.length === 2 ? `#/compare/${picked.map(encodeURIComponent).join("/")}` : undefined}>{t("action.compare")}</a>
    </div>
  {/if}
{/if}
