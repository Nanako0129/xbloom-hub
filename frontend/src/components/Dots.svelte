<script lang="ts">
  // The machine's display: glyphs of 7 rows of lit and unlit dots. Each dot is
  // its own element, kept across changes, so a new value turns over dot by
  // dot (the stylesheet's transition on .dots circle).
  import { GLYPHS } from "../lib/dots";

  let { text, label }: { text: string; label: string } = $props();
  const R = 0.36;      // of a 1-unit cell, as on the current dashboard

  const layout = $derived.by(() => {
    let x = 0;
    const dots: { cx: number; cy: number; on: boolean }[] = [];
    for (const char of text) {
      const rows = (GLYPHS[char] ?? GLYPHS["-"]).split(" ");
      rows.forEach((row, y) => [...row].forEach((bit, dx) => dots.push({ cx: x + dx + 0.5, cy: y + 0.5, on: bit === "1" })));
      x += rows[0].length + 1;
    }
    return { dots, width: Math.max(1, x - 1) };
  });
</script>

<svg viewBox="0 0 {layout.width} 7" class="dots" role="img" aria-label={label}>
  {#each layout.dots as dot, i (i)}<circle cx={dot.cx} cy={dot.cy} r={R} class={dot.on ? "on" : "off"} />{/each}
</svg>
