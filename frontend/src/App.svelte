<script lang="ts">
  import { hub, listen } from "./lib/hub.svelte";
  import { route } from "./lib/route.svelte";
  import { t } from "./lib/i18n.svelte";
  import { stateLabel, stateDetail, unheard } from "./lib/words";
  import Icon from "./components/Icon.svelte";
  import MachineCard from "./components/MachineCard.svelte";
  import Console from "./pages/Console.svelte";
  import History from "./pages/History.svelte";
  import Brew from "./pages/Brew.svelte";
  import Compare from "./pages/Compare.svelte";
  import Recipes from "./pages/Recipes.svelte";
  import Recipe from "./pages/Recipe.svelte";
  import RecipeEdit from "./pages/RecipeEdit.svelte";
  import Home from "./pages/Home.svelte";
  import Scale from "./pages/Scale.svelte";
  import Community from "./pages/Community.svelte";
  import CommunityRecipe from "./pages/CommunityRecipe.svelte";
  import Confirm from "./components/Confirm.svelte";
  import { work } from "./lib/actions.svelte";

  listen();

  const TABS = [
    { name: "home", href: "#/", icon: "drop", label: "tab.home" },
    { name: "recipes", href: "#/recipes", icon: "list", label: "tab.recipes" },
    { name: "history", href: "#/history", icon: "curve", label: "tab.history" },
    { name: "scale", href: "#/scale", icon: "scale", label: "tab.scale" },
    { name: "console", href: "#/console", icon: "prompt", label: "tab.console" },
  ];
  const ENTER_MS = 700;       // how long a page's arrival may animate

  const current = $derived(route.current.name === "compare" ? "history"
    : route.current.name === "community" ? "recipes" : route.current.name);
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
  // A message belongs to the page it was shown on.
  $effect(() => { route.hash; work.flash = null; });
  // While a request is on its way the pill shows; the stylesheet keys on body[data-busy].
  $effect(() => {
    if (work.busy || work.stopping) document.body.dataset.busy = ""; else delete document.body.dataset.busy;
  });
  // The ground's lights follow the machine.
  $effect(() => { document.body.dataset.state = s?.state ?? ""; });
</script>

<aside id="side">
  <div class="brand"><img src="./icon.svg" alt="" width="28" height="28" />xbloom hub</div>
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
    <span id="status-lamp" class="lamp" data-state={!hub.reachable ? "offline" : s && unheard(s) ? "unheard" : s?.state}></span>
    <span id="status-text">{statusText}</span>
    <span id="status-detail">{statusDetail}</span>
  </header>
  <main id="view" tabindex="-1" bind:this={view}>
    {#if work.flash}<p class="notice" class:bad={work.flash.bad} id="flash">{work.flash.text}</p>{/if}
    {#if route.current.name === "home"}
      <Home />
    {:else if route.current.name === "console"}
      <Console />
    {:else if route.current.name === "scale"}
      <Scale />
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
    {:else if route.current.name === "community"}
      {#key route.hash}
        {#if route.current.id}<CommunityRecipe id={route.current.id} />{:else}<Community />{/if}
      {/key}
    {:else if route.current.name === "compare"}
      {#key route.hash}<Compare ids={route.current.ids ?? []} />{/key}
    {/if}
  </main>
</div>

<Confirm />
<div id="working" role="status" aria-live="polite">
  <span class="beat"><i></i><i></i><i></i></span><span>{work.stopping ? t("working.stop") : work.text}</span>
</div>
