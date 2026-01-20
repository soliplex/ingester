<script lang="ts">
	import PageHeader from '$lib/components/PageHeader.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
	<PageHeader title="Workflow Definition: {data.definition.name}">
		{#snippet actions()}
			<a
				href="/definitions/workflows"
				class="text-sm font-medium text-gray-600 hover:text-gray-900"
			>
				← Back to definitions
			</a>
		{/snippet}
	</PageHeader>

	<div class="mt-6">
		<div class="overflow-hidden rounded-lg bg-white shadow">
			<div class="px-6 py-5">
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<div>
						<dt class="text-sm font-medium text-gray-500">Definition ID</dt>
						<dd class="mt-1 font-mono text-sm text-gray-900">{data.definition.id}</dd>
					</div>
					<div>
						<dt class="text-sm font-medium text-gray-500">Name</dt>
						<dd class="mt-1 text-sm text-gray-900">{data.definition.name}</dd>
					</div>
				</div>
			</div>
		</div>

		<div class="mt-8 overflow-hidden rounded-lg bg-white shadow">
			<div class="border-b border-gray-200 bg-gray-50 px-6 py-4">
				<h3 class="text-base font-semibold text-gray-900">Workflow Definition (YAML)</h3>
			</div>
			<div class="px-6 py-4">
				<pre
					class="overflow-x-auto rounded-md bg-gray-900 p-4 text-sm text-gray-100"><code>{data.yamlContent}</code></pre>
			</div>
		</div>
	</div>
</div>
