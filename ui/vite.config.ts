import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
	plugins: [sveltekit(), tailwindcss()],
	// Let SvelteKit/Vite handle chunking automatically to preserve module initialization order
	server: {
		proxy: {
			'/api/v1': {
				target: 'http://127.0.0.1:8000',
				changeOrigin: true
			}
		}
	}
});
