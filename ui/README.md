# Soliplex Ingester UI

A Svelte-based front-end application for monitoring and interacting with the Soliplex Ingester REST API.

## Tech Stack

- **Framework:** SvelteKit with Svelte 5 (runes syntax)
- **Language:** TypeScript
- **Styling:** Tailwind CSS v4
- **Build Tool:** Vite

## Development

### Prerequisites

- Node.js 18+ and npm

### Setup

Install dependencies:

```bash
npm install
```

### Development Server

Start the development server:

```bash
npm run dev
```

The application will be available at http://localhost:5173

### Building

Build for production:

```bash
npm run build
```

This command:

1. Bundles the application with optimized chunking (vendor + app)
2. Applies static filenames to entry points (`app.js`, `start.js`)
3. Adds version query strings for cache busting

Output is written to `build/` directory.

**Build Results:**

- 16 JavaScript files (down from 41) - optimal for SvelteKit
  - 2 entry files with static names (app.js, start.js)
  - 2 bundle chunks (vendor ~79KB, app ~74KB)
  - 12 route nodes (~1KB total) - required by SvelteKit
- ~153 KB total JavaScript
- Static filenames with query string versioning

See `README_BUILD.md` for detailed build configuration and deployment.

Preview production build:

```bash
npm run preview
```

**Deploy to FastAPI:**

Use the automated build scripts from the project root:

- Windows: `scripts\build-ui.bat`
- Linux/macOS: `./scripts/build-ui.sh`
- Docker: `./scripts/build_ui-docker.sh`

Or manually:

```bash
npm run build
cp -r build/* ../src/soliplex/ingester/server/static/
```

### Code Quality

Run type checking:

```bash
npm run check
```

Run type checking in watch mode:

```bash
npm run check:watch
```

Lint code:

```bash
npm run lint
```

Format code:

```bash
npm run format
```

## API Configuration

The application connects to the Soliplex Ingester API at `http://127.0.0.1:8000/api/v1` by default.

## Project Structure

```
src/
├── lib/
│   ├── components/     # Reusable Svelte components
│   ├── services/       # API client and business logic
│   ├── types/          # TypeScript type definitions
│   ├── stores/         # Svelte stores for state management
│   └── utils/          # Helper functions and constants
├── routes/             # SvelteKit routes
└── app.css             # Tailwind CSS imports
```

## Features

- Monitor workflow status
- Interrogate workflow and parameter definitions
- Display batch processing statistics
- Real-time status updates

## License

See LICENSE file in project root.
