<script lang="ts">
	import { apiClient } from '$lib/services/apiClient';
	import LoadingSpinner from './LoadingSpinner.svelte';
	import yaml from 'js-yaml';

	interface Props {
		onSuccess?: () => void;
	}

	let { onSuccess }: Props = $props();

	let yamlContent = $state('');
	let isSubmitting = $state(false);
	let error = $state<string | null>(null);
	let success = $state<string | null>(null);
	let previewId = $state<string | null>(null);
	let previewName = $state<string | null>(null);
	let previewSteps = $state<string[]>([]);
	let validationError = $state<string | null>(null);
	let isLoadingExample = $state(false);

	// Real-time YAML validation
	$effect(() => {
		if (!yamlContent.trim()) {
			previewId = null;
			previewName = null;
			previewSteps = [];
			validationError = null;
			return;
		}

		try {
			const parsed = yaml.load(yamlContent);

			// Basic structure validation
			if (!parsed || typeof parsed !== 'object') {
				validationError = 'YAML must be an object';
				previewId = null;
				return;
			}

			const data = parsed as Record<string, unknown>;

			// Check required fields
			if (!data.id) {
				validationError = 'Missing required field: id';
				previewId = null;
				return;
			}

			if (!data.config || typeof data.config !== 'object') {
				validationError = 'Missing or invalid required field: config';
				previewId = null;
				return;
			}

			// Extract preview data
			previewId = String(data.id);
			previewName = data.name ? String(data.name) : null;
			previewSteps = Object.keys(data.config as Record<string, unknown>);
			validationError = null;
		} catch (e) {
			validationError = e instanceof Error ? e.message : 'Invalid YAML syntax';
			previewId = null;
			previewName = null;
			previewSteps = [];
		}
	});

	async function handleSubmit(event: Event) {
		event.preventDefault();

		if (!yamlContent.trim()) {
			error = 'YAML content is required';
			return;
		}

		if (validationError) {
			error = `Cannot submit: ${validationError}`;
			return;
		}

		isSubmitting = true;
		error = null;
		success = null;

		try {
			const response = await apiClient.uploadParamSet(yamlContent);
			success = `Parameter set '${response.id}' uploaded successfully`;
			yamlContent = '';
			previewId = null;
			previewName = null;
			previewSteps = [];

			if (onSuccess) {
				setTimeout(onSuccess, 1500);
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to upload parameter set';
		} finally {
			isSubmitting = false;
		}
	}

	function handleClear() {
		yamlContent = '';
		previewId = null;
		previewName = null;
		previewSteps = [];
		validationError = null;
		error = null;
		success = null;
	}

	async function loadExample() {
		isLoadingExample = true;
		error = null;

		try {
			const defaultYaml = await apiClient.getParamSetYaml('default');
			yamlContent = defaultYaml;
		} catch (err) {
			error =
				err instanceof Error ? `Failed to load default: ${err.message}` : 'Failed to load default';
		} finally {
			isLoadingExample = false;
		}
	}
</script>

<div class="rounded-lg border border-gray-200 bg-white p-6 shadow">
	<h3 class="text-lg font-semibold text-gray-900">Upload Parameter Set</h3>
	<p class="mt-1 text-sm text-gray-600">Create a new parameter set by pasting YAML content below</p>

	<form onsubmit={handleSubmit} class="mt-4 space-y-4">
		<div>
			<div class="mb-2 flex items-center justify-between">
				<label for="yaml-content" class="block text-sm font-medium text-gray-700">
					YAML Content
				</label>
				<button
					type="button"
					onclick={loadExample}
					disabled={isLoadingExample}
					class="text-sm text-blue-600 hover:text-blue-700 disabled:text-gray-400"
				>
					{isLoadingExample ? 'Loading...' : 'Load Default'}
				</button>
			</div>
			<textarea
				id="yaml-content"
				bind:value={yamlContent}
				disabled={isSubmitting}
				rows="12"
				class="block w-full rounded-md border-gray-300 font-mono text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100 disabled:text-gray-500"
				placeholder="id: my_params&#10;config:&#10;  parse:&#10;    do_ocr: false&#10;  ..."
			></textarea>
			{#if validationError}
				<p class="mt-1 text-sm text-red-600" role="alert">{validationError}</p>
			{:else if previewId}
				<p class="mt-1 text-sm text-green-600">
					Valid YAML - ID: {previewId}
					{#if previewName}(Name: {previewName}){/if}
				</p>
			{/if}
		</div>

		{#if previewId && previewSteps.length > 0}
			<div class="rounded-md bg-blue-50 p-3">
				<h4 class="text-sm font-medium text-blue-900">Preview</h4>
				<dl class="mt-2 space-y-1 text-xs text-blue-800">
					<div>
						<dt class="inline font-medium">ID:</dt>
						<dd class="ml-1 inline">{previewId}</dd>
					</div>
					{#if previewName}
						<div>
							<dt class="inline font-medium">Name:</dt>
							<dd class="ml-1 inline">{previewName}</dd>
						</div>
					{/if}
					<div>
						<dt class="inline font-medium">Steps configured:</dt>
						<dd class="ml-1 inline">{previewSteps.join(', ')}</dd>
					</div>
				</dl>
			</div>
		{/if}

		{#if error}
			<div class="rounded-md bg-red-50 p-3" role="alert">
				<p class="text-sm text-red-800">{error}</p>
			</div>
		{/if}

		{#if success}
			<div class="rounded-md bg-green-50 p-3" role="alert">
				<p class="text-sm text-green-800">{success}</p>
			</div>
		{/if}

		<div class="flex justify-end gap-2">
			<button
				type="button"
				onclick={handleClear}
				disabled={isSubmitting || !yamlContent}
				class="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400"
			>
				Clear
			</button>
			<button
				type="submit"
				disabled={isSubmitting || !yamlContent || !!validationError}
				class="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-gray-400"
			>
				{#if isSubmitting}
					<LoadingSpinner size="sm" />
					<span>Uploading...</span>
				{:else}
					<span>Upload Parameter Set</span>
				{/if}
			</button>
		</div>
	</form>
</div>
