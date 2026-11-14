<script lang="ts">
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import type { LibraryEntry } from "../lib/recipes";
  import RecipeCard from "../components/RecipeCard.svelte";

  let library = $state<LibraryEntry[] | null>(null);
  let failed = $state("");
  api<LibraryEntry[]>("GET", "/api/library").then((list) => { library = list; }).catch((error) => { failed = error.message; });
</script>

<div class="row">
  <h1>{t("recipes.title")}</h1>
  <div class="actions">
    <a class="pill" href="#/community">{t("community.title")}</a>
    <a class="pill" href="#/recipes/new">{t("action.new_recipe")}</a>
  </div>
</div>
{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !library}
  <p class="muted">{t("loading")}</p>
{:else if !library.length}
  <p class="muted">{t("recipes.empty")}</p>
{:else}
  <div class="grid three">
    {#each library as entry (entry.slug)}<RecipeCard {entry} />{/each}
  </div>
{/if}
