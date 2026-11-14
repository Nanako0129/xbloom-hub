<script lang="ts">
  import { DOT_ICONS } from "../lib/dots";

  let { name }: { name: string } = $props();
  const R = 0.4;   // of a 1-unit cell: dots stay apart at 20 px, still read as filled
  const rows = $derived((DOT_ICONS[name] ?? "0").split(" "));
  const kind = (y: number) => (name === "cup" && y < 2 ? `steam s${y}` : name === "pour" ? `fall f${y}` : undefined);
</script>

<svg viewBox="0 0 {rows[0].length} {rows.length}" class="matrix dot-{name}" aria-hidden="true">
  {#each rows as row, y}
    {#each [...row] as bit, x}
      {#if bit === "1"}<circle cx={x + 0.5} cy={y + 0.5} r={R} class={kind(y)} />{/if}
    {/each}
  {/each}
</svg>
