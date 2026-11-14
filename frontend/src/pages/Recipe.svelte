<script lang="ts">
  import { api, hub } from "../lib/hub.svelte";
  import { act, work, load, ask } from "../lib/actions.svelte";
  import { t } from "../lib/i18n.svelte";
  import { planLine } from "../lib/records";
  import { isLink, type RecipeInfo } from "../lib/recipes";
  import Chart from "../components/Chart.svelte";
  import RecipeFacts from "../components/RecipeFacts.svelte";
  import PoursList from "../components/PoursList.svelte";

  let { slug }: { slug: string } = $props();
  let info = $state<RecipeInfo | null>(null);
  let failed = $state("");

  $effect(() => {
    info = null; failed = "";
    let current = true;
    api<RecipeInfo>("GET", `/api/recipes/${encodeURIComponent(slug)}/plan`)
      .then((answer) => { if (current) info = answer; }).catch((error) => { if (current) failed = error.message; });
    return () => { current = false; };
  });

  const META = ["bean", "roaster", "process", "roast"] as const;
  const canLoad = $derived(hub.state?.state === "idle" || hub.state?.state === "asleep");
  const meta = $derived(info?.recipe.meta ?? {});
</script>

<p><a href="#/recipes">{t("recipe.all")}</a></p>
{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !info}
  <p class="muted">{t("loading")}</p>
{:else}
  <h1>{info.recipe.name || slug}</h1>
  <div class="cols">
    <div>
      <PoursList plan={info.plan} pours={info.recipe.pours} />
      <p class="muted">{t("recipe.computed")}</p>
    </div>
    <div>
      <section class="card"><RecipeFacts {info} /></section>
      <div class="actions">
        <a class="pill" href="#/recipes/{encodeURIComponent(slug)}/edit">{t("action.edit")}</a>
        <a class="pill" href="#/recipes/new/{encodeURIComponent(slug)}">{t("action.duplicate")}</a>
        <button disabled={work.busy || !canLoad} onclick={() => act(() => load(slug), t("working.load"))}>{t("action.load")}</button>
        <button class="quiet" disabled={work.busy}
          onclick={async () => { if (info && await ask({ title: t("delete.title", { name: info.recipe.name }), body: t("delete.body"), yes: t("action.delete"), danger: true }))
            act(async () => { await api("DELETE", `/api/recipes/${encodeURIComponent(slug)}`); location.hash = "#/recipes"; }); }}>
          {t("action.delete")}</button>
      </div>
      {#if !canLoad}<p class="muted">{t("recipe.load_hint")}</p>{/if}
      <section class="card">
        <h2>{t("card.plan")}</h2>
        <Chart lines={[{ key: "plan", kind: "plan", label: t("chart.plan"), points: planLine(info.plan) }]} />
      </section>
      {#if META.some((key) => meta[key]) || meta.source || meta.notes}
        <section class="card">
          <dl class="facts long">
            {#each META as key (key)}
              {#if meta[key]}<dt>{t(`meta.${key}`)}</dt><dd>{meta[key]}</dd>{/if}
            {/each}
            {#if meta.source}
              <dt>{t("meta.source")}</dt>
              <dd>{#if isLink(meta.source)}<a href={meta.source} rel="noopener noreferrer" target="_blank">{meta.source}</a>{:else}{meta.source}{/if}</dd>
            {/if}
            {#if meta.notes}<dt>{t("meta.notes")}</dt><dd>{meta.notes}</dd>{/if}
          </dl>
        </section>
      {/if}
    </div>
  </div>
{/if}
