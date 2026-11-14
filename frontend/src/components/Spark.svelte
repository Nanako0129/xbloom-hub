<script lang="ts">
  // A brew's water curve, tiny: the history list's thumbnail of how it went.
  // Built as SVG elements (no markup string, no data: image).
  import type { Point } from "../lib/records";

  let { points }: { points: Point[] } = $props();
  const W = 64, H = 22;     // the viewBox; the stylesheet sets the size it is drawn at
  const PAD = 2;            // so the line's ends and its round caps are not cut off

  const d = $derived.by(() => {
    if (points.length < 2) return "";
    const tMax = points[points.length - 1][0] || 1;
    const yMax = Math.max(...points.map((p) => p[1])) || 1;
    return points.map(([x, y], i) => `${i ? "L" : "M"}${(PAD + (x / tMax) * (W - 2 * PAD)).toFixed(1)} ${(H - PAD - (y / yMax) * (H - 2 * PAD)).toFixed(1)}`).join(" ");
  });
</script>

<svg class="spark" viewBox="0 0 {W} {H}" aria-hidden="true">
  <path class="base" d="M{PAD} {H - PAD}H{W - PAD}" />
  {#if d}<path class="line" {d} pathLength="1" />{/if}
</svg>
