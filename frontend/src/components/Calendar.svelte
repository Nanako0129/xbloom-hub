<script lang="ts">
  // Brews per day over the last weeks, as a matrix of dots: a column a week, a
  // row a weekday, a dot a day, lit the more brews it holds. Hovering or
  // focusing a day reads it out as text; clicking one asks for that day.
  import { t, locale } from "../lib/i18n.svelte";

  let { counts, selected = null, onpick }:
    { counts: Record<string, number>; selected?: string | null; onpick: (day: string) => void } = $props();

  const WEEKS = 26;           // half a year: long enough to see a habit, short enough for dots to stay big
  const LEVELS = 4;           // 1, 2, 3, 4 or more brews in a day: four steps the eye tells apart
  const R = 0.38;             // dot radius in cell units: apart from its neighbours, as on the machine's display
  const MONTH_ROW = 1.4;      // room above the dots for the month names, cell units
  const MONTH_SIZE = 0.72;    // their font size, cell units (a cell is 14 px on a phone, 30 px on a large display)
  const MONDAY_FIRST = true;  // weeks run Monday to Sunday

  const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

  const grid = $derived.by(() => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const row = (today.getDay() + (MONDAY_FIRST ? 6 : 0)) % 7;
    const cells: { key: string; date: Date; col: number; row: number }[] = [];
    const months: { col: number; name: string }[] = [];
    let month = -1;
    for (let col = 0; col < WEEKS; col++) {
      for (let r = 0; r < 7; r++) {
        // new Date(y, m, d + n) steps whole calendar days, whatever the clock changes do
        const date = new Date(today.getFullYear(), today.getMonth(), today.getDate() - row - (WEEKS - 1 - col) * 7 + r);
        if (date > today) continue;
        cells.push({ key: ymd(date), date, col, row: r });
        if (r === 0 && date.getMonth() !== month) { month = date.getMonth(); months.push({ col, name: date.toLocaleDateString(locale(), { month: "short" }) }); }
      }
    }
    return { cells, months };
  });

  let hot = $state<string | null>(null);
  const long = (d: Date) => d.toLocaleDateString(locale(), { year: "numeric", month: "short", day: "numeric", weekday: "short" });
  const say = (cell: { key: string; date: Date }) => (counts[cell.key]
    ? t("cal.some", { date: long(cell.date), n: counts[cell.key] }) : t("cal.none", { date: long(cell.date) }));
  const readout = $derived.by(() => {
    const cell = grid.cells.find((c) => c.key === hot);
    return cell ? say(cell) : t("cal.hint");
  });
  const level = (n: number) => Math.min(n, LEVELS);
</script>

<svg viewBox="0 0 {WEEKS} {7 + MONTH_ROW}" class="cal" role="group" aria-label={t("cal.title")}>
  {#each grid.months as m (m.col)}
    <text x={m.col} y={MONTH_SIZE} font-size={MONTH_SIZE} class="month">{m.name}</text>
  {/each}
  {#each grid.cells as cell (cell.key)}
    {@const n = counts[cell.key] ?? 0}
    {#if n}
      <circle cx={cell.col + 0.5} cy={MONTH_ROW + cell.row + 0.5} r={R} class="d l{level(n)}" class:sel={selected === cell.key}
        style:--col={cell.col} role="button" tabindex="0" aria-pressed={selected === cell.key} aria-label={say(cell)}
        onpointerenter={() => { hot = cell.key; }} onpointerleave={() => { hot = null; }}
        onfocus={() => { hot = cell.key; }} onblur={() => { hot = null; }}
        onclick={() => onpick(cell.key)}
        onkeydown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onpick(cell.key); } }} />
    {:else}
      <circle cx={cell.col + 0.5} cy={MONTH_ROW + cell.row + 0.5} r={R} class="d l0" style:--col={cell.col} role="img" aria-label={say(cell)}
        onpointerenter={() => { hot = cell.key; }} onpointerleave={() => { hot = null; }} />
    {/if}
  {/each}
</svg>
<p class="cal-read num muted" aria-live="polite">{readout}</p>
