<script lang="ts">
	import { formatDateTimeLocal, formatDateTimeUTC } from '$lib/utils/format';

	interface Props {
		date: string | null;
		showUtc?: boolean;
		compact?: boolean;
	}

	let { date, showUtc = true, compact = false }: Props = $props();

	const localTime = $derived(formatDateTimeLocal(date));
	const utcTime = $derived(formatDateTimeUTC(date));
</script>

{#if !date}
	<span class="text-gray-400">—</span>
{:else if compact}
	<span title="UTC: {utcTime}" class="cursor-help">{localTime}</span>
{:else}
	<div class="flex flex-col">
		<span class="text-gray-900">{localTime}</span>
		{#if showUtc}
			<span class="text-xs text-gray-500">{utcTime}</span>
		{/if}
	</div>
{/if}
