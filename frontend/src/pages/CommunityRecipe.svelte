<script lang="ts">
  // One community recipe: what xBloom's users wrote, the recipe as the hub
  // would save it, and "Save to my recipes" (docs/PLAN.md, S3b). Saving never
  // loads the machine. Strangers' text goes into the page only as text.
  import { untrack } from "svelte";
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { act, work } from "../lib/actions.svelte";
  import { planLine, type Plan } from "../lib/records";
  import type { Recipe } from "../lib/recipes";
  import Chart from "../components/Chart.svelte";
  import PoursList from "../components/PoursList.svelte";

  let { id }: { id: string } = $props();

  interface Shown {
    id: number; importable: boolean; reasons: Record<string, string>; plan: Plan | null;
    from_xbloom_users: {
      community: { name: string; author: string; official: boolean; description: string; origin: string[];
                   varietal: string[]; process: string[]; roast: string; flavor: string[] };
      recipe: Recipe | null;
    };
  }

  let shown = $state<Shown | null>(null);
  let failed = $state("");
  // A suggestion only; the person decides the id. (App remounts this page per address.)
  let slug = $state(untrack(() => `xbloom-${id}`.toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 64)));

  $effect(() => {
    let current = true;
    api<Shown>("GET", `/api/community/${encodeURIComponent(id)}`)
      .then((answer) => { if (current) shown = answer; }).catch((error) => { if (current) failed = error.message; });
    return () => { current = false; };
  });

  const reason = (field: string, why: string) =>
    field === "bypass_water" || field === "share_url" ? t(`community.reason.${field}`) : t("community.reason.other", { field, reason: why });

  const save = () => act(async () => {
    const answer = await api<{ slug: string }>("POST", "/api/community/import", { id: Number(id), slug: slug.trim() });
    location.hash = `#/recipes/${encodeURIComponent(answer.slug)}`;
  }, t("community.save"));

  const card = $derived(shown?.from_xbloom_users.community);
  // What will be saved when there is a recipe (a pod's beans and name
  // included); else what the card lists.
  const saved = $derived(shown?.from_xbloom_users.recipe?.meta);
  const facts = $derived(saved ? [
    ["community.origin", saved.bean ?? ""], ["community.process", saved.process ?? ""],
    ["community.roast", saved.roast ?? ""], ["meta.notes", saved.notes ?? ""],
  ].filter(([, value]) => value) : card ? [
    ["community.origin", [...card.origin, ...card.varietal].join(", ")],
    ["community.process", card.process.join(", ")],
    ["community.roast", card.roast],
  ].filter(([, value]) => value) : []);
</script>

<p><a href="#/community">{t("community.back")}</a></p>
{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !shown || !card}
  <p class="muted">{t("loading")}</p>
{:else}
  <h1>{card.name}</h1>
  <p class="muted">{#if card.official}<span class="badge">{t("community.official")}</span>{/if}{card.author}</p>
  <div class="cols">
    <div>
      {#if shown.importable && shown.plan && shown.from_xbloom_users.recipe}
        <PoursList plan={shown.plan} pours={shown.from_xbloom_users.recipe.pours} />
        <form class="card community-form" onsubmit={(event) => { event.preventDefault(); save(); }}>
          <label><span>{t("community.slug")}</span><input id="community-slug" required pattern="[a-z0-9][a-z0-9\-]*" maxlength="64" bind:value={slug} /></label>
          <p class="muted">{t("community.slug_hint")}</p>
          <div class="actions"><button type="submit" disabled={work.busy}>{t("community.save")}</button></div>
        </form>
      {:else}
        <section class="card" id="not-importable">
          <p>{t("community.not_importable")}</p>
          <ul>{#each Object.entries(shown.reasons) as [field, why] (field)}<li>{reason(field, why)}</li>{/each}</ul>
        </section>
      {/if}
    </div>
    <div>
      {#if shown.plan}
        <section class="card">
          <h2>{t("card.plan")}</h2>
          <Chart lines={[{ key: "plan", kind: "plan", label: t("chart.plan"), points: planLine(shown.plan) }]} />
        </section>
      {/if}
      <section class="card">
        <p class="muted">{t("community.from")}</p>
        {#if facts.length}
          <dl class="facts long">{#each facts as [label, value] (label)}<dt>{t(label)}</dt><dd>{value}</dd>{/each}</dl>
        {/if}
        {#if card.description}<h2>{t("community.description")}</h2><p class="prose">{card.description}</p>{/if}
      </section>
    </div>
  </div>
{/if}
