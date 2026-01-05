import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
	plugins: [
		sveltekit(),
		tailwindcss(),
		{
			name: 'override-sveltekit-chunking',
			enforce: 'post',
			config: (config) => {
				// Override SvelteKit's chunk settings after it processes
				return {
					build: {
						rollupOptions: {
							output: {
								// Minimize code splitting
								inlineDynamicImports: false,  // Must be false for manualChunks to work
								manualChunks: (id) => {
									// Bundle everything into fewer chunks
									if (id.includes('node_modules')) {
										return 'vendor';
									}
									// All app code in main bundle
									return 'app';
								}
							}
						}
					}
				};
			}
		}
	],
	server: {
		proxy: {
			'/api/v1': {
				target: 'http://127.0.0.1:8000',
				changeOrigin: true
			}
		}
	}
});
