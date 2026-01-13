// API Client Service for Soliplex Ingester

import { API_BASE_URL, API_TIMEOUT } from '$lib/config/api';
import { handleApiError } from '$lib/utils/errors';
import type {
	DocumentBatch,
	BatchStatus,
	WorkflowRun,
	WorkflowRunWithDetails,
	RunStep,
	RunGroup,
	RunGroupStats,
	WorkflowDefinition,
	WorkflowDefinitionSummary,
	WorkflowParams,
	ParamSetSummary,
	UploadParamSetResponse,
	DeleteParamSetResponse,
	DocumentURI,
	RunStatus,
	PaginatedResponse,
	PaginationParams,
	LifecycleHistory,
	LanceDBListResponse,
	LanceDBInfoResponse,
	LanceDBDocumentsResponse,
	LanceDBVacuumResponse
} from '$lib/types/api';

class ApiClient {
	private baseUrl: string;
	private timeout: number;

	constructor(baseUrl: string = API_BASE_URL, timeout: number = API_TIMEOUT) {
		this.baseUrl = baseUrl;
		this.timeout = timeout;
	}

	private async fetchWithTimeout(url: string, options?: RequestInit): Promise<Response> {
		const controller = new AbortController();
		const timeoutId = setTimeout(() => controller.abort(), this.timeout);

		try {
			const response = await fetch(url, {
				...options,
				signal: controller.signal
			});
			clearTimeout(timeoutId);

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				throw new Error(errorData.error || response.statusText);
			}

			return response;
		} catch (error) {
			clearTimeout(timeoutId);
			handleApiError(error, url);
		}
	}

	private async get<T>(
		endpoint: string,
		params?: Record<string, string | number | boolean>
	): Promise<T> {
		let url = `${this.baseUrl}${endpoint}`;

		if (params) {
			const searchParams = new URLSearchParams();
			Object.entries(params).forEach(([key, value]) => {
				searchParams.append(key, String(value));
			});
			url += `?${searchParams.toString()}`;
		}

		const response = await this.fetchWithTimeout(url);
		return response.json();
	}

	private async post<T>(endpoint: string, data?: Record<string, unknown> | FormData): Promise<T> {
		const isFormData = data instanceof FormData;

		const response = await this.fetchWithTimeout(`${this.baseUrl}${endpoint}`, {
			method: 'POST',
			headers: isFormData ? {} : { 'Content-Type': 'application/json' },
			body: isFormData ? data : JSON.stringify(data)
		});

		return response.json();
	}

	// Batch Endpoints

	async getBatches(): Promise<DocumentBatch[]> {
		return this.get<DocumentBatch[]>('/batch/');
	}

	async getBatchStatus(batchId: number): Promise<BatchStatus> {
		return this.get<BatchStatus>('/batch/status', { batch_id: batchId });
	}

	// Document Endpoints

	async getDocuments(batchId?: number, source?: string): Promise<DocumentURI[]> {
		const params: Record<string, number | string> = {};
		if (batchId) params.batch_id = batchId;
		if (source) params.source = source;
		return this.get<DocumentURI[]>('/document/', params);
	}

	// Workflow Endpoints

	// Overload: without pagination returns array
	async getWorkflowRuns(batchId?: number): Promise<WorkflowRun[]>;
	async getWorkflowRuns(
		batchId: number | undefined,
		paginationParams: undefined,
		includeDocInfo: true
	): Promise<WorkflowRunWithDetails[]>;
	// Overload: with pagination returns paginated response
	async getWorkflowRuns(
		batchId: number | undefined,
		paginationParams: PaginationParams
	): Promise<PaginatedResponse<WorkflowRun>>;
	async getWorkflowRuns(
		batchId: number | undefined,
		paginationParams: PaginationParams,
		includeDocInfo: true
	): Promise<PaginatedResponse<WorkflowRunWithDetails>>;
	// Implementation
	async getWorkflowRuns(
		batchId?: number,
		paginationParams?: PaginationParams,
		includeDocInfo?: boolean
	): Promise<
		| WorkflowRun[]
		| WorkflowRunWithDetails[]
		| PaginatedResponse<WorkflowRun>
		| PaginatedResponse<WorkflowRunWithDetails>
	> {
		const params: Record<string, number | boolean> = {};
		if (batchId) params.batch_id = batchId;
		if (paginationParams) {
			params.page = paginationParams.page;
			params.rows_per_page = paginationParams.rows_per_page;
		}
		if (includeDocInfo) params.include_doc_info = true;
		return this.get<
			| WorkflowRun[]
			| WorkflowRunWithDetails[]
			| PaginatedResponse<WorkflowRun>
			| PaginatedResponse<WorkflowRunWithDetails>
		>('/workflow/', params);
	}

	async getWorkflowRunsByStatus(
		status: RunStatus,
		batchId?: number,
		paginationParams?: PaginationParams,
		includeDocInfo?: boolean
	): Promise<
		| WorkflowRun[]
		| WorkflowRunWithDetails[]
		| PaginatedResponse<WorkflowRun>
		| PaginatedResponse<WorkflowRunWithDetails>
	> {
		const params: Record<string, string | number | boolean> = { status };
		if (batchId) params.batch_id = batchId;
		if (paginationParams) {
			params.page = paginationParams.page;
			params.rows_per_page = paginationParams.rows_per_page;
		}
		if (includeDocInfo) params.include_doc_info = true;
		return this.get<
			| WorkflowRun[]
			| WorkflowRunWithDetails[]
			| PaginatedResponse<WorkflowRun>
			| PaginatedResponse<WorkflowRunWithDetails>
		>('/workflow/by-status', params);
	}

	async getWorkflowRunDetails(workflowId: number): Promise<WorkflowRun> {
		return this.get<WorkflowRun>(`/workflow/runs/${workflowId}`);
	}

	async getWorkflowSteps(status: RunStatus): Promise<RunStep[]> {
		return this.get<RunStep[]>('/workflow/steps', { status });
	}

	async getWorkflowLifecycleHistory(workflowId: number): Promise<LifecycleHistory[]> {
		return this.get<LifecycleHistory[]>(`/workflow/runs/${workflowId}/lifecycle`);
	}

	// Workflow Definition Endpoints

	async getWorkflowDefinitions(): Promise<WorkflowDefinitionSummary[]> {
		return this.get<WorkflowDefinitionSummary[]>('/workflow/definitions');
	}

	async getWorkflowDefinition(workflowId: string): Promise<WorkflowDefinition> {
		return this.get<WorkflowDefinition>(`/workflow/definitions/${workflowId}`);
	}

	// Parameter Set Endpoints

	async getParamSets(): Promise<ParamSetSummary[]> {
		return this.get<ParamSetSummary[]>('/workflow/param-sets');
	}

	async getParamSet(setId: string): Promise<WorkflowParams> {
		return this.get<WorkflowParams>(`/workflow/param-sets/${setId}`);
	}

	async getParamSetsByTarget(target: string): Promise<WorkflowParams[]> {
		return this.get<WorkflowParams[]>(`/workflow/param-sets/target/${target}`);
	}

	async uploadParamSet(yamlContent: string): Promise<UploadParamSetResponse> {
		const formData = new URLSearchParams();
		formData.append('yaml_content', yamlContent);

		const response = await this.fetchWithTimeout(`${this.baseUrl}/workflow/param-sets`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
			body: formData.toString()
		});

		return response.json();
	}

	async deleteParamSet(setId: string): Promise<DeleteParamSetResponse> {
		const response = await this.fetchWithTimeout(`${this.baseUrl}/workflow/param-sets/${setId}`, {
			method: 'DELETE'
		});

		return response.json();
	}

	// Run Group Endpoints

	async getRunGroups(batchId?: number): Promise<RunGroup[]> {
		const params = batchId ? { batch_id: batchId } : undefined;
		return this.get<RunGroup[]>('/workflow/run-groups', params);
	}

	async getRunGroup(runGroupId: number): Promise<RunGroup> {
		return this.get<RunGroup>(`/workflow/run-groups/${runGroupId}`);
	}

	async getRunGroupStats(runGroupId: number): Promise<RunGroupStats> {
		return this.get<RunGroupStats>(`/workflow/run-groups/${runGroupId}/stats`);
	}

	// Batch Action Endpoints

	async startWorkflows(
		batchId: number,
		workflowDefinitionId?: string,
		priority?: number,
		paramId?: string
	): Promise<import('$lib/types/api').StartWorkflowsResponse> {
		const formData = new URLSearchParams();
		formData.append('batch_id', String(batchId));
		if (workflowDefinitionId) formData.append('workflow_definition_id', workflowDefinitionId);
		if (priority !== undefined) formData.append('priority', String(priority));
		if (paramId) formData.append('param_id', paramId);

		const response = await this.fetchWithTimeout(`${this.baseUrl}/batch/start-workflows`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
			body: formData.toString()
		});

		return response.json();
	}

	// LanceDB Endpoints

	async getLanceDBList(): Promise<LanceDBListResponse> {
		return this.get<LanceDBListResponse>('/lancedb/list');
	}

	async getLanceDBInfo(dbName: string): Promise<LanceDBInfoResponse> {
		return this.get<LanceDBInfoResponse>('/lancedb/info', { db: dbName });
	}

	async getLanceDBDocuments(
		dbName: string,
		limit?: number,
		offset?: number,
		filter?: string
	): Promise<LanceDBDocumentsResponse> {
		const params: Record<string, string | number> = { db: dbName };
		if (limit !== undefined) params.limit = limit;
		if (offset !== undefined) params.offset = offset;
		if (filter) params.filter = filter;
		return this.get<LanceDBDocumentsResponse>('/lancedb/documents', params);
	}

	async vacuumLanceDB(dbName: string): Promise<LanceDBVacuumResponse> {
		return this.get<LanceDBVacuumResponse>('/lancedb/vacuum', { db: dbName });
	}
}

// Export singleton instance
export const apiClient = new ApiClient();

// Export class for testing
export { ApiClient };
