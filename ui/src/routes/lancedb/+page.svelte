<script lang="ts">
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LanceDBList from '$lib/components/LanceDBList.svelte';
	import ErrorMessage from '$lib/components/ErrorMessage.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
	<PageHeader title="Vector Databases" description="Manage LanceDB vector database instances" />

	{#if data.error}
		<div class="mt-6">
			<ErrorMessage error={data.error} />
		</div>
	{:else if data.response}
		<div class="mt-4 text-sm text-gray-500">
			Directory: <code class="rounded bg-gray-100 px-2 py-1">{data.response.lancedb_dir}</code>
		</div>
		<div class="mt-6">
			<LanceDBList databases={data.response.databases} />
		</div>
	{/if}
</div>
