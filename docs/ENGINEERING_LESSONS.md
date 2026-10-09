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

- **2026-10-09 — Alarm kanalı, izlediği şeyle aynı arıza alanında olamaz.** 2026-09-30 07:27 UTC'de OrbStack VM'inin dışa
  bağlantısı sessizce öldü (host interneti sağlamdı; OrbStack hiçbir hata loglamadı). notify durumu dakikalar içinde gördü ve
  6.3 gün boyunca **783 alarm (145 URGENT) üretti, hiçbirini teslim edemedi** — Telegram'a da aynı ölü VM ağından gidiyordu.
  Stall watchdog hiçbir şeyi yeniden başlatmadı, çünkü servisler uyarı loglamaya devam etti; yalnız log tazeliğini ölçüyordu.
  Kural: sağlık sinyali *çıktıdan* (veri/fill yaşı) ölçülür, alarm ise izlenen sistemin dışından (host `stall_watchdog.sh`,
  curl ile Telegram) gider; container egress'i host egress'iyle karşılaştır. Teslim edilemeyen alarm sayılır ve kanal
  dönünce bir kez söylenir.
- **2026-10-09 — Laptop host = pil + login.** 10-06'da makine pilde ~19 %/saat boşaldı ve kapandı (Postgres "not properly
  shut down"); 10-09'da boot sonrası 3 saat login ekranında bekledi — LaunchAgent'lar ve OrbStack yalnız login'de başlar.
  `pmset -g batt` "Battery Power" = geri sayım; watchdog bunu alarmlar. Otomatik login kapalıyken güç kaybından sonra
  hiçbir şey kendiliğinden dönmez. Kesinti teşhisi: `sysctl kern.boottime`, `last`, `log show --predicate 'process ==
  "powerd"'` (Capacity/Source satırları), `~/.orbstack/log/vmgr*.log` (vmgr saatleri yerel, scon/agent UTC).
- **2026-10-09 — B-tree silmeyle küçülmez; kirli kapanış istatistiği sıfırlar.** Retention `market_trades` heap'ini 49 GB'tan
  5.2 GB'a indirdi, dört indeks 54 GiB kaldı (boş sayfa yeniden kullanılır, OS'e dönmez) → `REINDEX INDEX CONCURRENTLY`.
  Güç kaybından sonra `pg_stat_user_tables.last_autovacuum` NULL ve `n_live_tup` saçma görünür: istatistikler sıfırlanmıştır,
  "autovacuum hiç çalışmadı" kanıtı değildir; boyut için `pg_class.reltuples` / `pg_relation_size` kullan.

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
- **2026-10-09 — Giriş barının yaşını kontrol et.** Edge study "generated_at'ten önceki son bar"ı alıyordu; seride boşluk varsa
  (kesinti, günlerce agrege edilmemiş sembol) giriş saatler/günler öncesi oluyor, ufuk boşluğun üstünden stratejinin zaten
  gördüğü fiyatlara uzanıyordu: bist_volume_breakout'un 244 bayat sinyali +300 bps (hepsi TP), matrix_agent/crypto "confirmed"
  +44.8 — taze girişlerde −14 ve +6.6. Kural: replay'de giriş barı ≤ birkaç dakika yaşlı olmalı ve ufuk penceresinin duvar
  saati süresi ≈ ufuk olmalı; "çok iyi" bir sonuçta önce girişin bayatlık dağılımına bak.
- **2026-10-09 — Funding settlement'ta ödenir, ticker oranıyla değil.** Carry PnL'i `saat/8 × kapanıştaki oran` idi ve flip tek
  okumayla tetikleniyordu. Bybit her settlement'tan sonra ~1 dk `+0.0000125` yer tutucu yayınlar → 51/51 carry ilk
  settlement'ta "flip" ile kapandı, ~0 tahakkuk, %0 kazanç. Doğrusu: kesişilen her settlement'ta, ondan hemen önce yayınlanan
  oran (`backtest.carry_funding`); flip 5 dk süreklilik ister. Bir stratejide %0 kazanç = önce muhasebeyi şüphelen.
- **2026-10-09 — Bahis başına bir prediction.** Modüller koşul sürdükçe her tick aynı (sembol, yön)'ü yeniden yayıyordu
  (momentum_xs %83, oi_delta %75, funding_reversion %91). Satırlar kanıt değil: t-istatistiğini şişirir, aday havuzunu
  kopyayla doldurur. `strategy.persist.drop_reemissions` önceki prediction'ın ufku içindeki tekrarı düşürür.
- **2026-10-09 — Bir kesme kuralı slot vermez.** Ardışık-kayıp auto-cut `= 1` idi; başka kuralın 0'a çektiği stratejiyi her
  geçişte 1'e geri açıyordu — kitapta kalan tek iki strateji 30 ve 23 ardışık kayıplı carry'lerdi. Kesme `min(eski, 1)`.

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

- **2026-09-20 — Motoru uzun bir komutun ortasında durdurma.** Backtest testleri canlı motorla
  aynı DB'yi paylaştığı için `docker compose stop backtest && <testler> && docker compose start backtest`
  kalıbını kullanıyordum. Test koşusu takılınca komut arka plana düştü ve **motor 22 dakika kapalı kaldı**;
  fark etmem ancak log'da "shutdown signal received"den sonra sessizlik görmemle oldu. `docker compose ps`
  "running" diyordu çünkü konteyner ayaktaydı, içindeki süreç yoktu (aynı arıza şekli 16 Eylül'deki üç
  günlük kesinti). Kural: durdurma ve başlatma **ayrı, kısa** komutlar olsun; motoru kapatan her komuttan
  sonra ilk iş `docker compose ps` değil, **son log satırının tazeliğine** bakmak.

- **2026-09-20 — `EXPLAIN ANALYZE` planı görmek için değil, sorguyu KOŞMAK içindir.** 300M satırlık
  `market_trades` üzerinde "bu global sıralı süpürme indeksi kullanıyor mu" diye `EXPLAIN (ANALYZE)`
  çalıştırdım; indeks henüz geçerli olmadığı için planlayıcı Gather Merge sort seçti ve sorgu
  **24 dakika** koşup 6.18M buffer okudu — üstelik tam da beklediğim indeksin kurulumuyla yarışarak.
  Planı görmek için `ANALYZE` olmadan düz `EXPLAIN` yeter ve hiçbir şey çalıştırmaz. Büyük tabloda
  `ANALYZE`'ı yalnız sonucu kesin küçük olan (LIMIT'i indeksle dolacak) sorgularda kullan.

- **2026-09-20 — Canlı strateji adına bağlı test, o strateji emekli olunca kırılır.**
  `labs/tests/test_auto_apply_safe.py::test_safe_apply_takes_param_tune_for_grid` gerçek `grid`
  stratejisinin **aktif** config'i olmasına dayanıyor. Öğrenme döngüsü bugün grid'i kanıta göre
  kapattı (15:09 efficacy challenger'ı emekli etti, 17:45 slot scorer 1→0 çekti, 18:02'den sonra
  hiç sinyal yok) ve test kırıldı. Yani sistem doğru çalıştığı için test kırmızıya döndü.
  Kural: fixture'lar sentetik strateji id'si üretsin (`chal_*` gibi), canlı bir strateji adına
  bağlanmasın. İlgili: backtest suite'inin canlı motorla aynı DB'yi paylaşması.

- **2026-10-09 — Satır ≠ bağımsız örnek (sözde-tekrar).** Edge study `momentum_xs` için "+36 bps, t=6.04, n=799"
  dedi; 3 hafta boyunca kitabın tek kanıtlı edge'i sayıldı, slot ve promosyon kararları ona dayandı. Satırların %87'si
  2026-09-13'teki **tek bir 3 saatlik v1 patlamasıydı**: aynı 10 (symbol, side) her ~90 s yeniden üretilmiş (UAIUSDT short
  294 kez). Her satır bağımsız işlem sayıldı; FDR/BHY bunu düzeltemez çünkü sorun p-değerinde değil n'de. Bölüm (episode)
  bazında aynı strateji −10.7 bps (t=−0.98). Cüzdan da tam bunu ödedi: 2026-09-21'in 155 dolumu −30.7 bps, aynı sinyallerin
  simülasyonu −26.2 brüt — yürütme sinyali ~5 bps içinde iletti, kaybedilecek edge yoktu. Kural: bir istatistikte **birim,
  cüzdanın tutabildiği bahistir**; aynı (strateji, sembol, yön) ufku içinde tekrar ederse bir sayılır (`one_per_episode`).
  Bir "edge" bulunduğunda önce gün/saat ve (sembol, saat) kümesine göre dağılımına bak — tek kümeden geliyorsa yoktur.
  Ayrıca: bar `ts` başlangıçtır; sinyal anındaki barın kapanışı gelecektir (`entry_index`).

- **2026-10-09 — 1 dakikalık bar 15 saniyelik gecikmeyi ölçemez; ölçmeden önce verinin kendi gecikmesine bak.**
  "15 s vs 60 s vs 300 s" karar gecikmesini 1m barlarla simüle ederken "0 s" girişini *son kapanmış bar* aldım:
  oi_breakout 0→15 s arasında 15.6 bps, oi_delta 6.7 bps kaybediyor göründü (t≈2.4) — hızın para ettiği sonucu.
  Yapaydı: son kapanmış bar ortalama sinyalden 30 s *önce* kapanır ve sinyali tetikleyen hareketi içerir; kimsenin
  işlem yapamayacağı bir fiyat. Zamanı ortalanmış barla (t+d−30 s'yi içeren bar) aynı stratejiler gecikmeyle *iyileşti*.
  Kural: bar çözünürlüğünden küçük gecikmeleri yalnız (a) zamanı ortalanmış fiyatla ve (b) gecikmeler arası **eşli fark**
  olarak raporla; seviye değil. `entry_price_ref`'e de çapalama: dca ve BIST'te bar fiyatından ortalama +38…+314 bps
  uzak, sinyal anı fiyatı değil. Ve hızı tartışmadan önce `created_at − ts` ile verinin gecikmesine bak: BIST yfinance
  barları **~15 dk** geç yazılıyor (12:17 barı 12:32:37'de) — 15 s'lik döngü 900 s'lik veriyle karar veriyordu.
  Kaynak: `docs/wiki/market-cadence-study.md`.

- **2026-10-09 — Sözde-tekrar tek modülde kalmaz; bar boşluğu da sahte edge üretir.** `one_per_episode` yalnız edge study'ye
  girmişti; barrier/horizon/execution/meta çalışmaları, sertifika, efficacy ve slot scorer hâlâ satır sayıyordu
  (funding_reversion 3 101 fill = 473 bahis; meta-label matrix_agent/crypto için +107 bps "lift" üretti, bölümle AUC 0.52).
  Kural: "örnek" tanımı tek yerde (`edge_study.episode_groups`), her tüketici onu çağırır; yeni bir istatistik yazarken
  `grep _load_candidates\|outcomes` ile tüm tüketicileri tara. İkinci tuzak: `entry_index` son kapanmış barı alıyordu ama
  barın **yaşına** bakmıyordu — seride delik varsa giriş saatler öncesine düşüp ufuk deliğin üstünden geçiyordu
  (bist_volume_breakout +104 bps t=16.7 → −10.7). Bar tabanlı her simülasyonda giriş tazeliği + pencere bitişikliği şart
  (`MAX_ENTRY_AGE`, `MAX_BAR_GAP`). Üçüncü: tek-strateji koşusu m=1 ile düzeltiliyordu (BHY = çıplak p<0.05, DSR deflate
  edilmiyordu) — aile boyutu koşunun kapsamından değil kitaptan gelir. Ölçüm biriminin değiştiği bir ön-kayıt "taşınmaz"
  kuralını çiğnemeden geçersizdir: `superseded` işaretle, sil değil.

- **2026-10-09 — Artımlı sayaç örnek birimini dondurur; tanımlı ama denetlenmeyen sabit koruma değildir.** Lab, 2026-09-13'te
  (exp, symbol) başına tek açık değerlendirme kuralını aldı ama `lab_experiments.n_evaluations/n_wins/total_score` her skorda
  `+= 1` ile büyüyordu: kuraldan önceki satırların %88'i hâlâ açık bir bahsin kopyasıydı ve sayaçlarda kaldı. 09-13'te
  terfi eden 4bbc9b "152 değerlendirme, %85 kazanç, fitness 0.61" ile geçti; bölümle 20 bahis, %65, fitness 0.02 (bar 0.05).
  Terfi edilmiş on genomdan hiçbiri bölüm sayımıyla barı geçmiyor. Kural: karar veren bir istatistiği kaynak satırlardan
  ve tek tanımla (`edge_study.one_per_episode`) yeniden hesapla, sayaç artırma; yayım tarafını düzeltmek geçmiş agregayı
  düzeltmez (`labs.main --recompute-fitness`). İkinci: `ENTRY_FRESHNESS_S = 30` tanımlıydı, hiç okunmuyordu — kesinti
  sonrası giriş deliğin öncesindeki ticker, çıkış canlı işlemdi. Bir sabiti tanımlarken kullanıldığı satırı da `grep` ile
  gör. Üçüncü: `test_auto_apply_safe` konteyner içinde canlı paylaşılan DB'ye bağlanıp `apply_best_pending_safe()` ile
  **bütün** bekleyen önerileri tarıyordu; artık `LABS_TEST_SHARED_DSN` yoksa atlanır. Kaynak: `docs/wiki/edge-study.md`.

- **2026-10-09 — Fonlama tilt'i hedge'siz para kazandırmaz; carry hedge'li ve kısa bacağı yapılabilir olmalı.** 1 yıllık Bybit
  geçmişinde fonlama kesit L/S (H2/H3) dönem başına +120…+300 bps fonlama topluyor, fiyat bacağı hepsini geri veriyor (holdout
  brüt −148). Aynı fonlamayı spot short ile hedge'leyen negatif-fonlama carry'si holdout'ta +135 bps/epizod (t=10.2, n=1 105,
  borç ve 30 bps dahil). Ama inverse_carry'nin Eylül sinyallerinin çoğu Bybit'te spot marjini OLMAYAN coin'lerdeydi: kısa
  bacak açılamıyorsa carry çıplak long perp'tür. Kural: carry sinyali üretmeden önce borç tablosunda (Bybit/Binance public)
  coin'i ara; borç maliyetini model'e koy. Ayrıca `entry_price_ref`'i modül değil dispatcher damgalar (dca 3,3 günlük trade
  fiyatı yazıyordu) ve paper engine aynı (strateji, sembol, yön) bahsini ikinci kez açmaz (funding_reversion 2 016/3 101).
- **2026-10-09 — Yalnız `shadow` satırı olan strateji hiç koşmuyordu.** Dispatcher aktif satırı olmayan stratejiyi atlıyordu,
  shadow satırına hiç bakmadan; yeni bir sinyali "shadow olarak kaydet" demek sessizce hiçbir şey yapmamaktı. Artık shadow-only
  strateji shadow cüzdanında challenger olarak koşar; şampiyonu olmadığı için efficacy onu terfi ettiremez — statü kanıttan gelir.
- **2026-10-09 — Karar sürücülerinin hepsi bölüm saymalı; "onaylandı" yolu da yeniden puanlamalı.** Ders ve yansıma katmanı
  satır sayıyordu: `agent_lessons` kovaları ve `_confidence`'in binom z'si, reflection `metrics_window` (mutasyon kapısı),
  setup memory kNN komşuları (Wilson), `load_pair_edges` (EV/boyut). 27 tarihsel dersin 6'sı ≥0.4 güvenle doğmuştu
  (0.46–0.59); kendi pencerelerinde bölümle 0.00–0.14, 23'ünün hükmü kalmıyor. Eylül günlük pencerelerinde mutasyon
  kapısı 152 strateji-günün 18'inde kapanıyor (momentum_xs 09-14: 95 satır = 8 bahis). İkinci tuzak: `_touch_confirmed`
  yeniden onaylanan dersin `n`'ini güncelliyor ama güvenini doğduğu örnekte bırakıyordu; birim değişince eski güven
  sonsuza kadar yaşardı. Kural: bir istatistiği tazeleyen her yol (confirm/touch/upsert) türetilmiş skoru da yeniden
  hesaplar; satır sayan yeni tüketici yazma, `edge_study.episode_summary` kullan.

- **2026-10-09 — "Mod kapalı" bayrağı maliyeti kapatmıyordu; ve farklı haftaları kıyaslamak rejimi ölçer.**
  `MATRIX_LLM_BLEND_MODE=rule_only` A/B kontrol kolu olarak yazılmıştı ama `decide_batch` LLM'i yine çağırıyor, yalnız
  cevabı atıyordu: kontrol kolu tedavi kolunun bütün maliyetini ödüyordu. Kural: bir özelliği "kapatan" her bayrak, o
  özelliğin *maliyetli çağrısından önce* kontrol edilsin ve bunun testi çağrının hiç yapılmadığını assert etsin.
  İkinci tuzak ölçümde: LLM-yönlü işlemler −0.9 net, kural-yalnız işlemler −13.5 — "LLM 12.6 bps kazandırıyor"
  sonucuna çok yakındı. Kural kolunun %95'i 09-12/13'ten, LLM kolu 09-13…09-30'dan; aynı saatlerde LLM'in reddettiği
  ε-probları +13.9 brüt, aldıkları +11.7 (t=−0.19), aynı girişte kuralın eğilimi yönüne göre fark −2.4 (t=−0.4).
  Kural: bir karar bileşeninin değerini **aynı saatlerde** ölç — eşli karşı-olgu (aynı giriş, öbür kolun yönü) veya
  aynı dönemde onun reddettikleri; dönemler arası fark önce rejimdir. Kaynak: `docs/wiki/llm-value-audit.md`.
