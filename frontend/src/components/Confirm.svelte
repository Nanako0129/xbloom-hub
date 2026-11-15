<script lang="ts">
  // The one confirmation dialog, mounted once beside the view and never
  // rebuilt by it. The ids are the current dashboard's, which the browser
  // tests drive on both pages.
  import { asking, answered } from "../lib/actions.svelte";
  import { t } from "../lib/i18n.svelte";
  import RecipeFacts from "./RecipeFacts.svelte";

  let dialog: HTMLDialogElement;
  let no: HTMLButtonElement;
  const q = $derived(asking.question);

  $effect(() => {
    if (q && !dialog.open) { dialog.returnValue = "no"; dialog.showModal(); no.focus(); }
    else if (!q && dialog.open) dialog.close();
  });
</script>

<dialog id="confirm" bind:this={dialog} onclose={() => { if (asking.question) answered(dialog.returnValue === "yes"); }}>
  <form method="dialog">
    <h2 id="confirm-title">{q?.title ?? ""}</h2>
    <div id="confirm-body">
      {#if q?.body}<p>{q.body}</p>{/if}
      {#if q?.info}
        <RecipeFacts info={q.info} />
      {/if}
    </div>
    <div class="actions">
      <button value="no" class="quiet" id="confirm-no" bind:this={no}>{t("back")}</button>
      <button value="yes" id="confirm-yes" class={q?.danger ? "danger" : ""}>{q?.yes ?? ""}</button>
    </div>
  </form>
</dialog>
