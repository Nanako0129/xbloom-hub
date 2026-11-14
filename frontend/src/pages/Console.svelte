<script lang="ts">
  // Read-only until the safety flows move (docs/PLAN.md, S3c, C11): no
  // release or acquire here yet; the current dashboard at / keeps them.
  import { api, hub, hasToken } from "../lib/hub.svelte";
  import { t, setLanguage, language, LANGUAGE_NAMES, locale } from "../lib/i18n.svelte";
  import { stateName, reasonText, cupText, tankText } from "../lib/words";

  let gate = $state<{ at: number; code: number }[]>([]);
  const refresh = () => { api<{ at: number; code: number }[]>("GET", "/api/gate").then((rows) => { gate = rows; }).catch(() => {}); };
  const GATE_POLL_MS = 3000;   // as the current dashboard: the gate log has no stream of its own
  $effect(() => {
    hub.log.length;            // and at once when the machine changes state
    refresh();
  });
  $effect(() => {
    const timer = setInterval(refresh, GATE_POLL_MS);
    return () => clearInterval(timer);
  });

  const timeOfDay = (date: Date) => date.toLocaleTimeString(locale());
  const s = $derived(hub.state);
  const changes = $derived(hub.log.slice().reverse()
    .map(({ at, snap }) => [timeOfDay(at), stateName(snap.state), `activity=${snap.activity}`, snap.error ?? ""].join("  ").trimEnd())
    .join("\n"));
  const commands = $derived(gate.slice().reverse().map((g) => `${timeOfDay(new Date(g.at * 1000))}  ${g.code}`).join("\n"));
</script>

{#if !s}
  <p class="muted">{t("connecting_hub")}</p>
{:else}
  <h1>{t("console.title")}</h1>
  <div class="cols even">
    <div>
      <section class="card">
        <dl class="facts">
          <dt>{t("console.link")}</dt><dd>{stateName(s.state)}</dd>
          {#if s.error}<dt>{t("console.reason")}</dt><dd>{reasonText(s.error)}</dd>{/if}
          <dt>{t("console.firmware")}</dt><dd class="num">{s.fw_version ?? "-"}</dd>
          <dt>{t("console.activity")}</dt><dd class="num">{s.activity ?? "-"}</dd>
          <dt>{t("tank")}</dt><dd>{tankText(s.water_enough)}</dd>
          <dt>{t("cup")}</dt><dd>{cupText(s.cup)}</dd>
          <dt>{t("console.token")}</dt><dd>{hasToken() ? t("console.token_yes") : t("console.token_no")}</dd>
          <dt><label for="language">{t("console.language")}</label></dt>
          <dd>
            <select id="language" value={language.code} onchange={(event) => setLanguage(event.currentTarget.value)}>
              {#each Object.entries(LANGUAGE_NAMES) as [code, name] (code)}<option value={code}>{name}</option>{/each}
            </select>
          </dd>
        </dl>
      </section>
    </div>
    <div>
      <section class="card"><h2>{t("console.changes")}</h2><pre class="log">{changes || t("console.no_changes")}</pre></section>
      <section class="card"><h2>{t("console.commands")}</h2><pre class="log">{commands || t("console.no_commands")}</pre></section>
    </div>
  </div>
{/if}
