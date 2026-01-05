# UI Build Configuration

## Overview

The Svelte UI build has been optimized to minimize JavaScript files and use static filenames with query string cache busting.

## Results

- **File Reduction:** 41 JS files → 16 JS files (61% reduction)
- **Static Filenames:** Entry points and assets use predictable names
  - JavaScript: `app.js`, `start.js`, `0.js` - `11.js`
  - CSS: `app.css`
- **Cache Busting:** Query string versioning (`?v=<timestamp>`)
- **Total Size:** ~176 KB (153KB JavaScript + 23KB CSS)
  - JavaScript: ~153 KB (74KB app + 79KB vendor)
  - CSS: ~23 KB
- **Node Files:** 12 route entry points (~1KB total) - required by SvelteKit

**Why 16 JavaScript files?**
- 2 main entry files (app.js, start.js) with static names
- 2 bundle chunks (vendor.js, app.js) with static names
- 12 route node files (0.js - 11.js) - required by SvelteKit's routing system

**Note:** Both chunks and entries have `app.js`, but they're in different directories:
- Entry: `_app/immutable/entry/app.js` (entry point, 0.23KB)
- Chunk: `_app/immutable/chunks/app.js` (app bundle, ~73KB)

This is the **optimal configuration** for SvelteKit applications, balancing file count with functionality.

## How It Works

### 1. Vite Configuration (`vite.config.ts`)

Custom plugin overrides SvelteKit's default chunking strategy:

```typescript
{
    name: 'override-sveltekit-chunking',
    enforce: 'post',
    config: (config) => {
        return {
            build: {
                rollupOptions: {
                    output: {
                        inlineDynamicImports: false,
                        manualChunks: (id) => {
                            if (id.includes('node_modules')) {
                                return 'vendor';
                            }
                            return 'app';
                        }
                    }
                }
            }
        };
    }
}
```

This creates two main bundles:
- `vendor` - Third-party dependencies
- `app` - Application code

### 2. Post-Build Script (`scripts/static-filenames.js`)

After Vite build completes, this script:
1. Finds all JavaScript files with hash-based names (e.g., `app.ChWf7yR6.js`)
2. Renames them to static names (e.g., `app.js`)
3. Updates `index.html` to reference static names with version query strings
4. Preserves SvelteKit's functionality and routing

### 3. Build Command

```json
{
  "scripts": {
    "build": "vite build && node scripts/static-filenames.js"
  }
}
```

## File Structure

After build:

```
build/
├── index.html                          # Updated with static names + query strings
├── _app/
│   ├── version.json                    # Build timestamp
│   └── immutable/
│       ├── entry/
│       │   ├── app.js                  # Main entry (static name, 0.23KB)
│       │   └── start.js                # SvelteKit start (static name, 0.08KB)
│       ├── chunks/
│       │   ├── vendor.js               # Vendor bundle (static name, ~79 KB)
│       │   └── app.js                  # App bundle (static name, ~74 KB)
│       ├── nodes/
│       │   ├── 0.js                    # Route manifests (static names)
│       │   ├── 1.js                    # ~90 bytes each
│       │   └── ...
│       └── assets/
│           └── app.css                 # Styles (static name, ~23 KB)
```

## Example HTML Output

```html
<!-- CSS with static name and version query string -->
<link href="/_app/immutable/assets/app.css?v=1767399045204" rel="stylesheet">

<!-- JavaScript module preloads (entries and chunks) -->
<link rel="modulepreload" href="/_app/immutable/entry/start.js?v=1767399045204">
<link rel="modulepreload" href="/_app/immutable/chunks/app.js?v=1767399045204">
<link rel="modulepreload" href="/_app/immutable/chunks/vendor.js?v=1767399045204">
<link rel="modulepreload" href="/_app/immutable/entry/app.js?v=1767399045204">

<!-- SvelteKit initialization -->
<script>
  Promise.all([
    import("/_app/immutable/entry/start.js?v=1767399045204"),
    import("/_app/immutable/entry/app.js?v=1767399045204")
  ]).then(([kit, app]) => {
    kit.start(app, element);
  });
</script>
```

**All assets use static names with query string versioning:**
- Entries: `entry/app.js`, `entry/start.js`
- Chunks: `chunks/vendor.js`, `chunks/app.js`
- Assets: `assets/app.css`
- Nodes: `nodes/0.js` through `nodes/11.js`

## Cache Strategy

- **HTML file:** Should use `Cache-Control: no-cache` (FastAPI default)
- **JavaScript files:** Can be cached indefinitely - query string changes on rebuild
- **CSS files:** Can be cached indefinitely - query string changes on rebuild
- **Version:** Based on build timestamp from `_app/version.json`

All static assets (JS and CSS) use the same version query string, ensuring synchronized cache invalidation across the entire application on each build.

## Benefits

1. **Predictable Deployment:** Entry files always named `app.js` and `start.js`
2. **Cache Control:** Query string versioning ensures fresh content after deploys
3. **Fewer Files:** 61% reduction in file count simplifies deployment and reduces HTTP requests
4. **SvelteKit Compatible:** Preserves all SvelteKit features (routing, preloading, etc.)
5. **Minimal Overhead:** Post-build processing adds only 1-2 seconds

## Deployment

To deploy the UI to the FastAPI static directory:

```bash
# Build the UI
cd ui
npm run build

# Copy to FastAPI static directory
cd ..
cp -r ui/build/* src/soliplex/ingester/server/static/
```

Or add to your deployment automation pipeline.

## Troubleshooting

**Build fails with "Cannot find module" error:**
- Ensure you're running `npm run build` from the `ui/` directory
- Check that `scripts/static-filenames.js` exists

**Files not renamed:**
- Check build output - script runs after "Using @sveltejs/adapter-static"
- Verify `ui/build/_app/version.json` exists

**HTML not updated:**
- Script should print "Updated build\index.html with static filenames"
- Check that renamed files match the pattern in the script

**Runtime errors:**
- Verify all referenced files in `index.html` exist in `build/_app/`
- Check browser console for 404 errors
