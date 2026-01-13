<script lang="ts">
	import type { LanceDBDatabase } from '$lib/types/api';
	import { truncateText } from '$lib/utils/format';
	import { apiClient } from '$lib/services/apiClient';

	interface Props {
		database: LanceDBDatabase;
	}

	let { database }: Props = $props();

	let isVacuuming = $state(false);
	let vacuumError = $state<string | null>(null);
	let vacuumSuccess = $state(false);

	async function handleVacuum(event: MouseEvent) {
		event.preventDefault();
		event.stopPropagation();

		isVacuuming = true;
		vacuumError = null;
		vacuumSuccess = false;

		try {
			const result = await apiClient.vacuumLanceDB(database.name);
			if (result.status === 'ok') {
				vacuumSuccess = true;
				setTimeout(() => (vacuumSuccess = false), 3000);
			} else {
				vacuumError = result.error ?? 'Vacuum failed';
			}
		} catch (err) {
			vacuumError = err instanceof Error ? err.message : 'Vacuum failed';
		} finally {
			isVacuuming = false;
		}
	}
</script>

<div
	class="rounded-lg border border-gray-200 bg-white p-5 transition hover:border-blue-300 hover:shadow-md"
>
	<a
		href="/lancedb/{database.name}"
		class="block focus:outline-none focus:ring-2 focus:ring-blue-500"
	>
		<div class="flex items-start justify-between">
			<div class="flex-1">
				<div class="flex items-center gap-2">
					<h3 class="text-lg font-semibold text-gray-900">
						{database.name}
					</h3>
					<span
						class="inline-flex items-center rounded-full bg-blue-100 px-2 py-1 text-xs font-medium text-blue-800"
					>
						{database.size_human}
					</span>
				</div>
				<p class="mt-2 text-sm text-gray-600">
					{truncateText(database.path, 60)}
				</p>
			</div>
			<div class="ml-4 text-right">
				<span class="text-2xl" aria-hidden="true">🗄️</span>
			</div>
		</div>
	</a>

	<div class="mt-4 flex items-center justify-between border-t border-gray-100 pt-4">
		<div class="flex-1">
			{#if vacuumError}
				<span class="text-sm text-red-600">{vacuumError}</span>
			{:else if vacuumSuccess}
				<span class="text-sm text-green-600">Vacuum completed</span>
			{/if}
		</div>
		<button
			type="button"
			onclick={handleVacuum}
			disabled={isVacuuming}
			class="inline-flex items-center rounded-md bg-gray-100 px-3 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
		>
			{#if isVacuuming}
				<svg class="mr-1.5 h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
					<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"
					></circle>
					<path
						class="opacity-75"
						fill="currentColor"
						d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
					></path>
				</svg>
				Vacuuming...
			{:else}
				Vacuum
			{/if}
		</button>
	</div>
</div>
