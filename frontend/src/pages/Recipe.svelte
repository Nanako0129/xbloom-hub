<script lang="ts">
  import { api } from "../lib/hub.svelte";
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
        <!-- Loading and deleting stay on the current dashboard until the safety flows move (S3c, C11). -->
        <a class="pill" href="/#/recipes/{encodeURIComponent(slug)}">{t("next.load_there")}</a>
      </div>
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
