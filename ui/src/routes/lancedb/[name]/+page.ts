import { apiClient } from '$lib/services/apiClient';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	try {
		const [info, documents] = await Promise.all([
			apiClient.getLanceDBInfo(params.name),
			apiClient.getLanceDBDocuments(params.name)
		]);
		return { dbName: params.name, info, documents };
	} catch (error) {
		console.error('Failed to load LanceDB info:', error);
		return { dbName: params.name, info: null, documents: null, error };
	}
};
