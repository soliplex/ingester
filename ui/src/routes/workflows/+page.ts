import { apiClient } from '$lib/services/apiClient';
import { RunStatus } from '$lib/types/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
	try {
		const statusParam = url.searchParams.get('status');
		const batchIdParam = url.searchParams.get('batch_id');

		const batchId = batchIdParam ? parseInt(batchIdParam) : undefined;

		let workflows;
		if (statusParam && Object.values(RunStatus).includes(statusParam as RunStatus)) {
			workflows = await apiClient.getWorkflowRunsByStatus(statusParam as RunStatus, batchId);
		} else {
			workflows = await apiClient.getWorkflowRuns(batchId);
		}

		return {
			workflows,
			filters: {
				status: statusParam as RunStatus | null,
				batchId
			}
		};
	} catch (error) {
		console.error('Failed to load workflows:', error);
		return {
			workflows: [],
			filters: {
				status: null,
				batchId: undefined
			},
			error
		};
	}
};
