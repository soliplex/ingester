<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import WorkflowList from '$lib/components/WorkflowList.svelte';
	import FilterBar from '$lib/components/FilterBar.svelte';
	import ErrorMessage from '$lib/components/ErrorMessage.svelte';
	import { RunStatus } from '$lib/types/api';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const currentStatus = $derived<RunStatus | 'all'>((data.filters.status as RunStatus) || 'all');

	function handleStatusChange(status: RunStatus | 'all') {
		const url = new URL($page.url);
		if (status === 'all') {
			url.searchParams.delete('status');
		} else {
			url.searchParams.set('status', status);
		}
		goto(url.toString());
	}

	function handleRefresh() {
		window.location.reload();
	}
</script>

<div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
	<PageHeader title="Workflows" description="Monitor workflow runs and their status" />

	{#if data.error}
		<div class="mt-6">
			<ErrorMessage error={data.error} retry={handleRefresh} />
		</div>
	{:else}
		<div class="mt-6">
			<FilterBar {currentStatus} onStatusChange={handleStatusChange} onRefresh={handleRefresh} />
		</div>

		<div class="mt-6">
			<WorkflowList workflows={data.workflows} />
		</div>
	{/if}
</div>
