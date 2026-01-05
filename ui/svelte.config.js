import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		adapter: adapter({
			fallback: 'index.html'  // SPA mode - serve index.html for all routes
		}),
		version: {
			// Use name-based versioning instead of content hash
			name: Date.now().toString()
		}
	}
};

export default config;
