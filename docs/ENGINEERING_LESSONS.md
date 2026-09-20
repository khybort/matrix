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
- **2026-09-13 — `UV_PROJECT_ENVIRONMENT` miras kalır.** Image `UV_PROJECT_ENVIRONMENT=/workspace/services/dev_agent/.venv`
  ihraç eder; dev_agent worktree'de `uv run --project <svc>` çalıştırınca uv o projeyi dev_agent'ın venv'ine SENKRONLADI —
  her test koşusu dev_agent'ın kendi paketlerini sildi, hata bir sonraki reload'da çıktı ("No module named 'dev_agent'").
  Alt süreçlere env geçirirken `UV_PROJECT_ENVIRONMENT` ve `VIRTUAL_ENV`'i düşür (`integrate._test_env`).
- **2026-09-13 — Engine testleri canlı cüzdana dokunmaz.** `test_slot_enforcement` `_open_for_market("crypto")`'yi canlı
  default cüzdana karşı koşuyordu: canlı prediction'lara gerçek pozisyon açtı, engine ile yarışıp
  `paper_positions_prediction_id_key` ihlaliyle canlı tick'i düşürdü, assert'leri kuyruk derinliğine bağlıydı. Artık
  `tests/isolated_market.py`: sentetik `asset_class="test"` + kendi champion/shadow cüzdanları (`all_markets()` "test"
  içermez → engine hiç görmez). Engine'e dokunan her yeni test bunu kullanır.
- **2026-09-13 — Hot reload uzun sorguları öksüz bırakır.** watchfiles çocuğu SIGKILL'lediğinde Postgres tarafındaki
  INSERT/DELETE devam eder; yeni süreç aynı satırlara yazmaya kalkınca Lock'ta bekler (3 paralel `INSERT INTO market_bars`,
  6 restart/15 dk). Paylaşılan koda sık dokunurken uzun startup işleri olan servisleri hesaba kat; startup işini boşluk
  kadar boyutlandır; yığılma görürsen `pg_cancel_backend` ile öksüzleri kes.
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

## Altyapı (OrbStack)
- **2026-09-14 — Kapak kapalı = sistem yok.** `pmset -g log` "Entering DarkWake state due to 'Clamshell Sleep'": kapak kapalıyken
  macOS AC'de ve caffeinate/Amphetamine açıkken bile uyur; OrbStack VM'i de uyur (`vmgr.log: msg=sleep`), `docker ps` askıda kalır.
  Bunu kodla çözemezsin: kapak açık / harici ekran / `sudo pmset -a disablesleep 1`. `docker` askıdaysa OOM'dan önce `pmset -g log`'a bak.
- **2026-09-13 — Sınırsız container'lar VM'i öldürür.** 10 GiB OrbStack VM'i kernel OOM'a girdi ("VM_FAULT_OOM leaked", `~/.orbstack/log/vmgr.log`),
  Docker soketi ~11 saat askıda kaldı, `orb restart docker` "stopping container docker"da takıldı. Kural: her servise
  `mem_limit` (docker-compose.limits.yml tier'ları; postgres-shared için docker-compose.local.yml), VM belleği yükün
  toplam cap'inin üstünde (`orb config set memory_mib 12288`). Belirti: `docker ps` 20 s+ → önce vmgr.log'a bak.
  `docker-compose.limits.yml` yalnız base compose'daki servisleri içerebilir; overlay'e olmayan servis eklemek projeyi geçersiz kılar.

- **2026-09-14 — `docker compose up -d <svc>` bağımlılıkları da yeniden yaratabilir.** ingestion-market'i recreate ederken
  config'i değişmiş `postgres` de recreate edildi (15:55, tüm servislerde 20 s "database system is shutting down").
  Tek servis için `up -d --no-deps <svc>` kullan; DB'ye dokunacaksan bilinçli yap.

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

- **2026-09-13 — Emisyon ≠ kapasite.** Slot sayısından bağımsız prediction üretimi: 1 slotlu strateji 9k/gün expired, agent
  1.4k boşa LLM çağrısı. Kural: üretim, tüketim kapasitesine (slot × k) bağlı olsun; `matrix_shared.backpressure.room()`.

## LLM / kimlik
- **2026-09-13 — launchd + ~/Documents = çalışmaz.** macOS TCC yüzünden launchd ajanı `~/Documents` altındaki script'i
  çalıştıramaz (exit 126, 'Operation not permitted'), log'a bakılmazsa sessizce ölü kalır. Periyodik host işlerini
  `~/.matrix/bin/`e kopyala, plist'i oraya işaret ettir, kurulumdan sonra `launchctl list` exit kodunu doğrula.
- `claude setup-token` uzun ömürlü token'ları bu hesapta 401 verdi → host keychain oturumu (refresh token'sız) saatlik
  volume senkronu (`scripts/claude_creds_sync.sh`). Token'ları asla loga/rapora yazma.
- `from __future__ import annotations` modülün ilk statement'ı olmalı (docstring hariç).

- **2026-09-20 — Hunk izolasyonunda blok sınırı: iki kez aynı fonksiyon.** Yabancı WIP taşıyan dosyalarda kendi
  değişikliğimi `HEAD` üzerine yeniden uygularken bloğu "benim başlangıcım → bir sonraki tanıdık fonksiyon" diye
  kestim; araya giren dört fonksiyon da bloğa dahil oldu ve staged blob'da **iki kez** tanımlandı (`allocation.py`:
  shrink_pair_edge / edge_multiplier / expected_value / risk_multiplier). Diff `0 deletion` gösterdiği için gözden
  kaçıyordu. Kural: staged blob'u commit'ten ÖNCE AST ile tara — top-level `def`/`class` adlarında tekrar varsa
  sınır yanlıştır. (Aynı ailedeki eski hata: sınırın *dışında* kalan tanımın hiç commit edilmemesi, 03e52ea.)

- **2026-09-20 — Backtest testleri canlı motorla aynı shared DB'yi paylaşıyor.** `test_open_skips_expired_predictions`
  deterministik olarak fail ediyordu: fixture'ın ürettiği prediction'ı, 5 saniyede bir tik atan **canlı** `matrix-backtest`
  konteyneri `expired` yapıyor. `docker compose stop backtest` ile aynı test anında geçiyor. Yani suite yeşil/kırmızı
  sinyali, o an motorun açık olup olmamasına bağlı — regresyon avı buraya kilitlenip saat yakabilir. Şimdilik kural:
  backtest suite'ini motor kapalıyken koş. Kalıcı çözüm test'lere ayrı şema/DB (açık madde).
