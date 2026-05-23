# Matrix — Claude Code Instructions

> Bu dosya hem Machine A (Cortex) hem Machine B (Output) tarafından okunur.
> İki makine de aynı git repo üzerinde çalışır. Çakışmayı önlemek için aşağıdaki sınırlara dikkat et.

## Proje özeti

**Matrix** = kendi kendini güncelleyen, context-graph tabanlı, multi-market AI fintech araştırma motoru ve Muhsin'in "ikinci beyni". Hem direkt PnL hem ücretli araştırma bülteni üzerinden gelir. 6 aylık hedef: $1-5k MRR. 2-3 yıllık hedef: satılabilir varlık.

Tam vizyon: `docs/VISION.md`. Mimari: `docs/ARCHITECTURE.md`. Yol haritası: `docs/ROADMAP.md`. İki makine iş bölümü: `docs/WORK_SPLIT.md`.

## Hangi makinedesin?

Çalışmaya başlamadan **ilk iş** olarak hangi makinede olduğunu öğren:

```bash
cat .matrix-machine 2>/dev/null || echo "unset"
```

- `cortex` → Machine A. Sadece `services/`, `packages/python-shared/`, `infra/db/`, `docs/` altında çalış.
- `output` → Machine B. Sadece `apps/`, `packages/ts-shared/`, `docs/` altında çalış.
- `unset` → Kullanıcıya sor, `.matrix-machine` dosyasını oluştur, commit ETME (gitignored).

Sınır dışı bir klasöre dokunman gerekiyorsa **önce** `docs/CHANGES.md`'a tek satır ekle ki diğer makine fark etsin.

## Çalışma kuralları

1. **Her oturum başında**: `git pull --rebase` çalıştır. Sonra TaskList ile devam eden işleri kontrol et.
2. **Her commit**: küçük, atomik, açıklayıcı mesaj. `feat:`, `fix:`, `chore:`, `docs:` prefiksleri.
3. **Push**: her anlamlı iş bitiminde push et — diğer makine senin işine bağımlı olabilir.
4. **Çakışma çıkarsa**: rebase tercih et, ama `docs/`, `CLAUDE.md` çakışmasında merge OK (her iki tarafın eklediği bilgi de değerli).
5. **Sırrar/keys**: asla commit etme. `.env.local` gitignored. Şablon `.env.example`'da tut.

## Teknoloji kararları (taşa kazılmamış ama varsayılan)

- **Web**: Next.js 16 App Router + Tailwind + shadcn/ui + AI SDK v6 (Vercel AI Gateway üzerinden Claude)
- **Engine**: Python 3.13 + FastAPI + Polars + SQLAlchemy + Pydantic v2 + LangGraph (agent orchestration)
- **DB**: Neon Postgres + pgvector + Apache AGE (graph)
- **Queue/jobs**: Postgres-based (initial), Vercel Queues (production scale)
- **LLM**: Vercel AI Gateway → Claude Opus 4.7 (analiz) + Haiku 4.5 (filtreleme, entity extraction)
- **Deployment**: Vercel (web), self-hosted Python services on local machines (initial), containerize later
- **Auth/Billing**: Clerk (Vercel Marketplace), Stripe (ileride bülten subs)

## Asla yapmayacağın şeyler

- Ana branch'a force push — yasak.
- Diğer makinenin domain'ine (Python ↔ TS) müdahale — koordinasyon olmadan.
- "Defensive coding" — internal sınırlarda gereksiz validation eklemek yok; user input/external API'lerde validasyon var.
- Sahte iş — boş yorum, README spam, gereksiz abstraction yaratma. Üç benzer satır, erken soyutlamadan iyidir.
- Kullanıcı onayı olmadan: PR push, dependency yükseltme, force push, migration drop, herhangi bir destructive komut.

## Sık kullanılan komutlar

```bash
# Hangi makinedesin
cat .matrix-machine

# Bağımlılık kurulumu (root)
pnpm install

# Python servis (Machine A)
cd services/<service> && uv sync && uv run python -m <service>.main

# Web (Machine B)
cd apps/web && pnpm dev

# DB migration (her iki makineden çalıştırılabilir, dikkat)
cd infra/db && # migration komutları (henüz seçilmedi: drizzle? alembic? prisma?)
```

## Tools

- **rtk** (Rust Token Killer) — Claude Code hook ile otomatik olarak shell komutlarını tokensiz proxy'liyor. Sen bir şey yapmıyorsun, sadece bilgi.
- **Vercel CLI** — `vercel deploy`, `vercel env pull` vs. Plugin var, ihtiyaç olduğunda `vercel:*` skill'leri kullan.

## Hatırlatmalar

- Kullanıcı (Muhsin) senior bir engineer. Basic açıklama yok. Tradeoff'ları söyle.
- Kullanıcı direktif istiyor — sonu açık sorularda öneri + tradeoff + ilerle.
- Her iki makinenin de işlem süresi maliyetli; gereksiz exploration yapma — bu repo dosyaları ve docs/ kanon.
