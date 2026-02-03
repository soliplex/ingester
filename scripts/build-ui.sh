#!/bin/bash
set -e

echo "========================================"
echo "Building Soliplex Ingester UI"
echo "========================================"
echo ""
echo "This will:"
echo "  1. Install npm dependencies"
echo "  2. Build production bundle (with optimized chunking)"
echo "  3. Apply static filenames and version query strings"
echo "  4. Copy to server static directory"
echo ""

# Navigate to UI directory
cd ui || { echo "ERROR: Failed to navigate to ui directory"; exit 1; }

# Install dependencies
echo "[1/4] Installing dependencies..."
npm install
echo ""

# Build the UI (includes static-filenames post-processor)
echo "[2/4] Building production UI..."
echo "      - Bundling with Vite (vendor + app chunks)"
echo "      - Renaming to static filenames"
echo "      - Adding version query strings"
npm run build
echo ""

# Clear and copy build artifacts to server static directory
echo "[3/4] Copying build artifacts..."
rm -rf ../src/soliplex/ingester/server/static
mkdir -p ../src/soliplex/ingester/server/static
cp -r build/* ../src/soliplex/ingester/server/static/
echo "      Copied to: src/soliplex/ingester/server/static/"
echo ""

# Verify key files exist
echo "[4/4] Verifying build..."
if [ -f "../src/soliplex/ingester/server/static/index.html" ] && \
   [ -f "../src/soliplex/ingester/server/static/_app/immutable/entry/app.js" ] && \
   [ -f "../src/soliplex/ingester/server/static/_app/immutable/entry/start.js" ]; then
    echo "      ✓ index.html"
    echo "      ✓ app.js (static name)"
    echo "      ✓ start.js (static name)"
else
    echo "WARNING: Some expected files not found"
    [ ! -f "../src/soliplex/ingester/server/static/index.html" ] && echo "  - Missing: index.html"
    [ ! -f "../src/soliplex/ingester/server/static/_app/immutable/entry/app.js" ] && echo "  - Missing: app.js"
    [ ! -f "../src/soliplex/ingester/server/static/_app/immutable/entry/start.js" ] && echo "  - Missing: start.js"
fi
echo ""

echo "========================================"
echo "✓ UI build complete!"
echo "========================================"
echo ""
echo "Static files ready at: src/soliplex/ingester/server/static/"
echo "Entry points use static filenames with query string cache busting"
echo ""

cd ..
