# Engineering Lessons — bir kez ödenmiş tuzaklar

> Her node ve dev_agent kod yazmadan önce okur. Yeni bir ders öğrenildiğinde **aynı commit'te**
> buraya madde eklenir ve `dev_agent_lessons` tablosuna `status='active'` satır yazılır
> (dev_agent görev prompt'una retrieval ile girer). Tarih + gerekçe + doğru yaklaşım; roman yok.

## Test ve çalıştırma
- **2026-09-12 — Testler image içinde koşar.** Host'ta `uv`/`timeout` yok. `docker run --rm --entrypoint uv
  -v $PWD/services/<svc>/src:/app/services/<svc>/src -v $PWD/services/<svc>/tests:/app/services/<svc>/tests
  matrix-<svc>:local run --no-sync pytest`. Image entrypoint `uv run python -m` olduğundan `--entrypoint uv` şart.
  pyproject mount edilmez → session-scope loop isteyen suite'lere `-o asyncio_default_fixture_loop_scope=session
  -o asyncio_default_test_loop_scope=session`.
- **2026-09-13 — Test fixture'ı CANLI tabloya yazmaz.** dev_agent conftest `dev_tasks`'ı TRUNCATE etti; 9 görev gitti,
  canlı worker test görevini aldı, yedekten restore gerekti. Kural: her servis testi `<db>_devagent_test` gibi izole
  DB kullanır (AGE + `vector SCHEMA ag_catalog` + gerekli tablolar orada yaratılır). TRUNCATE/DELETE-all fixture = red.
- **2026-09-13 — Engine testleri canlı cüzdana dokunmaz.** `test_slot_enforcement` `_open_for_market("crypto")`'yi canlı
  default cüzdana karşı koşuyordu: canlı prediction'lara gerçek pozisyon açtı, engine ile yarışıp
  `paper_positions_prediction_id_key` ihlaliyle canlı tick'i düşürdü, assert'leri kuyruk derinliğine bağlıydı. Artık
  `tests/isolated_market.py`: sentetik `asset_class="test"` + kendi champion/shadow cüzdanları (`all_markets()` "test"
  içermez → engine hiç görmez). Engine'e dokunan her yeni test bunu kullanır.
- **2026-09-13 — Startup'ta tam tablo taraması yok.** bars-aggregator `backfill_all()` 302M `market_trades` satırını
  tarıyordu, backtest testleri asılıyordu → `BARS_STARTUP_BACKFILL_MAX_HOURS=48`. `DELETE … WHERE exchange_trade_id IN (…)`
  index'siz; kullanma.
- **2026-09-13 — Toplu geçmiş üzerinde bounded pass.** Reflection efficacy 2.675 legacy proposal'ı tek tick'te taradı →
  `MAX_PER_TICK`, aggregate SQL, testte `strategy_id` filtresi.
- **2026-09-13 — Event loop ve engine.** Throwaway loop'ta yaratılan SQLAlchemy engine sonraki loop'ta patlar →
  `matrix_shared.db.reset_engines()`.

- **2026-09-13 — Bayrak kaybı = yanlış cüzdan.** `_open_for_market(shadow=True)` içteki `_resolve_wallet` çağrısına `shadow`
  bayrağını geçirmiyordu; challenger pozisyonları şampiyon cüzdanına slot cap'siz yazıldı (1 günde −$343). Bir fonksiyon
  aynı ayrımı iki kez çözüyorsa bayrağı ikisine de ver; regresyon testi cüzdan kimliğini assert eder
  (`test_shadow_wallet_booking.py`). Belirti: `paper_positions` cüzdan dağılımı ile `predictions.context.is_shadow` uyuşmaz.
- **2026-09-13 — Sırasız LIMIT.** Aday havuzu `LIMIT n*5` ile ORDER BY'sız çekiliyordu; kuyruk büyüdükçe (764 açık prediction)
  rastgele dilim alınıyor, EV sıralaması hiç görmediği adayları seçemiyordu. LIMIT varsa ORDER BY şart.
- **2026-09-13 — `market_trades` silmede indeks.** Tek indeks `(exchange, exchange_trade_id)`; `WHERE exchange_trade_id IN (...)`
  full scan (300M satır) → test temizliği 10+ dk. Her zaman `exchange = 'bybit' AND exchange_trade_id IN (...)`.

## Git ve migration
- **Sadece kendi hunk'larını stage et.** Ağaçta başka bir node'un uncommitted WIP'i durabilir (US-market adapter, 0038).
  `git add -A` yasak; ortak dosyalarda HEAD + kendi patch'in (`git hash-object` + `update-index`). CHANGES.md'ye
  giriş eklerken index = HEAD + giriş.
- **Migration zinciri.** Ağaçta uncommitted `00NN_*.py` varken yeni alembic revision yazma; önce o commit'lenir.
- **Her servis dev overlay'de değil.** `docker-compose.dev.yml`'de girdisi olmayan servis (notify öyleydi) image'daki
  kodu çalıştırır; `src/` düzenlemesi canlıya yansımaz ve import hatası vermez, sessizce eski kod koşar. Yeni modül
  eklediğin servisin overlay'de mount'u var mı kontrol et (`docker compose exec <svc> ls .../src/<pkg>`).
- **İki DB tier'ında aynı tablo olabilir.** `dev_tasks` migration'la hem LOCAL hem SHARED'da yaratıldı; dev_agent LOCAL'a
  yazar. Yanlış tier'ı sorgulayan probe (notify health) hata vermez, boş döner → alarm hiç çalmaz. Tablonun sahibi servisin
  DSN'ine bak, ona göre `local_session_scope`/`shared_session_scope` seç. Aynı gün ikinci kez: Director `file_dev_task`,
  digest ve efficacy `maybe_file_dev_task` SHARED'a yazıyordu → görev #11 hiç işlenmedi. `dev_tasks` = LOCAL, nokta.
- **Hot reload canlıdır.** `src/` bind-mount + watchfiles: kaydettiğin an canlı servis restart olur. Edit sonrası
  `docker compose logs --since 2m <svc>` ile Traceback tara (labs/evaluate.py'de unutulan `func` import'u canlıda NameError attı).

## SQL / Postgres / AGE
- **`json` ≠ `jsonb`.** `predictions.context`, `mutation_proposals.metrics_window` `json`; `?` operatörü yok, hata da
  sessiz kalabilir → `col->>'key' IS NOT NULL`.
- **AGE cypher içinde `-[:REL]->`** SQLAlchemy `text()` tarafından `:REL` bind param sanılır → her ilişkiye alias:
  `-[ri:RESULTED_IN]->`.
- **pgvector AGE ile aynı DB'de** `vector` extension'ı `ag_catalog` şemasında (`ALTER EXTENSION vector SET SCHEMA ag_catalog`);
  yeni test DB'lerinde aynısını yap.

## İstatistik ve öğrenme döngüsü (neden böyle)
- 10 trade'lik win rate ±16pp gürültü → slot değişimi için n≥30, Wilson alt sınırı (`reflection/slot_scorer.py`).
- "Pozitif toplam" tek başına kanıt değil → cert için trade-başı ortalama PnL %95 CI alt sınırı > 0; DD aşımında otomatik iptal.
- Hedef metrik **total_pnl_usd** (fee+slippage sonrası); score/win-rate tanı aracı. `orphan_flat_close` outcome'ları kanıt değil.
- Reflection mutasyonu yalnız `_underperforming` stratejilerde; sağlıklı stratejiyi her tick yeniden ayarlamak öğrenmeyi bozar.
- **2026-09-13 — Mutation spiral: param_tune n < 30 + no cooldown.** oi_delta v2→v3→v4→v5 zinciri: `MIN_N_OUTCOMES=10` ile n=12 örneğinde
  param_tune tetiklendi, labs 600s içinde uyguladı, efficacy'nin 24h değerlendirme süresi dolmadan yeni versiyon oluştu. İki savunma eklendi:
  (1) `PARAM_TUNE_MIN_N` per-strategy dict — oi_delta/grid/dca/oi_breakout/funding_reversion için n≥30 (`reflection/mutate.py`);
  (2) `MATRIX_PARAM_TUNE_COOLDOWN_HOURS=24` — aynı strateji için ardışık param_tune'lar arası minimum bekleme süresi (`reflection/main.py`).
  Ek: son 3 günde denenen knob'lar `skip_knobs` ile atlanır, aynı knob tekrar seçilmez.

## LLM / kimlik
- `claude setup-token` uzun ömürlü token'ları bu hesapta 401 verdi → host keychain oturumu (refresh token'sız) saatlik
  volume senkronu (`scripts/claude_creds_sync.sh`). Token'ları asla loga/rapora yazma.
- `from __future__ import annotations` modülün ilk statement'ı olmalı (docstring hariç).
