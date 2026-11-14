<script lang="ts">
  import { t } from "../lib/i18n.svelte";
  import { clock } from "../lib/format";
  import type { RecipeInfo } from "../lib/recipes";

  let { info }: { info: RecipeInfo } = $props();
</script>

<dl class="facts">
  <dt>{t("fact.dose")}</dt><dd class="num">{info.as_sent.dose_g} g</dd>
  <dt>{t("fact.water")}</dt><dd class="num">{info.as_sent.total_ml} ml</dd>
  <dt>{t("fact.pours")}</dt><dd class="num">{t("fact.pours_value", { count: info.recipe.pours.length, time: clock(info.plan.pour_end_s) })}</dd>
  <dt>{t("fact.grind")}</dt>
  <dd class="num">{info.recipe.grind ? t("fact.grind_value", { size: info.recipe.grind_size ?? "", rpm: info.recipe.grinder_speed_rpm ?? "" }) : t("fact.no_grind")}</dd>
</dl>
