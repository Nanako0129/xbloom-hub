<script lang="ts">
  // xBloom's community recipes, searched through the hub (docs/PLAN.md, S3b).
  // Every string under from_xbloom_users was written by strangers: it is only
  // ever put into the page as text, which Svelte does for {expressions}.
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { act, work } from "../lib/actions.svelte";

  interface Card {
    id: number; official: boolean; name: string; author: string; origin: string[]; roast: string;
    dose_g: number | null; water_ratio: number | null; pour_count: number | null; likes: number | null;
  }
  interface Page { page: number; total_pages: number; total: number; from_xbloom_users: { recipes: Card[] } }
  type Criteria = Record<string, string[]>;

  const FILTERS = [["machine", "community.machine"], ["origins", "community.origin"],
                   ["processes", "community.process"], ["roasts", "community.roast"]] as const;

  let criteria = $state<Criteria>({});
  let keyword = $state("");
  let source = $state("");
  let sort = $state("date");
  let chosen = $state<Record<string, string>>({});
  const MAX_PAGE = 50;        // the hub's own limit on a page number
  let page = $state(1);
  let asked = new URLSearchParams();   // the query whose results are on screen
  let results = $state<Page | null>(null);
  let failed = $state("");
  let searching = $state(false);
  let link = $state("");
  let linkSlug = $state("");

  // One after the other: the hub runs two requests to xBloom at most.
  api<{ from_xbloom_users: Criteria }>("GET", "/api/community/criteria")
    .then((answer) => { criteria = answer.from_xbloom_users; }).catch(() => { /* filters stay empty */ })
    .finally(() => search());

  // A new search reads the form; paging keeps the query that is on screen.
  async function search(at = 1, query = formQuery()) {
    failed = ""; searching = true;
    query.set("page", String(at));
    try { results = await api<Page>("GET", `/api/community/search?${query}`); asked = query; page = at; }
    catch (error) { failed = (error as Error).message; }
    finally { searching = false; }
  }
  function formQuery(): URLSearchParams {
    const query = new URLSearchParams({ sort });
    if (keyword.trim()) query.set("keyword", keyword.trim());
    if (source) query.set("source", source);
    for (const [key] of FILTERS) if (chosen[key]) query.set(key, chosen[key]);
    return query;
  }
  const lastPage = $derived(Math.min(results?.total_pages ?? 1, MAX_PAGE));

  const importLink = () => act(async () => {
    const answer = await api<{ slug: string }>("POST", "/api/community/import", { share_url: link.trim(), slug: linkSlug.trim() });
    location.hash = `#/recipes/${encodeURIComponent(answer.slug)}`;
  }, t("community.import"));
</script>

<p><a href="#/recipes">{t("recipe.all")}</a></p>
<h1>{t("community.title")}</h1>
<p class="muted">{t("community.hint")}</p>

<form class="card community-form community-search" onsubmit={(event) => { event.preventDefault(); search(1); }}>
  <label><span>{t("community.keyword")}</span><input type="search" maxlength="80" bind:value={keyword} /></label>
  <label><span>{t("community.source")}</span>
    <select bind:value={source}>
      <option value="">{t("community.any")}</option>
      <option value="official">{t("community.official")}</option>
      <option value="community">{t("community.users")}</option>
    </select></label>
  <label><span>{t("community.sort")}</span>
    <select bind:value={sort}>
      <option value="date">{t("community.sort_date")}</option>
      <option value="likes">{t("community.sort_likes")}</option>
      <option value="downloads">{t("community.sort_downloads")}</option>
    </select></label>
  {#each FILTERS as [key, label] (key)}
    {#if criteria[key]?.length}
      <label><span>{t(label)}</span>
        <select bind:value={chosen[key]}>
          <option value="">{t("community.any")}</option>
          {#each criteria[key] as name (name)}<option value={name}>{name}</option>{/each}
        </select></label>
    {/if}
  {/each}
  <div class="actions"><button type="submit" disabled={searching}>{t("community.search")}</button></div>
</form>

{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !results}
  <p class="muted">{t("loading")}</p>
{:else if !results.from_xbloom_users.recipes.length}
  <p class="muted">{t("community.none")}</p>
{:else}
  <p class="muted num">{t("community.results", { total: results.total, page: results.page, pages: results.total_pages })}</p>
  <div class="grid three" aria-busy={searching}>
    {#each results.from_xbloom_users.recipes as card (card.id)}
      <a class="recipe community-card" href="#/community/{card.id}">
        <div class="name">{card.name}</div>
        <div class="line">{#if card.official}<span class="badge">{t("community.official")}</span>{/if}{card.author}</div>
        <div class="line num">{t("community.line", { dose: card.dose_g ?? "-", ratio: card.water_ratio ?? "-", pours: card.pour_count ?? "-" })}</div>
        <div class="line muted">{[card.origin.join(", "), card.roast].filter(Boolean).join(" · ")}</div>
      </a>
    {/each}
  </div>
  <div class="actions">
    <button class="quiet" disabled={searching || results.page <= 1} onclick={() => search(page - 1, new URLSearchParams(asked))}>{t("community.prev")}</button>
    <button class="quiet" disabled={searching || results.page >= lastPage} onclick={() => search(page + 1, new URLSearchParams(asked))}>{t("community.next")}</button>
  </div>
{/if}

<form class="card community-form" onsubmit={(event) => { event.preventDefault(); importLink(); }}>
  <h2>{t("community.link")}</h2>
  <p class="muted">{t("community.link_hint")}</p>
  <label><span>{t("community.link")}</span><input id="share-link" type="url" required bind:value={link} placeholder="https://share-h5.xbloom.com/?id=…" /></label>
  <label><span>{t("community.slug")}</span><input id="share-slug" required pattern="[a-z0-9][a-z0-9\-]*" maxlength="64" bind:value={linkSlug} /></label>
  <p class="muted">{t("community.slug_hint")}</p>
  <div class="actions"><button type="submit" disabled={work.busy}>{t("community.import")}</button></div>
</form>
