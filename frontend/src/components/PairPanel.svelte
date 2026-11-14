<script lang="ts">
  // Add a device: a QR code of this page's address with a pairing code
  // (docs/PLAN.md, S3d). The code lives two minutes, works once, and is void
  // when this panel closes or a new one is made.
  import qrcode from "qrcode-generator";
  import { api } from "../lib/hub.svelte";
  import { t } from "../lib/i18n.svelte";

  const POLL_MS = 2000;       // how soon the panel sees the phone has paired
  const QUIET = 4;            // the blank margin, in modules, that scanners need
  // A phone cannot reach this computer's own loopback; say so above the code.
  const loopback = /^(localhost|127\.\d+\.\d+\.\d+|\[::1\])$/.test(location.hostname);

  let open = $state(false);
  let code = $state("");
  let left = $state(0);
  let paired = $state(false);
  let failure = $state("");

  // Drawn from the module matrix only: one path, no markup from the library.
  const qr = $derived.by(() => {
    if (!code) return null;
    const q = qrcode(0, "M");
    q.addData(`${location.origin}${location.pathname}#p=${code}`);
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
      const answer = await api<{ code: string; expires_in_s: number }>("POST", "/api/pair/start");
      code = answer.code;
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

  $effect(() => {
    if (!open || !code) return;
    const tick = setInterval(() => { left = Math.max(0, left - 1); if (!left) code = ""; }, 1000);
    const poll = setInterval(async () => {
      const answer = await api<{ state: string }>("POST", "/api/pair/status").catch(() => null);
      if (answer?.state === "claimed") { paired = true; code = ""; }
    }, POLL_MS);
    return () => { clearInterval(tick); clearInterval(poll); };
  });
</script>

<section class="card" id="pair">
  <h2>{t("pair.title")}</h2>
  <p class="muted">{t("pair.hint")}</p>
  {#if failure}<p class="notice bad">{failure}</p>{/if}
  {#if open}
    {#if loopback}<p class="notice bad" id="pair-loopback">{t("pair.loopback", { host: location.hostname })}</p>{/if}
    {#if paired}
      <p class="notice" id="pair-paired">{t("pair.paired")}</p>
    {:else if qr}
      <svg class="qr" viewBox="{-QUIET} {-QUIET} {qr.n + 2 * QUIET} {qr.n + 2 * QUIET}" role="img" aria-label={t("pair.qr_label")}
        shape-rendering="crispEdges">
        <rect class="qr-ground" x={-QUIET} y={-QUIET} width={qr.n + 2 * QUIET} height={qr.n + 2 * QUIET} />
        <path class="qr-ink" d={qr.d} />
      </svg>
      <p class="num muted" id="pair-left">{t("pair.left", { s: left })}</p>
    {:else}
      <p class="muted">{t("pair.expired")}</p>
    {/if}
  {/if}
  <div class="actions">
    {#if !open}
      <button id="pair-start" onclick={begin}>{t("pair.start")}</button>
    {:else}
      {#if !code}<button id="pair-start" onclick={begin}>{t("pair.again")}</button>{/if}
      <button class="quiet" id="pair-close" onclick={close}>{t("pair.close")}</button>
    {/if}
  </div>
</section>
