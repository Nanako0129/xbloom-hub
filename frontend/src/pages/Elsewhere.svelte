<script lang="ts">
  // A page that has not moved yet: say so, and link to it on the current dashboard.
  import { route } from "../lib/route.svelte";
  import { t } from "../lib/i18n.svelte";

  let { title }: { title: string } = $props();
</script>

<h1>{title}</h1>
<p class="muted">{t("next.elsewhere")}</p>
<p><a href="/{route.hash || '#/'}">{t("next.open_current")}</a></p>
