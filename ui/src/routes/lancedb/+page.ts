import { apiClient } from '$lib/services/apiClient';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	try {
		const response = await apiClient.getLanceDBList();
		return { response };
	} catch (error) {
		console.error('Failed to load LanceDB databases:', error);
		return { response: null, error };
	}
};
