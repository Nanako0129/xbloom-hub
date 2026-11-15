<script lang="ts">
  // The scale on its own (docs/PLAN.md, S3e): the machine's reading, live,
  // and the three things the hub may ask of it. Which one is allowed when is
  // the hub's rule; the buttons only follow it.
  import { hub } from "../lib/hub.svelte";
  import { act, work, scaleEnter, tare, scaleQuit } from "../lib/actions.svelte";
  import { t } from "../lib/i18n.svelte";
  import { stateLabel } from "../lib/words";
  import Dots from "../components/Dots.svelte";

  const s = $derived(hub.state);
  const name = $derived(s?.state ?? "");
  // The number the machine reports, one decimal; no zero of the page's own.
  const grams = $derived(s?.weight_g == null ? "-" : s.weight_g.toFixed(1));
  const note = $derived(
    !s ? t("connecting_hub")
    : name === "scale" ? t("scale.on")
    : name === "idle" ? t("scale.idle")
    : name === "asleep" ? t("scale.asleep")
    : name === "armed" || name === "brewing" ? t("scale.loaded")
    : t("scale.unavailable", { state: stateLabel(s) }));
</script>

<h1>{t("scale.title")}</h1>
<div class="weigh">
  <section class="card weigh-now" id="scale-reading">
    <Dots text={grams} label="{t('scale.weight')} {grams}" />
    <div class="label">{t("scale.weight")}</div>
  </section>
  <div class="weigh-side">
    <p class="muted" id="scale-note" aria-live="polite">{note}</p>
    <div class="actions">
      <button class:quiet={name !== "idle"} disabled={work.busy || name !== "idle"}
        onclick={() => act(scaleEnter, t("working.scale_enter"))}>{t("scale.enter")}</button>
      <button class:quiet={name !== "scale"} disabled={work.busy || (name !== "idle" && name !== "scale")}
        onclick={() => act(tare, t("working.zero"))}>{t("scale.zero")}</button>
      <button class="quiet" disabled={work.busy || name !== "scale"}
        onclick={() => act(scaleQuit, t("working.scale_leave"))}>{t("scale.leave")}</button>
    </div>
  </div>
</div>
