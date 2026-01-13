<script lang="ts">
	import type { LanceDBDocument } from '$lib/types/api';
	import Timestamp from './Timestamp.svelte';
	import { truncateText } from '$lib/utils/format';
	import EmptyState from './EmptyState.svelte';

	interface Props {
		documents: LanceDBDocument[];
	}

	let { documents }: Props = $props();
</script>

{#if documents.length === 0}
	<EmptyState
		title="No documents found"
		description="This database does not contain any documents yet."
	/>
{:else}
	<div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow">
		<table class="min-w-full divide-y divide-gray-200">
			<thead class="bg-gray-50">
				<tr>
					<th
						scope="col"
						class="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500"
					>
						ID
					</th>
					<th
						scope="col"
						class="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500"
					>
						URI
					</th>
					<th
						scope="col"
						class="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500"
					>
						Title
					</th>
					<th
						scope="col"
						class="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500"
					>
						Chunks
					</th>
					<th
						scope="col"
						class="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500"
					>
						Created
					</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-gray-200 bg-white">
				{#each documents as doc (doc.id)}
					<tr class="hover:bg-gray-50">
						<td class="whitespace-nowrap px-6 py-4 text-sm font-medium text-gray-900">
							{truncateText(doc.id, 12)}
						</td>
						<td class="px-6 py-4 text-sm text-gray-600" title={doc.uri}>
							{truncateText(doc.uri, 40)}
						</td>
						<td class="px-6 py-4 text-sm text-gray-600">
							{doc.title ? truncateText(doc.title, 30) : '-'}
						</td>
						<td class="whitespace-nowrap px-6 py-4 text-right text-sm text-gray-600">
							{doc.chunk_count ?? '-'}
						</td>
						<td class="whitespace-nowrap px-6 py-4 text-sm text-gray-600">
							{#if doc.created_at}
								<Timestamp date={doc.created_at} compact={true} showUtc={false} />
							{:else}
								-
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
