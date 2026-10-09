# Cross-cutting Changes Log

> Append-only. Each entry: date + machine + one-line description of a cross-cutting change.
> Read this on every pull. If you're about to make a change that affects both Python and TS sides, add an entry here BEFORE coding.

---

## 2026-10-09 — İki bacaklı carry yürütme yolu (`CarryExecutor`), varsayılan dry-run; paper carry'lerin dry-run aynası
- `matrix_shared.carry_executor` + `matrix_shared.carry_venues` (execution facade: `execution.carry`): ödünç → spot sat → perp al (kapanış: spot al → perp sat → geri öde), Limit-IOC fiyat bandı, ikinci bacak düşerse ilk bacak hemen geri alınır, geri alma da düşerse alarm + kill switch (süreç kilidi + cüzdanın `circuit_tripped_at`'i). Her emir `live_gate.should_submit_live`'dan **iki bacağın toplam notional'ıyla** geçer → canlı bacak = paper bacağının yarısı. Modlar: `MATRIX_CARRY_EXEC_MODE=dry_run` (varsayılan, `NeverSend`) | `testnet` (yalnız `api-testnet.bybit.com`; Binance margin'in testnet'i yok → reddedilir). Mainnet modu yok.
- `backtest.paper_trade`: book-priced carry açılış/kapanışından sonra `mirror_paper_open/close` (hep dry-run, 5 s, asla raise etmez) → log satırı `carry_exec[dry_run] …` + `predictions.context.carry_exec_dry_run.{open,close}` (bacak dolumları, `gap_bps`). Kapatma: `MATRIX_CARRY_MIRROR=false`.
- dev_agent `FORBIDDEN_PATHS` += `live_gate.py`, `carry_executor.py`, `carry_venues.py`. Tasarım: `docs/wiki/carry-execution.md`.

## 2026-10-09 — r5f ileri testi: Deribit IV term kaydedici (0043), `iv_inversion` shadow modülü, harness forward modu
- `matrix_shared.research`: `Spec(forward_n=N)` = ileri test (train yok; `forward_status` yalnız sayar, `open_forward` ilk N epizot kapanınca bir kez karar verir; ledger forward testte train satırını, ikinci açılışı, karardan önce final'i reddeder). Klasik spec'lerin hash'i değişmedi (`forward_n` yalnız >0 iken spec bloğunda).
- `matrix_shared.iv_term`: tur 5 D.3 kuralının tek tanımı (TERM, 365 g persentil, pencere başına tek epizot); kaydedici, modül ve r5f builder aynı kodu koşar. Tur 5'in 118 girişini birebir üretir.
- Göç **0043** `deribit_iv_daily` (lokal): `ingestion.iv_term_recorder` (ingestion-market, 15 dk) her gün 00:02 UTC'den sonra [D−4h, D) opsiyon işlemlerinden satır yazar, son 400 günü backfill eder, boşlukları doldurur (`IV_TERM_RECORDER_ENABLED`).
- `strategy_configs` (shared): `iv_inversion` crypto v1 **shadow**, params'ta `shadow_band` (+166…+201, floor 0, n 60; karar r5f harness'ında n=60'ta). `DEFAULT_BANDS`'e de eklendi. Ön-kayıt d283c9a, ledger m = 172.

## 2026-10-09 — İnce kitapta carry kapanışı cezalı fiyatlanır (risk kapısı markı); eski fonlama muhasebesi kanıt değil; shadow alarmları teslimde işaretlenir
- `backtest.carry_books.close_cost_bps` (ve açık carry equity markı `mark_cost_bps`): kitap VAR ama bacağı dolduramayacak kadar ince ise (`walk_bps` None — sıkışma durumu) artık açılış tahminine düşmez; görünen derinlik yürünür, kalan kısım en kötü seviye + `MATRIX_CARRY_THIN_BOOK_PENALTY_BPS` (50) ile, tahminin altına inmeden ödenir; `book_close.close_source=thin_book`. Günlük zarar devre kesicisinin okuduğu mark bu durumda daha düşük (daha erken tetikler). Kitap hiç yoksa davranış aynı (`entry_estimate`).
- `edge_study._load_carry_fills`: 2026-10-09 13:39:14 UTC'den (per-settlement fonlama 7d7b854 ile canlıya girdi) önce kapanmış carry'ler kanıt değil (`MATRIX_EDGE_CARRY_EVIDENCE_SINCE`); stratejinin daha geç band `since`'i önceliklidir. inverse_carry'nin 70 placeholder-flip kapanışı kanıttan çıktı.
- `shadow_tracker`: sıfır borç kotası ya da tamamı sıfır kayıtlı seri `borrow_not_charged` / `broken` değil; eksik alan ya da pozitif kotada sıfır ücret hâlâ broken. notify shadow alarmları (verdict, `review_due`) yalnız Telegram teslimi onaylanınca işaretlenir, yoksa sonraki shadow tick'inde tekrar; durum göç **0042** `notify_alert_state` (lokal DB), `/tmp` değil.

## 2026-10-09 — Edge study: dürüst giriş kuralı; carry'ler gerçekleşmiş epizodlarla `confirmed` olabilir; venue-sabit okuyucular
- `edge_study.entry_index` / `simulate_bracket`: her kol (tedavi + iki kontrol) `generated_at + MATRIX_EDGE_ENTRY_LATENCY_S` (4 s) anında veya sonrasında başlayan İLK 1m barın **açılışında** girer ve o bardan itibaren skorlanır; sinyal öncesi hiçbir yol skora girmez. Aynı kural barrier/horizon/execution/meta-label çalışmalarında (`entry_price`). 30 günlük önce/sonra: `docs/wiki/edge-study.md` "Correction — pre-signal path". Hiçbir `status` değişmedi.
- Carry kanıt yolu: `edge_study.carry_edge_rows` + ortak `apply_promotion_bar` — CARRY_SIDES pozisyonlarının KAPANMIŞ paper epizodlarının gerçekleşmiş net bps'i (90 g, `MATRIX_EDGE_CARRY_DAYS`), sıfıra karşı gün-kümeli t (≥20 gün, `MATRIX_EDGE_CARRY_MIN_DAYS`), aynı BHY/DSR/ön-kayıtlı n; satır `kind: carry`, `net_of_costs: true` (`verdict` maliyeti ikinci kez düşmez). `_promotion_confirmed` değişmeden okur. Açık: `paper_trade._kelly_fractions` carry satırında round-trip'i yine düşüyor (muhafazakâr).
- `regime.py` funding, `labs.evaluate.fresh_symbols`, `backtest.replayers.matrix_agent` ticker/book: `exchange='bybit'` sabit.

## 2026-10-09 — Borç faizi kaydediliyor; carry kapanışı borcu kayıtlı seriden öder
- Göç **0041** `margin_borrow_rates` (lokal DB): `venue, coin, ts, hourly_rate, max_borrow, borrowable`. `ingestion.borrow_recorder` (`ingestion-market` içinde) Bybit spot-margin VIP0 + Binance cross-margin VIP0 public tablolarını 10 dk'da bir okur, TÜM coin'leri yazar (değişince veya saatte bir nabız); 180 gün tutar, saatlik silme `ts` indeksiyle. `BORROW_RECORDER_ENABLED=false` kapatır.
- `paper_trade` carry kapanışı ve açık carry equity işareti (ortak `backtest.carry_books.carry_borrow`): borç her başlamış saat için o saatin kayıtlı oranıyla ×1; serinin kaçırdığı saatler giriş kotası × `MATRIX_CARRY_BORROW_STRESS` (3). `context.borrow_source` = `series|stressed_entry|mixed`.
- `neg_funding_carry` giriş filtresi: borç = max(kota, fonlama derinliğinin kesitsel medyanı) × `MATRIX_NFC_BORROW_HOLD_STRESS` (1.0); eski kota × 3 `MATRIX_NFC_BORROW_MODEL=flat` ile, eski karar her sinyalde `flat_keep`. 1/3 kuralı aynı. Detay: `docs/wiki/signal-research-2026-10.md` "Borrow measurement".

## 2026-10-09 — Açık carry'nin equity markı kapanışın realize edeceğinden yüksek olamaz; kapanış döngüsü REST beklemez (risk kapısı davranışı)
- `backtest.paper_trade._current_equity`: açık carry artık `hours/8 × canlı oran` (maliyetsiz) değil, şimdi kapansa realize edeceği değerle işaretlenir: o ana kadar ödenen settlement fonlaması (`carry_funding`, pozisyon başına artımlı önbellek) − tam gidiş-dönüş maliyeti (book-priced: 4 taker ücreti + açılışta ödenen 2 yürüyüş + kapanış yürüyüşleri, her biri DB kitabı ile açılış tahmininin KÖTÜSÜ; diğerleri: 2 bacak round-trip) − tahakkuk eden borç (kapanışla aynı seri/stres girdileri, saatte bir yeniden hesap). Günlük zarar devre kesicisi ve trailing stop bu equity'yi okur → $500'lük açık carry başına ~$3–5 daha erken tetiklenir; 1/2/4 saatlik fonlama aralıklı coin'ler de doğru işaretlenir. Tick'te REST yok, 48 saatlik tarama yok.
- `close_due_positions`: book-priced carry'ler en sona kalır; bacak kitapları önce DB'den, eksikler REST'ten eşzamanlı ve toplam `MATRIX_CARRY_CLOSE_BOOK_TIMEOUT_S` (3 s) içinde; cevap gelmeyen bacak açılış tahminiyle kapanır (`book_close.close_source=entry_estimate`). `_close_position` kendisi hiç REST çağırmaz (flatten dahil). Yön TP/SL kapanışları carry kitabını beklemez.

## 2026-10-09 — Öğrenme döngüsü seçicileri sıfır-edge null'a karşı kalibre edildi (labs, mutasyon, challenger, ε)
- `labs`: evrim artık ham fitness'a göre n ≥ 5'te sıralayıp üretmiyor. Sıralama = aynı UTC saatindeki diğer genomlara göre fazlanın (excess) empirical-Bayes posterior ortalaması (son 7 günün genomları, DerSimonian–Laird τ²); üreme ≥ `MATRIX_LAB_MIN_BREED_EPISODES` (20) bölüm, < 2 aday → rastgele göçmen; terfi önceden kayıtlı bakışlarda (ilk 30/60/120/240 bölüm), ≥ 3 gün, ham ortalama ≥ 0.05, gün-kümeli %95 alt sınır > 0 ve posterior excess alt sınır > 0. Yeni paylaşılan `matrix_shared/evidence.py` (t sınırları, EB). Sıfır-edge simülasyonu (`python -m labs.zero_edge_sim`): yanlış terfi 18.3/30 gün → 0.04. `lab_promotion.metrics_window.fitness_score` artık test edilen ortalama.
- `reflection`: `_underperforming` = n ≥ 10 VE bölüm başına net USD'nin tek yönlü %95 t üst sınırı < 0 (eskiden toplam < 0 veya skor < tetik; sıfır edge'de ~%50 tetikleniyordu, şimdi ~%5). `--score-trigger` yok sayılır. Metrikler ve efficacy örneği ε-probe'ları (`context.is_exploration`) dışlar. Challenger cutover `MATRIX_EFFICACY_Z_POS` 1.0 → 1.645.
- `agent`: ε bütçesi bilgi değerine göre: aktif, güvenli (≥ 0.4), operatör-olmayan `avoid` dersinin kapsadığı hücrede ε × `MATRIX_EXPLORE_CORRIDOR_MULT` (3), başka her yerde ε × `MATRIX_EXPLORE_MAINTENANCE_SHARE` (0.33) × sembol-edge ölçeği; probe'lar `explore_cell` taşır. Bugün aktif ders yok → probe hacmi ~⅓. Detay: `docs/wiki/learning-loop-statistics.md`.

## 2026-10-09 — Testler tek komutla koşar; DB testleri canlı tabloya commit edemez
- `make test-all` (`scripts/test_all.sh`): 16 Python suite'i kendi imajında + web typecheck, özet tablo; backtest suite'i için canlı `backtest` konteyneri durdurulur, trap ile her durumda başlatılır. Yeni `matrix_shared.testing.db_writes_rolled_back()`: director/reflection/strategy/execution/labs conftest'lerinde her async test dış transaction içinde koşar ve rollback'lenir (global slot puanlama / sertifika iptali / dev_tasks yazımı canlıya değmez). `openrouter_llm`: compose'un boş geçirdiği `MATRIX_OPENROUTER_MODEL_*`/`_FALLBACKS` artık default'a düşer (önceden model `""`). `reflection.main.sweep_stale_proposals()` `_tick`'ten ayrıldı (davranış aynı). Detay: `docs/wiki/operations.md` "Tests".

## 2026-10-09 — Kripto akış evreni = işlem evreni + carry watchlist (spot bacaklarıyla)
- `ingestion-market` saatte bir (:40) `tradable_symbols` asset_class `crypto_carry`'ye ≤ 20 borçlanabilir negatif-fonlamalı perp yazar (min(son settle, tahmin) ≤ −0.05 %, histerezis, açık `inverse_carry` sabitlenir). Ingestion ve bars-aggregator `crypto_ingest_universe_async()` (işlem evreni ∪ watchlist) okur; stratejiler `crypto_universe_async()`'te kalır. Her coin'in spot bacağı Bybit spot / Binance spot'tan akar: ticker + book `exchange='bybit-spot'|'binance-spot'` (perp ile aynı sembol), 1m bar `market_bars` asset_class `crypto_spot`. Abonelikler canlı sokette değişir (yeniden bağlanma yok). `symbol_costs` artık yalnız `exchange='bybit'` kitaplarını ölçer; retention anahtarlarına `crypto_spot` bar sembolleri eklendi. Exchange filtresiz ticker/book okuyan yeni kod watchlist coin'lerinde spot satırı görebilir. Detay: `docs/wiki/operations.md` "Crypto universe".

## 2026-10-09 — Agent kural-yalnız karar verir; graph LLM'siz çıkarır
- `agent`: `MATRIX_LLM_BLEND_MODE=rule_only` (compose varsayılanı). `rule_only` artık LLM çağrısını hiç yapmaz (önceden çağırıp cevabı atıyordu). Sebep: `docs/wiki/llm-value-audit.md` — 251 kripto bölümde LLM'in yönü kuralın kendi eğiliminden iyi değil (−2.4 bps, t=−0.4), aldığı işlemler aynı saatlerde reddettiklerinden iyi değil (−2.1, t=−0.19); LLM-yönlü net −0.9 bps (t=−0.16). Geri almak: `MATRIX_LLM_BLEND_MODE=blend`.
- `graph`: `GRAPH_EXTRACT_MODE=heuristic` (yeni anahtar, compose varsayılanı) — ajan/LLM çıkarımı yok, anahtar kelime çıkarıcı; `GRAPH_BACKFILL_ENABLED=false`. Graph kapsamı olan kararlar olmayanlardan iyi değil (her kolda ≤); haber skorunun yönü çevirdiği 14 bölüm +5.0 (t=0.14). Geri almak: `GRAPH_EXTRACT_MODE=llm`, `GRAPH_BACKFILL_ENABLED=true`.

## 2026-10-09 — Dersler, mutasyon kapısı, setup memory, pair edge ve Telegram win rate bölüm sayar
- `agent_lessons`: kovalar, `_confidence` (binom z) ve koridor emekliliği `edge_study.episode_summary` ile bölüm başına; yeniden onaylanan dersin güveni de taze örnekle yeniden yazılır. `reflection.metrics_window` (`_underperforming`, param_tune yönü) ve LLM'in `recent_outcomes` aracı bölüm başına; öneri snapshot'ı `n_raw` + `unit=episode` taşır. `setup_memory` komşuları ve `allocation.load_pair_edges` (EV sıralaması + `risk_multiplier`) bölüm başına. notify 24h win rate bölüm başına (yalnız gösterim). Satır sayan yeni tüketici yazma; `episode_summary` kullan.

## 2026-10-09 — Lab fitness, sembol edge'i, Director ve dashboard bölüm (episode) sayar
- `labs`: `n_evaluations / n_wins / total_score / fitness_score` artık skorlanmış satırlardan `edge_study.one_per_episode` ile yeniden hesaplanır (artımlı sayaç yok); `labs.main --recompute-fitness` aktif+terfi etmiş 51 deneyi yeniden skorladı. Terfi taraması bölüm sayımıyla tekrar kontrol eder, `metrics_window`'a `n/n_raw/n_unscorable/unit` yazar. `ENTRY_FRESHNESS_S` (60 s) artık uygulanıyor. `universe._edge_map` sembol edge'ini bölüm başına hesaplar (paper engine EV sıralaması bunu okur).
- Director digest + `strategy_pnl` aracı, web `/api/dashboard` `strategyAgg` ve `/api/public/stats`: n ve win rate bölüm başına, `n_raw` (fill) ve `n_unscorable` (orphan flat-close) yanında. Web, Python tanımının TS aynasını kullanır (`apps/web/src/lib/episodes.ts`) — ikisi birlikte değişmeli. `public/stats` artık orphan flat-close'ları saymaz.

## 2026-10-09 — BIST ve US durduruldu; agent karar aralığı 15 s → 60 s
- `strategy_configs`: 5 BIST + 1 US satırı `active` → `paused` (geri almak: aynı id'lerde `status='active'`). Ingestion yalnız `--markets crypto` (compose base + dev). Sebep: `docs/wiki/market-cadence-study.md` — hiçbir BIST/US stratejisi rastgele girişi t≥2 ile yenmiyor, BIST round trip 40 bps, veri ~15 dk gecikmeli, yfinance hataları ingestion logunun %68'i.
- `agent.main` `--interval 60` (`DEFAULT_INTERVAL_S`): aynı bahisler 60 s gecikmeyle 15 s'den 1.4–2.5 bps iyi (t=2.4–4.3); hızın getirisi yok, LLM rate bütçesi rahatlar. Paper engine'in 5 s çıkış monitörü değişmedi.

## 2026-10-09 — Alarmlar host'tan da gider; notify teslim edilemeyeni sayar ve fill'siz günü alarmlar
- `scripts/stall_watchdog.sh` (launchd, 5 dk) artık yalnız sessiz servisi restart etmiyor: pilde çalışma, watchdog boşluğu/reboot, Docker erişilemez (OrbStack kapalıysa başlatır), container egress ölü/host sağlam, BTCUSDT ticker > 30 dk, host disk < 30 GiB için Telegram'a **host'tan** (curl) yazar. Sebep: 2026-09-30→10-06 VM egress'i öldü, notify 783 alarm üretip hiçbirini teslim edemedi; 10-06'da pil bitti, kimse duymadı. `make watchdog-install` iki Telegram anahtarını `~/Library/Application Support/matrix/watchdog.env`'e (0600) kopyalar; `make watchdog-test`. `MATRIX_WATCHDOG_HEAL_EGRESS=1` → 30 dk ölü egress'te `orb restart docker` (vars. kapalı).
- notify: teslim edilemeyen alarmlar sayılır, kanal dönünce tek mesajla bildirilir; `no_fills` dedektörü (24 saat hiç paper pozisyon açılmadı, `MATRIX_HEALTH_FILL_STALE_S`); ticker probu `exchange='bybit'`'e pinli.

## 2026-10-09 — Strateji servisi bahis başına bir prediction yazar; funding settlement'ta işlenir
- `strategy.persist`: aynı (strateji, market, sembol, yön, şampiyon|shadow) için önceki prediction'ın ufku dolmadan gelen draft yazılmaz (tüm modüller etkilenir; 30 günde oi_delta satırlarının %75'i, momentum_xs'in %83'ü tekrardı). Edge study'nin `one_per_episode`'u ile aynı birim.
- Paper engine (çalışma ağacında, carry WIP'iyle birlikte): carry ve yönlü kripto funding'i kesişilen settlement'larda, o anki oranla (`backtest.carry_funding`); funding flip 5 dk süreklilik ister. Bu tarihten önceki carry outcome'ları kanıt değil.
- `reflection.slot_scorer`: ardışık-kayıp kesmesi `min(eski, 1)` — 0 slotlu stratejiye slot açmaz.

## 2026-09-20 — Berabere biten challenger slotu bırakır; ufuk değişikliği devrede
- Her stratejinin tek challenger slotu var ve sıra önceliği yoktu: kanıtsız bir vol_filter tweak'i (106 sonuç sonrası |z|=0.08, yani ölçülmüş FARK YOK) t=3.59'luk ufuk değişikliğini bir hafta daha bekletiyordu. `challenger_verdict`'e eşdeğerlik kuralı eklendi: her iki taraf da `INDIFFERENCE_MIN_N` (100) örneğe ulaşmış ve |z| < 0.5 ise challenger emekli edilir — beraberlik de bir cevaptır, slot sıradaki öneriye daha çok yarar.
- Canlıda zincir tamamlandı: v3 emekli → labs bekleyen öneriyi uyguladı → **momentum_xs v4 (horizon_s 5400) shadow olarak koşuyor**, şampiyon v2'ye (3600) karşı. Efficacy gerçekleşmiş PnL ile karar verecek. Ölçüm → öneri → challenger → karar döngüsü ilk kez uçtan uca kanıtla işledi.

## 2026-09-20 — Sembol bazlı ölçülmüş işlem maliyeti (düz 2 bps slippage kalktı)
- `matrix_shared/symbol_costs.py`: kendi order book snapshot'larımızdan sembol başına medyan spread ölçülür, geçiş maliyeti spread'in yarısı olarak alınır (`MIN_SLIPPAGE_BPS`=0.5, `MAX`=25 kıskacı), paylaşılan volume'da JSON'a yazılır, reflection tick'inde tazelenir. Ölçülmemiş sembol düz değere düşer — model yalnız iyileştirir, hiçbir kapıyı bozmaz.
- Ölçüm (3 saat, 24 sembol): medyan spread 1.16 bps (UNIUSDT) … 5.95 bps (BRUSDT), 5 kat fark. Gidiş-dönüş maliyeti artık BTCUSDT 12.0 bps, ADAUSDT 15.5, BRUSDT 16.9 — eski düz 15 bps majorlarda fazla sert, illikit altcoin'lerde fazla cömertti ve EV tabanını iki yönde de bozuyordu.

## 2026-09-20 — Git'te gizli kalmış iki tanımsız referans düzeltildi
- Ortak ağaçta başka yazarın commit'lenmemiş değişiklikleri olan dosyalarda kendi hunk'larımı elle stage ederken iki tanım git'e girmemişti: `slot_scorer._entry_edge_verdict` (bugün) ve `paper_trade._BAR_PRICE_CLASSES` (572966c, bir haftadır). Çalışan ağaç tanımları taşıdığı için canlı sistem hiç patlamadı; yalnızca temiz bir checkout'ta NameError verirlerdi.
- Dokunduğum her dosya için AST taraması yapıldı (çağrılan fonksiyon + büyük harfli sabit adları, tanım/atama/import/builtins kümesine karşı). İkisi de düzeltildi, kalan dosyalar temiz.

## 2026-09-20 — Sermaye kanıta göre dağıtılıyor: momentum_xs 1 → 7 slot
- Slot scorer sadece gerçekleşmiş PnL'e (perf_score) bakıyordu; bu bir dikiz aynası. momentum_xs taban olan 1 slotta oturuyordu çünkü geçmiş dolumları ufkunun yarısı geçtikten sonra açılmıştı — oysa kontrollü çalışma girişlerini rastgele zamana karşı +31 bps, rastgele yöne karşı +33 bps üstün buluyor. Artık bir null'ı maliyetin üstünde yenen strateji cüzdanın tam payını (base_share) alıyor. Canlıda uygulandı: momentum_xs 1 → 7.
- Kitap şimdi: momentum_xs 7, screener_follow 6, cash_and_carry 3, oi_delta 3, inverse_carry 1, xexch_funding_arb 1; hiçbir null'ı yenemeyen 5 strateji 0 slotta.

## 2026-09-20 — Telegram canlı
- Bot @Khybortbot doğrulandı (token'da fazladan bir karakter vardı, temizlendi), operatörün chat_id'si allowlist'e alındı, onay mesajı gönderildi. Uyarılar artık loga değil telefona düşüyor: paper engine durması, sinyal/ingestion bayatlaması, devre kesici, sertifika, günlük LLM bütçesi, dev_agent görevleri. Komutlar: /status /strategies /circuit /circuit_reset /dev_tasks /dev_accept /dev_discard /dev_revise, serbest metin → Brain.
- Not: chat_id'yi `getUpdates` ile yakalamaya çalışan yardımcı izleyici hiçbir şey görmedi çünkü notify'ın kendi long-polling'i update'leri tüketiyor. Doğru kaynak notify'ın kendi logu: "ignoring message from unauthorized chat_id=...".

## 2026-09-20 — Alfa bozunma (ufuk) çalışması: momentum_xs 60 → 90 dk önerildi
- `make horizon-report`: her sinyalin AYNI sembolde rastgele girişe göre FAZLA getirisi 1…120 dk ufuklarında ölçülür (sürüklenme çıkarılmadan bakmak yükselen piyasada her long'u alfa gibi gösteriyordu; sembol başına 40 rastgele çekilişle drift tahmin edilip çıkarılıyor), maliyet düşülür, her ufuk için t verilir.
- Tek belirleyici sonuç: `momentum_xs` 90 dk'da +37.0 bps (t=3.59), mevcut 60 dk'da −26.8. Diğerlerinin "daha iyi" ufku kendi gürültüsünü aşmıyor (t<2) — özellikle uzun ufuklarda varyans devasa olduğu için nokta tahmini her zaman uzun ufku seçer; rapor bunu `act` sütunuyla açıkça ayırır.
- Değişiklik doğrudan uygulanmadı: sistemin kendi hattından `param_tune` önerisi açıldı (horizon_s 3600 → 5400, kanıt metrics_window'da). momentum_xs'in halihazırda bir challenger'ı (v3) olduğu için öneri sırada bekliyor — "aynı anda tek challenger" kuralı. Efficacy v3'ü kapattığında uygulanacak.

## 2026-09-20 — Post-only giriş testi: genel kazanç var ama EDGE'İ OLAN stratejide yok
- `matrix_shared/execution_study.py` + `make execution-report`: her sinyal için sinyal barının kapanışına limit konur, `WAIT` bar beklenir, dolarsa maker (1 bps) doldu sayılır, dolmazsa piyasadan geçilir (taker). Bracket kalan ufukla oynatılır; bekleme süresi ufuktan düşülür.
- Sonuç (14 g, 1 bar bekleme): dolum oranı %82–91. Çoğu stratejide +2…+16 bps kazanç. **Ama `momentum_xs`'te −2.9 bps (t=−0.35)** — momentum sinyalinde limit emri ancak fiyat geri geldiğinde doluyor, yani tam da momentum bozulduğunda: ters seçim. Kabul kriteri (net edge ≥ +10 bps) sağlanmadı, bu yüzden **uygulanmadı**.
- Ders: post-only bir "bedava 6 bps" değil, strateji tipine bağlı. Ortalamaya dönen kurgularda kazandırır, momentumda kaybettirir. Edge'i olan tek stratejide geçiş (taker) doğru karar.

## 2026-09-20 — Edge testine ikinci null: rastgele ZAMAN yetmez, rastgele YÖN de gerekiyor
- Metodolojik hata düzeltildi (dış araştırma ajanının tespiti): test yönü sabit tutup zamanı rastgeleliyordu, yani yalnız zamanlamayı sınıyordu. Bir strateji yön bilgisine sahip olup zamanlama edge'i olmayabilir (veya tersi); tek null'a bakıp strateji silmek yanlış karar riski taşıyordu. Artık iki aile test ediliyor: (a) rastgele giriş zamanı, aynı yön; (b) aynı an, rastgele yön. Her ikisine de BH/FDR uygulanıyor.
- Sonuç (14 g, 5 277 sinyal): `momentum_xs` her iki null'ı da yeniyor — zamana karşı +31.2 bps (t=5.25), yöne karşı +33.0 bps (t=5.55). `bist_news_event` de yeniyor ama n=33. Diğer 11'i hiçbir null'ı yenmiyor. Ayrıca `oi_breakout` (−14.9, t=−2.46) ve `dca` (−5.8, t=−2.56) yön olarak yazı-turadan KÖTÜ — ters çalışıyorlar.
- Sermaye kapısı (`verdict`) artık iki null'a birden bakıyor: bir null'ı maliyetin üstünde yenen korunur, herhangi birinde anlamlı negatif olan kitaptan çıkar.

## 2026-09-20 — Vol-ölçekli bare stratejinin ödeme oranını korur (simetrik bare kitabı durdurmuştu)
- İlk sürüm tp=sl=m·σ_h veriyordu. Simetrik bir bare EV tabanını ancak %60 isabetle geçer; kitap 40 dakika boyunca hiç pozisyon açmadı. `preserve_ratio`: volatilite ÖLÇEĞİ belirler (stop gürültü bandının dışına), tp:sl oranı stratejinin tezidir ve korunur ([0.5, 4] kıskacıyla). Örnek: grid 0.024/0.015 → 0.0024/0.0015 (oran 1.6 sabit).
- Bunun ortaya çıkardığı gerçek: eski EV hesabı ULAŞILAMAZ barelerle şişiyordu (8.8σ'lık bir tp, conf×tp olarak EV'ye tam puan veriyordu). Gerçekçi barelerle EV tabanı kitabın çoğunu doğru şekilde reddediyor — çünkü edge yok. Öğrenme durmuyor: sinyal bazlı çalışmalar ve virtual outcome'lar dolum gerektirmiyor.

## 2026-09-20 — Meta-labeling uygulandı ve ÖLÇÜLDÜ: bu kitapta işe yaramıyor
- `matrix_shared/meta_label.py` + `make meta-report`: birincil model yönü, ikincil model *bu sinyale girilsin mi* sorusunu cevaplar (López de Prado). Etiketler triple-barrier'dan (dolum gerekmez), feature'lar prediction'ın kendi snapshot'ından; L2 lojistik regresyon, zaman sıralı bölme + bir ufukluk embargo, eşik YALNIZ train dağılımından.
- Sonuç (14 g, örneklem dışı): AUC 0.43–0.54, en iyi lift +2.9 bps. Yani mevcut feature'lar hangi sinyalin tutacağını bilmiyor. `momentum_xs` ve `oi_breakout` için lift negatif. Model diske yazma barı sıkıldı (lift ≥ 5 bps ve AUC ≥ 0.55); ilk koşuda kaydedilen 3 gürültü modeli silindi. Sermaye kapısına BAĞLANMADI — kanıt yok.
- Director'a `quant_research` (edge | barrier | meta) read-only tool'u eklendi: saatlik ajan artık "neden kaybediyor" sorusunu PnL'e bakarak değil kontrollü çalışmayla cevaplayıp ona göre dev task açabiliyor / stratejiyi emekli edebiliyor.

## 2026-09-20 — Çoklu test düzeltmesi (Benjamini-Hochberg) edge raporuna eklendi
- 13 stratejiyi aynı anda test etmek, düzeltmesiz %5 eşikte her koşuda ~1 sahte keşif demek (Bailey & López de Prado, Deflated Sharpe). Edge raporu artık iki yanlı p hesaplıyor ve FDR %5'te BH uyguluyor: **13'ten 2'si** hayatta kalıyor — `momentum_xs` (p<0.0001, edge +30.8 bps) ve `bist_news_event` (n=33, sermaye ayırmak için çok ince). Yani kitapta gerçekten kanıtlanmış tek edge momentum_xs.

## 2026-09-20 — Vol-ölçekli triple barrier (López de Prado) uygulandı
- `make barrier-report`: her geçmiş sinyal, bareler m·σ_h olacak şekilde yeniden oynatılır (σ_h = 1m getirilerin std'si × √ufuk). Ölçüm: mevcut take-profit'ler ufuk volatilitesinin **8.8σ** (grid), 6.8σ (matrix_agent/crypto), 6.3σ (matrix_agent/us), 5.6σ (bist_volume_breakout) uzağında — yani ufuk içinde ulaşılamaz. Çıkışların %57'sinin "hit_horizon ≈ eksi maliyet" olmasının sebebi bu.
- Sweep sonucu (14 g, maliyet düşülmüş net bps): `oi_breakout` −6.7 → **+14.1** (m=1.5), `momentum_xs` +15.7 → **+26.9** (m=3), `bist_intraday_reversion` −6.1 → +5.1, `funding_reversion` −12.8 → −2.2. `grid`/`dca`/`oi_delta`/`matrix_agent` hiçbir m'de pozitif değil — bare geometrisi sinyalsizliği kurtarmıyor.
- `matrix_shared/barriers.py`: tp/sl artık sembolün güncel volatilitesinden türetiliyor (`MATRIX_BARRIER_M`=1.5, [0.15%, 3%] kıskacı, 60 s cache). Strategy dispatch'inde tüm draft'lara, agent'ta prediction'a uygulanır; carry/delta-neutral bacaklara dokunulmaz, volatilite bilinmiyorsa stratejinin kendi baresi kalır. `MATRIX_VOL_BARRIERS=0` ile kapatılır. Her prediction `context.barrier` altında eski/yeni değerleri taşır.

## 2026-09-20 — Asıl kayıp mekanizması: sinyal dolana kadar ölüyordu
- Edge çalışması artık DOLDURULAN işlemleri değil TÜM sinyalleri değerlendiriyor (14 günde 5 281 sinyal / 997 dolum; predictions'ın yalnızca %12'si pozisyona dönüşüyor). Tablo tersine döndü: `momentum_xs` sinyalleri rastgele girişten **+36.0 bps** iyi (t=6.04, n=799) — oysa DOLUMLARI −35.8 bps'ti. `oi_delta` sinyal bazında +1.3 (t=0.34); dolum bazındaki +19.4'ü seçim katmanının şansıydı.
- Nedeni ölçüldü: ortalama dolum, prediction'ın kendi ufkunun **%53'ü** (momentum_xs), %41 (dca), %40 (grid), %32 (oi_delta) geçtikten sonra oluyor — slot dolu olduğu için aday kuyrukta bekliyor ve `close_by`'a kadar taze adaylarla eşit yarışıyor. Sinyal dolana kadar bozuluyor.
- `paper_trade`: ufkunun `MATRIX_MAX_SIGNAL_AGE_FRAC`'ından (vars. %20, kısa ufuklar için `MATRIX_MIN_SIGNAL_WINDOW_S`=45 s taban) fazlası geçmiş aday artık açılmaz; EV yaşla sönümlenir, böylece bayat aday taze adayı geçemez. Açılmayanlar zaten virtual outcome üretiyor — kanıt bedavaya kalıyor.

## 2026-09-20 — Kapak uykusu kapatıldı: G1 sayacı yeniden başladı
- Operatör `sudo pmset -a disablesleep 1` çalıştırdı; `pmset -g` artık `SleepDisabled 1`. Son dört kesintinin ikisinin nedeni (kapak kapalı uyku: 11 s, 21 s) ortadan kalktı, diğer ikisininki (sessiz ölen worker) 095b2c4 supervisor'ı ile. Sistem ilk kez kesintisiz örneklem biriktirebilir. Kalan tek operatör eksiği: `TELEGRAM_BOT_TOKEN`.

## 2026-09-19 — Shadow-cüzdan bug'ının iadesi uygulandı
- Operatör `make reset-capital ASSET=crypto AMOUNT=+346.76` çalıştırdı: 2026-09-13'te challenger pozisyonlarının şampiyon cüzdanına slot cap'siz yazılmasından doğan kayıp geri verildi (default/crypto equity 9166.55 → 9513.31). Öğrenme verisine dokunulmadı; bu tutar artık strateji performansı olarak sayılmıyor.

## 2026-09-19 — Giriş-zamanlaması edge çalışması: stratejilerin sinyali var mı, kontrollü test
- Yeni `matrix_shared/edge_study.py` + `make edge-report [DAYS=] [STRATEGY=]`: her kapanmış işlem 1m bar'lar üzerinde yeniden oynatılır (`simulate_bracket`), sonra AYNI sembol/yön/TP/SL/ufuk ile K rastgele giriş zamanı için tekrar oynatılır. Tek fark girişin zamanı; maliyet, çıkış kuralı, tutma süresi, sembol karışımı sabit. Welch t-testi ile karşılaştırılır.
- **Bulgu (14 g, 2 839 işlem, 60 kontrol çekilişi):** `oi_delta/crypto` rastgele girişten **+19.4 bps** iyi (t=2.90, n=274) — 15 bps gidiş-dönüş maliyetini aşan tek strateji. `momentum_xs` −35.8 bps (t=−2.78) ve `dca` −12.0 bps (t=−2.21) rastgeleden anlamlı KÖTÜ. İşlem hacminin %67'sini yapan `funding_reversion` (brüt +1.2) ve `grid` (brüt −0.1) sıfır edge: sadece spread ödüyorlar. Kayıp bir sızıntı değil, sinyal yokluğu.
- Slot scorer artık bu ölçümü kullanıyor (`_entry_edge_verdict`, 6 s cache): `pays` → gerçekleşmiş-zarar demotion'ından muaf; `harmful` → gerçekleşmiş PnL iyi görünse bile kitaptan çıkar. 2026-09-15'te eklenen sert demotion `oi_delta`'yı 0 slota indirmişti (kaybı maliyet+boyutlandırmadandı, sinyalden değil); guard devreye girip sistemin kendi slot pass'i onu 1 slota geri aldı. NOT: `slot_scorer.py`'deki bağlantı çalışan ağaçta duruyor (canlıda aktif), commit'i o dosyadaki başka yazarın commit'lenmemiş demotion bloğuyla birlikte gelmeli.

## 2026-09-19 — LLM wiki (Karpathy deseni): proje bilgi tabanı `docs/wiki/`
- `llm-wiki` skill'i kuruldu (`~/.claude/skills/llm-wiki/SKILL.md`): üç katman (ham kaynak / model-sahipli wiki / şema), sayfa formatı, ingest-query-lint operasyonları, global (`~/.claude/wiki/`) ve proje (`docs/wiki/`) ayrımı.
- Proje wiki'si 15 sayfa: amaç+G1-G4 durumu, mimari, servisler, veri modeli, stratejiler, paper engine, **pnl-reality** (ölçülmüş ekonomi), öğrenme döngüsü, risk gate'leri, operasyon, LLM yığını, geliştirme, olaylar, açık sorular. CLAUDE.md oturum başında okumaya yönlendiriyor.
- Global wiki: operatör, çalışma anlaşması, makine (clamshell sleep), harness tuhaflıkları, projeler arası hata kalıpları.

## 2026-09-16→19 — SESSİZ DURUŞ: paper engine 3 gün ölüydü; dev entrypoint artık supervisor
- 2026-09-16 08:16'da postgres kısa süre "recovery mode"a girdi; `backtest` worker'ı bu geçici hatada öldü. `watchfiles.run_process` yalnız dosya değişiminde restart ettiği için container "Up" göründü ama içinde işçi yoktu: 3 gün boyunca hiç pozisyon açılmadı/kapanmadı (son kapanış 09-16 08:17, 8 pozisyon donmuş kaldı). Aynı desen daha önce reflection'da da görülmüştü.
- `matrix_shared/dev_watchfiles.py` yeniden yazıldı: hot reload + **crash supervisor** (çocuk kendi kendine çıkarsa 2→60 s kapaklı backoff ile yeniden başlatılır, her restart loglanır; `MATRIX_WATCH_SUPERVISE=0` ile kapatılır). Regresyon testi: sürekli çöken çocuk ≥3 kez yeniden başlatılır.

## 2026-09-15 — LLM failover: subscription CB açıkken Cursor/OpenRouter'a düş
- Claude subscription 9 ardışık hatada 900s breaker açıyordu; plan `sub → openrouter → rules` idi ama `OPENROUTER_API_KEY` boş olduğu için zincir **boşalıyordu** — agent 15 dk LLM'siz, analiz duruyordu. Cursor Docker volume login'i (`cursor_available=true`) plan dışında kalmıştı. Default plan artık `subscription → cursor → openrouter → rules`; breaker açıkken `cursor → openrouter`. Deterministik kural yolu hâlâ son durak. `test_openrouter_llm.py`. OpenRouter primary için `.env`'de key şart (`make llm-openrouter`).

## 2026-09-15 — Carry ailesi: sembol başına tek funding-harvest
- inverse_carry + xexch_funding_arb aynı underlying'de (STEEM/CAP/CVC) yan yana açıldı: iki tez aynı funding'i biçer, ikinci ~30 bps round-trip boşa gider. Kök: (1) per-symbol cap mevcut açık pozisyonları saymıyordu (her tick 0'dan), (2) cap ≈ 10 olduğu için iki carry sığıyordu. `_open_for_market` artık açık carry sembollerini prefetch eder; carry adayı o sembolde zaten carry varsa skip (EV-sort aynı tick'te iyisini seçer). Mevcut örtüşmeler kapatılmadı — az önce açıldılar, şimdi flatten maliyeti hold'suz realize eder. `tests/test_carry_overlap.py`.

## 2026-09-15 — inverse_carry + xexch_funding_arb: iki gerçek arb, canlı kitapta
- **inverse_carry** (v1 active): `cash_and_carry`'nin ayna görüntüsü. Negatif funding'de (shortlar longlara öder) short-spot / long-perp; `side='inverse_carry'`, paper engine accrual'e −1 işaret uygular. Aynı ekonomi: |funding| ≥ 0.08%/8h, 48h hold, 2-bacak ~30 bps maliyet, funding-flip close. Şu anki rejim (STEEM/LSK/CAP derin negatif) classic carry'nin boşta oturduğu yer — bu bacak onu doldurur.
- **xexch_funding_arb** (v1 active): fiyat-spread arb değil (ms kapanır, coloc + iki kitap stok ister). Dürüst kenar: **funding diferansiyeli**. SHORT yüksek-funding venue / LONG düşük-funding venue; `side='xexch_carry'`, PnL = notional × elapsed/8 × (f_short − f_long). Binance USDⓈ-M `premiumIndex` public REST poller (`ingestion.binance_funding`, auth yok, 60s) ikinci venue feed. min_diff 0.05%/8h (gürültü + testnet-bybit vs mainnet-binance sapması), 48h hold, flip guard diferansiyel çökünce kapatır. Price-spread arb v1'de descoped.
- Tek-venue carry ticker/accrual **Bybit'e pinli** (`exchange='bybit'`). Binance poller aynı sembolü daha yeni satır olarak yazınca `_latest_funding_rate` onu karıştırıyordu — Bybit fill'e Binance oranı işlenirdi. `test_inverse_carry.py` + `test_delta_neutral.py` venue-izolasyon testleri.
- Slot satırları default cüzdana eklendi (yoksa scorer/pay bölenine girmezler, cap'siz açılırlardı).

## 2026-09-15 — EV tabanı: negatif-EV işlemleri slot doldurmak için açma (Lever 1)
- Paper engine adayları EV'ye göre sıralıyor ama eşik yoktu → slot boşsa tüm havuz negatif-EV olsa bile "en az kötü"yü açıyordu (hit_horizon'da 2985 crypto işlem @ -9.7 bps, 3g: hiçbir yere gitmeyip sadece fee ödeyen). `_open_for_market`'a sert kapı: `EV < round_trip_cost_pct × MIN_EV_OVER_COST` (env `MATRIX_MIN_EV_OVER_COST`, vars. 1.0) ise açma; edge yokken kitap nakitte oturur. delta_neutral iki bacaklı olduğu için 2× maliyet eşiği. Conservatism gate — yalnız bloklar, hiçbir risk limitini gevşetmez (TRADING.md uyumlu). `services/backtest/tests/test_ev_floor.py`.

## 2026-09-15 — perf_score kalibrasyon bug fix (EV ranking + notional sizing + slot tahsisi)
- `slot_scorer._perf_score` işaretli **[-0.6, 1.0]** (nötr ≈ 0.2) döndürüyordu ama üç tüketici üç ayrı ölçek varsayıyordu: `edge_multiplier` [0,1] nötr 0.5 (→ her skorlanmış stratejiyi EV'de cezalandırıyordu), `risk_multiplier` perf_score'u doğrudan [0.25,1.25] çarpan sanıyordu (→ negatifleri 0.25× notional'a kırpıyordu), `_slots_for_score` nötr 0.2 < 0.3 eşiği (→ nötr stratejileri çeyrek slota). Sonuç: EV floor'u açınca **tüm kitap** bloklanıyordu. `_perf_score` artık **[0,1], 0.5 nötr** (reflection unit testlerinin zaten beklediği kontrat); `risk_multiplier` `0.5+perf_score` ile hizalandı (nötr→1.0×). Canlı perf_score'lar reflection tarafından yeni formülle yeniden skorlandı. `test_allocation.py` + `test_slot_scorer.py` yeni invariant testleri. Bu üç yolu da doğru besleyerek kazanan stratejinin otomatik ölçeklenmesini (skor≥0.7 → 2× slot) mümkün kılar.

## 2026-09-15 — Otomatik demote: realized-kaybeden stratejiyi aktif kitaptan çıkar (Lever 2)
- `slot_scorer` kaybedenleri slot kısıyordu ama `_slots_for_score` min 1 slot bırakıyordu → kronik kaybeden (ör. funding_reversion günlerce) 1 slotla kanamaya devam ediyordu. Ayrıca EV floor *modellenmiş* negatifi engelliyor ama modellenmiş EV'si maliyeti geçen "kendinden emin ama yanılan" stratejiler (conf=1.0, WR %42) floor'u geçebiliyor. Yeni sert demote: tam pencerede (`DEMOTE_MIN_N`=20) `avg_pnl_pct < -5bps` **ve** `total_pnl_usd < 0` **ve** Wilson-lower `win_rate < 0.5` ise `allocated_slots=0` (aktif kitaptan tamamen çıkar), mutation_proposal ile loglanır. Recovery otomatik değil — labs/efficacy yeni versiyon önerir. consec-8 auto-cut ve skor-tabanlı yol korunur. `test_slot_scorer.py` demote testi. Env: `MATRIX_SLOT_DEMOTE_MIN_N`, `MATRIX_SLOT_DEMOTE_AVG_PNL_PCT`.

## 2026-09-15 — cash_and_carry ekonomi düzeltmesi: delta_neutral gerçek maliyet + break-even eşiği (Lever 3)
- Paper engine delta_neutral pozisyonlara **hiç işlem maliyeti** yüklemiyordu (`apply_slippage` delta_neutral'ı değiştirmeden döndürüyor, close'da exit=entry) → cash_and_carry'nin funding capture'ı bedavaydı; +1.3 bps tüm-zaman "edge"i bir maliyet-modeli sübvansiyonuydu (TRADING.md: asla venue'dan ucuz modele karşı tune etme). `_close_position` artık iki bacaklı carry için `2 × round_trip_cost_pct` (~30 bps) düşer. `_ev` delta_neutral'ı funding-capture (fr × horizon/8h) olarak modeller, floor 2× maliyet ister. cash_and_carry v4: eşik 0.01%→**0.08%/8h** (break-even üstü), hold 9h→**48h** (6 funding döngüsü, sabit round-trip'i amorti eder), FUNDING_CAP 0.20%. Funding-flip close downside'ı hâlâ kesiyor. `test_delta_neutral.py` maliyet-uygulandı regresyon testi.

## 2026-09-15 — funding_reversion geometri revizyonu (v6): negatif beklenti düzeltmesi
- Crypto default cüzdanı -837 USD (%-8.4) ile ana kayıp kaynağıydı; `funding_reversion` tek başına son 2 günün kaybının ~%80'i (-461 USD, ~-24 bps/işlem). Kök neden: (1) giriş eşiği 0.015%/8h'a gevşetilmişti → nötr funding gürültüsünü fade ediyordu, (2) horizon 10 dk, 8 saatlik funding tezine uyumsuz → kısa vadeli fiyat gürültüsüne maruz, (3) SL gürültü bandında, TP'nin 2 katı sıklıkta tetikleniyordu (win rate ~%34, RR ~1.2). Eski v4 (active) + v5 (shadow) `retired`; yeni **v6 active**: eşik 0.05%/8h (yalnız gerçekten kalabalık funding), horizon 4h (tez zaman ölçeği + fade edilen tarafta funding carry kazanılır), TP/SL 1.5%/1.0% (fee bandı üstü, stop gürültü aralığı dışı). Param'lar `strategy_configs`'ta (60 s TTL, restart'sız); modül default'ları da senkronlandı (`crypto/funding_reversion.py`, STRATEGY_VERSION=6). Not: motor mimarisinde shadow config yalnız aktif bir champion'ın yanında koşar — "champion'ı durdur, sadece shadow'da izle" desteklenmiyor; kitap paper olduğu için düzeltme doğrudan champion'a alındı. İzleme: `funding_reversion` v6'nın `outcomes` beklentisi ve crypto default günlük PnL trendi.

## 2026-09-14 — Agent LLM çağrı sayısı: batch 4 → 12, HOLD cooldown 5 → 10 dk
- US açılınca agent 10 dk'da 43 CLI çağrısı yapıyordu (~260/saat). Her çağrının maliyeti içerikten bağımsız ~20k token harness overhead'i olduğu için batch büyütüldü (`MATRIX_AGENT_BATCH_CHUNK`, market başına tek çağrı) ve HOLD sonrası bekleme 600 s (`MATRIX_AGENT_HOLD_COOLDOWN_S`). Hedef ≤ 60 çağrı/saat.

## 2026-09-14 — Regime referansı için yedek semboller
- US referansı SPY ingest edilmiyor (universe endeks bileşenleri); 1h geçmişi yetersizse QQQ→AAPL→MSFT→NVDA denenir (BIST: GARAN/AKBNK, crypto: ETH). Böylece US/BIST regime `unknown` kalmaz.

## 2026-09-14 — 1h rollup tüm marketler için
- `_ROLLUP_1H_SQL` yalnızca crypto'yu rollup'lıyordu; BIST/US için 1h bar yoktu → regime `unknown`, 7 günlük lookback'ler boş. Filtre kaldırıldı (GROUP BY zaten asset_class içeriyor).

## 2026-09-14 — Bar fiyatlı marketler (US/BIST) için agent feature'ları
- `extract_symbol_features` yalnızca trade/orderbook/ticker (crypto) okuyordu; US/BIST sembolleri LLM'e `last_price=None` ile gidip boş prompt'la HOLD alıyordu. Şimdi 1m `market_bars`'tan last_price, Δ5m ve notional türetilir (`apply_bar_features`). Agent tick'i fiyatı olmayan sembolleri LLM'e hiç göndermez.

## 2026-09-14 — Agent market başına universe sınırı
- US universe 503 sembol; agent 15 s'de 10 sembol sorarak tüm listeyi dolaşacaktı (~600 CLI çağrısı/saat). `MATRIX_AGENT_UNIVERSE_CAP` (vars. 60): market başına en iyi realised edge'li N sembol; HOLD cooldown ile birlikte 5 dk'da ≤ 6 batch.

## 2026-09-14 — ingestion image'ına lxml (US universe discovery)
- US discover `pandas.read_html` için `lxml` istiyordu, image'da yoktu ("Import lxml failed", `us_symbols` boş). `services/ingestion/pyproject.toml`'a `lxml>=5.0` eklendi, `uv.lock` güncellendi, image yeniden build edilip ingestion-market/news/bars-aggregator recreate edildi.

## 2026-09-14 — US ingestion dev overlay'de açıldı
- `docker-compose.dev.yml` ingestion-market komutu `--markets crypto bist` ile US adapter'ı dışlıyordu; `us` eklendi (0038 ile `us_symbols`/`market_bars_us` artık var). Agent/strategy US seansında universe bulacak.

## 2026-09-14 — 0038 (US market) migration'ı iki tier'a uygulandı
- Ağaçtaki commit'lenmemiş `0038_us_market.py` (US adapter WIP'inin parçası) LOCAL ve SHARED'a uygulandı: `us_symbols`, `market_bars_us` partition'ı, `default/us` cüzdanı, `matrix_agent/us` config. Canlı kod zaten bu tabloları arıyordu (ABD seansında agent tick'i düşüyordu). Dosya hâlâ commit'lenmemiş; sahibinin commit'lemesi gerekir. `make migrate-shared` host'ta `SHARED_DATABASE_URL` set edilmeden çalışmaz: `SHARED_DATABASE_URL=postgres://matrix:matrix_dev_only@postgres-shared:5432/matrix_shared make migrate-shared`.

## 2026-09-14 — bars-aggregator tick zaman sınırı
- Host uykusundan sonra tick sessizce hiç dönmedi; `BARS_TICK_TIMEOUT_S` (240 s) ile sınırlandı, aşımda hata loglanır ve döngü devam eder.

## 2026-09-14 — Bybit WS ölü soket bekçisi
- Host uykudan dönünce WebSocket 5 saat "açık" ama sessiz kaldı (`ping_interval=None`, ConnectionClosed hiç gelmedi); yalnız restart tick getirdi. Connector artık 60 s frame gelmezse soketi kapatıp yeniden bağlanır (`STALE_AFTER_S`). Aynı uykudan bars-aggregator da askıda kaldı; restart edildi.

## 2026-09-14 — Agent tick'i bir marketin universe hatasıyla ölmez
- US adapter'ın `us_symbols` tablosu henüz migrate edilmemiş (0038 yabancı WIP); ABD seansında `_resolve_targets` patlıyor, crypto dahil hiçbir karar alınmıyordu. Market başına try/except: universe alınamayan market uyarıyla atlanır.

## 2026-09-14 — Kesintinin kök nedeni: kapak kapalı uyku
- `pmset -g log`: 02:09 ve 13:26 (yerel) "Entering DarkWake state due to 'Clamshell Sleep'". Mac AC'de ve caffeinate/Amphetamine açık olsa da kapak kapalıyken uyuyor; OrbStack VM birlikte uyuyor, Docker soketi askıda kalıyor. VM OOM ikincil bulguydu. Kod çözümü yok; operatör: kapak açık / harici ekran / `sudo pmset -a disablesleep 1`. OPS_HARDENING ve ENGINEERING_LESSONS güncellendi.

## 2026-09-14 — Agent: HOLD sonrası sembol başına soğuma
- LLM bir sembol için HOLD dediyse aynı sembol `MATRIX_AGENT_HOLD_COOLDOWN_S` (vars. 300 s) boyunca tekrar sorulmaz; 15 s döngüde aynı 6 sembol her tick ~30 s / ~20k token'lık CLI çağrısıyla yeniden HOLD'a karar ediliyordu. Exploration ve kural kararları etkilenmez.

## 2026-09-13/14 — OrbStack VM OOM: bellek sınırları
- ~23:30 UTC'de 10 GiB OrbStack VM'i kernel OOM'a girdi ("VM_FAULT_OOM leaked"), Docker soketi saatlerce askıda kaldı; `orb restart docker` "stopping container docker"da takıldı. `docker-compose.limits.yml` artık tier başına `mem_limit` verir (db 2g, heavy 1g, worker 768m, light 512m; env ile ayarlanır) — kaçak servis VM yerine kendi container'ında OOM-kill olur. VM belleği 12 GiB'a çıkarıldı (`orb config set memory_mib 12288`, OrbStack yeniden başlatılınca geçerli). Operatör: OrbStack'i yeniden başlat (`orb stop && orb start`), sonra `make up-dev` (limits overlay ile recreate).

## 2026-09-13 — Slot scorer: pass ortasında silinen satır tüm pass'i düşürmez
- Her config savepoint içinde flush edilir; `StaleDataError` (labs/test temizliği veya operatör SQL'i satırı sildi) yalnızca o config'i atlar. Bugün iki kez saatlik pass tamamen iptal olmuştu.

## 2026-09-13 — Director restart'ta hemen review yapmaz
- Hot reload her paylaşılan kod kaydında Director'ı yeniden başlatıyor ve her seferinde LLM review koşuyordu (30 dk'da 4 brief). Başlangıçta kullanım defterinden son review zamanına bakılır (`usage_ledger.last_record_ts`), saatlik kadans oradan devam eder.

## 2026-09-13 — Reflection metriklerine çıkış-nedeni dağılımı
- `StrategyMetrics.by_reason` (hit_tp/hit_sl/hit_horizon → n, avg_pnl_bps, total): 24h şampiyon verisinde `hit_horizon` ≈ −6…−31 bps ile baskın ve `hit_sl:hit_tp` ≈ 2-3:1 — kayıp ≈ maliyet, TP ufuk içinde erişilemiyor, stop gürültü bandında. Bu geometri sinyali LLM tek-atış prompt'una, reflection agent `recent_outcomes` özetine (`by_exit_reason`) ve system prompt'a (nasıl okunacağı) eklendi; proposal `metrics_window` da taşır.

## 2026-09-13 — labs challenger testi slot satırlarını da temizler
- `test_challenger` `apply_proposal`'ın her cüzdana açtığı `strategy_slot_configs` satırlarını silmiyordu (canlıda 12 `chal_*` kalıntı). Teardown eklendi.

## 2026-09-13 — Slot payı yalnızca canlı stratejiler arasında bölünür
- `strategy_slot_configs`'ta 15 hayalet satır (labs test kalıntısı `chal_*`, crypto cüzdanında BIST modülleri) bölen sayısını 22'ye çıkarıyordu: gerçek stratejilerin taban payı 80//22=3 yerine 80//9=8 olmalıydı. Kalıntılar silindi; scorer bölen olarak yalnızca `active`/`shadow` config'i olan stratejileri sayar.

## 2026-09-13 — Paper engine snapshot yalnızca kayıtlı marketlerin cüzdanları için
- `snapshot_wallet`/`ensure_shadow_wallets` `all_markets()` dışındaki asset class'ları (testlerin sentetik `test` cüzdanları) atlar; kaybolan bir cüzdan için `wallet_snapshots` FK hatası artık tick'i düşürmez (20:16'da bir tick bu yüzden iptal olmuştu).

## 2026-09-13 — LLM tek-atış timeout 45 → 60 s
- Defter: agent tek-atış p50 23 s / p95 38 s (subscription CLI). 45 s'de %21 timeout → 60 s (`MATRIX_LLM_CALL_TIMEOUT_S`). Defterdeki `timeouts`/`p95_s` bir sonraki ayar için ölçüt.

## 2026-09-13 — Paper engine retired versiyonların prediction'larını açmaz
- Aday sorgusu `strategy_configs` ile join'lenir; status `retired` (veya active/shadow dışı) olan versiyonun kuyruktaki prediction'ları atlanır (452 açık momentum_xs v1 prediction'ı retired v1 için slot dolduruyor ve skor topluyordu). Config satırı olmayan prediction'lar etkilenmez. Regresyon testi.

## 2026-09-13 — bars-aggregator startup backfill boşluk kadar
- Her yeniden başlatmada 48 saatlik REST + aggregation yerine en yeni bar'ın yaşı + 15 dk (min 30 dk, maks 48 s) kadar backfill. Paylaşılan kod her kaydedildiğinde tüm servisler restart oluyor; 48 saatlik başlangıç işi bu yüzden hiç bitmiyor ve bar'lar bayatlıyordu.

## 2026-09-13 — LLM defteri süre ve timeout kaydeder
- Tek-atış çağrılarda 45 s timeout'lar deftere hiç düşmüyordu; 2 saatte agent 74 timeout / 274 başarı (%21), synthesis 11/11. `duration_s` ve `reason=timeout|<Exception>` satırları eklendi; `summary()` servis başına `timeouts`, `p50_s`, `p95_s` verir. `agent.usage` log satırına `dur_s`.

## 2026-09-13 — Retention prune bar tick'ini bloklamıyor
- bars-aggregator prune'u tick döngüsünün içinde çalıştırıyordu; 90 GB `market_trades` üzerinde tek 50k satırlık soğuk DELETE batch'i 45 s+ DataFileRead sürüp bar üretimini durduruyordu ("bars stale"). Prune artık ayrı asyncio task (`MATRIX_RETENTION_INTERVAL_S`), batch 50k → 10k (`MATRIX_RETENTION_BATCH`) ki 45 s bütçe gerçekten uygulansın.

## 2026-09-13 — Claude kimlik senkron launchd işi hiç çalışmamış (Operation not permitted)
- launchd `~/Documents` altındaki script'i çalıştıramıyor (macOS TCC; log: `bash: ./scripts/claude_creds_sync.sh: Operation not permitted`, exit 126). `make claude-creds-install` artık script'i `~/.matrix/bin/`e kopyalar, plist oradan çalıştırır; periyot 60 → 30 dk. Token bitimine ~2.5 saat kala fark edildi; iş yüklendi ve doğrulandı (exit 0).

## 2026-09-13 — notify: günlük LLM bütçe alarmı
- `health.py` kullanım defterinden bugünkü harcamayı okur; `MATRIX_LLM_DAILY_BUDGET_USD` (vars. 25) aşılırsa ⚡ alarm (stateful, düzelince "Recovered"). notify dev overlay'ine `matrix_claude_config` volume'u salt-okunur eklendi.

## 2026-09-13 — bars-aggregator her dakika 284M satırlık indeksi tarıyordu
- `_AGGREGATE_SQL` yalnızca `trade_ts` ile filtreliyordu; tek indeks `(symbol, trade_ts)` → her tick tüm indeksi yürüyordu (planner maliyeti 7.1M, ~6 dk; bugünkü "bars stale"/"ingestion stale" alarmlarının kaynağı). Tick artık `symbol = ANY(universe)` ekler (maliyet 18); 1h rollup penceresi 8 gün → 3 saat (`BARS_ROLLUP_TICK_HOURS`), tam rollup startup backfill'de.

## 2026-09-13 — LLM kullanım defteri (migration'sız `agent_usage`)
- `matrix_shared/usage_ledger.py`: her `agent.usage` satırı ayrıca `~/.claude/matrix_usage/YYYY-MM-DD.jsonl`'e (tüm LLM servislerinde mount'lu `matrix_claude_config` volume'u) JSON olarak eklenir; `summary(days=)` servis başına çağrı/turn/maliyet/hata verir. subscription, agent tool-loop ve openrouter yollarına bağlandı. Alembic zinciri açılınca tabloya taşınır.

## 2026-09-13 — Director: discarded görevler rapordan çıktı, yanlış-öncül koruması
- `dev_tasks_report` `discarded` satırları göstermez; #4–#9'un `failure_reason`'ı temizlendi; Director aynı hayalet "feeder crash" görevini ikinci kez (#16, `max_turns` ile düştü) açmıştı → discarded. System prompt: discarded / `stale_heartbeat` / olaya bağlanmış satırlar bug kanıtı değildir, aynı sorun için ikinci görev açılmaz.

## 2026-09-13 — Reflection: challenger koşarken veya öneri beklerken mutasyon yok
- `_mutation_blocked`: (strateji, market) için `shadow` config varsa ya da aynı versiyondan `pending` öneri varsa tick LLM tool-loop'unu hiç çağırmaz (funding_reversion için 12 dk'da iki öneri üretilmiş, ikincisi zaten uygulanamazdı). Pending dedup artık LLM çağrısından ÖNCE.

## 2026-09-13 — Challenger karşılaştırma taban çizgisi sıfırlandı (veri düzeltmesi)
- Shadow-cüzdan düzeltmesinden (74eb6ff, 19:03 UTC) önce başlamış iki challenger'ın (`matrix_agent` v8, `oi_delta` v9) `promoted_at`'i şimdiye çekildi; önceki örnekleri şampiyon cüzdanında slot cap'siz oluşmuştu, kıyas geçersizdi. Diğer challenger'lar düzeltmeden sonra doğdu.

## 2026-09-13 — Slot scorer shadow cüzdan satırlarını puanlamıyor
- Paper engine shadow pass'i şampiyonun slot config'lerini kullandığı için shadow cüzdanındaki 16 slot satırı yalnızca gürültü `slot_adjustment` önerisi üretiyordu; scorer artık `shadow` cüzdanını atlar. Test eklendi.

## 2026-09-13 — Prediction backlog backpressure (LLM/DB israfı)
- 24 saatte funding_reversion 9.257 expired / 36 closed; matrix_agent 1.441 expired prediction için LLM çağrısı yaptı (~$29/gün boşa). Yeni `matrix_shared/backpressure.py`: `room()` = max(3, şampiyon slot × 5) − dolmamış açık prediction sayısı (champion/shadow ayrı). Strategy `persist_drafts` her (strateji, market, shadow) grubunu en yüksek güvenle `room` kadar keser; agent tick'i feature/LLM'den ÖNCE sembolleri `room`'a göre (edge sırasıyla) kısar. Env: `MATRIX_BACKLOG_SLOTS_MULT`, `MATRIX_BACKLOG_MIN`, `MATRIX_BACKLOG_DEFAULT_SLOTS`. Probe hatasında 3'e düşer, hiçbir strateji tamamen susmaz.

## 2026-09-13 — momentum_xs her versiyonu v1 damgalıyordu; test kalıntıları temizlendi
- `modules/crypto/momentum_xs.py` draft'a `STRATEGY_VERSION` sabitini yazıyordu → v2 (active) ve v3 (shadow) prediction'ları v1 (retired) olarak kaydediliyor, reflection/efficacy bu strateji için n=0 görüyordu, retired v1 skor topluyordu. `self.version` ile düzeltildi; regresyon testi. Diğer 16 modül zaten doğruydu.
- Canlı shared DB'de 342 `TEST_scorer_*` prediction ve 15 test slot config kalıntısı silindi; `test_slot_scorer` fixture'ı artık position/prediction'larını da temizler.

## 2026-09-13 — dev_agent worktree testleri kendi venv'ini siliyordu
- `integrate._test_env` artık `UV_PROJECT_ENVIRONMENT`/`VIRTUAL_ENV`'i düşürür: image'ın ihraç ettiği değer yüzünden `uv run --project <worktree>/services/<svc>` hedef projeyi dev_agent venv'ine senkronluyor, dev_agent paketi kayboluyordu (reload'da `No module named 'dev_agent'`). Venv `uv sync` ile onarıldı, servis yeniden başlatıldı. Regresyon testi eklendi.

## 2026-09-13 — dev_agent reaper etiketi `worker_crash` → `stale_heartbeat`
- Reaper'ın damgası gerçek çökmeden ayrılamıyordu; Director bunu bug diye okuyup görev açtı (#13). Artık `stale_heartbeat`. README/test güncellendi.

## 2026-09-13 — Director yanlış öncülle görev açtı; reaper artefaktları temizlendi
- Director tick'i (LOCAL tier'a geçtikten sonra) #4–#9 `worker_crash` satırlarını "lessons-feeder çöküyor" diye okuyup görev #13 açtı. O satırlar 14:05 test-DB olayı sonrası reaper damgasıydı → #13 iptal, #4–#9 `discarded` + not. Eski dev-agent worktree/branch'leri (task-2..9, ahead=0) silindi.

## 2026-09-13 — İlk otonom dev_agent yaması main'e alındı: param_tune soğuma süresi (mutasyon sarmalı)
- Director'ın açtığı görev #10 (oi_delta v2→v3→v4→v5 mutasyon sarmalı) dev_agent tarafından yazıldı: `PARAM_TUNE_MIN_N` (oi_delta/oi_breakout/grid/dca/funding_reversion için n≥30), `MATRIX_PARAM_TUNE_COOLDOWN_HOURS=24` (aynı stratejide ardışık param_tune arası bekleme), son 3 günde denenen knob'lar `skip_knobs` ile atlanır; 5 yeni test. Görev `test_broke` ile düştü çünkü ilgisiz `test_grants` env'e bağlıydı (`granted_by` "+relaxed" eki `MATRIX_CERT_*` varken gelir) → assert `startswith`. Yama gözden geçirilip elle main'e alındı.
- `test_operator_directives`: event-loop'lar arası cache'lenmiş engine için `reset_engines()` fixture'ı.

## 2026-09-13 — Director/efficacy dev task'ları yanlış DB tier'a yazıyordu; `make reset-capital`
- `director/tools.py` (`file_dev_task`, `dev_tasks_report`), `director/digest.py` (dev bölümü) ve `reflection/efficacy.maybe_file_dev_task` `dev_tasks`'ı SHARED tier'da okuyup yazıyordu; dev_agent LOCAL okur → Director'ın açtığı görev #11 hiç işlenmedi (dev_agent API'den #10 olarak yeniden açıldı, shared kopya `discarded`). Hepsi LOCAL tier'a taşındı; testler de.
- `make reset-capital ASSET=crypto [WALLET=default] [AMOUNT=+346.76]`: öğrenme verisine dokunmadan paper cüzdan sermayesini düzeltir (AMOUNT verilmezse başlangıç sermayesine döner). Operatör notu: shadow→default sızıntısı default/crypto cüzdanına −$346.76 yazdı; `make reset-capital ASSET=crypto AMOUNT=+346.76` ile geri alınabilir (bu oturumda izin sınıflandırıcısı engelledi).

## 2026-09-13 — Reflection LLM önerileri otomatik challenger olur (insan onayı kalktı)
- `labs.promote.apply_best_pending_safe`: `threshold_change` / `weight_tune` (source `agent`|`llm`) önerileri challenger modunda otomatik uygulanır → `shadow` config; efficacy ölçer, ancak kanıtla cutover. Anahtarlar şampiyon params'ında yoksa (hallucinated knob) atlanır ve uyarı loglanır; challenger modu kapalıysa pending kalır (insan). 7 pending LLM önerisi (18:10–18:17) bu yolla işlenir. AUTONOMY_PLAN §6 #11 kapandı.

## 2026-09-13 — Paper engine: challenger pozisyonları şampiyon cüzdanına yazılıyordu (−$343/gün); aday havuzu sıralı
- `paper_trade._open_for_market(shadow=True)` pozisyon açarken cüzdanı `shadow` bayrağı olmadan çözüyordu → tüm shadow (challenger) pozisyonları DEFAULT cüzdana, per-strateji slot cap'i olmadan yazıldı. 2026-09-13'te funding_reversion v3 (shadow) 1.198 trade ile şampiyon cüzdanını $343 eritti; şampiyon v2 aynı günde 25 trade. Fix: doğru cüzdan + shadow pass şampiyonun `strategy_slot_configs`'unu ödünç alır (challenger aynı sermaye disipliniyle koşar; karşılaştırma parametreleri ölçer, slot sayısını değil).
- Aday havuzu `ORDER BY confidence DESC, generated_at DESC` sonra LIMIT (önce sırasız LIMIT vardı; 764 açık prediction'lık kuyrukta en iyi/en yeni adaylar EV sıralamasına hiç ulaşmıyordu; `test_slot_enforcement` bu yüzden 0 pozisyon görüyordu).
- Testler: `test_shadow_wallet_booking.py` (regresyon); `market_trades` test temizlikleri `(exchange, exchange_trade_id)` indeksini kullanır (yalnızca `exchange_trade_id IN` 300M satırı tarıyor, suite 10+ dk asılıyordu — backtest ve strategy conftest'leri).

## 2026-09-13 — Telegram'dan dev task onayı; accept gerçekten merge eder; notify dev_agent probe tier fix
- notify: `/dev_tasks`, `/dev_accept <id>`, `/dev_discard <id>`, `/dev_revise <id> <notes>` (yeni `notify/dev_client.py`, dev_agent REST `DEV_AGENT_API_URL`, vars. `http://dev_agent:8009`); `awaiting_review`'a düşen görev için 🧩 push (komutlarla). Health: `dev_tasks` probe'ları SHARED yerine LOCAL tier'dan (shared kopya boş migration artefaktı → failed/stuck alarmları hiç çalmıyordu).
- dev_agent `POST /tasks/{id}/accept`: `merge_reviewed_task` — branch'i base'e merge eder (dirty-tree guard, worktree temizliği); başarısızsa 409 + `review_notes`, status `awaiting_review` kalır. Önceden yalnızca status'u `merged` yapıyor, branch askıda kalıyordu.
- `docker-compose.dev.yml`: notify hot-reload girdisi (image'daki eski kodu çalıştırıyordu).

## 2026-09-13 — P2.5 benzer-setup hafızası (embedding'siz k-NN) + paylaşılan Wilson istatistikleri
- Yeni `matrix_shared/setup_memory.py`: `setup_vector()` prediction `context.features` → 14-dim vektör (flow, book, funding, OI, Δfiyat, haber, regime eksenleri, graph polaritesi); `similar_setups()` aynı sembol/strateji/yön son 60 günde cosine k-NN (k=20, sim≥0.85), sembol tarihi n<10 ise asset-class havuzu (sim≥0.95); Wilson aralığı ile `good/bad/neutral`. Süreç içi 300s satır cache'i; her hata `EMPTY` döner.
- Agent: `_apply_setup_memory` lesson'lardan sonra çalışır — `good` güven +0.10, `bad` güven ÷2, yön asla değişmez, hold/exploration dokunulmaz; `feature_dump.setup_memory` audit. LLM prompt'una "Nearest past setups" bloğu (long/short). `feature_dump.features` artık `price_change_pct_5m` içerir.
- Yeni `matrix_shared/stats.py` (`wilson_bounds/lower/upper`); slot_scorer buradan kullanır.

## 2026-09-13 — Sistem öz-hafızası: docs/ENGINEERING_LESSONS.md + dev_agent lesson retrieval düzeltmesi
- Yeni `docs/ENGINEERING_LESSONS.md`: bir kez ödenmiş tuzaklar (test harness, izole test DB, json≠jsonb, AGE alias, staging, hot reload, istatistik kararları). CLAUDE.md ve dev_agent BASE_LAYER prompt'u ona işaret eder; kural: yeni ders aynı commit'te eklenir.
- `dev_agent.memory.search_lessons_text`: tüm görev açıklamasını tek substring olarak arıyordu (hiç eşleşmiyordu) → token bazlı any-match + `relevant_paths` prefix boost. `dev_agent_lessons` tablosuna 10 aktif ders tohumlandı.

## 2026-09-13 — İstatistiksel titizlik: cert CI alt sınırı + otomatik iptal, slot n≥30/Wilson, orphan dışlama, reflection LLM kapısı (P1.3/P1.5/P1.6/P1.7)
- `trading_safety.evaluate_eligibility`: yeni `min_ci_lower_usd` (varsayılan 0; `MATRIX_CERT_MIN_CI_LOWER_USD`) — trade başına ortalama PnL'in %95 CI alt sınırı pozitif değilse sertifika yok; `orphan_flat_close` outcome'ları kanıt sayılmaz. `revoke_breached_certificates()` drawdown cap'i aşan GRANTED sertifikaları `revoked` yapar (reflection cert pass'inde grant'tan önce koşar).
- Slot scorer: son 30 pozisyon, `MIN_N_FOR_SLOT_CHANGE` (`MATRIX_SLOT_MIN_N`, vars. 30) altında slot değişmez (ardışık-kayıp auto-cut hariç); win rate Wilson alt sınırı.
- Reflection: LLM mutation yolu artık yalnızca `_underperforming` stratejilerde koşar (öncesinde her aktif config her tick'te mutasyona giriyordu); SYSTEM_PROMPT hedefi "average score" → gerçekleşmiş total_pnl_usd.
- `metrics_window` ve lessons synthesizer bucket'ları `orphan_flat_close` dışlar.

## 2026-09-13 — Subscription LLM path live via synced host credentials

- Two `claude setup-token` tokens returned `401 OAuth access token is invalid` on host and in
  containers while the host's claude.ai login worked. New path: `scripts/claude_creds_sync.sh` reads
  the keychain item "Claude Code-credentials", strips the refresh token (containers must never rotate
  the host session), and writes `.credentials.json` into the `matrix_claude_config` volume mounted at
  `/root/.claude` in all 11 LLM services. Hourly launchd job `com.matrix.claude-creds`
  (`make claude-creds-install`) refreshes it, poking the host CLI first when < 90 min remain.
- `subscription_llm._subscription_ready()` and notify `llm_configured()` accept the credentials file
  as auth (env token still works and takes precedence). Verified: agent container single-shot → "OK";
  Director ran its first LLM review with tools.

## 2026-09-13 — Web `apply` route aligned with the Python apply path

- `apps/web/.../proposals/[id]/apply/route.ts`: computes `max(version)+1` (the verbatim `to_version`
  insert hit `uq_strategy_configs_id_class_ver` once other promotions landed), merges `after_params`
  over the current params (weights deep-merged), scrubs the full FORBIDDEN set incl.
  `live_capital_cap_usd`/`live_execution_enabled`, inserts a `shadow` challenger by default
  (`MATRIX_CHALLENGER_MODE`, refuses a second challenger) and records
  `metrics_window.applied_version/challenger/applied_by=operator` for reflection.efficacy.

## 2026-09-13 — P1.8 counterfactual virtual outcomes + P3.3 cross-container LLM slots

- `paper_trade.expire_stale_predictions` → `_record_virtual_outcomes`: every untraded prediction
  gets `context.virtual_outcome` (horizon-exit mark ±120s, same taker+slippage costs via
  `trading.virtual_pnl_pct`). Stored on the prediction, never in `outcomes`, so realised metrics,
  lessons and certs are untouched. `make ranker-ab [DAYS]` and the Director brief compare traded vs
  skipped-would-have bps per market — the EV ranker's own report card.
- `agent_runtime.ratelimit`: with `MATRIX_LLM_GLOBAL_SLOTS` (compose default 4) each tool loop also
  takes a Postgres advisory-lock slot on the shared tier; waits cap at `MATRIX_LLM_GLOBAL_WAIT_S`
  (30s) then proceed rather than deadlock. Per-container semaphores stay as the inner layer.

## 2026-09-13 — P1.4 labs sampling/fitness + P4.3 dev_agent codebase context

- `labs/evaluate.emit_signals`: no new evaluation while the (experiment, symbol) pair still has an
  OPEN one — `n_evaluations` now counts independent samples. `compute_fitness(n, mean, std)` =
  (mean − k·std/√n) × √(min(n,25)/25): a high-variance lucky genome no longer wins best-of-20.
- dev_agent: `search_codebase_context` result is finally passed into the system prompt (was `""`),
  the index refreshes hourly in the worker loop (`DEV_AGENT_INDEX_EVERY_S`), and `POST /codebase/reindex`
  exists so `make dev-agent-index` works.
- OPS_HARDENING.md gains an "Autonomy operations" table of the remaining operator touchpoints.

## 2026-09-13 — P2.3 regime memory

- `matrix_shared/regime.py`: `classify(closes_1h, funding)` → `Regime(vol|trend|funding)`; `current_regime(asset_class)`
  from the market's reference symbol (`MATRIX_REGIME_REF_*`, crypto BTCUSDT) 1h bars + latest funding,
  cached 5 min; `regime_matches("high/*/*", key)` wildcard filter.
- Every prediction now carries `context.regime` (agent via `SymbolFeatures.regime`, deterministic
  strategies via the dispatcher); the LLM prompt shows it; `agent_lessons` synthesizes
  `pattern_kind='regime'` lessons per (regime, side) beside symbol lessons; `matches()` and the
  strategy draft filter honour them. Director brief prints the current regime per market.

## 2026-09-13 — P2.4 overlay chain completed + P2.6 operator directives

- `matrix_shared/graph_overlay.py` (service-agnostic AGE writers): `backtest/paper_trade._close_position`
  links `Prediction-[RESULTED_IN]->Outcome` on every close; `agent_lessons` upserts `Lesson` nodes with
  `GENERALIZED_INTO` edges from the bucket's outcomes and mirrors superseded/expired status. The
  reasoning overlay was write-only Prediction nodes before.
- Operator directives (`matrix_shared.agent_lessons.remember_directive/forget_directive`): stored as
  `OPERATOR:`-prefixed `agent_lessons` rows (confidence 0.99, 10-year `observed_until`) for every
  strategy in the market, so the agent veto/boost, the strategy draft filter and reflection tools
  honour them with no new plumbing. Exploration corridor, corridor-based retirement, TTL sweep and
  statistical supersede all skip operator lessons. Brain gains the two `write` tools
  (`assert_all_read_only(..., allow_write=)`), prompt updated — "stop trading DOGE" in Telegram
  now becomes durable system behaviour.

## 2026-09-13 — P3.2: LLM decisions blended with the rule model + method A/B

- `agent/decide.blend_decisions`: agreement → trade at mean confidence (`llm+rule`); only the LLM
  wants to trade → its side at 0.6× (`llm`); only the rule → 0.6× (`rule`); opposite sides → HOLD
  (`conflict`). `MATRIX_LLM_BLEND_MODE=llm_overrides` restores the old unconditional override,
  `rule_only` is the control arm. Both verdicts land in `predictions.context` (`rule_side`,
  `llm_side`, confidences) — the material for the method-level A/B.
- `_llm_prompt` now includes knowledge-graph polarity/related entities, the rule verdict and
  weighted total, the symbol's realised edge and active lessons; batch system prompt explains the
  blend so the model states real confidence.
- `make method-ab [DAYS=7]` and a "matrix_agent by method" line in the Director brief.

## 2026-09-13 — Test hygiene: every suite green; engine cache reset after sync bridge

- `matrix_shared.db.reset_engines()` + call from `crypto_universe()`'s sync bridge: the
  `asyncio.run()` at strategy-module import cached an asyncpg engine bound to a throwaway loop,
  which later surfaced as "attached to a different loop" (strategy cash_and_carry test; same hazard
  for any process that imports a strategy module before starting its real loop).
- dev_agent tests: isolated `<db>_devagent_test` gets AGE + pgvector (in `ag_catalog`, like the live
  DB) and the `dev_codebase_nodes` table; cursor-runner test drives a fake `cursor` binary
  (`MATRIX_CURSOR_BIN`) instead of patching a function the runner never called; codebase search now
  matches ANY token (ranked by hits) instead of the ordered `%a%b%c%` pattern that never matched.
- labs: `test_safe_apply_takes_lab_promotion_at_strategy_threshold` no longer inserts/applies a
  proposal against the LIVE `matrix_agent` config (uuid strategy + monkeypatched threshold).
- shared: crypto_universe "no DB" test simulates the DB path instead of assuming none exists;
  strategy lesson tests disable the exploration corridor (deterministic drops).

## 2026-09-13 — P0.7 single live gate + P0.8 circuit trip flattens positions

- `matrix_shared/live_gate.py` is now THE composite gate (posture, flag, cert, circuit, per-trade
  cap, LIVE_CAPITAL_CAP_USD, concurrent cap, slot cap). `execution.safety.should_submit_live`
  delegates to it; `exchange_shadow._per_trade_allowed` calls it too (previously skipped the capital,
  concurrent and slot caps). `closing=True` for reduce-only exits evaluates posture/flag/cert/circuit
  only — a held position must always be closable. exchange_shadow keeps one `BybitV5Client` per
  network so its TokenBucket limits across orders.
- `backtest/paper_trade`: close logic extracted into `_close_position(force=)`; `flatten_wallet()`
  closes every open position (no-price → flat at entry). A daily-loss / trailing-stop trip now
  flattens the wallet (TRADING.md #2 "all positions close"). Auto re-arm at the UTC day roll only
  in paper mode; with `LIVE_EXECUTION_ENABLED=true` the circuit stays tripped until
  `make circuit-reset ASSET=…` or Telegram `/circuit_reset <asset_class> [wallet]`.

## 2026-09-13 — OpenRouter backend (free models) in the LLM failover chain

- `matrix_shared/openrouter_llm.py`: OpenAI-compatible chat completions with tool calling.
  `openrouter_single_shot` and `openrouter_agent_stream` (drives the existing `ToolRegistry` via
  function calling, honours `can_use_tool`, yields the same `AgentEvent`s as the SDK path). Tier map
  `MATRIX_OPENROUTER_MODEL_<HAIKU|SONNET|OPUS>` (defaults are `:free` models), `MATRIX_OPENROUTER_FALLBACKS`
  walked on 400/404 model errors, 429 → 90s per-process cooldown, `list_free_models()`.
- `subscription_llm._plan_backends`: OpenRouter is appended as the LAST LLM in every plan
  (subscription/bedrock/cursor → openrouter → rule-only) and becomes primary with
  `MATRIX_LLM_BACKEND=openrouter` (subscription stays as fallback). `subscription_enabled()` is
  true with only an OpenRouter key. The agent loop now skips a breaker-open subscription and
  moves to the next backend instead of returning nothing.
- Wiring: compose `*python-env` passes `OPENROUTER_API_KEY` + model envs; `.env.example` section;
  `make llm-openrouter`, `make openrouter-models`; notify `llm_configured()` counts the key.

## 2026-09-13 — Director service (P3.1) + agents always on + LLM → subscription

- New `services/director` (default service, hourly): `digest.py` builds a deterministic system
  digest (health ages, wallets, per-strategy 24h/7d PnL, challengers, efficacy verdicts, proposals,
  dev_agent queue, lessons, certs) and renders an operator brief; `agent.py` runs one Sonnet tool
  loop over the Director belt — read tools + `file_dev_task` (write, deduped, ≤2/tick),
  `retire_strategy` (write), `revoke_certificate` (risk-gated, safe direction only). No tool can
  grant certs, change caps, enable live or touch wallets. Without an LLM the tick is rules-only
  (stalled paper engine / stalled bars / recurring dev failure → dev task). Brief → logs + Telegram.
  `make director-once|director-digest|director-tail`.
- Operator: "agents always run" → `synthesis` (phase6) and `execution` (phase5) profile gates removed;
  both run by default now. `bulletin` stays phase6 (product, not an agent).
- LLM backend switched Cursor → Claude subscription (`make llm-subscription`, `.env`). Cursor was
  unauthenticated in containers anyway. **`CLAUDE_CODE_OAUTH_TOKEN` must be set** (`claude setup-token`)
  for any LLM path; until then every agent runs its deterministic fallback.
- `ingestion/bars.py`: startup backfill bounded to `BARS_STARTUP_BACKFILL_MAX_HOURS` (48h) for both
  the REST kline fill and trade aggregation. It previously scanned all 300M trades on every restart,
  which is why bars lagged 30+ min after each hot reload and retention never started.
- Fix: `predictions.context` / `mutation_proposals.metrics_window` are `json`, not `jsonb` — the
  `?` operator used by notify.health (LLM detector) and the digest failed silently; replaced with
  `->> IS NOT NULL`.

## 2026-09-13 — dev_agent test isolation + incident note

- **Incident:** `services/dev_agent/tests/conftest.py` TRUNCATEs every dev_agent table per test and
  pointed at the live local `matrix` DB; running the suite on 2026-09-13 wiped `dev_tasks`,
  `dev_task_runs`, `dev_task_events`, `dev_agent_lessons`, and the live worker briefly picked up a
  test-inserted task (cancelled). Data restored from the 2026-09-12 15:35 UTC backup
  (`pg_restore --data-only`, sequences reset); test worktrees/branches `task-42..45` removed.
- **Fix:** conftest now redirects to `<db>_devagent_test` (created on demand) unless the DSN already
  names a `*_test` database. The live DB is never truncated by tests again.

## 2026-09-12 — P4: dev_agent closes its loop (test → commit → merge) + guards

- `dev_agent/integrate.py`: after a successful run — pytest for every touched service
  (`uv run --project`, python-shared included) → commit on `dev-agent/task-N` → `git merge --no-ff`
  into `base_branch` in the live repo, unless the repo has uncommitted edits to the same files or
  the merge conflicts (then `awaiting_review` with the reason). Test failure → `failed/test_broke`.
  Merged worktrees are removed. `DEV_AGENT_INTEGRATION=branch` disables merging. Previously
  `merged` was only a DB label (9 tasks, 0 commits).
- Guards that existed on paper now run: `reap_stuck_running` each worker iteration, heartbeat every
  5 SDK events, per-task cost cap enforced from the SDK result cost, daily cap idles the picker,
  worktree failure fails the task instead of running in `/workspace`.
- `FORBIDDEN_PATHS` re-closed for exactly `trading_safety.py`, `exchange_shadow.py`,
  `execution/safety.py` (everything else stays open). Prompt/README/tests updated.
- Lessons: drafts whose topic recurs twice auto-activate (`auto-repeat`).
- `reflection/efficacy.maybe_file_dev_task`: ≥3 negative efficacy verdicts (rollbacks / retired
  challengers) for a strategy in 14 days → files one `dev_tasks` row (source `reflection`) asking
  dev_agent to rework the strategy logic. First automatic task source besides the lessons feeder.
- `make dev-agent-clean` now points at a real module (`dev_agent.clean`, DAYS=7).
- Known pre-existing failures left alone: `test_codebase` (codebase index never wired) and
  `test_cursor_runner_blocks_trading_path`.

## 2026-09-12 — P2.1: lesson lifecycle — TTL, effect-size confidence, exploration corridor

- `agent_lessons/synthesizer`: `_confidence(n, win_rate)` = sample curve × binomial-z significance
  (z ≤ 1 → 0, z ≥ 2.5 → full), so 39%/20 trades no longer clears the 0.40 gate while 20%/60 does.
  Confirmed lessons refresh `observed_until`; `expire_stale_lessons` expires anything not
  re-confirmed within `MATRIX_LESSON_TTL_DAYS` (14). `retire_contradicted_lessons` expires an
  `avoid` lesson when ≥10 corridor trades that bypassed it were profitable (lesson efficacy).
- Exploration corridor: `agent/decide._apply_lessons` lets `MATRIX_LESSON_EXPLORE_BYPASS` (25%) of
  *exploration* trades through an `avoid` veto, tagged `context.lesson_bypass=<lesson id>`;
  `strategy/lessons.filter_drafts` keeps `MATRIX_LESSON_STRATEGY_BYPASS` (10%) of matching drafts
  the same way. Breaks the self-lock where a lesson suppressed the trades that could refute it.
- `lessons_relevant_to(..., asset_class=)` now market-scoped on the decision path (was the bug the
  parity test warned about); `strategy/lessons` applies the same 0.40 confidence gate as the agent.

## 2026-09-12 — P1.2: champion/challenger (shadow configs) end to end

- `MATRIX_CHALLENGER_MODE=true` (default): `labs/promote.apply_proposal` now inserts the new
  version as `status='shadow'` beside the active champion instead of cutting over (one challenger
  per strategy/market; a second proposal stays pending). `as_shadow=False` = legacy cutover.
- Challengers run on the same data: strategy dispatcher instantiates the shadow config too and tags
  drafts `context.is_shadow=true`; the agent runs a rule-only decision with the shadow
  `matrix_agent` config (`agent/config.load_shadow_config`, no LLM spend). Module dedup and the
  agent's `_recent_signal_exists` are version-scoped so champion and challenger don't suppress
  each other.
- `backtest/paper_trade`: per-market `shadow` wallet (created at engine start with the default
  wallet's caps); shadow predictions open only there, champion wallet never sees them.
  `exchange_shadow` never mirrors challenger trades.
- `reflection/efficacy.evaluate_challengers`: champion vs challenger outcomes since the challenger
  started → `cutover` (z ≥ 1, positive PnL) / `retire` (z ≤ −1 or 14 days without a win) /
  `pending`, with an auditable `cutover` / `challenger_retired` proposal row.

## 2026-09-12 — P1.1: mutation efficacy + automatic rollback

- `reflection/efficacy.py`: every `applied` proposal (≥24h old) gets a before/after comparison of
  realised outcome PnL (from_version window vs the version it created, `metrics_window.applied_version`)
  → verdict `pending | insufficient | negative | neutral | positive` (two-sample z on mean pnl/outcome),
  stored in `metrics_window.efficacy` (JSON, no schema change). `negative` while that version is still
  active → **auto-rollback**: new config version with the proposal's `before_params`, auditable
  `rollback` proposal (source `efficacy`), original marked `reverted`. Bounded to 25 proposals/tick
  (`MATRIX_EFFICACY_*` env for windows/thresholds/rollback toggle).
- `reflection/main._tick`: runs the efficacy pass; skips drafts whose `after_params` equal a mutation
  reverted in the last 7 days (oscillation guard).
- `labs/promote.apply_proposal`: records `metrics_window.applied_version` (the real new version).

## 2026-09-12 — P0.6: realistic execution cost model

- `matrix_shared/trading.py`: `execution_cost_bps(asset_class, symbol)` = FeeModel `taker_bps +
  slippage_bps`; `apply_slippage(..., asset_class=, symbol=)` market-aware (legacy flat 2 bps only
  when no market given); `funding_pnl_usd()` for directional perp holds.
- `CryptoMarket.fees`: taker 10 → **5.5 bps** (Bybit non-VIP), maker 1 → 2, slippage 2 → per side
  7.5 bps, round trip 15 bps (was 4 bps and no fees at all in the paper engine).
- `backtest/paper_trade.py`: entry/exit fills use the market cost; crypto long/short closes add
  funding paid/received over the hold. `labs/evaluate.py` and `backtest/historical.py` +
  matrix_agent replayer use the same function. Expect paper PnL to look worse — it is now honest.
  TRADING.md gains a "Paper-trade cost model" section.

## 2026-09-12 — P0.5: retention + scheduled backups

- `matrix_shared/retention.py`: policy table (`market_trades` 7d, `market_orderbook_snapshots` 2d,
  `market_ticker_snapshots` 30d, `wallet_snapshots` 30d), bounded batched deletes through the
  existing `(symbol|wallet_id, ts)` indexes under a wall-clock budget. `bars-aggregator` runs
  `prune_once()` every 5 min (`MATRIX_RETENTION_*`). `make retention-drain` forces a full pass;
  `make db-compact TABLE=` (VACUUM FULL) returns space to the OS.
- `paper_trade._snapshot_one`: `wallet_snapshots` row written at most every 60s
  (`WALLET_SNAPSHOT_INTERVAL_S`); circuit/trailing-stop still evaluated every tick.
- `backup` compose sidecar (`infra/db/backup.sh`): daily `pg_dump -Fc` of both tiers to
  `./backups/<ts>/`, market stream tables schema-only (52 MB + 30 MB instead of ~90 GB), 14-day prune.
  `make backup-now` for an immediate one. First backup taken 2026-09-12 15:35 UTC.
- Found while testing: bars-aggregator's startup `backfill_all()` and the backtest test
  cleanup (`DELETE … WHERE exchange_trade_id IN`, no usable index) both full-scan the 302M-row
  trades table; both become cheap once retention has drained it.

## 2026-09-12 — P0.4: notify always-on + cross-process liveness alerts

- `docker-compose.yml`: `notify` no longer profile-gated; without `TELEGRAM_BOT_TOKEN` it runs
  dry-run (alerts in logs) so detection never depends on Telegram setup.
- `notify/health.py`: table-freshness detectors (restart-safe, work across containers):
  paper engine stalled (`wallet_snapshots`), crypto signals stalled (`predictions`), ingestion
  stalled (`market_ticker_snapshots`), bars stalled (`market_bars` 1m), disk pressure, LLM path
  rule-only (`predictions.context.method`), dev_agent task failed / stuck heartbeat.
  Edge-triggered with hourly re-alert + recovery INFO. Thresholds via `MATRIX_HEALTH_*`.
- `agent/main.py`: persists `method` (`rule`/`llm`/`+explore`/`+lesson`) into `predictions.context`
  so LLM-vs-rule decisions are queryable (also the basis for the method-level A/B in P3).

## 2026-09-12 — P0.3: reflection proposals + metrics scoped by `asset_class`

- `reflection/metrics.metrics_window(..., asset_class=)` filters predictions by market;
  `StrategyMetrics.asset_class` carried through. `reflection/main._tick` passes
  `cfg.asset_class` to metrics, dedup and the `MutationProposal` row (was defaulting to
  `crypto`, so BIST proposals were applied against — and kept re-creating — crypto rows:
  `bist_volume_breakout/crypto` reached v686 while `bist_volume_breakout/bist` stayed v1).
- Reflection agent tools `recent_outcomes` / `active_lessons` filter by the metrics' asset_class.
- Data fix (shared DB, one-off, no migration): the 3 phantom active
  `bist_*/crypto` StrategyConfig rows were set to `retired` with a rationale note. Their 1,781
  applied crypto-labelled proposals and 3 phantom crypto slot rows are left as history.

## 2026-09-12 — P0.2: strategy dispatcher binds `strategy_configs.params` + version

- `strategy/params.py`: `build_kwargs` (alias `price_band_pct→band_pct`, `horizon_seconds→horizon_s`;
  coerce JSON → Decimal/int by ctor default type; unknown keys logged+dropped) and `instantiate`
  (stamps `strat.version` from the active config row).
- `strategy/main.py`: `_active_configs()` replaces `_active_strategy_ids()`; every registered
  strategy is built from its `(strategy_id, asset_class)` active row. Applied `param_tune` /
  `lab_promotion` proposals now change live behaviour for all 11 deterministic strategies, and
  emitted predictions carry the real version so reflection's per-version metrics stop reading n=0.
- `grid` gains `horizon_s`; `oi_delta` gains `oi_threshold_pct`/`horizon_s`; `oi_breakout` gains
  `oi_threshold_pct`/`horizon_s`/`tp_pct`/`sl_pct` (previously untunable).

## 2026-09-12 — P0.1: cert threshold overrides refused on mainnet

- `trading_safety`: `is_mainnet()` (BYBIT_TESTNET=false or ALPACA_PAPER=false),
  `cert_overrides_active()`, `mainnet_refusal_reasons()`. On mainnet `MATRIX_CERT_*`
  overrides are ignored when granting; grants made under overrides get
  `granted_by … '+relaxed'`; `has_valid_certificate` on mainnet also re-checks the
  cert's evidence snapshot against TRADING.md defaults (catches legacy rows, no migration).
- `execution.safety.should_submit_live` gate 0 + `exchange_shadow._per_trade_allowed`
  both refuse when mainnet + any override is set. TRADING.md hard limit #6.

## 2026-09-12 — Labs eval pipeline unstuck + stall watchdog

- **fix(labs)**: `labs/main.py` called the *sync* `crypto_universe()` inside the
  `asyncio.run(run())` loop. The sync path returns `[]` under a running loop (it
  guards against `asyncio.run()` re-entry), so `emit_signals` iterated 0 symbols
  → 0 lab evaluations since ~2026-06-01 → evolution frozen ("only N ranked
  candidates; waiting"). Switched both call sites to `await crypto_universe_async()`.
  Lab eval now opens ~17 signals/tick again; evolution resumes as evals score.
- **chore(reliability)**: `scripts/stall_watchdog.sh` + `infra/launchd/` agents
  (`make watchdog-install`). macOS suspend freezes Docker VM asyncio timers;
  `restart: unless-stopped` doesn't help (process stays alive, sleep never fires).
  Watchdog restarts loop-services silent past a per-service budget; `caffeinate -s`
  keeps the host awake on AC. (agent-lessons + synthesis had been frozen since
  2026-07-08 from exactly this.)
- **known-gap**: LLM synthesis layer (agent-lessons/synthesis themes, dev_agent
  self-coding) still idle — container cursor CLI unauthenticated + stale images
  lack the binary. Needs `make build` + one-time `make cursor-login-docker` (or
  `CURSOR_API_KEY`). Algorithmic self-learning is restored regardless.

## 2026-09-12 — Otonomi denetimi + planı (`docs/AUTONOMY_PLAN.md`)

- Ajan/tool, memory, self-learning ve insan-bağımlı ops noktaları denetlendi; bulgular dosya:satır
  referanslı. Kritik: 11 stratejide param mutasyonları no-op (dispatcher DB params geçmiyor),
  reflection proposal'ları `asset_class` taşımıyor (BIST → crypto fantom v686), cert eşikleri `.env`
  ile 0 gün / −$50'e gevşetilmiş, dev_agent `merged` sadece DB etiketi (0 gerçek merge), `notify`
  çalışmıyor, retention/backup yok (local DB 97 GB). Sıralı P0-P5 planı ve ilk 10 iş dokümanda.

## 2026-06-02 — US equities market (asset_class='us')

- New `MarketAdapter`: `matrix_shared/markets/us.py` (`UsMarket`, name/asset_class
  `us`). NYSE calendar in `matrix_shared/markets/us_calendar.py` — 09:30–16:00
  America/New_York, US holidays (Good Friday + floating Mondays) + 13:00
  early-close half-days. **Shorting allowed**, **T+1** settlement.
- New `us_symbols` table + `UsSymbol` model. Universe discovered from S&P 500 +
  Nasdaq-100 (Wikipedia, no API key). `market_bars` gains a `market_bars_us`
  partition (migration 0038). Default US wallet + US `matrix_agent` config seeded.
- Ingestion: `ingestion/us/{discover,bars,symbols}.py` + `adapters/us.py`
  (yfinance, no ticker suffix). Entry points `matrix-us-bars` / `matrix-us-symbols`.
- Execution: `execution/adapters/us.py` `UsAlpacaExecutor` — real Alpaca adapter
  that degrades to `health()=False` without `ALPACA_*` creds; live still gated by
  `paper_trade_certificate`.
- Strategy: `strategy/modules/us/` (gap_fade, intraday_reversion, volume_breakout,
  momentum, news_event) — long **and** short; registered in `STRATEGIES_BY_MARKET`.
- Cross-cutting: `backtest/paper_trade.py` short-allow + bar-price selection are now
  market-aware (`get_market(asset_class).allows_short()` + bar-based classes),
  replacing the hardcoded crypto/bist special-cases.
- Web: `/api/markets` gains a `us` row.

## 2026-06-02 — Graph hot-path vs backfill priority (speed-first)

- Ingest: agent only for docs within `GRAPH_HOT_WINDOW_HOURS` (48h); older backlog
  drains via heuristic (8-wide) without blocking Cursor. Backfill upgrades heuristic→agent
  only when idle (`GRAPH_BACKFILL_ONLY_IDLE`, unprocessed=0, no active tick).
- Agent features: prefer precomputed `graph_signals` over AGE cypher when signal is rich.

## 2026-06-02 — Graph backfill agent upgrade fix

- Backfill bypasses stale-doc heuristic shortcut (`force_agent=True`) so queued rows
  actually re-run agent extraction.
- Cooldown (`graph_backfill_at`) applied after failed attempts only, not before.
- Upgrade metric counts `graph_source=agent`; batch 6 / interval 120s / concurrency 2.

## 2026-06-02 — Graph backlog drain fix

- `graph/main.py`: `_fetch_batch` SQL-filters unprocessed docs (was top-N newest then
  filter → 0/tick when recent rows already processed). Higher defaults: batch 50,
  interval 20s, concurrency 6. Stale docs (>7d) use heuristic-only path; backfill
  pauses while unprocessed > 20.

## 2026-06-02 — Mutation proposal auto-apply fixes

- `apply_proposal`: `slot_adjustment` updates `strategy_slot_configs` (was wrongly
  writing `allocated_slots` into strategy_configs.params).
- `apply_best_pending_safe`: lab_promotion uses per-strategy scan threshold (0.05 for
  matrix_agent); slot_scorer adjustments auto-close; default `AUTO_APPLY_MIN_FITNESS=0.05`.
- `slot_scorer`: large slot cuts recorded as `applied` audit rows (change is live in-tick).

## 2026-06-02 — Graph semantic chunking for long-document extraction

- `graph/chunking.py`: sentence/paragraph splits merged by bag-of-words cosine
  similarity; agent runs per chunk, results merged via `parse_extraction.merge_extractions`.
- Backfill + live ingestion both use chunked agent path when body > `GRAPH_CHUNK_MAX_CHARS`.

## 2026-06-02 — Graph heuristic backfill scheduler + dev_agent task #1 docs

- `graph/backfill.py`: background scheduler re-runs agent extraction on docs
  where `graph_source != 'agent'` (batch/interval/cooldown via env).
- `make graph-backfill-once` for manual one-shot batch.
- dev_agent task #1 merged: README/config/prompt align on empty `FORBIDDEN_PATHS`.

## 2026-06-02 — Cursor CLI agent stream (login without CURSOR_API_KEY)

- `cursor_llm.cursor_agent_stream`: when CLI login is active but no API key,
  prefetches read-only tool results and runs `cursor agent -p --stream-json`
  instead of cursor-sdk bridge (fixes graph/synthesis/reflection agent loops).
- Reflection: remove `--no-llm` from compose (LLM mutate path enabled).
- dev_agent: event seq continues from MAX(seq) on task retry.

## 2026-06-02 — Cursor Docker auth: shared config volume

- `matrix_cursor_config:/root/.config/cursor` mounted on all LLM containers
  alongside `matrix_cursor_agent:/root/.cursor` — login tokens live in
  `auth.json` under config, not only in `.cursor`.

## 2026-06-02 — dev_agent: Cursor backend + codebase context graph

- `dev_agent/cursor_runner.py`: tool-loop tasks run via Cursor Auto when
  `MATRIX_LLM_BACKEND=cursor` (shared `matrix_cursor_agent` volume + CLI in image).
- `dev_agent/codebase.py`: indexes repo into `dev_codebase_nodes`; text retrieval
  injected into system prompt; `POST /codebase/reindex`, `make dev-agent-index`.
- `dev_agent/llm_backend.py`: routes Cursor vs Claude SDK; fails fast with
  `llm_not_configured` when neither auth path is ready.
- Docker: `dev_agent` gets Cursor CLI install + auth volume (dev compose).

## 2026-06-02 — Public dashboard overlay (DuckDNS / host :3030)

- `docker-compose.public.yml` + `make up-dev-public`, `make public-ip`, `scripts/duckdns-update.sh`

## 2026-06-02 — Exchange shadow: paper trade mirrors to Bybit testnet

- `matrix_shared/exchange_shadow.py`: when `LIVE_EXECUTION_ENABLED=true`, paper
  opens/closes also submit market orders to Bybit (testnet via `BYBIT_TESTNET`).
  Paper DB stays source of truth; exchange errors are logged only.
- Env: `MATRIX_EXCHANGE_SHADOW=true` (default). Cert + per-trade size gate apply;
  total `LIVE_CAPITAL_CAP_USD` does not block shadow (paper locked can exceed cap).

## 2026-06-02 — BIST 1m bar backfill (strategy preconditions)

- `ingestion/bist/bars.py`: bootstrap + off-session 6h refresh for `1m` bars when missing/stale
- `make bist-bars-backfill`: one-shot `1m` / `5d` pull via yfinance (works outside TR session)

## 2026-06-01 — Cursor Auto LLM backend (`make llm-cursor`)

Third LLM backend alongside subscription and Bedrock. `make llm-cursor` sets
`MATRIX_LLM_BACKEND=cursor`; all LLM calls use **model=auto** (tier pins ignored).
Auth is subscription-style: `cursor agent login` on the host (browser OAuth);
`CURSOR_API_KEY` is optional for CI/Docker. Single-shot calls use `cursor agent -p`;
tool loops use `cursor-sdk` with the same login or API key. Docker: Cursor CLI
installed in graph/agent/brain/synthesis/reflection images; dev compose uses a
`matrix_cursor_agent` volume on `/root/.cursor` (`make cursor-login-docker` or
`docker exec -it matrix-agent cursor agent login` once per machine — do not mount macOS
`~/.local/share/cursor-agent`, it breaks the Linux CLI). Restart stack after
switching. **dev_agent** still uses `claude_agent_sdk` directly (not wired).

---

## 2026-06-01 — Dev CPU limits (docker-compose.limits.yml)

`make up-dev` / `up-dev-local` merge `docker-compose.limits.yml`. Default
**performance** caps: heavy 1.0, db 0.75, worker 0.5, light 0.25. Tighten via
`MATRIX_CPU_*` in `.env`. `make up-dev-core` = trading core only (7 services).
Recreate after limit changes: `--force-recreate`.

---

## 2026-06-01 — Dev watchfiles debounce (restart storm)

`docker-compose.dev.yml` uses `matrix_shared.dev_watchfiles` instead of the
stock `watchfiles` CLI: 2.5s debounce, 2s post-start grace, ignores
`tests/` + tool caches. Tunable via `MATRIX_WATCH_DEBOUNCE_MS` /
`MATRIX_WATCH_GRACE_S`. Recreate dev containers after pull:
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`.

---

## 2026-06-01 — Profit loop fix: align mutations + lab scoring with paper PnL

Self-improvement was optimizing the wrong objective and mutating in the wrong direction:

- **reflection/mutate**: rule path now triggers on negative `total_pnl_usd`, shifts weight toward `oi_delta`/`news`, *lowers* threshold, extends `horizon_seconds`, reduces `explore_epsilon` (reverses the pre-2026-06 heuristic that dampened news and raised threshold). `matrix_agent` added to `PARAM_TUNERS` + safe auto-apply.
- **reflection/metrics**: `win_rate` now counts `pnl_usd > 0`, not `score > 0`.
- **labs/evaluate**: slippage-adjusted PnL via `matrix_shared.trading.apply_slippage`; wins counted on `pnl_pct > 0`.
- **labs/promote**: merge patches into existing params (no more partial overwrites); lab promotions enriched with `tp_pct`/`sl_pct`/`explore_epsilon`; `_params_equivalent` includes `horizon_seconds`.
- **agent**: v3 defaults (1800s horizon, 40/40 oi_delta/news, threshold 0.15, explore 5%); OI score aligned with 5m price direction.
- **migration 0034**: upgrades legacy short-horizon `matrix_agent` configs to v3 params.

Run `make migrate` to apply 0034 on existing DBs.

---

## 2026-06-01 — Seed missing strategy_configs (0035)

Registered crypto/BIST modules were skipped by the dispatcher because no
`strategy_configs` row existed (0026/0027 only UPDATE). Migration 0035
INSERTs grid, funding_reversion, oi_delta + four BIST strategies with slot
configs. Also fixes `uq_strategy_configs_id_ver` → per-asset_class unique
and seeds BIST `matrix_agent`. Run `make migrate-local-shared`.

---

## 2026-06-01 — Re-enable dca + oi_breakout for analysis (0036)

Registry + migration re-open `dca` and `oi_breakout` with slot configs.
Crypto wallet cap raised 50→80 for parallel paper analysis. `bars.py` now
rolls 1m→1h bars so `momentum_xs` can fire. Run migrate + restart
`bars-aggregator` and `strategy`.

---

## 2026-06-01 — matrix_agent slot cap + dedup + bar rollup tuning (0037)

- Migration 0037: `matrix_agent` capped at 18 concurrent slots; other crypto
  strategies rebalanced (sum of caps 64 / wallet 80).
- `agent/main.py`: horizon dedup — no repeat signal per symbol within 1800s.
- `explore_epsilon` lowered to 0.03.
- `bars-aggregator`: lookback 120m + 168h REST backfill on startup (compose).

---

## 2026-06-01 — Profit-first allocation: strategy×symbol fit + dynamic risk

- `matrix_shared/allocation.py`: pair-edge from closed paper trades, EV ranking
  (symbol + strategy perf + pairing), and `risk_multiplier` for notional sizing.
- `paper_trade.py`: candidates sorted by full EV; position size scales with
  confidence, strategy `perf_score`, pair history, and consecutive losses.

---

## 2026-06-01 — Dynamic universe: remove hardcoded crypto fallback

- `crypto_universe()`: no `_DEFAULT_UNIVERSE`; reads `tradable_symbols.active`,
  cold-bootstraps from `screener_universe_snapshot`, else empty.
- Compose: `UNIVERSE_MANAGER_ENABLED/ENFORCE=true` by default; ingestion no
  longer passes BTCUSDT/ETHUSDT on the CLI.

---

## 2026-06-01 — Dev watchfiles crash-loop fix

- `Dockerfile`: `uv pip install watchfiles` after service `uv sync` so dev
  hot-reload works in every service venv.
- `docker-compose.dev.yml`: entrypoint uses `uv run --with watchfiles` +
  `matrix_shared.dev_watchfiles` (debounced reload).

---

## 2026-06-01 — BIST dynamic discover (no embedded seed)

- `ingestion/bist/discover.py`: fetches ~600+ tickers from configurable JSON
  endpoint (default Fintables public API); upserts `bist_symbols`, deactivates
  delisted. Embedded `BIST_SEED` list removed.
- BIST ingestor runs discover on startup + daily refresh (`BIST_DISCOVER_INTERVAL_S`).
- `make bist-seed` → `matrix-bist-symbols --bootstrap-active` via ingestion-market.

---

- Foundation docs created: `VISION.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `WORK_SPLIT.md`
- `CLAUDE.md` written with two-machine instructions
- Monorepo skeleton scaffolded (pnpm workspace)
- Tech stack defaults locked in `docs/ARCHITECTURE.md` (Next.js 16 + Python 3.13 + Neon + pgvector + AGE)

## 2026-05-23 — Pivot: trading-first, local-first, node-based

User clarified priorities after seeing the initial foundation. Three changes:

1. **Local-first**: removed Neon dependency. Local Postgres via `docker compose up -d`. Cloud (Neon + Vercel) only when bulletin product launches in Phase 6+. Added `docker-compose.yml` + `infra/db/Dockerfile` (apache/age + pgvector layered).
2. **Node-based**: dropped the fixed "Machine A / Machine B" model. Now `N` nodes can be added; each declares roles in `.matrix-node.json`. See revised `docs/WORK_SPLIT.md`.
3. **Trading-first**: bulletin product deferred to Phase 6+. Primary product is the autonomous self-improving trading system. New `docs/TRADING.md` with code-enforced risk framework. Roadmap rewritten around Phase 1-5 = build-then-paper-trade-then-small-live.

First market choice: **crypto perpetuals** (Bybit testnet) — 24/7 markets, low-friction testnet, smaller minimum capital, simpler regulatory positioning than equity options.

Next action: Phase 1 begins. First concrete coding task = bring up local DB + smoke-test Python service that writes a row.

## 2026-05-26 — Phase 5 gate: paper_trade_certificate table + has_valid_certificate()

Until this commit, `docs/TRADING.md` and the in-memory project rules promised a "code-enforced certificate gate" between paper trading and live capital, but no such table or check existed. Filled the gap before any live execution code is written, so the gate is in place when `services/execution/` lands.

- Migration `0011_paper_trade_certificate.py` (applies to LOCAL + SHARED tiers; runs via `make migrate` + `make migrate-local-shared`).
- `matrix_shared.models.PaperTradeCertificate` — one row per (strategy_id, asset_class, version). Status: pending|granted|revoked|expired. Carries evidence snapshot (n_outcomes, win_rate, total_pnl_usd, max_drawdown_pct, sharpe_ratio) and lifecycle fields (granted_at, granted_by, validity_until).
- `matrix_shared.trading_safety` — `has_valid_certificate(strategy_id, asset_class, version) -> bool` is the gate; `LIVE_EXECUTION_REQUIRES_CERT = True` is the hardcoded constant. `evaluate_eligibility(...)` reads outcomes + computes certificate-grant evidence. Drawdown anchored to a $10k reference (avoids the "tiny peak → huge percentage" trap when running PnL hovers near zero).
- Also made migration 0010 idempotent (`ADD COLUMN IF NOT EXISTS`), since the dev_agent test conftest seeded `dev_tasks.review_mode` directly into LOCAL — `make migrate` would otherwise fail with DuplicateColumn on first run.

Smoke-verified on live data: gate denies missing cert; eligibility for matrix_agent v1 returns `eligible=False` with reasons `[observation_days=1<60, win_rate=0.099<0.40, total_pnl=-17.32<0]` — exactly what we want pre-Phase-5.

## 2026-05-26 — matrix_agent diagnosis: signal direction works at long horizon, slippage kills short horizon

First empirical study of why the LLM-blended agent has been losing money. Used the historical replayer (Phase 3.5) to run three sweeps on 7d of BTCUSDT:

**Horizon sweep** (default weights, threshold 0.18). Win rate climbs monotonically with horizon:
- 60s → 4.76%
- 120s → 8.57%
- 300s → 22.86%
- 600s → 28.57%
- 1800s → 48.57%

Direction is roughly right at long horizons (approaching 50/50 = chance). At short horizons, the 0.04% round-trip slippage swamps everything.

**Threshold sweep** (horizon 120s). Tighter threshold → WORSE win rate (15.5% → 0% as threshold goes 0.10 → 0.35). "High conviction" trades aren't more predictive; they're extrema.

**Signal attribution** (each signal solo, threshold 0.01). At h=120s nothing carries alpha: trade_flow 13%, funding 16%, oi_delta 17%, ob_imbalance 16%, news 18%. At h=1800s, only oi_delta (42% win, -$6.86) and news (38% win, -$26.94) are even close to break-even.

Verdict: at current horizons the blend is averaging noise. Total PnL stays negative across horizon settings because losers are systematically bigger than winners — direction salvageable, magnitude not (yet). Next step is TP/SL in the replayer + a config that's oi_delta + news heavy at long horizons. Deployed `matrix_agent v3` (40% oi_delta, 40% news, threshold 0.15, horizon 1800s) as the hypothesis test — needs 7+ days of live paper data to evaluate.

CLI: `make diagnose-matrix-agent SYMBOL=BTCUSDT DAYS=7` reproduces this on demand. Operator should re-run weekly and compare deltas.

## 2026-05-26 — Market parity: pluggable MarketAdapter (Phase A-G)

BIST and crypto pipelines were "half-symmetric" — DB had asset_class, ingestion was two services, strategies mixed flat-crypto + bist-subfolder, agent_lessons + wallet had no per-market split. This commit chain reshapes the whole pipeline around a single abstraction so adding a new market = one adapter + a registry entry, not a refactor of every service.

**Core:** `packages/python-shared/src/matrix_shared/markets/` — `MarketAdapter` ABC + `FeeModel` + `ExecutionAdapter` / `IngestorAdapter` protocols; `register/get_market/all_markets/infer_market` registry; concrete `CryptoMarket` + `BistMarket`. `infer_market(symbol)` routes by claims_symbol; BIST cache primes on universe refresh, falls back to regex pre-warm.

**Pipeline phases:**
- B (strategy): `modules/crypto/{dca,grid,funding_reversion,oi_breakout,oi_delta}.py` mirrors `modules/bist/`. main.py dispatcher walks all_markets() × STRATEGIES_BY_MARKET. `trade_flow_imbalance` removed (retired 2026-05-26).
- C (wallet): migration 0017 adds `wallets.asset_class` (default `crypto`), drops single-column `name` unique, adds composite `(name, asset_class)`. risk_caps.yaml provides per-market seed defaults (BIST gets fewer slots + wider DD).
- D (execution): per-market `ExecutionAdapter` factories. `CryptoLiveExecutor` wraps the existing BybitConnector; `BistLiveExecutor` stub raises NotImplementedError on every action (Phase 1 wall). Daemon heartbeat walks all_markets() so the not-wired markets show up next to wired ones.
- E (agent_lessons): migration 0018 adds `agent_lessons.asset_class`. synthesize + feed_once take an asset_class kwarg; per-market active-lesson index + filter. agent_lessons daemon runs one synth+feed pass per market each cycle. agent main.py uses MarketAdapter.is_session_open + universe — no more hard-coded BIST helpers.
- F (ingestion): `services/bist-ingestion` folded into `services/ingestion`. ingestion.main is a market fan-out (asyncio task per registered adapter). Compose `bist-ingestion` service deleted; one image, one log stream.
- G (web): `/api/markets` route mirrors the registry with a live stats join (universe, predictions, positions, wallet, realised PnL). `/api/dashboard?market=` filters recent predictions + outcomes. Dashboard gains a "Registered market adapters" panel.

Out of scope (Phase 1+): BIST live broker integration (AlgoLab / Garanti API); MarketAdapter ABC for paper-trade engine (still in services/backtest, not behind ExecutionAdapter); cross-market lesson transfer; dynamic capital allocation across markets.

## 2026-05-27/28 — Agent-driven architecture + Brain chat (branch `feat/agent-driven-brain`)

Operator brief: re-architect the system to be agent-driven, keep context in the knowledge graph, expose a super-intelligent "ask anything" chat across web + Telegram + API. Strangler-fig migration; the paper-trade loop and the live-execution certificate gate stay code-enforced throughout.

**Framework override** (vs. `ARCHITECTURE.md:110` naming LangGraph): the runtime is a thin layer built ON `claude_agent_sdk`'s native tool loop (proven in `services/dev_agent/src/dev_agent/sdk_runner.py`) — multi-step tool loops on subscription auth via in-process MCP servers (`create_sdk_mcp_server` + `@tool`), `can_use_tool` deny-hook, `max_turns`. LangGraph's tool loop expects an API-key chat client; that's exactly the path this repo blanks out (`docker-compose.yml` clears `ANTHROPIC_API_KEY`). Reuse the SDK loop; don't pay for LangGraph to get a state machine you can express in ~200 lines.

**Shared agent runtime** (`packages/python-shared/src/matrix_shared/agent_runtime/`): `runtime.py` `run_agent_stream` (normalized AgentEvents, bounded by `max_turns` + a `can_use_tool` deny-hook + one shared rate-limiter slot); `tool.py` `Tool`/`ToolRegistry`/`@tool` with side-effect classes (`read`/`write`/`risk-gated`); `guards.py` read-only SQL (SELECT/WITH only, table allow-list, auto-LIMIT) + Cypher (rejects CREATE/MERGE/SET/DELETE/...) gates; `ratelimit.py` process-shared semaphore. `subscription_llm.call_subscription_agent` is the tool-loop entry on the subscription path (keeps single-shot `call_subscription` intact).

**Matrix Brain** (`services/brain`, port 3032): Opus-4.7 read-only "ask anything" agent. FastAPI SSE `/chat`, conversation persistence (`chat_sessions` / `chat_messages`, migration 0020). Tool belt: `list_tables`, `describe_table`, `sql_read` (allow-listed, tier-routed crypto/graph LOCAL vs domain SHARED), `cypher_query` (read-only AGE). Hard guardrails: no write tool registered; `disallowed_tools=[Bash,Write,Edit,NotebookEdit]`; `can_use_tool` deny-hook; `assert_all_read_only` at startup. The Brain cannot place trades, move money, or grant certificates.

**Chat surfaces**: web `/chat` page + thin `/api/chat` SSE pass-through (no AI SDK in JS — the model lives in Python on subscription); Telegram free-text via `services/notify` extension routes to the same brain SSE (session `external_ref` = chat id).

**Context-graph overlay** (`services/graph/overlay.py`): reasoning episodes — `Prediction`/`Decision`/`Outcome`/`Lesson`/`Strategy` — layered on the AGE graph as an INDEX over the ACID relational rows (node keys = relational PKs; money/risk stay authoritative in Postgres). Best-effort idempotent MERGE; a failed graph write never breaks the relational write. The agent dual-writes a Prediction node (+ `PRODUCED` from Strategy, `PREDICTS` Asset via BTCUSDT→BTC canonical) when it persists. `graph_summary` counts overlay labels so the dashboard sees them.

**Exploration (more positions → faster learning)**: epsilon-greedy at `decide.maybe_explore` — with `AgentConfig.explore_epsilon` (default 0.15) flips a `hold` into a low-confidence trade in the sub-threshold lean, tagged `is_exploration` (lands in `prediction.context`). Runs BEFORE the lessons gate so `avoid` lessons still veto; respects BIST long-only. Concurrency cap raised: `wallets.max_concurrent_positions` 5 → 15 (migration 0021 lifts existing wallets still at the old default). Fixed an interaction bug: `agent.main` `confidence<0.1` floor was dropping exploration trades — now bypassed when `is_exploration`.

**BIST / crypto data separation**: `market_bars` is LIST-partitioned on `asset_class` (migration 0022); crypto and BIST rows live in physically separate partitions (`market_bars_crypto` / `market_bars_bist` / `_other` DEFAULT). PK becomes `(id, asset_class)` and the `(symbol, interval, ts)` uniqueness becomes `(asset_class, symbol, interval, ts)` named `uq_market_bars_class_sit` (partitioning requires the partition key in every unique/PK constraint). All existing ORM queries unchanged; ingestion upserts retargeted. `market_trades` is already crypto-only (BIST has no tick data) so unchanged.

**First strangler-fig conversion**: `services/synthesis` drives the SDK tool loop with read-only tools (`recent_documents`, `existing_concepts`, `existing_assets`) so themes are grounded in existing graph coverage and don't duplicate Concepts. Legacy single-shot path kept as fallback. Concept upserts stay in service code — the LLM never touches graph state.

Remaining conversions (sequencing — each behind the stable `predictions` interface, with `rule_decide` fallback preserved on the hot path): graph extraction → reflection + agent_lessons → labs → decision agent (last) → execution (only when broker connector lands; `submit_order` will be `risk-gated`, never converted to an agent decision).

Risks tracked: subscription rate/latency × many multi-step agents (mitigated by the shared `agent_runtime.ratelimit` semaphore and deterministic fallbacks on the hot path); graph/SQL divergence (SQL is source of truth, graph writes idempotent + best-effort, never read money/cert from graph); brain data exposure (read-only belt + SQL/Cypher gates + `disallowed_tools` + deny-hook, defense in depth).

## 2026-05-28 — Token optimization + birincil amaç netleştirildi

- Operatör gözlemi: Brain her sorguda Opus 4.7 koşturuyordu (~$0.20/turn); rate bütçesi
  decision loop ile paylaşılıyor. 6 commit'lik token-disiplini turu:
  - `agent.usage` telemetrisi (`subscription_llm` tek nokta, brain SSE result.model). Önce ölç.
  - Brain default `claude-sonnet-4-6`, `BRAIN_MODEL=opus` veya per-istek `{"model":"…"}` opt-in.
    Aynı sorguda **Opus $0.197 → Sonnet $0.022, ~9× ucuz**.
  - Tool çıktıları: brain `sql_read`/`cypher_query` default head/tail+summary (`_truncate_row`
    + `_summarize_rows`), `verbose=true` ile eski davranış. Synthesis/graph/reflection backend
    agent'larında MAX_DOCS, BODY_CHARS, recent_outcomes LIMIT'leri sıkıştırıldı (verbose opt-in).
  - System prompt'lar modül-seviye literal'a alındı (reflection/graph extract): CLI içi prompt
    cache'e tek seviyemizden uygun zemin. `claude_agent_sdk`'da explicit `cache_control` yok.
  - `make agent-usage` operatör görünürlüğü.
- `CLAUDE.md`: "Birincil amaç — her zaman yüksek profit" bölümü en üste eklendi; her teknik
  kararın aktif süzgeci olarak. Token tasarrufu kendi başına amaç değil — rate-budget'ın
  decision loop'a kalması içindir.

## 2026-05-28 (later) — Worker pattern (Haiku distillation) — proven live

3 commit'lik mini-tur: orchestrator/worker model tieringi gerçek koda taşındı.

- `matrix_shared.agent_runtime.worker.haiku_distill`: tek-atış Haiku 4.5 çağrısı (subscription path), opsiyonel/ücretsiz; subscription kapalıysa deterministic truncate fallback.
- İlk uygulama: `synthesis.recent_documents` tool'u artık DAİMA Haiku-distilled 5-10 bullet özeti döner. `verbose=true` opt-out kaldırıldı (operatör memory'sindeki ders: model verbose'a kaçıyor — distillation tool seviyesinde *yapısal* enforce edilmeli).
- `call_subscription` single-shot path da `agent.usage` log atıyor (Haiku worker + legacy fallback'ler artık `make agent-usage`'da görünür).

Canlı ölçüm (aynı synthesis tick'i, öncesi/sonrası): Sonnet orkestratör cost **$0.183 → $0.086 (−53%)**, Haiku worker $0.032 eklenince **total $0.118 (−35%)**. Tema sayısı 7 → 4 (recall düştü, precision yüksek; özetin verdiği focus daha az gürültü demek).

Sonraki uygulama alanları (canlı veri toplandıkça): brain `sql_read` (büyük tablolar), reflection `recent_outcomes` (uzun PnL izleri), brain `cypher_query` (uzun sonuç listeleri).

## 2026-05-28 (later still) — Provider-portable LLM (subscription / Bedrock / Vertex)

`claude_agent_sdk` zaten backend seçimini standart env'lerle yapıyor; iki düzeltme yetti:

1. **Hardcoded model adları indirection'a alındı.** `matrix_shared.subscription_llm`'de
   `MODEL_HAIKU / MODEL_SONNET / MODEL_OPUS` modül-seviye sabitleri, `MATRIX_MODEL_<role>`
   env'inden çözülür (yoksa Anthropic kısa adları — eski davranış). 10 call site bu sabitleri
   kullanır.
2. **Compose env passthrough.** `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`,
   `AWS_REGION/AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN/AWS_PROFILE`,
   `MATRIX_MODEL_<HAIKU|SONNET|OPUS>` artık `*python-env` anchor'ında.

`subscription_enabled()` tek bir provider'dan biri yapılandırıldıysa True döner (subscription /
Bedrock / Vertex) — isim geriye dönük uyum için korundu, anlamı "LLM yolu hazır mı?".

**Subscription operatörü için**: hiçbir değişiklik gerekmiyor.

**Bedrock'a geçmek için** `.env`'e şunları yaz, `make build && docker compose up -d` ile yenile:

```
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
MATRIX_MODEL_HAIKU=us.anthropic.claude-haiku-4-5-20251001-v1:0
MATRIX_MODEL_SONNET=us.anthropic.claude-sonnet-4-6-20250929-v1:0
MATRIX_MODEL_OPUS=us.anthropic.claude-opus-4-7-20251022-v1:0
```

Brain + 5 backend agent (synthesis / graph extract / reflection / decision LLM / bulletin) + Haiku
worker birlikte flip eder. `CLAUDE_CODE_OAUTH_TOKEN` set'liyse Bedrock yine de override eder
(CLI'nın iç önceliği bu yönde).

## 2026-09-20 — Kanıtlanmış edge'e quarter-Kelly boyutlandırma

`allocation.kelly_fraction_of_equity` + `kelly_notional`: her iki null'ı da geçen (`edge_study.verdict == "pays"`)
stratejiler için pozisyon büyüklüğü, ölçülen edge'in **%95 alt sınırından** hesaplanır — maliyet düşülür, eşzamanlı
pozisyon sayısına bölünür, çeyrek Kelly alınır, `KELLY_MAX_F` ile tavanlanır. Cüzdanın `max_position_pct` risk kapısı
her dalda tavan olarak kalır ve Kelly yalnızca **büyütebilir**, slot kapısının izin verdiği bir işlemi susturamaz.
Kanıtsız stratejilerin boyutlandırması aynen korunur. Edge satırına `sd_bps` eklendi (varyans olmadan Kelly hesaplanamaz).

Gerekçe: boyutlandırma bugüne dek `risk_multiplier` üzerinden **gerçekleşmiş PnL**'e bakıyordu — yani bozuk fill'lerin
kirlettiği sinyale. Ölçülen edge farklı bir soruyu yanıtlıyor ve tek kanıtlanmış strateji (`momentum_xs`) bu kuralla
%2'lik kapının hemen altına, ~%1.6 equity'ye boyutlanıyor (önceki efektif ~%0.9).

## 2026-09-20 — Promosyon barı: sermaye için kanıt eşiği

`matrix_shared/promotion.py`. Bir stratejinin sermaye tutabilmesi için üçü birden gerekli:
**Benjamini-Yekutieli** (on üç test aynı bar/sembol/pencerede ölçüldüğü için BH'nin bağımsızlık
varsayımı geçersiz; BHY keyfi bağımlılıkta geçerli, m=13'te 3.18 kat sıkı), **deflated Sharpe**
(aranmış olmanın yarattığı yukarı sapmayı düşer; eşik 0.95), ve **önceden kayıtlı örneklem
hedefi** (edge'in yarısının hâlâ tespit edilebilir olması için gereken n; kayıt asla üzerine
yazılmaz). Yalnız `confirmed` durumu `pays` verdict'ini kazanır; `status` yoksa eski davranış
korunur, okunamayan bir registry kitabı aç bırakmasın diye.

Ölçülebilenden büyük hedef kaydeden strateji `unprovable` işaretlenir — sonsuza dek "beklemede"
bırakmak, cevabı soru gibi göstermek olurdu.

momentum_xs/crypto: n=1989, hedef 936, +29.7 bps (t=7.85), DSR 1.000 → **confirmed**.

## 2026-09-20 — Retention sekiz gündür hiçbir şey silmiyormuş

`retention` beş dakikada bir "pruned market_trades=200" yazıyordu; tablo ise 322M satırdı ve
1 Haziran'a kadar gidiyordu (politika 7 gün), local DB 112 GB, disk %78 dolu. Sebep kod değil
**sorgu planı**: `LIMIT`'li iç SELECT seq scan seçiyor, eski satırı olmayan bir sembolde LIMIT
hiç dolmuyor, tarama 322M satırı okuyup 45 saniyelik bütçenin tamamını yiyordu. `ORDER BY ts`
indeksi zorunlu kılıyor: boş sembol dakikalardan 0.9 ms'ye düşüyor. Tabloya ayrıca ilk kez
`ANALYZE` çekildi.

Açık kalan, plan hatasından büyük: sembol-başına silme bu tabloda yapısal olarak yavaş
(append-only, ~900 sembol iç içe, tek sembolün eski satırları 97 GB'a dağılmış). Kalıcı çözüm
zaman-bazlı partition veya son 7 günü tutan tek seferlik yeniden yazım — ikisi de operatör kararı.

## 2026-09-20 — Ölçülmüş modeller tek paylaşılan dizinde

`matrix_shared/model_store.py`: `symbol_costs.json`, `edge_cache.json` ve `edge_registry.json`
artık tek bir çözücüden geçiyor (`~/.claude/matrix_models`, `matrix_claude_config` volume'ü).
Öncesinde ikisi volume olmayan `/var/lib/matrix/models`'e yazıyordu, yani her konteynerin kendi
kopyası vardı: `reflection` edge cache'ini göremeyip her restart'tan sonra "ölçüm yok"u "edge yok"
sayıyor ve momentum_xs'i 7 slottan 3'e düşürüyordu; ön-kayıt registry'si de konteyner başına bir kez
yazılıyordu. Slot scorer'a ayrıca `unknown` ≠ `unproven` ayrımı eklendi — kanıtla alınmış tahsis,
ölçüm geçici olarak okunamıyorken geri alınmıyor. Gerçekleşmiş zarar dalı eski davranışını koruyor.

## 2026-09-20 — Motorda boşta nabız

`backtest/main.py` iki dakikada bir `heartbeat: idle` yazıyor. Motor yalnız iş yaptığında log
yazdığı için boştaki motorla ölü motor dışarıdan aynı görünüyordu; bu belirsizlik eylülde üç gün,
bugün yirmi iki dakika kesintiye yol açtı ve her ikisinde de konteyner "running" diyordu. Artık log
tazeliği güvenilir bir canlılık sinyali. `MATRIX_ENGINE_HEARTBEAT_S` ayarlar.

## 2026-09-20 — market_trades zaman indeksi ve autovacuum

Göç 0039 `(trade_ts)` indeksini CONCURRENTLY ekliyor (3.4 GiB), 0040 ise bu tabloya
`autovacuum_vacuum_scale_factor=0.02` veriyor. İkisi retention'ın global zaman sıralı süpürmesi için:
indeks olmadan sıralı süpürme 300M satırı sıralamaya kalkıyor, autovacuum olmadan da silinen satırlar
boş-alan haritasına dönmediği için dosya küçülmek yerine büyüyor. Uyarı: yeni indeks, aynı tabloya
dokunan başka sorguların planını da değiştirir — eklendikten sonra `ANALYZE` çek ve zaten hızlı olan
sorguları düz `EXPLAIN` ile yeniden kontrol et.
