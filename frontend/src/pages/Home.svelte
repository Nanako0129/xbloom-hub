<script lang="ts">
  // "Now": what the machine is doing, and the controls for it, as on the
  // current dashboard. Every rule for those controls is in lib/actions.
  import { api, hub } from "../lib/hub.svelte";
  import { act, work, ownLoad, load, start, stop, cancel, acquire, ask } from "../lib/actions.svelte";
  import { t } from "../lib/i18n.svelte";
  import { live } from "../lib/live.svelte";
  import { clock, whole, when, endingText } from "../lib/format";
  import { reasonText, tankText, unheard } from "../lib/words";
  import { eventMarks, planLine, recordName, startMarks, startsByIndex, type BrewRecord, type BrewSummary } from "../lib/records";
  import type { LibraryEntry, RecipeInfo } from "../lib/recipes";
  import Chart, { type Line } from "../components/Chart.svelte";
  import Icon from "../components/Icon.svelte";
  import Readout from "../components/Readout.svelte";
  import RecipeCard from "../components/RecipeCard.svelte";
  import RecipeFacts from "../components/RecipeFacts.svelte";
  import PoursList from "../components/PoursList.svelte";

  const PICK = 6;              // recipes offered on the home screen; the rest are a click away
  const RECENT = 5;            // brews listed under "recent"

  const s = $derived(hub.state);
  const name = $derived(s?.state ?? "");

  // ---- what the page fetches -------------------------------------------------
  let library = $state<LibraryEntry[] | null>(null);
  let recent = $state<BrewSummary[]>([]);
  let lastBrew = $state<BrewRecord | null>(null);

  api<LibraryEntry[]>("GET", "/api/library").then((list) => { library = list; }).catch(() => {});

  // Ask again each time a brew ends, so the one that just ended shows.
  let askedFor = "";
  $effect(() => {
    const mark = `${live.id ?? ""}|${name === "brewing" ? "" : "rest"}`;
    if (name === "brewing" || mark === askedFor) return;
    askedFor = mark;
    api<BrewSummary[]>("GET", "/api/brews").then((list) => {
      recent = list;
      return list.length ? api<BrewRecord>("GET", `/api/brews/${encodeURIComponent(list[0].id)}`) : null;
    }).then((record) => { lastBrew = record; }).catch(() => {});
  });

  // The recipe on the machine, as the hub holds it now; fetched again when the machine arms.
  const slug = $derived(s?.recipe?.slug ?? s?.brew?.slug ?? null);
  // Keyed by the load: one fetch per load, and an answer for an older load is dropped.
  const loadKey = $derived(slug ? `${slug}|${s?.load_id ?? s?.brew?.id ?? ""}` : "");
  let plan = $state<{ key: string; info: RecipeInfo } | null>(null);
  $effect(() => {
    const key = loadKey;
    if (!slug || (name !== "armed" && name !== "brewing") || plan?.key === key) return;
    const want = slug;
    api<RecipeInfo>("GET", `/api/recipes/${encodeURIComponent(want)}/plan`)
      .then((answer) => { if (loadKey === key) plan = { key, info: answer }; }).catch(() => {});
  });
  // Only the plan fetched for this very load: right after a load again of the
  // same recipe, the previous load's plan is not shown in the confirmation.
  const info = $derived(plan && plan.key === loadKey ? plan.info : null);

  // ---- the brew that is running ---------------------------------------------
  const pourText = $derived.by(() => {
    const brew = s?.brew;
    // 34: the grinder is running (measured 2026-11-15), before any pour.
    if (!brew || brew.pour === null || brew.pour === undefined) return t(s?.activity === 34 ? "brew.grinding" : "brew.preparing");
    const total = info?.recipe.pours.length;
    return total ? t("brew.pour_of", { n: brew.pour + 1, total }) : t("brew.pour", { n: brew.pour + 1 });
  });
  const liveMarks = $derived(startMarks(Object.entries(live.startAt).map(([i, time]) => [Number(i), time] as [number, number])));
  const liveLines = $derived.by((): Line[] => {
    const out: Line[] = [];
    // The plan counts from the first pour; grinding comes before it. Drawn
    // once the first pour is seen, from that moment.
    const first = live.startAt[0];
    if (info && first !== undefined)
      out.push({ key: "plan", kind: "plan", label: t("chart.plan"),
                 points: planLine(info.plan).map(([x, y]) => [x + first, y] as [number, number]) });
    out.push({ key: "water", kind: "water", label: t("chart.water"), points: live.water, gaps: live.gaps });
    out.push({ key: "coffee", kind: "coffee", label: t("chart.coffee"), points: live.weight, gaps: live.gaps });
    return out;
  });

  // ---- the brew that has just ended ------------------------------------------
  const finishedRecord = $derived(lastBrew && (!live.id || lastBrew.id === live.id) ? lastBrew : null);
  const lastValue = (points: [number, number][]) => (points.length ? points[points.length - 1][1] : null);
  const recordLines = (record: BrewRecord): Line[] => [
    { key: "water", kind: "water", label: t("chart.water"), points: record.samples.water, gaps: record.gaps },
    { key: "coffee", kind: "coffee", label: t("chart.coffee"), points: record.samples.weight, gaps: record.gaps },
  ];
  const totalWater = $derived(recent.reduce((sum, b) => sum + (b.water_ml ?? 0), 0));
</script>

{#if !s}
  <p class="muted">{t("connecting_hub")}</p>

{:else if name === "offline"}
  <h1>{t("home.offline")}</h1>
  <p class="muted">{reasonText(s.error) || t("home.offline_retry")}</p>
  <p><a href="#/console">{t("home.to_console")}</a></p>

{:else if name === "released"}
  <h1>{t("home.released")}</h1>
  <p class="muted">{t("home.released_hint")}</p>
  <div class="actions"><button disabled={work.busy} onclick={() => act(acquire)}>{t("action.acquire")}</button></div>

{:else if name === "busy" && unheard(s)}
  <h1>{t("home.unheard")}</h1>
  <p class="muted">{t("home.unheard_hint")}</p>

{:else if name === "busy"}
  <h1>{t("home.busy")}</h1>
  <p class="muted">{t("home.busy_hint")}</p>

{:else if name === "brewing" && s.brew}
  <div class="row">
    <div class="done"><Icon name="pour" /><h1>{s.brew.recipe_name || t("brew.at_machine")}</h1></div>
    <span class="muted num">{pourText}</span>
  </div>
  <div class="readouts" id="live-readouts">
    <Readout value={clock(s.brew.elapsed_s)} label={t("readout.time")} />
    <Readout value={whole(s.water_ml)} label={t("readout.water")} series="water" />
    <Readout value={whole(s.weight_g)} label={t("readout.coffee")} series="coffee" />
  </div>
  <div class="cols">
    <div>
      <Chart lines={liveLines} marks={liveMarks} live />
      <!-- Not held behind another request, and the same element for the whole brew. -->
      <div class="actions">
        <button id="stop" class="danger" disabled={work.stopping}
          onclick={async () => { if (!work.stopping && await ask({ title: t("stop.title"), body: t("stop.body"), yes: t("action.stop"), danger: true })) stop(); }}>
          {t("action.stop")}</button>
      </div>
    </div>
    {#if info}<div><PoursList plan={info.plan} pours={info.recipe.pours} current={s.brew.pour ?? null} water={s.water_ml} starts={live.startAt} /></div>{/if}
  </div>

{:else if name === "armed" && s.recipe}
  <h1>{s.recipe.name || s.recipe.slug}</h1>
  <div class="cols">
    <div>
      {#if info}
        <div class="readouts">
          <Readout value={info.recipe.grind ? String(info.recipe.grind_size) : "--"} label={t("readout.grind")}
            spoken={info.recipe.grind ? String(info.recipe.grind_size) : t("fact.no_grind")} />
          <Readout value={info.as_sent.ratio.split(":").pop() ?? "-"} label={t("readout.ratio")} spoken={info.as_sent.ratio} />
          <Readout value=">" label={t("readout.waiting")} spoken={t("readout.waiting")} />
        </div>
        <section class="card"><RecipeFacts {info} /></section>
      {/if}
      {#if !ownLoad()}<p class="notice">{t("armed.not_mine")}</p>{/if}
      <div class="actions">
        {#if ownLoad()}
          <button disabled={work.busy || !info}
            onclick={async () => { if (info && await ask({ title: t("start.title", { name: info.recipe.name }), body: t("start.body"), yes: t("action.start"), info })) act(start, t("working.start")); }}>
            {t("action.start")}</button>
        {:else}
          <!-- The slug is read before the cancel: once it is answered the machine is at rest and the state has no recipe. -->
          <button disabled={work.busy} onclick={() => { const again = s.recipe!.slug; act(async () => { await cancel(); await load(again); }, t("working.load")); }}>{t("action.reload")}</button>
        {/if}
        <button class="quiet" disabled={work.busy} onclick={() => act(cancel, t("working.cancel"))}>{t("action.cancel_load")}</button>
      </div>
    </div>
    {#if info}
      <div class="wide-only">
        <section class="card"><h2>{t("card.plan")}</h2>
          <Chart lines={[{ key: "plan", kind: "plan", label: t("chart.plan"), points: planLine(info.plan) }]} /></section>
        <PoursList plan={info.plan} pours={info.recipe.pours} />
      </div>
    {/if}
  </div>

{:else if name === "finished"}
  <div class="done"><Icon name="cup" /><h1>{t("home.finished")}</h1></div>
  {#if finishedRecord}
    <div class="muted">{recordName(finishedRecord)}</div>
    <div class="readouts">
      <Readout value={clock(finishedRecord.duration_s ?? 0)} label={t("readout.time")} />
      <Readout value={whole(lastValue(finishedRecord.samples.water))} label={t("readout.water")} series="water" />
      <Readout value={whole(lastValue(finishedRecord.samples.weight))} label={t("readout.coffee")} series="coffee" />
    </div>
    <section class="card"><Chart lines={recordLines(finishedRecord)} marks={eventMarks(finishedRecord)} /></section>
    <p><a href="#/history/{encodeURIComponent(finishedRecord.id)}">{t("home.open")}</a></p>
  {/if}

{:else}
  <h1>{name === "asleep" ? t("home.asleep") : name === "scale" ? t("home.on_scale") : t("home.ready")}</h1>
  {#if name === "scale"}<p><a href="#/scale">{t("home.to_scale")}</a></p>{/if}
  <div class="home">
    <div class="home-now">
      <section class="card scale">
        <Readout value={whole(s.weight_g)} label={t("home.scale")} />
        <dl class="facts"><dt>{t("tank")}</dt><dd>{tankText(s.water_enough)}</dd></dl>
      </section>
      <div class="tiles">
        <a class="tile" href="#/recipes"><span class="icon"><Icon name="list" /></span><span>{t("tab.recipes")}</span></a>
        <a class="tile" href="#/history"><span class="icon"><Icon name="curve" /></span><span>{t("tab.history")}</span></a>
      </div>
    </div>
    <div class="home-pick">
      <h2>{t("home.pick")}</h2>
      {#if !library}
        <p class="muted">{t("loading")}</p>
      {:else if !library.length}
        <p class="muted">{t("recipes.empty")}</p>
      {:else}
        <div class="grid">{#each library.slice(0, PICK) as entry (entry.slug)}<RecipeCard {entry} />{/each}</div>
        {#if library.length > PICK}<p><a href="#/recipes">{t("home.all_recipes")}</a></p>{/if}
      {/if}
    </div>
    <div class="home-past">
      <section class="card wide-only">
        {#if lastBrew}
          <div class="row"><h2>{t("home.last_brew")}</h2><a href="#/history/{encodeURIComponent(lastBrew.id)}">{t("home.open")}</a></div>
          <div>{recordName(lastBrew)}</div>
          <dl class="facts">
            <dt>{t("record.time")}</dt><dd class="num">{when(lastBrew.started_at)}</dd>
            <dt>{t("record.water")}</dt><dd class="num">{lastValue(lastBrew.samples.water) === null ? "-" : `${whole(lastValue(lastBrew.samples.water))} ml`}</dd>
            <dt>{t("record.coffee")}</dt><dd class="num">{lastValue(lastBrew.samples.weight) === null ? "-" : `${whole(lastValue(lastBrew.samples.weight))} g`}</dd>
            <dt>{t("record.result")}</dt><dd>{endingText(lastBrew.reason)}</dd>
          </dl>
          <Chart lines={recordLines(lastBrew)} />
        {:else}
          <h2>{t("home.last_brew")}</h2><p class="muted">{t("history.empty")}</p>
        {/if}
      </section>
      {#if recent.length}
        <section class="card wide-only">
          <div class="row"><h2>{t("home.recent")}</h2><a href="#/history">{t("home.all_brews")}</a></div>
          <dl class="facts">
            <dt>{t("home.count")}</dt><dd class="num">{recent.length}</dd>
            <dt>{t("home.total_water")}</dt><dd class="num">{Math.round(totalWater)} ml</dd>
          </dl>
          <ul class="list compact">
            {#each recent.slice(0, RECENT) as b (b.id)}
              <li><a href="#/history/{encodeURIComponent(b.id)}">
                <span class="title">{b.recipe_name || t("brew.at_machine")}</span>
                <span class="sub num">{[when(b.started_at), `${b.water_ml == null ? "-" : Math.round(b.water_ml)} ml`, endingText(b.reason)].join("   ")}</span>
              </a></li>
            {/each}
          </ul>
        </section>
      {/if}
    </div>
  </div>
{/if}
