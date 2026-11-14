<script lang="ts">
  // A value as the official app draws it: a block with its label at the top
  // left, the number large at the right, and the block filled as far as the
  // value goes. Drag anywhere on it (a native range input lies over it, so
  // keys and screen readers work), or type into the number.
  let { label, unit = "", min, max, step = 1, value = $bindable(), aside = "" }:
    { label: string; unit?: string; min: number; max: number; step?: number; value: number | null; aside?: string } = $props();

  const fill = $derived(value == null || Number.isNaN(Number(value)) ? 0
    : Math.min(1, Math.max(0, (Number(value) - min) / (max - min))));
</script>

<div class="slider" style:--fill={fill}>
  <span class="slider-label">{label}</span>
  {#if unit}<span class="slider-unit">{unit}</span>{/if}
  <input class="slider-range" type="range" {min} {max} {step} bind:value aria-label={label} />
  <span class="slider-value">
    {#if aside}<small>{aside}</small>{/if}
    <input class="slider-number num" type="number" {min} {max} {step} bind:value aria-label={label} />
  </span>
</div>
