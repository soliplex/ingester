<script lang="ts">
	import type { LifecycleHistory } from '$lib/types/api';
	import { LifeCycleEvent, RunStatus } from '$lib/types/api';
	import StatusBadge from './StatusBadge.svelte';
	import { formatDuration, formatDateTime } from '$lib/utils/format';

	interface Props {
		history: LifecycleHistory[];
	}

	let { history }: Props = $props();

	let expandedRecords = $state<Set<number>>(new Set());

	function handleToggleRecord(recordId: number) {
		if (expandedRecords.has(recordId)) {
			expandedRecords.delete(recordId);
		} else {
			expandedRecords.add(recordId);
		}
		expandedRecords = new Set(expandedRecords);
	}

	function getEventLabel(event: LifeCycleEvent): string {
		const labels: Record<LifeCycleEvent, string> = {
			[LifeCycleEvent.GROUP_START]: 'Run Group Started',
			[LifeCycleEvent.GROUP_END]: 'Run Group Completed',
			[LifeCycleEvent.ITEM_START]: 'Item Processing Started',
			[LifeCycleEvent.ITEM_END]: 'Item Processing Completed',
			[LifeCycleEvent.ITEM_FAILED]: 'Item Processing Failed',
			[LifeCycleEvent.STEP_START]: 'Step Started',
			[LifeCycleEvent.STEP_END]: 'Step Completed',
			[LifeCycleEvent.STEP_FAILED]: 'Step Failed'
		};
		return labels[event] || event;
	}

	function getEventIcon(event: LifeCycleEvent): string {
		switch (event) {
			case LifeCycleEvent.GROUP_START:
			case LifeCycleEvent.ITEM_START:
			case LifeCycleEvent.STEP_START:
				return '▶';
			case LifeCycleEvent.GROUP_END:
			case LifeCycleEvent.ITEM_END:
			case LifeCycleEvent.STEP_END:
				return '✓';
			case LifeCycleEvent.ITEM_FAILED:
			case LifeCycleEvent.STEP_FAILED:
				return '✗';
			default:
				return '•';
		}
	}

	function getEventColor(event: LifeCycleEvent, status: RunStatus): string {
		if (
			event === LifeCycleEvent.ITEM_FAILED ||
			event === LifeCycleEvent.STEP_FAILED ||
			status === RunStatus.FAILED ||
			status === RunStatus.ERROR
		) {
			return 'text-red-600';
		}
		if (status === RunStatus.COMPLETED) {
			return 'text-green-600';
		}
		if (status === RunStatus.RUNNING) {
			return 'text-blue-600';
		}
		return 'text-gray-600';
	}

	function getEventBorderColor(event: LifeCycleEvent, status: RunStatus): string {
		if (
			event === LifeCycleEvent.ITEM_FAILED ||
			event === LifeCycleEvent.STEP_FAILED ||
			status === RunStatus.FAILED ||
			status === RunStatus.ERROR
		) {
			return 'border-red-300';
		}
		if (status === RunStatus.COMPLETED) {
			return 'border-green-300';
		}
		if (status === RunStatus.RUNNING) {
			return 'border-blue-300';
		}
		return 'border-gray-300';
	}

	function calculateDuration(record: LifecycleHistory): number | null {
		if (!record.start_date || !record.completed_date) {
			return null;
		}
		const start = new Date(record.start_date).getTime();
		const end = new Date(record.completed_date).getTime();
		return (end - start) / 1000;
	}

	const sortedHistory = $derived([...history].sort((a, b) => {
		const dateA = new Date(a.start_date).getTime();
		const dateB = new Date(b.start_date).getTime();
		return dateA - dateB;
	}));
</script>

<div class="space-y-2">
	{#each sortedHistory as record (record.id)}
		<div class="rounded-lg border {getEventBorderColor(record.event, record.status)} bg-white">
			<button
				type="button"
				class="w-full px-4 py-3 text-left hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
				onclick={() => handleToggleRecord(record.id)}
			>
				<div class="flex items-center gap-3">
					<span
						class="text-lg {getEventColor(record.event, record.status)}"
						aria-hidden="true"
					>
						{getEventIcon(record.event)}
					</span>
					<div class="flex-1">
						<div class="flex items-center gap-2">
							<span class="font-medium text-gray-900">
								{getEventLabel(record.event)}
							</span>
							<StatusBadge status={record.status} compact={true} />
						</div>
						<div class="mt-1 text-xs text-gray-500">
							{formatDateTime(record.start_date)}
							{#if record.completed_date}
								{@const duration = calculateDuration(record)}
								{#if duration !== null}
									• Duration: {formatDuration(duration)}
								{/if}
							{/if}
						</div>
					</div>
					<svg
						class="h-5 w-5 text-gray-400 transition-transform {expandedRecords.has(record.id)
							? 'rotate-180'
							: ''}"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
						aria-hidden="true"
					>
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"
						></path>
					</svg>
				</div>
			</button>

			{#if expandedRecords.has(record.id)}
				<div class="border-t border-gray-200 bg-gray-50 px-4 py-3">
					<dl class="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
						<div>
							<dt class="font-medium text-gray-700">Event Type</dt>
							<dd class="mt-1 text-gray-900">{record.event}</dd>
						</div>
						<div>
							<dt class="font-medium text-gray-700">Status</dt>
							<dd class="mt-1 text-gray-900">{record.status}</dd>
						</div>
						{#if record.handler_name}
							<div>
								<dt class="font-medium text-gray-700">Handler Name</dt>
								<dd class="mt-1 text-gray-900">{record.handler_name}</dd>
							</div>
						{/if}
						<div>
							<dt class="font-medium text-gray-700">Started</dt>
							<dd class="mt-1 text-gray-900">{formatDateTime(record.start_date)}</dd>
						</div>
						<div>
							<dt class="font-medium text-gray-700">Completed</dt>
							<dd class="mt-1 text-gray-900">{formatDateTime(record.completed_date)}</dd>
						</div>
						{#if record.step_id}
							<div>
								<dt class="font-medium text-gray-700">Step ID</dt>
								<dd class="mt-1 text-gray-900">{record.step_id}</dd>
							</div>
						{/if}
						<div>
							<dt class="font-medium text-gray-700">Run Group ID</dt>
							<dd class="mt-1 text-gray-900">{record.run_group_id}</dd>
						</div>
						{#if record.status_date}
							<div>
								<dt class="font-medium text-gray-700">Status Updated</dt>
								<dd class="mt-1 text-gray-900">{formatDateTime(record.status_date)}</dd>
							</div>
						{/if}
						{#if calculateDuration(record) !== null}
							{@const duration = calculateDuration(record)}
							<div>
								<dt class="font-medium text-gray-700">Duration</dt>
								<dd class="mt-1 text-gray-900">{formatDuration(duration)}</dd>
							</div>
						{/if}
					</dl>
					{#if record.status_message}
						<div class="mt-3">
							<dt class="font-medium text-gray-700">Status Message</dt>
							<dd class="mt-1 text-gray-900">{record.status_message}</dd>
						</div>
					{/if}
					{#if Object.keys(record.status_meta).length > 0}
						<div class="mt-3">
							<dt class="font-medium text-gray-700">Metadata</dt>
							<dd class="mt-1">
								<pre
									class="overflow-x-auto rounded bg-gray-100 p-2 text-xs text-gray-900">{JSON.stringify(
										record.status_meta,
										null,
										2
									)}</pre>
							</dd>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	{/each}
</div>
