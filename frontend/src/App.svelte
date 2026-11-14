<script lang="ts">
  import { hub, listen } from "./lib/hub.svelte";
  import { route } from "./lib/route.svelte";
  import { t } from "./lib/i18n.svelte";
  import { stateLabel, stateDetail } from "./lib/words";
  import Icon from "./components/Icon.svelte";
  import MachineCard from "./components/MachineCard.svelte";
  import Console from "./pages/Console.svelte";
  import Elsewhere from "./pages/Elsewhere.svelte";
  import History from "./pages/History.svelte";
  import Brew from "./pages/Brew.svelte";
  import Compare from "./pages/Compare.svelte";
  import Recipes from "./pages/Recipes.svelte";
  import Recipe from "./pages/Recipe.svelte";
  import RecipeEdit from "./pages/RecipeEdit.svelte";
  import Home from "./pages/Home.svelte";

  listen();

  const TABS = [
    { name: "home", href: "#/", icon: "drop", label: "tab.home" },
    { name: "recipes", href: "#/recipes", icon: "list", label: "tab.recipes" },
    { name: "history", href: "#/history", icon: "curve", label: "tab.history" },
    { name: "console", href: "#/console", icon: "prompt", label: "tab.console" },
  ];
  const ENTER_MS = 700;       // how long a page's arrival may animate

  const current = $derived(route.current.name === "compare" ? "history" : route.current.name);
  const s = $derived(hub.state);
  const statusText = $derived(!hub.reachable && s ? t("status.no_hub") : s ? stateLabel(s) : t("status.connecting"));
  const statusDetail = $derived(s && hub.reachable ? stateDetail(s) : "");

  let view: HTMLElement;
  // A page arriving: the stylesheet animates #view[data-enter] for a moment.
  $effect(() => {
    route.hash;
    view.dataset.enter = "";
    const timer = setTimeout(() => { delete view.dataset.enter; }, ENTER_MS);
    return () => clearTimeout(timer);
  });
  // The ground's lights follow the machine.
  $effect(() => { document.body.dataset.state = s?.state ?? ""; });
</script>

<aside id="side">
  <div class="brand">xbloom hub</div>
  <nav id="tabs" aria-label={t("tab.label")}>
    {#each TABS as tab (tab.name)}
      <a href={tab.href} data-tab={tab.name} aria-current={current === tab.name ? "page" : undefined}>
        <span class="icon"><Icon name={tab.icon} /></span><span>{t(tab.label)}</span>
      </a>
    {/each}
  </nav>
  <section id="machine" class="card" aria-live="polite"><MachineCard /></section>
</aside>

<div id="main">
  <header id="status" aria-live="polite">
    <span id="status-lamp" class="lamp" data-state={hub.reachable ? s?.state : "offline"}></span>
    <span id="status-text">{statusText}</span>
    <span id="status-detail">{statusDetail}</span>
  </header>
  <main id="view" tabindex="-1" bind:this={view}>
    {#if route.current.name === "home"}
      <Home />
    {:else if route.current.name === "console"}
      <Console />
    {:else if route.current.name === "history" && route.current.id}
      {#key route.current.id}<Brew id={route.current.id} />{/key}
    {:else if route.current.name === "history"}
      <History />
    {:else if route.current.name === "recipes"}
      {#key route.hash}
        {#if route.current.id === "new"}
          <RecipeEdit slug={null} from={route.current.sub ?? null} />
        {:else if route.current.id && route.current.sub === "edit"}
          <RecipeEdit slug={route.current.id} from={null} />
        {:else if route.current.id}
          <Recipe slug={route.current.id} />
        {:else}
          <Recipes />
        {/if}
      {/key}
    {:else if route.current.name === "compare"}
      {#key route.hash}<Compare ids={route.current.ids ?? []} />{/key}
    {:else}
      <Elsewhere title={t(`tab.${current}`)} />
    {/if}
  </main>
</div>
