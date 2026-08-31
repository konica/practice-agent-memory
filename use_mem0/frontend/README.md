# Frontend

React 19 + Vite + TypeScript. Tailwind CSS v4 with shadcn/ui, themed with the
approved mockup's design tokens.

```bash
../npm-install   # not `npm install` — see below
npm run dev      # http://localhost:5173
npm run build    # tsc -b && vite build
npm run lint
```

`../npm-install` is a plain `npm install` wherever symlinks work. On filesystems
that refuse them — including the `/c/...` mount this repo is often checked out
on — npm cannot write `node_modules/.bin`, and `npm run dev` then fails with
`sh: 1: vite: not found`; the wrapper installs with `--no-bin-links` and writes
those entries as small shell scripts instead. `use_mem0/up` calls it for you.

`VITE_API_BASE` points the typed API client at the backend (defaults to
`http://localhost:8000`). See `.env.example`. Every call in `src/api.ts` sends
`credentials: "include"` so the backend's session cookie rides along.

## Design tokens

`src/index.css` holds the mockup's palette, applied by overriding shadcn's CSS
variable *values* while keeping its variable *names*, so generated components
inherit the design. Two things not to collapse:

- **Radius is a scale**, not one value: `--radius-input` 6px (rename input),
  `--radius-mark` 7px (avatar/logo marks), `--radius` 10px (controls),
  `--radius-card` 13px (cards, modals), `--radius-logo` 16px (sign-in mark).
- **Bubble radius is directional**: the 2px corner is the tail and mirrors the
  side the bubble is aligned to. See `--radius-bubble-user` /
  `--radius-bubble-assistant`.

Use Tailwind utility classes referencing these tokens (`bg-background`,
`text-muted-foreground`, `rounded-lg`, …) rather than inline `style` objects.
The shadcn source in `src/components/ui/` is copied in, not vendored — edit it
directly.
