<script lang="ts">
	import PageHeader from '$lib/components/PageHeader.svelte';
	import DefinitionCard from '$lib/components/DefinitionCard.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorMessage from '$lib/components/ErrorMessage.svelte';
	import UploadParamSetForm from '$lib/components/UploadParamSetForm.svelte';
	import type { PageData } from './$types';
	import { invalidateAll } from '$app/navigation';

	let { data }: { data: PageData } = $props();

	let showUploadForm = $state(false);

	async function handleUploadSuccess() {
		showUploadForm = false;
		await invalidateAll();
	}

	// Separate built-in and user params
	const builtInParams = $derived(data.paramSets.filter((p) => !p.source || p.source === 'app'));
	const userParams = $derived(data.paramSets.filter((p) => p.source === 'user'));
</script>

<div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
	<div class="flex items-center justify-between">
		<PageHeader title="Parameter Sets" description="Browse and manage parameter configurations" />
		<button
			onclick={() => (showUploadForm = !showUploadForm)}
			class="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
			aria-expanded={showUploadForm}
		>
			{showUploadForm ? 'Hide Upload Form' : 'Upload New Parameter Set'}
		</button>
	</div>

	{#if showUploadForm}
		<div class="mt-6">
			<UploadParamSetForm onSuccess={handleUploadSuccess} />
		</div>
	{/if}

	{#if data.error}
		<div class="mt-6">
			<ErrorMessage error={data.error} />
		</div>
	{:else if data.paramSets.length === 0}
		<div class="mt-6">
			<EmptyState
				title="No parameter sets found"
				description="There are no parameter sets configured."
			/>
		</div>
	{:else}
		{#if userParams.length > 0}
			<div class="mt-6">
				<h2 class="mb-4 text-base font-semibold text-gray-900">User-Uploaded Parameters</h2>
				<div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
					{#each userParams as paramSet (paramSet.id)}
						<DefinitionCard definition={paramSet} type="param" />
					{/each}
				</div>
			</div>
		{/if}

		<div class="mt-6">
			<h2 class="mb-4 text-base font-semibold text-gray-900">Built-in Parameters</h2>
			<div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
				{#each builtInParams as paramSet (paramSet.id)}
					<DefinitionCard definition={paramSet} type="param" />
				{/each}
			</div>
		</div>
	{/if}
</div>
