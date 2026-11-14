<script lang="ts">
  import { DOT_ICONS } from "../lib/dots";

  let { name }: { name: string } = $props();
  const R = 0.4;   // of a 1-unit cell: dots stay apart at 20 px, still read as filled
  const rows = $derived((DOT_ICONS[name] ?? "0").split(" "));
  // "1" is a dot; "a" and "b" are dots of the cup's two steam frames.
  const kind = (bit: string, y: number) => (bit === "a" || bit === "b" ? `steam ${bit}` : name === "pour" ? `fall f${y}` : undefined);
</script>

<svg viewBox="0 0 {rows[0].length} {rows.length}" class="matrix dot-{name}" aria-hidden="true">
  {#each rows as row, y}
    {#each [...row] as bit, x}
      {#if bit !== "0"}<circle cx={x + 0.5} cy={y + 0.5} r={R} class={kind(bit, y)} />{/if}
    {/each}
  {/each}
</svg>
