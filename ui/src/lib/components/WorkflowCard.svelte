<script lang="ts">
	import type { WorkflowRun, WorkflowRunWithDetails, DocumentInfo } from '$lib/types/api';
	import StatusBadge from './StatusBadge.svelte';
	import {
		formatDuration,
		formatRelativeTime,
		truncateText,
		formatFileSize
	} from '$lib/utils/format';

	interface Props {
		workflow: WorkflowRun | WorkflowRunWithDetails;
	}

	let { workflow }: Props = $props();

	// Helper to check if workflow has details wrapper
	const isWithDetails = (w: WorkflowRun | WorkflowRunWithDetails): w is WorkflowRunWithDetails => {
		return 'workflow_run' in w;
	};

	// Extract the actual workflow run and document info
	const workflowRun = $derived(isWithDetails(workflow) ? workflow.workflow_run : workflow);
	const documentInfo = $derived<DocumentInfo | null>(
		isWithDetails(workflow) ? workflow.document_info : null
	);
	const steps = $derived(isWithDetails(workflow) ? workflow.steps : workflow.steps);

	const progress = $derived(() => {
		if (!steps || steps.length === 0) {
			return null;
		}
		const completed = steps.filter((s) => s.status === 'COMPLETED').length;
		return `${completed}/${steps.length}`;
	});
</script>

<a
	href="/workflows/{workflowRun.id}"
	class="block rounded-lg border border-gray-200 bg-white p-4 transition hover:border-blue-300 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-blue-500"
>
	<div class="flex items-start justify-between">
		<div class="flex-1">
			<div class="flex items-center gap-2">
				<h3 class="text-sm font-bold text-gray-900">
					Workflow #{workflowRun.id}
				</h3>
				<StatusBadge status={workflowRun.status} />
			</div>

			{#if documentInfo?.uri}
				<h4 class="mt-2 text-sm font-semibold text-gray-700" title={documentInfo.uri}>
					URI: {truncateText(documentInfo.uri, 50)}
				</h4>
			{:else}
				<p class="mt-2 text-sm text-gray-600">
					Document: {truncateText(workflowRun.doc_id, 40)}
				</p>
			{/if}
			{#if documentInfo}
				<div class="mt-1 flex flex-wrap gap-3 text-xs text-gray-500">
					{#if documentInfo.source}
						<span>Source: <span class="font-medium">{documentInfo.source}</span></span>
					{/if}
					{#if documentInfo.file_size !== null}
						<span>Size: <span class="font-medium">{formatFileSize(documentInfo.file_size)}</span></span>
					{/if}
					{#if documentInfo.mime_type}
						<span>Type: <span class="font-medium">{documentInfo.mime_type}</span></span>
					{/if}
					<span class="font-mono text-xs" title={workflowRun.doc_id}>
						Hash: {truncateText(workflowRun.doc_id, 20)}
					</span>
				</div>
			{:else}
				<p class="mt-1 text-xs text-gray-500">
					Source: {workflowRun.run_params.source}
				</p>
			{/if}
			<div class="mt-2 flex flex-wrap gap-4 text-xs text-gray-500">
				<span>
					Definition: <span class="font-medium">{workflowRun.workflow_definition_id}</span>
				</span>
				<span>
					Parameter Set: <span class="font-medium">{workflowRun.run_params.param_id}</span>
				</span>
				<span>Batch: <span class="font-medium">#{workflowRun.batch_id}</span></span>
				{#if progress()}
					<span>Progress: <span class="font-medium">{progress()}</span></span>
				{/if}
			</div>
		</div>
		<div class="ml-4 text-right text-xs text-gray-500">
			<div>{formatRelativeTime(workflowRun.created_date)}</div>
			{#if workflowRun.duration !== null}
				<div class="mt-1 font-medium">{formatDuration(workflowRun.duration)}</div>
			{/if}
		</div>
	</div>
	{#if workflowRun.status_message}
		<div class="mt-2 rounded bg-gray-50 p-2 text-xs text-gray-700">
			{workflowRun.status_message}
		</div>
	{/if}
</a>
