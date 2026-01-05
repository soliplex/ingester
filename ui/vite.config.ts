import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
	plugins: [
		sveltekit(),
		tailwindcss()
	],
	build: {
		rollupOptions: {
			output: {
				// Use selective chunking that preserves module initialization order
				manualChunks: (id) => {
					// Only chunk large vendor libraries separately
					if (id.includes('node_modules')) {
						// Group by major dependency to reduce chunk count while maintaining init order
						if (id.includes('@sveltejs')) {
							return 'svelte-vendor';
						}
						// All other dependencies in one vendor chunk
						return 'vendor';
					}
					// Let SvelteKit handle app code chunking automatically
					// This preserves module initialization order for Svelte components
				}
			}
		}
	},
	server: {
		proxy: {
			'/api/v1': {
				target: 'http://127.0.0.1:8000',
				changeOrigin: true
			}
		}
	}
});
