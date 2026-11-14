<script lang="ts" module>
  // The brews ticked for comparing stay ticked while the page is away.
  let kept: string[] = [];
</script>

<script lang="ts">
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { clock, when, endingText, dayKey } from "../lib/format";
  import type { BrewSummary } from "../lib/records";
  import Spark from "../components/Spark.svelte";
  import Calendar from "../components/Calendar.svelte";

  let brews = $state<BrewSummary[] | null>(null);
  let failed = $state("");
  let picked = $state<string[]>(kept);
  $effect(() => { kept = picked; });

  api<BrewSummary[]>("GET", "/api/brews").then((list) => { brews = list; }).catch((error) => { failed = error.message; });

  function toggle(id: string, on: boolean) {
    picked = on ? [...picked.filter((x) => x !== id), id].slice(-2) : picked.filter((x) => x !== id);
  }

  // ---- filters: a recipe, and a range of days (the calendar sets a single day) ----
  let recipe = $state("");
  let from = $state("");
  let to = $state("");
  const nameOf = (b: BrewSummary) => b.recipe_name || t("brew.at_machine");
  // The day a brew started, on this device's calendar, as YYYY-MM-DD (what a date input holds).
  const dayOf = (iso: string | null): string => {
    const d = new Date(iso ?? "");
    return isNaN(+d) ? "" : dayKey(d);
  };

  // Filtered by slug, not by name: two recipes may share a name. A brew with
  // no slug (started at the machine with nothing loaded) is its own entry.
  const NO_SLUG = "\u0000";
  const keyOf = (b: BrewSummary) => b.slug ?? NO_SLUG;
  const names = $derived([...new Map((brews ?? []).map((b) => [keyOf(b), nameOf(b)])).entries()]
    .sort((a, b) => a[1].localeCompare(b[1])));
  const ofRecipe = $derived((brews ?? []).filter((b) => !recipe || keyOf(b) === recipe));
  // The calendar counts the recipe's brews on every day; the range is what it then picks.
  const counts = $derived.by(() => {
    const out: Record<string, number> = {};
    for (const b of ofRecipe) { const day = dayOf(b.started_at); if (day) out[day] = (out[day] ?? 0) + 1; }
    return out;
  });
  const shown = $derived(ofRecipe.filter((b) => {
    const day = dayOf(b.started_at);
    return (!from || day >= from) && (!to || day <= to);
  }));
  const filtering = $derived(Boolean(recipe || from || to));
  const day = $derived(from && from === to ? from : null);
  const pickDay = (key: string) => { from = to = day === key ? "" : key; };
  const clear = () => { recipe = from = to = ""; };
</script>

<h1>{t("history.title")}</h1>
{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !brews}
  <p class="muted">{t("loading")}</p>
{:else if !brews.length}
  <p class="muted">{t("history.empty")}</p>
{:else}
  <div class="cols history">
    <div class="history-aside">
      <section class="card filters">
        <label class="field wide"><span>{t("history.recipe")}</span>
          <select bind:value={recipe}>
            <option value="">{t("history.all_recipes")}</option>
            {#each names as [key, name] (key)}<option value={key}>{name}</option>{/each}
          </select></label>
        <label class="field"><span>{t("history.from")}</span><input type="date" bind:value={from} /></label>
        <label class="field"><span>{t("history.to")}</span><input type="date" bind:value={to} /></label>
        <div class="row wide">
          <span class="muted num">{t("history.count", { shown: shown.length, total: brews.length })}</span>
          <button type="button" class="quiet small" disabled={!filtering} onclick={clear}>{t("history.clear")}</button>
        </div>
      </section>
      <section class="card">
        <h2>{t("cal.title")}</h2>
        <Calendar {counts} selected={day} onpick={pickDay} />
      </section>
    </div>
    <div>
      {#if shown.length}
        <ul class="list">
          {#each shown as b (b.id)}
            <li class="pick">
              <input type="checkbox" aria-label={t("history.pick")} checked={picked.includes(b.id)}
                onchange={(event) => toggle(b.id, event.currentTarget.checked)} />
              <a href="#/history/{encodeURIComponent(b.id)}">
                <span class="title">{nameOf(b)}</span>
                <Spark points={b.spark ?? []} />
                <span class="sub num">{[when(b.started_at), `${b.water_ml == null ? "-" : Math.round(b.water_ml)} ml`,
                  b.duration_s == null ? "" : clock(b.duration_s), endingText(b.reason)].filter(Boolean).join("   ")}</span>
              </a>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="muted">{t("history.none")}</p>
      {/if}
      {#if brews.length > 1}
        <div class="actions">
          <button class="quiet" disabled={picked.length !== 2}
            onclick={() => { location.hash = `#/compare/${picked.map(encodeURIComponent).join("/")}`; }}>{t("action.compare")}</button>
        </div>
      {/if}
    </div>
  </div>
{/if}
