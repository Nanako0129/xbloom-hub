<script lang="ts">
  // On or off, as a native checkbox drawn as a switch.
  let { label, checked = $bindable() }: { label: string; checked: boolean } = $props();
</script>

<label class="switch"><input type="checkbox" role="switch" bind:checked /><span>{label}</span></label>
