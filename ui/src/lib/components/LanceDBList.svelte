<script lang="ts">
	import type { LanceDBDatabase } from '$lib/types/api';
	import LanceDBCard from './LanceDBCard.svelte';
	import EmptyState from './EmptyState.svelte';

	interface Props {
		databases: LanceDBDatabase[];
	}

	let { databases }: Props = $props();
</script>

{#if databases.length === 0}
	<EmptyState
		title="No vector databases found"
		description="There are no LanceDB databases in the configured directory."
	/>
{:else}
	<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
		{#each databases as database (database.name)}
			<LanceDBCard {database} />
		{/each}
	</div>
{/if}
