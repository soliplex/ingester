import { apiClient } from '$lib/services/apiClient';
import { RunStatus } from '$lib/types/api';
import type { PageLoad } from './$types';
import type {
	WorkflowRun,
	WorkflowRunWithDetails,
	PaginatedResponse
} from '$lib/types/api';

export const load: PageLoad = async () => {
	try {
		const [batches, workflowsResponse] = await Promise.all([
			apiClient.getBatches(),
			apiClient.getWorkflowRuns(undefined, undefined, true) // include doc info
		]);

		// Extract workflows array from response (handles both array and paginated response)
		// When includeDocInfo=true, we get WorkflowRunWithDetails
		type WorkflowItem = WorkflowRun | WorkflowRunWithDetails;
		const workflowItems: WorkflowItem[] = Array.isArray(workflowsResponse)
			? workflowsResponse
			: (workflowsResponse as PaginatedResponse<WorkflowItem>).items;

		// Helper to get the underlying WorkflowRun for filtering
		const getWorkflowRun = (item: WorkflowItem): WorkflowRun =>
			'workflow_run' in item ? item.workflow_run : item;

		const now = new Date();
		const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

		const recentBatches = batches.filter((b) => new Date(b.start_date) >= weekAgo);

		const workflowsByStatus = {
			pending: workflowItems.filter((w) => getWorkflowRun(w).status === RunStatus.PENDING).length,
			running: workflowItems.filter((w) => getWorkflowRun(w).status === RunStatus.RUNNING).length,
			completed: workflowItems.filter((w) => getWorkflowRun(w).status === RunStatus.COMPLETED)
				.length,
			error: workflowItems.filter((w) => getWorkflowRun(w).status === RunStatus.ERROR).length,
			failed: workflowItems.filter((w) => getWorkflowRun(w).status === RunStatus.FAILED).length
		};

		const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
		const completedToday = workflowItems.filter((w) => {
			const run = getWorkflowRun(w);
			return (
				run.status === RunStatus.COMPLETED && new Date(run.completed_date || '') >= todayStart
			);
		}).length;

		const totalCompleted = workflowsByStatus.completed;
		const totalWorkflows = workflowItems.length;
		const successRate =
			totalWorkflows > 0 ? Math.round((totalCompleted / totalWorkflows) * 100) : 0;

		return {
			metrics: {
				totalBatches: batches.length,
				recentBatches: recentBatches.length,
				activeWorkflows: workflowsByStatus.running + workflowsByStatus.pending,
				pendingWorkflows: workflowsByStatus.pending,
				runningWorkflows: workflowsByStatus.running,
				failedWorkflows: workflowsByStatus.failed + workflowsByStatus.error,
				completedToday,
				successRate
			},
			recentActivity: workflowItems.slice(0, 10)
		};
	} catch (error) {
		console.error('Failed to load dashboard data:', error);
		return {
			metrics: {
				totalBatches: 0,
				recentBatches: 0,
				activeWorkflows: 0,
				pendingWorkflows: 0,
				runningWorkflows: 0,
				failedWorkflows: 0,
				completedToday: 0,
				successRate: 0
			},
			recentActivity: [],
			error
		};
	}
};
