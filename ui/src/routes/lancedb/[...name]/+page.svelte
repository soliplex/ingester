<script lang="ts">
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorMessage from '$lib/components/ErrorMessage.svelte';
	import LanceDBDocumentTable from '$lib/components/LanceDBDocumentTable.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const hasVectorIndex = $derived(data.info?.vector_index.exists ?? false);
</script>

<div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
	<PageHeader title="Database: {data.dbName}">
		{#snippet actions()}
			<a href="/lancedb" class="text-sm font-medium text-gray-600 hover:text-gray-900">
				← Back to databases
			</a>
		{/snippet}
	</PageHeader>

	{#if data.error}
		<div class="mt-6">
			<ErrorMessage error={data.error} />
		</div>
	{:else if data.info}
		<div class="mt-6">
			<div class="overflow-hidden rounded-lg bg-white shadow">
				<div class="px-6 py-5">
					<div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
						<div>
							<dt class="text-sm font-medium text-gray-500">Documents</dt>
							<dd class="mt-1 text-lg font-semibold text-gray-900">
								{data.info.documents.count.toLocaleString()}
							</dd>
						</div>
						<div>
							<dt class="text-sm font-medium text-gray-500">Chunks</dt>
							<dd class="mt-1 text-lg font-semibold text-gray-900">
								{data.info.chunks.count.toLocaleString()}
							</dd>
						</div>
						<div>
							<dt class="text-sm font-medium text-gray-500">Storage</dt>
							<dd class="mt-1 text-lg font-semibold text-gray-900">
								{data.info.documents.size_human} + {data.info.chunks.size_human}
							</dd>
						</div>
						<div>
							<dt class="text-sm font-medium text-gray-500">Vector Index</dt>
							<dd class="mt-1">
								{#if hasVectorIndex}
									<span
										class="inline-flex items-center rounded-full bg-green-100 px-3 py-1 text-sm font-medium text-green-800"
									>
										✓ Indexed
									</span>
								{:else}
									<span
										class="inline-flex items-center rounded-full bg-gray-100 px-3 py-1 text-sm font-medium text-gray-800"
									>
										No index
									</span>
								{/if}
							</dd>
						</div>
					</div>

					<div class="mt-6 border-t border-gray-200 pt-6">
						<h3 class="text-sm font-medium text-gray-700">Embeddings Configuration</h3>
						<div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
							<div>
								<dt class="text-sm text-gray-500">Provider</dt>
								<dd class="mt-1 text-sm font-medium text-gray-900">
									{data.info.embeddings.provider ?? 'Not configured'}
								</dd>
							</div>
							<div>
								<dt class="text-sm text-gray-500">Model</dt>
								<dd class="mt-1 text-sm font-medium text-gray-900">
									{data.info.embeddings.model ?? 'Not configured'}
								</dd>
							</div>
							<div>
								<dt class="text-sm text-gray-500">Dimensions</dt>
								<dd class="mt-1 text-sm font-medium text-gray-900">
									{data.info.embeddings.vector_dim ?? '-'}
								</dd>
							</div>
						</div>
					</div>

					{#if hasVectorIndex}
						<div class="mt-6 border-t border-gray-200 pt-6">
							<h3 class="text-sm font-medium text-gray-700">Vector Index Status</h3>
							<div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
								<div>
									<dt class="text-sm text-gray-500">Indexed Rows</dt>
									<dd class="mt-1 text-sm font-medium text-green-600">
										{data.info.vector_index.indexed_rows.toLocaleString()}
									</dd>
								</div>
								<div>
									<dt class="text-sm text-gray-500">Unindexed Rows</dt>
									<dd class="mt-1 text-sm font-medium text-orange-600">
										{data.info.vector_index.unindexed_rows.toLocaleString()}
									</dd>
								</div>
							</div>
						</div>
					{/if}
				</div>

				<div class="border-t border-gray-200 bg-gray-50 px-6 py-4">
					<h3 class="text-sm font-medium text-gray-700">Version Information</h3>
					<div class="mt-3 flex flex-wrap gap-4 text-sm">
						<div>
							<span class="text-gray-500">LanceDB:</span>
							<span class="ml-1 font-medium text-gray-900">{data.info.versions.lancedb}</span>
						</div>
						<div>
							<span class="text-gray-500">Haiku RAG:</span>
							<span class="ml-1 font-medium text-gray-900">{data.info.versions.haiku_rag}</span>
						</div>
						<div>
							<span class="text-gray-500">Stored Version:</span>
							<span class="ml-1 font-medium text-gray-900">{data.info.versions.stored_version}</span
							>
						</div>
					</div>
				</div>
			</div>
		</div>

		{#if data.documents}
			<div class="mt-8">
				<div class="mb-4 flex items-center justify-between">
					<h2 class="text-lg font-semibold text-gray-900">
						Documents ({data.documents.document_count})
					</h2>
				</div>
				<LanceDBDocumentTable documents={data.documents.documents} />
			</div>
		{/if}
	{/if}
</div>
