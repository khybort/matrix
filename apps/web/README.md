# apps/web

Next.js 16 (App Router) — internal dashboard + public bulletin site.

**Owner:** Machine B (Output)

## Setup (when scaffolded)

```bash
cd apps/web
pnpm install
pnpm dev
```

## Scaffolding plan

Run from this directory on Machine B:

```bash
pnpm create next-app@latest . --typescript --tailwind --app --src-dir --use-pnpm --no-eslint --no-import-alias
# then:
pnpm dlx shadcn@latest init
pnpm dlx shadcn@latest add button card input dialog
pnpm add ai @ai-sdk/react
```

Use the `vercel:nextjs` and `vercel:shadcn` skills for guidance.
