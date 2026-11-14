<script lang="ts" generics="T extends string | number">
  // One of a few, as large buttons with a drawing and a name (native buttons,
  // aria-pressed for the chosen one).
  let { options, value = $bindable(), label }:
    { options: { value: T; name: string; icon?: string }[]; value: T; label: string } = $props();
</script>

<div class="choice" role="group" aria-label={label}>
  {#each options as option (option.value)}
    <button type="button" class="choice-item" aria-pressed={value === option.value} onclick={() => { value = option.value; }}>
      {#if option.icon}
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d={option.icon} /></svg>
      {/if}
      <span>{option.name}</span>
    </button>
  {/each}
</div>
