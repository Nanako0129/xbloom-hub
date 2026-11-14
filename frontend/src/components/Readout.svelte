<script lang="ts">
  import Dots from "./Dots.svelte";

  let { value, label, series = undefined, spoken = undefined }:
    { value: string; label: string; series?: "water" | "coffee"; spoken?: string } = $props();
</script>

<div class="readout" data-series={series}>
  <Dots text={value} label="{label} {spoken ?? value}" />
  <div class="label">{label}</div>
</div>
