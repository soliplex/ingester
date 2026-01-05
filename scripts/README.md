# Build Scripts

This directory contains scripts for building the Svelte UI and deploying it to the FastAPI static directory.

## Available Scripts

### `build-ui.bat` (Windows)

Windows batch script for building the UI.

**Usage:**
```cmd
scripts\build-ui.bat
```

**Requirements:**
- Node.js and npm installed on Windows

---

### `build-ui.sh` (Linux/macOS)

Unix shell script for building the UI.

**Usage:**
```bash
./scripts/build-ui.sh
# or
bash scripts/build-ui.sh
```

**Requirements:**
- Node.js and npm installed
- Execute permission: `chmod +x scripts/build-ui.sh`

---

### `build_ui-docker.sh` (Docker-based)

Docker-based build script for environments without local Node.js/npm installation.

**Usage:**
```bash
./scripts/build_ui-docker.sh
# or
bash scripts/build_ui-docker.sh
```

**Requirements:**
- Docker installed and running
- Execute permission: `chmod +x scripts/build_ui-docker.sh`
- Uses `node:lts-alpine` Docker image

**Note:** This script is non-interactive (no `-it` flags) to work in CI/CD pipelines.

---

## What These Scripts Do

All scripts perform the same steps:

1. **Install npm dependencies** in `ui/` directory
2. **Build production bundle** with:
   - Optimized chunking (vendor + app bundles)
   - Static filenames for entry points (`app.js`, `start.js`)
   - Query string cache busting (`?v=<timestamp>`)
3. **Copy build artifacts** to `src/soliplex/ingester/server/static/`
4. **Verify** that key files exist

## Build Process Details

The build process uses:

- **Vite + SvelteKit** for bundling and optimization
- **Custom Vite plugin** to override SvelteKit's default chunking strategy
- **Post-build script** (`ui/scripts/static-filenames.js`) that:
  - Renames hash-based filenames to static names
  - Updates `index.html` with versioned references
  - Preserves SvelteKit functionality

See `ui/README_BUILD.md` for detailed build configuration.

## Output

After running any of these scripts, you'll find:

```
src/soliplex/ingester/server/static/
├── index.html                          # Updated with version query strings
├── _app/
│   ├── version.json                    # Build timestamp
│   └── immutable/
│       ├── entry/
│       │   ├── app.js                  # Main entry (static name)
│       │   └── start.js                # SvelteKit start (static name)
│       ├── chunks/
│       │   ├── [hash].js               # Vendor bundle (~79 KB)
│       │   └── [hash].js               # App bundle (~74 KB)
│       ├── nodes/
│       │   └── [0-11].js               # Route manifests (~90 bytes each)
│       └── assets/
│           └── app.[hash].css          # Styles
```

**Total:** ~16 JavaScript files (down from 41)

## Troubleshooting

### "Failed to navigate to ui directory"
- Ensure you're running the script from the project root directory
- Check that the `ui/` directory exists

### "Failed to install dependencies"
- Check your internet connection
- Verify Node.js/npm is installed: `node --version && npm --version`
- For Docker script: ensure Docker is running

### "Failed to build UI"
- Check build output for specific errors
- Verify `ui/package.json` has the build script defined
- Ensure `ui/scripts/static-filenames.js` exists

### Missing files in verification
- Check if the build completed successfully
- Look for error messages in the npm build output
- Verify the post-build script ran (should see "Renamed:" messages)

### Docker script hangs
- Ensure you're not using `-it` flags in a non-interactive environment
- Check Docker has sufficient resources allocated
- Try pulling the image first: `docker pull node:lts-alpine`

## CI/CD Integration

These scripts can be integrated into CI/CD pipelines:

**GitHub Actions Example:**
```yaml
- name: Build UI
  run: ./scripts/build-ui.sh

# Or using Docker:
- name: Build UI (Docker)
  run: ./scripts/build_ui-docker.sh
```

**Docker Build Example:**
```dockerfile
# In your Dockerfile
COPY ui/package*.json ui/
COPY ui/ ui/
COPY scripts/build_ui-docker.sh scripts/
RUN bash scripts/build_ui-docker.sh
```

## Manual Build

If you prefer to build manually:

```bash
cd ui
npm install
npm run build
cd ..
cp -r ui/build/* src/soliplex/ingester/server/static/
```

## Additional Resources

- **UI Build Configuration:** `ui/README_BUILD.md`
- **Build Evaluation:** `docs/UI_BUNDLING_EVALUATION.md`
- **UI Development:** `ui/CLAUDE.md`
- **Project Documentation:** `docs/`
