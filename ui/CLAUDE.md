# CLAUDE.md

Guidance for Claude Code when working with this Svelte UI project.

## Project Overview

**soliplex-ingester-ui** - Svelte 5 frontend for monitoring document ingestion workflows.

- **Stack:** Svelte 5 (runes syntax), TypeScript, Tailwind CSS v4, SvelteKit
- **API:** Connects to REST server at `/api/v1` (spec: `http://127.0.0.1:8000/openapi.json`)
- **Backend docs:** `../CLAUDE.md`

## Commands

```bash
npm install          # Install dependencies
npm run dev          # Dev server (http://localhost:5173)
npm run build        # Production build
npm run check        # TypeScript + Svelte type checking
npm run format       # Prettier formatting
npm run lint         # ESLint
```

## Project Structure

```
src/
├── lib/
│   ├── components/     # Reusable UI components
│   ├── services/       # API client (apiClient.ts)
│   ├── types/          # TypeScript definitions (api.ts)
│   ├── utils/          # Helpers (format.ts, errors.ts)
│   └── config/         # Configuration (api.ts)
└── routes/             # SvelteKit pages
```

## Key Components

| Component | Purpose |
|-----------|---------|
| `PageHeader.svelte` | Page title with optional actions |
| `StatusBadge.svelte` | Color-coded status indicators |
| `StepTimeline.svelte` | Expandable workflow steps view |
| `LifecycleHistoryTimeline.svelte` | Lifecycle events timeline |
| `Pagination.svelte` | Table pagination controls |

## Code Style

### Naming
- **Variables/functions:** camelCase
- **Components:** PascalCase
- **Event handlers:** `handle` prefix (e.g., `handleClick`)
- **Constants:** camelCase with `Value` suffix for objects

### TypeScript
- No `any` type or unsafe casting
- Destructure imports: `import { foo } from 'bar'`
- Each component declares its own prop types

### Svelte 5
- Use runes: `$state`, `$derived`, `$props`, `$effect`
- Props: `let { prop1, prop2 }: Props = $props();`
- Derived: `const value = $derived(expression);`

### Styling
- Tailwind CSS only - no inline styles
- Mobile-first responsive design

### Files
- Max 200 lines per component - split if larger
- Trailing newline required
- TypeScript files: camelCase (e.g., `myService.ts`)

## API Integration

```typescript
import { apiClient } from '$lib/services/apiClient';

// All methods are type-safe
const batches = await apiClient.getBatches();
const workflow = await apiClient.getWorkflowRunDetails(id);
```

Types in `src/lib/types/api.ts` must match backend Pydantic models.

## Accessibility Requirements

- WCAG AA compliant
- Include `aria-label` on interactive elements
- Use `aria-expanded` for expandable sections
- Never rely on color alone for status indication
- Proper focus indicators (`focus:ring-2`)

## Before Committing

1. Run `npm run check` - fix all type errors
2. Run `npm run format` - ensure consistent formatting
3. Verify components under 200 lines
