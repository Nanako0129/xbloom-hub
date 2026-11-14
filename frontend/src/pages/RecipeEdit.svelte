<script lang="ts">
  // Writing a recipe. The draft lives in this component, so a state change
  // elsewhere on the page never wipes what is being typed; leaving the page
  // drops it. The hub checks the recipe when it is saved.
  import { api, HubError } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";
  import { clock } from "../lib/format";
  import { planLine } from "../lib/records";
  import { previewPlan, type Pour, type Recipe } from "../lib/recipes";
  import Chart from "../components/Chart.svelte";

  // slug: the recipe being edited, or null for a new one; from: a recipe to start a new one from.
  let { slug, from }: { slug: string | null; from: string | null } = $props();

  const MAX_POURS = 9;            // the machine's limit (xbloom-py spec)
  const NAME_MAX = 80;            // the hub's limit (recipes.py NAME_MAX)
  const SLUG = /^[a-z0-9][a-z0-9-]{0,63}$/;

  const blankPour = (): Pour => ({ volume_ml: 45, temperature_c: 92, pause_s: 30, pattern: 2, flow_rate: 3, agitate_before: 2, agitate_after: 2 });
  interface Draft { name: string; cup_type: number; dose_g: number | null; ratio: number | null; grind: boolean;
    grind_size: number | null; grinder_speed_rpm: number | null; pours: Pour[];
    meta: { bean: string; roaster: string; process: string; roast: string; notes: string; source: string } }
  const blank = (): Draft => ({ name: "", cup_type: 2, dose_g: 15, ratio: 15, grind: true, grind_size: 60, grinder_speed_rpm: 80,
    pours: [blankPour(), { ...blankPour(), volume_ml: 180, pause_s: 0, pattern: 3, flow_rate: 3.5 }],
    meta: { bean: "", roaster: "", process: "", roast: "", notes: "", source: "" } });

  let draft = $state<Draft | null>(null);
  let id = $state("");
  let errors = $state<Record<string, string> | null>(null);
  let failed = $state("");
  let saving = $state(false);

  $effect(() => {
    const source = slug ?? from;
    if (!source) { draft = blank(); return; }
    let current = true;
    api<Recipe>("GET", `/api/recipes/${encodeURIComponent(source)}`).then((stored) => {
      if (!current) return;
      const base = blank();
      const ratio = Number(String(stored.ratio).split(":").pop());
      draft = { ...base, ...stored, ratio: Number.isFinite(ratio) ? ratio : null,
        pours: stored.pours.map((pour) => ({ ...pour })), meta: { ...base.meta, ...(stored.meta ?? {}) } };
      if (!slug && draft) draft.name = t("edit.copy_of", { name: stored.name }).slice(0, NAME_MAX);
    }).catch((error) => { if (current) failed = error.message; });
    return () => { current = false; };
  });

  const poured = $derived(draft ? draft.pours.reduce((n, pour) => n + (Number(pour.volume_ml) || 0), 0) : 0);
  const target = $derived(draft ? Math.round((Number(draft.dose_g) || 0) * (Number(draft.ratio) || 0) * 10) / 10 : 0);
  const preview = $derived(draft ? previewPlan(draft.pours) : null);

  function move(index: number, by: number) {
    if (!draft) return;
    const to = index + by;
    if (to < 0 || to >= draft.pours.length) return;
    const [pour] = draft.pours.splice(index, 1);
    draft.pours.splice(to, 0, pour);
  }

  async function save() {
    if (!draft || saving) return;
    saving = true; errors = null;
    try {
      const dest = slug ?? id.trim();
      // A new recipe gets an id of its own: well formed and not "new"; the hub
      // refuses one that is taken (?create=1), so nothing is ever replaced.
      if (!slug && (!SLUG.test(dest) || dest === "new")) { errors = { slug: "bad_slug" }; return; }
      if (!Number.isFinite(Number(draft.ratio)) || draft.ratio === null) { errors = { ratio: "ratio_invalid" }; return; }
      const meta = Object.fromEntries(Object.entries(draft.meta).filter(([, value]) => value.trim()));
      const body: Record<string, unknown> = { ...$state.snapshot(draft), ratio: `1:${draft.ratio}` };
      if (Object.keys(meta).length) body.meta = meta; else delete body.meta;
      await api("PUT", `/api/recipes/${encodeURIComponent(dest)}${slug ? "" : "?create=1"}`, body);
      location.hash = `#/recipes/${encodeURIComponent(dest)}`;
    } catch (error) {
      const e = error as HubError;
      errors = e.code === "recipe_exists" ? { slug: "in_use" } : e.fields ?? { [e.code ?? "error"]: e.message };
    } finally {
      saving = false;
    }
  }

  const PATTERNS = [1, 2, 3];
  const CUPS = [1, 2, 3, 4];
</script>

<p><a href={slug ? `#/recipes/${encodeURIComponent(slug)}` : "#/recipes"}>{t("back")}</a></p>
<h1>{slug ? t("edit.title") : t("edit.title_new")}</h1>

{#if failed}
  <p class="notice bad">{failed}</p>
{:else if !draft}
  <p class="muted">{t("loading")}</p>
{:else}
  {#if errors}
    <section class="notice bad">
      <div>{errors.slug === "in_use" ? t("edit.slug_taken") : errors.slug === "bad_slug" ? t("edit.slug_bad") : t("edit.errors")}</div>
      <ul>{#each Object.entries(errors) as [name, code] (name)}<li class="num">{name}: {code}</li>{/each}</ul>
    </section>
  {/if}
  <div class="cols">
    <div>
      <section class="card form">
        <label class="field wide"><span>{t("edit.name")}</span><input type="text" maxlength="80" bind:value={draft.name} /></label>
        {#if !slug}
          <label class="field wide"><span>{t("edit.slug")}</span>
            <input type="text" maxlength="64" placeholder="my-recipe" autocapitalize="none" spellcheck="false" bind:value={id} /></label>
        {/if}
        <label class="field"><span>{t("edit.cup")}</span>
          <select bind:value={draft.cup_type}>{#each CUPS as cup (cup)}<option value={cup}>{t(`cup_type.${cup}`)}</option>{/each}</select></label>
        <label class="field"><span>{t("fact.dose")}</span><input type="number" min="1" max="25" step="1" bind:value={draft.dose_g} /></label>
        <label class="field"><span>{t("edit.ratio")}</span><input type="number" min="5" max="25" step="0.5" bind:value={draft.ratio} /></label>
        <label class="field"><span>{t("edit.grind")}</span><input type="checkbox" bind:checked={draft.grind} /></label>
        <label class="field"><span>{t("edit.grind_size")}</span><input type="number" min="1" max="80" step="1" bind:value={draft.grind_size} /></label>
        <label class="field"><span>{t("edit.rpm")}</span><input type="number" min="60" max="120" step="10" bind:value={draft.grinder_speed_rpm} /></label>
      </section>

      <h2>{t("edit.pours")}</h2>
      <ol class="pours-edit">
        {#each draft.pours as pour, i (pour)}
          <li class="pour-edit">
            <div class="n">{i + 1}</div>
            <label class="field"><span>{t("edit.volume")}</span><input type="number" min="1" max="240" step="1" bind:value={pour.volume_ml} /></label>
            <label class="field"><span>{t("edit.temperature")}</span><input type="number" min="20" max="98" step="1" bind:value={pour.temperature_c} /></label>
            <label class="field"><span>{t("edit.flow")}</span><input type="number" min="3" max="3.5" step="0.1" bind:value={pour.flow_rate} /></label>
            <label class="field"><span>{t("edit.pause")}</span><input type="number" min="0" max="59" step="1" bind:value={pour.pause_s} /></label>
            <label class="field"><span>{t("edit.pattern")}</span>
              <select bind:value={pour.pattern}>{#each PATTERNS as p (p)}<option value={p}>{t(`pattern.${p}`)}</option>{/each}</select></label>
            <label class="field"><span>{t("edit.agitate_before")}</span>
              <input type="checkbox" checked={pour.agitate_before === 1} onchange={(e) => { pour.agitate_before = e.currentTarget.checked ? 1 : 2; }} /></label>
            <label class="field"><span>{t("edit.agitate_after")}</span>
              <input type="checkbox" checked={pour.agitate_after === 1} onchange={(e) => { pour.agitate_after = e.currentTarget.checked ? 1 : 2; }} /></label>
            <div class="pour-tools">
              <button type="button" class="quiet small" disabled={i === 0} aria-label={t("edit.earlier")} onclick={() => move(i, -1)}>↑</button>
              <button type="button" class="quiet small" disabled={i === draft.pours.length - 1} aria-label={t("edit.later")} onclick={() => move(i, 1)}>↓</button>
              {#if draft.pours.length > 1}
                <button type="button" class="quiet small" onclick={() => draft?.pours.splice(i, 1)}>{t("action.remove")}</button>
              {/if}
            </div>
          </li>
        {/each}
      </ol>
      <p class="muted num" id="edit-total" class:bad={Math.abs(poured - target) > 0.5}>{t("edit.total", { poured, target })}</p>
      <div class="actions">
        <button type="button" class="quiet" disabled={draft.pours.length >= MAX_POURS}
          onclick={() => { const last = draft?.pours[draft.pours.length - 1]; draft?.pours.push(last ? { ...$state.snapshot(last), pause_s: 0 } : blankPour()); }}>
          {t("action.add_pour")}</button>
      </div>
    </div>

    <div>
      {#if preview}
        <section class="card">
          <h2>{t("edit.preview", { time: clock(preview.pour_end_s), total: preview.total_ml })}</h2>
          <Chart lines={[{ key: "plan", kind: "plan", label: t("chart.plan"), points: planLine(preview) }]} live />
        </section>
      {/if}
      <section class="card form">
        <label class="field wide"><span>{t("meta.bean")}</span><input type="text" maxlength="200" bind:value={draft.meta.bean} /></label>
        <label class="field wide"><span>{t("meta.roaster")}</span><input type="text" maxlength="200" bind:value={draft.meta.roaster} /></label>
        <label class="field"><span>{t("meta.process")}</span><input type="text" maxlength="200" bind:value={draft.meta.process} /></label>
        <label class="field"><span>{t("meta.roast")}</span><input type="text" maxlength="200" bind:value={draft.meta.roast} /></label>
        <label class="field wide"><span>{t("meta.notes")}</span><textarea rows="4" bind:value={draft.meta.notes}></textarea></label>
      </section>
      <div class="actions">
        <button type="button" disabled={saving} onclick={save}>{t("action.save")}</button>
      </div>
      <p class="muted">{t("edit.hint")}</p>
    </div>
  </div>
{/if}
