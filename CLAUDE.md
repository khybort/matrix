# Matrix — Claude Code Instructions

> Bu dosya bütün node'lar tarafından okunur. N tane PC bu sistemi çalıştırabilir; sabit rol yok.

## Proje özeti

**Matrix** = kendi kendini güncelleyen, context-graph tabanlı, multi-market AI fintech araştırma + **otonom trading** motoru. Birincil hedef: sistemin paper-trade'i validate ettikten sonra küçük canlı sermaye ile **doğrudan piyasada para kazanması**. Bülten ürünü ertelenmiş ikincil ürün.

- Vizyon: `docs/VISION.md`
- Mimari: `docs/ARCHITECTURE.md`
- Yol haritası: `docs/ROADMAP.md`
- Node dağıtımı: `docs/WORK_SPLIT.md`
- **Trading risk framework**: `docs/TRADING.md` ← live capital'a dokunulan her şey burada yazılı kurallara uymak ZORUNDA

## Hangi node'dasın? (her oturum ilk iş)

```bash
cat .matrix-node.json 2>/dev/null || echo "unset"
```

`unset` ise: `.matrix-node.example.json` kopyala, `node_id` + `roles` doldur, **commit etme** (gitignored). Sor kullanıcıya hangi role'leri verecek.

## Çalışma kuralları

1. **Oturum başı**: `git pull --rebase` (remote varsa) → `TaskList` → ne işin var bak
2. **Atomik commit**: `feat:`, `fix:`, `chore:`, `docs:` prefiks, küçük scope
3. **Push sık** (remote eklendiğinde): her anlamlı iş bitiminde
4. **Çakışma**: rebase tercih. Docs çakışmasında merge OK
5. **Sırlar/keys**: asla commit etme. `.env.local` gitignored. Şablon `.env.example`
6. **Cross-cutting değişiklik**: önce `docs/CHANGES.md`'a bir satır, sonra kod

## Local-first kararı

Şu anda **hiçbir bulut servisi yok**. Postgres dahil her şey local Docker'da. `docker compose up -d` ile ayağa kalkıyor.

Production / cloud sadece şu durumda gündeme gelir:
- Multi-node setup ağ üzerinden veriyi paylaşmak gerektiğinde (LAN veya VPN yeterli olmazsa)
- Bülten ürünü canlıya çıkacaksa (Vercel + Neon o aşamada eklenir)
- Bunlardan önce: **localhost, localhost, localhost**

## Teknoloji kararları (varsayılan)

- **Engine**: Python 3.13 + FastAPI + Polars + SQLAlchemy + Pydantic v2 + LangGraph
- **DB**: Postgres 16 + **pgvector** + **Apache AGE** (Docker)
- **Web** (Phase 6 sonrası): Next.js 16 + Tailwind + shadcn + AI SDK v6
- **LLM**: Vercel AI Gateway → Claude Opus 4.7 (synthesis) + Haiku 4.5 (extraction/filter)
- **Job queue**: Postgres `FOR UPDATE SKIP LOCKED` (basit, ölçeklenince yer değiştirir)
- **Exchanges**: Bybit/Binance testnet ilk; live'a Phase 5'te geçilir; secrets `.env.local`'de

## Asla yapmayacağın şeyler

- **Live trading ile experiment etme** — paper-trade tamamlanmadan canlı sermaye **yok**. Phase 5 öncesi sertifikalar geçilmeden execution role'ü gerçek exchange API'sine bağlanamaz.
- Ana branch'a force push — yasak
- "Defensive coding" — internal sınırlarda gereksiz validation yok; user input / external API'lerde validation var
- Sahte iş — gereksiz abstraction, dolgu yorum, README spam
- Kullanıcı onayı olmadan destructive komut (drop, force, --no-verify, rm -rf, vs.)

## Risk gates (kritik)

Aşağıdakiler kod düzeyinde **enforce** edilir, opsiyon değil:
- Strateji hiçbir koşulda capital'in N%'inden fazlasını tek trade'e koyamaz (config'ten okur)
- Daily P&L < -X% → bütün açık emirler kapatılır, kill switch tetiklenir
- "Live" execution role'ü olan node, paper-trade certificate olmayan strateji için emir gönderemez
- Bug-induced çok hızlı seri trade → rate limiter devreye girer

Detay: `docs/TRADING.md`.

## Sık kullanılan komutlar

```bash
# Node identity check
cat .matrix-node.json

# Local DB ayağa kaldır
docker compose up -d
docker compose logs -f postgres

# Bağımlılık (root)
pnpm install

# Python servis
cd services/<service> && uv sync && uv run python -m <service>.main

# DB migration (sadece bir node bir seferde author'lar)
cd infra/db && uv run alembic upgrade head

# Web (Phase 6+)
cd apps/web && pnpm dev
```

## Tools

- **rtk** — Claude Code hook'la tokensiz proxy. Otomatik.
- **Vercel CLI** — şu anda kullanılmıyor; Phase 6'da bülten/web prod için lazım olur.
- **Docker** — local infra için zorunlu.

## Hatırlatmalar

- Kullanıcı (Muhsin) senior engineer; basic açıklama yok, tradeoff söyle
- Kullanıcı direktif istiyor — sonu açık sorularda öneri + tradeoff + ilerle
- Her node Claude Code çalıştırıyor; iletişim docs/ ve git üzerinden
- "Para kazanan, kendi kendini geliştiren" — bu birincil hedef. Her feature kararını "bu sistemin live PnL'ini iyileştirir mi?" sorusuyla tart.
