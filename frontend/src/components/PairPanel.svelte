<script lang="ts">
  // Add a device: a QR code of this page's address with a pairing code
  // (docs/PLAN.md, S3d). The code lives two minutes, works once, and is void
  // when this panel closes or a new one is made.
  import { onDestroy } from "svelte";
  import qrcode from "qrcode-generator";
  import { api, hasToken } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";

  const POLL_MS = 2000;       // how soon the panel sees the phone has paired
  const QUIET = 4;            // the blank margin, in modules, that scanners need
  // A phone cannot reach this computer's own loopback: a page opened there
  // puts the hub's network address in the code instead (the first allowed
  // host that is not loopback), and warns only if the hub has none.
  const isLoopback = (host: string) => /^(localhost|127\.\d+\.\d+\.\d+|\[::1\])$/.test(host);
  // ponytail: an IPv6 network host would need brackets here; the hub has none.
  let host = $state(location.hostname);

  let open = $state(false);
  let code = $state("");
  let left = $state(0);
  let deadline = 0;           // counted from a fixed moment: a background tab's timers slow down
  let paired = $state(false);
  let failure = $state("");

  // Drawn from the module matrix only: one path, no markup from the library.
  const qr = $derived.by(() => {
    if (!code) return null;
    const q = qrcode(0, "M");
    q.addData(`${location.protocol}//${host}${location.port ? `:${location.port}` : ""}${location.pathname}#p=${code}`);
    q.make();
    const n = q.getModuleCount();
    let d = "";
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (q.isDark(y, x)) d += `M${x} ${y}h1v1h-1z`;
    return { n, d };
  });

  async function begin() {
    failure = "";
    paired = false;
    try {
      const answer = await api<{ code: string; expires_in_s: number; hosts: string[] }>("POST", "/api/pair/start");
      host = isLoopback(location.hostname) && answer.hosts?.length ? answer.hosts[0] : location.hostname;
      code = answer.code;
      deadline = Date.now() + answer.expires_in_s * 1000;
      left = answer.expires_in_s;
      open = true;
    } catch (err) {
      failure = (err as Error).message;
    }
  }

  function close() {
    open = false;
    code = "";
    api("POST", "/api/pair/cancel").catch(() => {});
  }

  // Leaving the page voids a code still shown, as Close does.
  onDestroy(() => { if (code) api("POST", "/api/pair/cancel").catch(() => {}); });

  $effect(() => {
    if (!open || !code) return;
    const tick = setInterval(() => {
      left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      if (!left) code = "";
    }, 1000);
    const poll = setInterval(async () => {
      const answer = await api<{ state: string }>("POST", "/api/pair/status").catch(() => null);
      if (answer?.state === "claimed") { paired = true; code = ""; }
    }, POLL_MS);
    return () => { clearInterval(tick); clearInterval(poll); };
  });
</script>

<section class="card" id="pair">
  <h2>{t("pair.title")}</h2>
  <p class="muted">{hasToken() ? t("pair.hint") : t("pair.needs_token")}</p>
  {#if failure}<p class="notice bad">{failure}</p>{/if}
  {#if open}
    {#if isLoopback(host)}<p class="notice bad" id="pair-loopback">{t("pair.loopback", { host })}</p>{/if}
    {#if paired}
      <p class="notice" id="pair-paired">{t("pair.paired")}</p>
    {:else if qr}
      <svg class="qr" viewBox="{-QUIET} {-QUIET} {qr.n + 2 * QUIET} {qr.n + 2 * QUIET}" role="img" aria-label={t("pair.qr_label")}
        shape-rendering="crispEdges">
        <rect class="qr-ground" x={-QUIET} y={-QUIET} width={qr.n + 2 * QUIET} height={qr.n + 2 * QUIET} />
        <path class="qr-ink" d={qr.d} />
      </svg>
      <p class="num muted" id="pair-left">{t("pair.left", { s: left })}</p>
      <p class="muted" id="pair-host">{t("pair.opens", { host })}</p>
    {:else}
      <p class="muted">{t("pair.expired")}</p>
    {/if}
  {/if}
  <div class="actions">
    {#if !hasToken()}
      <!-- Nothing to press: pairing another device needs this one paired. -->
    {:else if !open}
      <button id="pair-start" onclick={begin}>{t("pair.start")}</button>
    {:else}
      {#if !code}<button id="pair-start" onclick={begin}>{t("pair.again")}</button>{/if}
      <button class="quiet" id="pair-close" onclick={close}>{t("pair.close")}</button>
    {/if}
  </div>
</section>
