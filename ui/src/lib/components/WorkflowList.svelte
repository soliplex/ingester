<script lang="ts">
	import type { WorkflowRun, WorkflowRunWithDetails } from '$lib/types/api';
	import WorkflowCard from './WorkflowCard.svelte';
	import EmptyState from './EmptyState.svelte';

	type WorkflowItem = WorkflowRun | WorkflowRunWithDetails;

	interface Props {
		workflows: WorkflowItem[];
		compact?: boolean;
	}

	let { workflows, compact = false }: Props = $props();

	// Helper to get the id for keying - handles both types
	function getWorkflowId(workflow: WorkflowItem): number {
		return 'workflow_run' in workflow ? workflow.workflow_run.id : workflow.id;
	}
</script>

{#if workflows.length === 0}
	<EmptyState title="No workflows found" description="There are no workflow runs to display." />
{:else}
	<div class="space-y-3">
		{#each workflows as workflow (getWorkflowId(workflow))}
			<WorkflowCard {workflow} />
		{/each}
	</div>
{/if}
