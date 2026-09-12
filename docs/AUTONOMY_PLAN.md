# Matrix — Otonomi Denetimi ve Planı

> Tarih: 2026-09-12. Kapsam: ajan/sub-agent/tool mimarisi, memory, self-learning, insan-bağımlı
> operasyon noktaları. Her bulgu kaynak dosyaya referanslı; her iş için kabul kriteri var.
> Bu doküman `docs/ROADMAP.md`'nin Phase 4-5 kısmını fiilen yeniden yazar.

---

## 1. Amaç ve hedef (tek cümle + ölçülebilir)

**Amaç:** İnsan müdahalesi olmadan 7/24 çalışan, kendi stratejilerini üretip ölçen, işe yaramayanı
öldüren, işe yarayanı küçük canlı sermayeyle çalıştıran ve bunu kendi kodunu düzelterek sürdüren
bir kâr motoru.

**Hedef (sıralı gate'ler, hepsi otomatik ölçülür):**

| Gate | Ölçüt | Şu an |
|---|---|---|
| G1 Sürekli çalışma | 30 gün kesintisiz tick; ingestion gap < %5; hiçbir servis 1 saatten uzun sessiz kalmaz | ❌ 10 Haz → 12 Eyl arası ~3 ay kapalı kalmış |
| G2 Öğrenme döngüsü kapalı | Uygulanan her mutasyonun 7 gün sonrası PnL etkisi ölçülür; efficacy < 0 olanlar otomatik geri alınır | ❌ efficacy takibi yok, rollback yok |
| G3 Paper pozitif EV | En az 1 strateji, 60 gün, ücret+slippage sonrası PnL > 0, DD < %15, n ≥ 200, %95 CI alt sınırı > 0 | ❌ 36k trade, toplam **−$421**; tüm stratejiler negatif (oi_breakout +$6 hariç) |
| G4 Kendi kodunu düzeltir | dev_agent'ın ürettiği patch test edilir, main'e merge olur, sonucu ölçülür | ❌ 9 task, 0 merge (status='merged' sadece DB etiketi) |
| G5 Canlı küçük sermaye | Yalnızca G3 geçen strateji, `LIVE_CAPITAL_CAP_USD` altında, insan yalnızca flag'i açar | ⚠️ gate env ile 0 gün / −$50'e gevşetilmiş |

İnsanın **tasarım gereği** kalacağı üç nokta: `LIVE_EXECUTION_ENABLED`, `LIVE_CAPITAL_CAP_USD`
ve mainnet API key. Bunlar dışında her karar otomatik olmalı.

---

## 2. Mevcut durum — sayılarla (2026-09-12, shared DB)

- Predictions: 274.922 (1 Haz–12 Eyl). Haftalık dağılım: 200k, 74k, **136, 351**. Sistem 10 Haziran'dan
  bugüne fiilen kapalıymış; "3.5 ay çalıştı" değil, **9 gün çalıştı**.
- Kapalı paper trade: 36.657, toplam PnL **−$421** ($10k başlangıç). Win rate %41-44 (crypto), %13-16 (BIST).
- Mutation proposals: 2.675 adet 10 günde; 1.788 `param_tune` + 883 `slot_adjustment` **otomatik uygulanmış**.
- `strategy_configs`: `bist_volume_breakout` **v686**, `bist_gap_fade` v564, `bist_intraday_reversion` v534 —
  hepsi `asset_class='crypto'` altında (bkz. §5 bug #3: BIST proposal'ları crypto olarak yazılıyor).
- Certificate: 7 adet `granted`, `granted_by='auto-eligibility'`; `.env` eşikleri 0 gün / 20 outcome / −$50.
- `LIVE_EXECUTION_ENABLED=true` (.env:26). Mainnet'ten tek fark `BYBIT_TESTNET=true`.
- Local DB **97 GB**: `market_trades` 85 GB (302M satır), `market_orderbook_snapshots` 11 GB. Retention yok.
- `wallet_snapshots`: 272k satır, 5 saniyede bir, her tick'te taranıyor.
- `backups/` dizini **hiç oluşmamış**. `notify` servisi profile-gated, **çalışmıyor**; Telegram token boş.
- LLM backend: `cursor` tek başına, `CLAUDE_CODE_OAUTH_TOKEN` boş → cursor oturumu düşerse fallback yok, alarm yok.

---

## 3. Ajan / sub-agent / tool mimarisi

### Ne var
- Ortak runtime: `matrix_shared/agent_runtime/` (SDK tool loop, `@tool`, `SideEffect` sınıfları
  `read/write/risk-gated`, read-only SQL/Cypher guard, process-içi semaphore).
- 8 bağımsız daemon-ajan: decision agent (15s, tek-atış, **tool'suz**), brain (read-only chat),
  graph extract, synthesis, reflection (SDK loop, 2-3 read tool), dev_agent (SDK built-in tools,
  worktree), Haiku distill worker, lesson feeder (SQL → `dev_tasks`).

### Kritik boşluklar
1. **Orkestratör yok.** N bağımsız container; ajanlar birbirini görmez, ortak plan/öncelik yok.
   Tek ajan-ajan bağı `agent_lessons/feeder.py:120` → `dev_tasks` (fire-and-forget).
2. **Sub-agent delegasyonu yok.** `agent_runtime/worker.py:20` bunu açıkça söylüyor; `haiku_distill`
   tek yerde kullanılıyor (`synthesis/tools.py:82`). Brain/reflection/graph kendi truncation'ını elle yazıyor.
3. **Tool taksonomisi kâğıt üstünde.** Repo'daki 12 tool'un hepsi `read`. `write` ve `risk-gated`
   sınıfı hiç kullanılmadı (`agent_runtime/tool.py:12-23`).
4. **Para kararı veren tek ajanın tool'u yok.** Decision agent tek-atış metin çağrısı
   (`agent/llm.py:33`); graph özelliklerini, lesson'ları, kendi geçmiş outcome'larını, cüzdan/pozisyon
   durumunu **görmez** (`decide.py:260-275`). Lesson'lar LLM'den *sonra* mekanik veto (`decide.py:389`).
5. **LLM, rule kararını koşulsuz ezer** (`decide.py:365-376`). Uyum kontrolü, confidence blend, method-level A/B yok.
6. **Cursor backend'de güvenlik hook'ları düşüyor.** `disallowed_tools` + `can_use_tool`
   `cursor_agent_stream`'e iletilmiyor (`subscription_llm.py:468-482`); CLI `--trust --workspace /tmp`
   ile açılıyor (`cursor_llm.py:322-334`). "Read-only brain" cursor modunda shell alıyor.
7. **Rate limiter process-lokal** (`ratelimit.py:11`): 5 container × 3 slot, tek subscription'a karşı.
8. **Telemetri log-only**: `agent.usage` grep ile okunuyor (`Makefile:216`); persist yok, bütçe yok.
9. dev_agent: per-task cost cap gerçek SDK'da hiç tetiklenmiyor (`sdk_runner.py:202-224`); daily cap
   okunup kullanılmıyor (`config.py:45`); `reap_stuck_running` yazılmış, test edilmiş, **çağrılmıyor**
   (`runtime.py:67`); worktree açılamazsa ajan canlı repo'da çalışıyor (`worker.py:82-88`);
   `codebase.py` indeksi üretiliyor ama prompt'a `""` geçiliyor (`worker.py:107`).

---

## 4. Memory

### Ne var (hot path'te olanlar)
- `agent_lessons` (istatistiksel, symbol×side, 7g pencere) → decision veto/boost.
- `tradable_symbols.score` (per-symbol edge) → ε-exploration ölçeği.
- Pair edge (`allocation.py`, 14g, Bayesian shrink) → EV sıralama + notional.
- Graph domain memory (AGE, 24h) + `graph_signals` (120s materialize) → `news` sinyali (ağırlık 0.40).

### Kritik boşluklar
1. **Reasoning overlay write-only.** Sadece `Prediction` node yazılıyor (`agent/main.py:105`);
   `Outcome`/`Lesson`/`Strategy` node'ları ve 3/4 edge tipi hiç yazılmıyor (`overlay.py:73,118,178`);
   hiçbir şey `:Prediction` traverse etmiyor. "Ne kararlaştırdık → ne oldu → ne öğrendik" sorgusu boş döner.
2. **Embedding sıfır.** pgvector kurulu, 2 `vector(1536)` kolon + ivfflat index var, **hiçbir kod
   embedding hesaplamıyor**. tsvector kolonu hiç yok. Hybrid retrieval yok (ROADMAP Phase 2 açık).
3. **`predictions.context` hiç okunmuyor.** Feature dump + LLM confidence + `lesson_override` audit'i
   her trade'de yazılıyor (`decide.py:423,447`), hiçbir karar/yansıma/değerlendirme okumuyor.
   Sistemdeki en zengin kullanılmayan bellek.
4. **Lesson kendini kilitliyor.** `avoid` lesson → o bucket'ta trade yok → outcome yok → lesson asla
   yeniden değerlendirilemez. Exploration lesson'dan *önce* çalışıyor (`decide.py:381-389`), o da veto yiyor.
   Yaşa bağlı TTL yok; confidence sadece n'e bağlı, etki büyüklüğüne değil (`synthesizer.py:68`).
5. **Lesson efficacy yok.** Ne `agent_lessons` ne `dev_agent_lessons` "uyulunca kâr etti mi" tutuyor.
   `helpful_count` hiç artmıyor; `hit_count` retrieval'ı ölçüyor, faydayı değil (`memory.py:86`).
6. **Regime memory yok.** `regime` sadece yorumlarda. Lesson, edge, ağırlık — hepsi rejim-kör.
7. **Operatör tercihi kalıcı olmuyor.** Brain tamamen read-only; chat'te söylenen hiçbir şey başka
   ajana ulaşmıyor. Cross-session memory yok (`load_recent(limit=20)`).
8. **Incident memory yok.** `notify` `PollSnapshot` process-içi; restart'ta kaybolur. Circuit trip,
   kill switch, shadow-order hatası hiçbir yerde sorgulanabilir kayıt değil.
9. Bug: `lessons_relevant_to` `asset_class` geçirmiyor (`matrix_shared/agent_lessons.py:166`) —
   kendi testi bunun tersini assert ediyor. `strategy/lessons.py:52` confidence filtresi yok,
   `decide.py:407` 0.4 istiyor.

---

## 5. Self-learning

### Döngü (otomatik / eksik)
```
signal → prediction        AUTO   (matrix_agent gerçek version; diğer 11 strateji version=1 sabit)
prediction → paper pos     AUTO   EV sıralı, risk_multiplier
pos → outcome              AUTO   TP/SL, horizon; 2 bps slippage, ücret yok, funding yok
outcome → metric           AUTO   600s; asset_class filtresi YOK
metric → proposal          AUTO   rule + LLM (LLM ungated, hâlâ "average score" hedefli)
proposal → apply           AUTO   lab_promotion / slot / param_tune (11 strateji) → insan yok
apply → canlı etki         ❌     11 stratejide params hiç okunmuyor
apply → ölçüm (efficacy)   ❌     yok
efficacy → rollback        ❌     yok
```

### Kritik bug'lar (öğrenme sessizce ölü)
1. **Param mutasyonları 11 stratejide no-op.** Dispatcher `cls(symbols=symbols)` / `cls()` ile
   kuruyor, DB params geçmiyor (`strategy/main.py:97-98`). Sadece `matrix_agent` `strategy_configs`
   okuyor (`agent/config.py:70-118`). 1.788 param_tune uygulandı; **hiçbiri kodu etkilemedi**.
2. **İlk apply'dan sonra öğrenme durur.** Modüller `strategy_version=1` damgalıyor
   (`funding_reversion.py:105`); reflection metric'i aktif config'in versiyonuna kilitli
   (`metrics.py:40`). v2'ye geçince `n_outcomes=0` → bir daha proposal yok.
3. **Reflection proposal'ı `asset_class` taşımıyor** (`reflection/main.py:109-122`) → default `crypto`.
   BIST stratejileri crypto altında v686'ya kadar fantom config üretti. Dedup de asset_class'ı görmüyor.
4. **Certificate gate env ile devre dışı.** `trading_safety.py:70-100` env override; `.env:29-33`
   0 gün / 20 / 0.35 / −$50 / %50. İki daemon otomatik cert basıyor. `TRADING.md` 60 gün + pozitif der.
5. **İstatistiksel rigor sıfır.** Significance, holdout, walk-forward, çoklu karşılaştırma, CI — repo'da
   tek hit yorum satırı (`labs/main.py:45`). Promotion = `fitness ≥ 0.05 and n ≥ 30`. Slot scorer
   son **10** trade'e bakıp 2×/¼× sermaye kararı veriyor (`slot_scorer.py:26`).
6. **Labs örneklemesi bağımsız değil.** 20s'de bir aynı symbol için üst üste 180 örtüşen eval;
   `n_evaluations` bağımsız sayı değil (`evaluate.py:34-100`). Best-of-20 seçim in-sample.
7. **Train/serve mismatch.** Genom TP/SL'siz değerlendirilir, promote edilirken `tp=0.02 sl=0.01`
   enjekte edilir (`promote.py:130-142`).
8. **Maliyet modeli iyimser.** 2 bps flat; Bybit taker ~5.5 bps; spread/impact/funding yok
   (`paper_trade.py:76`). Döngü gerçekte olmayan bir edge'e optimize oluyor.
9. **Selection bias.** Sadece slot kazanan prediction outcome üretir; reddedilenler hiç ölçülmez
   (`paper_trade.py:363-386,506-520`). Counterfactual yok.
10. Orphan flat-close `pnl=0, score=0` satırları cert ve mutasyon eşiklerine sayılıyor (`paper_trade.py:753`).
11. Web apply route `to_version`'ı olduğu gibi yazıyor (unique violation), Python tarafı düzeltilmiş
    (`api/proposals/[id]/apply/route.ts:75-88` vs `promote.py:348-357`).

---

## 6. İnsan gereken noktalar (bugün)

| # | Nokta | Neden | Otomasyon |
|---|---|---|---|
| 1 | `notify` başlatma + Telegram token/chat-id | profile-gated, token boş → **sıfır alarm** | Profile kaldır; chat-id'yi `/start`'ta DB'ye yaz |
| 2 | dev_agent çıktısını main'e almak | `status='merged'` sadece DB string; git merge/push/PR **yok** | test → commit → merge adımı `worker.py`'ye |
| 3 | Test koşmak | `run_tests` yalnızca prompt metni | worktree'de `uv run pytest` gate |
| 4 | dev task açmak | Yalnızca `/dev-task`; hiçbir servis POST etmiyor | incident/efficacy/reap → otomatik task |
| 5 | dev lesson onayı | Hepsi `draft`; onaysız hiç öğrenmez | confidence/age ile auto-activate |
| 6 | Backup | `make backup` manuel; hiç çalışmamış | sidecar cron + retention |
| 7 | Migration | profile-gated one-shot | `depends_on: migrate: service_completed_successfully` |
| 8 | LLM re-auth | Cursor/Bedrock oturumu düşer, fallback yok, alarm yok | backend_state() → alarm + otomatik fallback |
| 9 | Disk / retention | 97 GB, büyüme sınırsız | time-partition + TTL job |
| 10 | Wallet reset | Sadece `db-reset` (TRUNCATE CASCADE, öğrenme verisini de siler) | ayrı "reset capital" |
| 11 | `weight_tune`/`llm_guide` onayı | Dashboard butonu; 7 günde süpürülüyor | efficacy-gated auto-apply veya Telegram onayı |
| 12 | `LIVE_EXECUTION_ENABLED`, capital cap, mainnet key | **Tasarım gereği manuel — kalacak** | — |

Doc↔kod sapmaları (güvenlik): `risk_caps.yaml` hiç okunmuyor; circuit breaker UTC gün dönümünde
**otomatik** sıfırlanıyor ve pozisyonları **kapatmıyor** (`paper_trade.py:182-192,348-362`; TRADING.md
"manual reset + close all" der); `exchange_shadow._per_trade_allowed` `should_submit_live`'dan **zayıf**
(capital cap, concurrent cap, slot cap yok) ve `TokenBucket` kullanmıyor (`exchange_shadow.py:61-90`).
Emir gönderen tek kod yolu bu.

---

## 7. Hedef mimari

```
                    ┌──────────────────────────────────────────────┐
                    │  DIRECTOR (tek orkestratör ajan, Sonnet)     │
                    │  - saatlik: sağlık + PnL + efficacy okur     │
                    │  - öncelik verir: araştır / tune / kod düzelt│
                    │  - risk-gated tool'lar: cert grant, rollback │
                    │  - insan yalnızca Telegram'da bilgilendirilir│
                    └───┬──────────┬───────────┬──────────┬────────┘
                        │          │           │          │
                 research      reflection    dev_agent   ops
                 worker        worker        (kod)       worker
                 (Haiku)       (Sonnet)                  (retention,
                                                          backup, auth)
                        └──────────┴───────────┴──────────┘
                                       │
                    ┌──────────────────▼───────────────────────────┐
                    │  MEMORY (Postgres tek kaynak, AGE index)     │
                    │  episodic: predictions.context + outcomes    │
                    │  semantic: lessons (efficacy'li, TTL'li)     │
                    │  regime:   market_regime (saatlik snapshot)  │
                    │  incident: incidents tablosu                 │
                    │  operator: operator_directives (chat→durable)│
                    │  embeddings: pgvector (lesson + thesis)      │
                    └──────────────────────────────────────────────┘
                                       │
                    ┌──────────────────▼───────────────────────────┐
                    │  HOT PATH (deterministik, 15s, LLM opsiyonel)│
                    │  decision agent + tool belt (read)           │
                    │  paper_trade + champion/challenger           │
                    │  execution gate (tek yol, risk-gated)        │
                    └──────────────────────────────────────────────┘
```

İlkeler:
- **Hot path deterministik kalır**; LLM `rule_decide` ile *blend* olur, ezmez. LLM düşerse PnL düşmez.
- **Para hareketi tek kod yolundan**: `exchange_shadow` → `execution.safety.should_submit_live`.
  `risk-gated` tool sınıfı yalnızca cert grant / rollback / slot değişimi için; emir tool'u yok.
- **Her mutasyon champion/challenger**: `status='shadow'` (modelde var, kullanılmıyor) ile paralel
  paper; efficacy ≥ 0 ve CI alt sınırı > champion → cutover; değilse retire + lesson.
- **Director kararlarını da ölçeriz**: hangi task'ı açtı, kaç $ harcadı, PnL'e etkisi.

---

## 8. Plan — fazlar, kabul kriterleri

### P0 — Kanamayı durdur (1-2 gün, kod düzeltmeleri)
1. `.env` cert override'larını kaldır **veya** `BYBIT_TESTNET=false` iken herhangi bir `MATRIX_CERT_*`
   override varsa `should_submit_live` → False (kod seviyesi refuse). Kabul: test.
2. Dispatcher params geçirir: `strategy/main.py` aktif `strategy_configs.params`'ı `cls(**params)` ile
   verir; modüller `version`'ı config'ten damgalar. Kabul: param_tune sonrası metric n>0.
3. Reflection proposal `asset_class` taşır; dedup + `apply_proposal` asset_class filtreli. Fantom crypto
   BIST config satırları migration ile retire. Kabul: BIST strateji max version = gerçek.
4. `notify` profile kaldır; dry-run'da bile çalışsın. Chat-id DB'ye. LLM backend down / breaker open /
   ingestion stale / disk > %80 / dev task fail alarmları. Kabul: token boşken log, doluyken Telegram.
5. Retention: `market_trades`, `market_orderbook_snapshots` 7 gün (time-partition veya günlük DELETE);
   `wallet_snapshots` 1 dk'ya seyrelt + 90 gün; `_peak_equity_today` günlük agregata bak. Backup sidecar
   (günlük `pg_dump`, 14 gün retention). Kabul: 97 GB → < 15 GB; `backups/` dolu.
6. Maliyet modeli: taker 5.5 bps + spread yarısı + funding (directional hold). Kabul: replayer PnL değişir,
   `make diagnose-matrix-agent` yeni sayıları yazar.
7. `exchange_shadow` gate'i `should_submit_live` + `TokenBucket`'a bağla. Kabul: mevcut execution testleri
   shadow yolundan da geçer.
8. Circuit breaker: trip'te açık pozisyonları kapat; reset **Director'un risk-gated tool'u** veya
   operatör Telegram komutu ile (UTC auto-reset kaldır). TRADING.md ile hizala.

### P1 — Öğrenme döngüsünü gerçekten kapat (1 hafta)
1. **`mutation_efficacy` tablosu**: her `applied` proposal için `applied_at` ± 7g pencere PnL, n, CI.
   Günlük job. `efficacy < 0` ve n ≥ 50 → otomatik rollback (retired satırı geri `active`) + lesson.
2. **Champion/challenger**: `status='shadow'` config'ler paper'da paralel çalışır (ayrı wallet
   veya `is_shadow` pozisyon flag'i, sermaye saymaz). Cutover kuralı: 7g, n ≥ 100, PnL CI alt > champion.
3. Reflection LLM path'i `_underperforming` ile gate'le; system prompt hedefi `total_pnl_usd`
   (`reflection/agent.py:172`). Score trigger'ı kaldır, sadece PnL + Sharpe-benzeri.
4. Labs: eval dedup (symbol×genome başına açık eval ≤ 1); fitness = ortalama − k·std/√n; TP/SL genomun
   parçası. BIST/US genomlarını feature'ları yokken seed etme.
5. Slot scorer minimum n = 30, bucket sınırlarında Wilson CI kullan.
6. Certificate: env override kaldır; ek koşul "CI alt sınırı > 0"; drawdown'da otomatik revoke.
7. Orphan flat-close outcome'ları `excluded_from_metrics=true`.
8. Counterfactual: reddedilen prediction'lar için `virtual_outcome` (fiyat izlenir, pozisyon açılmaz)
   → EV ranker'ın kendisini ölçmek için. Kabul: "ranker seçimi vs rastgele" raporu.

### P2 — Memory'yi karar yoluna bağla (1 hafta)
1. Lesson yaşam döngüsü: TTL (14g), effect-size'a bağlı confidence, exploration'ı lesson'dan **sonra**
   %2 zorunlu koridorla çalıştır (kilit kırılır), `lesson_efficacy` (uyulan vs uyulmayan PnL).
2. `lessons_relevant_to` asset_class fix; `strategy/lessons.py` ile aynı confidence gate.
3. **Regime**: saatlik `market_regime(asset_class, symbol?, vol_bucket, trend_bucket, funding_bucket)`;
   lesson/edge/pair-edge regime-keyed. Decision prompt'a regime satırı.
4. Overlay'i tamamla: `Outcome`, `Lesson` node + `RESULTED_IN`/`GENERALIZED_INTO` yaz (kod hazır,
   çağrılmıyor). Decision agent'a `past_episodes(symbol, regime)` tool'u.
5. Embedding: `predictions.thesis` + `agent_lessons.pattern_description` için Haiku-ucuz embedding
   (veya lokal model), pgvector. "Benzer setup geçmişte ne yaptı" tool'u.
6. `operator_directives` tablosu: Brain'e tek `write` tool (`remember_directive`), decision ve reflection
   prompt'larına enjekte. Chat'te "DOGE'a girme" → kalıcı.
7. `incidents` tablosu: circuit trip, breaker open, auth fail, shadow error → satır; notify buradan okur.

### P3 — Director + tool belt (1-2 hafta)
1. `services/director`: saatlik tick. Tool'lar: `health()`, `pnl_report()`, `efficacy_report()`,
   `open_dev_task()` (write), `grant_cert()`/`revoke_cert()`/`rollback_config()` (risk-gated,
   TRADING.md'deki yasak listeye dokunamaz), `notify()`.
2. Decision agent'a tool belt: `recent_outcomes(symbol)`, `active_lessons`, `wallet_state`,
   `peer_signals(symbol)`, `regime`. LLM kararı rule ile blend: `conf = 0.5·rule + 0.5·llm`,
   fark > 0.4 ise hold. Method A/B: `method` alanı zaten var, PnL'i method bazında raporla.
3. Cross-process rate limiter (Postgres advisory lock veya token tablosu); `agent_usage` tablosu;
   Director günlük LLM bütçesi (`$`/gün) aşımında Haiku'ya düşer.
4. Cursor backend'e `disallowed_tools`/deny hook taşı veya Brain'i cursor'da kapat.
5. `haiku_distill`'i brain `sql_read`, reflection `recent_outcomes` tool'larına da uygula.

### P4 — dev_agent'ı kapat (1 hafta)
1. `worker.py`: completed → `uv run pytest` (touches_files'a göre servis seç) → fail ise `test_broke`
   lesson → pass ise commit → `git merge --no-ff` main'e (veya PR + auto-merge) → compose servis restart.
2. `reap_stuck_running` çağır; heartbeat'i event loop içinde at; cost cap SDK `result` mesajından oku;
   daily cap uygula; worktree açılamazsa **task fail** (repo'da çalışma yok).
3. `codebase_ctx` gerçekten geçir (embedding ile P2'den sonra).
4. Lesson auto-activate: 2 tekrar eden failure_reason veya Director onayı.
5. Otomatik task kaynakları: `incidents` (tekrarlayan hata), `mutation_efficacy` (aynı strateji 3 kez
   negatif → "stratejiyi yeniden yaz"), coverage boşluğu (BIST feature extraction yok).
6. `FORBIDDEN_PATHS` geri: `matrix_shared/trading_safety.py`, `execution/safety.py`, `exchange_shadow.py`
   yalnızca insan. Gerisi serbest.

### P5 — Canlıya kontrollü geçiş (G3 sonrası)
1. G3'ü otomatik ölçen job; geçen strateji için Director cert basar, Telegram'a "hazır" der.
2. İnsan `LIVE_EXECUTION_ENABLED=true` + `BYBIT_TESTNET=false` + cap yazar. Başka hiçbir şey.
3. Paper-vs-live gap ölçümü (`live_fill_price − paper_price`) → maliyet modelini besler.
4. Live sermaye scale-up yalnızca insan (TRADING.md).

---

## 9. İlk 10 iş (sıralı, direktif)

1. `.env` cert override kaldır + kod-seviyesi refuse (P0.1)
2. Dispatcher params + version damgası (P0.2)
3. Proposal asset_class + fantom config temizliği (P0.3)
4. notify default + 5 alarm (P0.4)
5. Retention + backup sidecar (P0.5)
6. Maliyet modeli gerçekçi (P0.6)
7. `mutation_efficacy` + auto-rollback (P1.1)
8. Champion/challenger shadow (P1.2)
9. Lesson TTL + exploration koridoru + efficacy (P2.1)
10. dev_agent test→merge (P4.1)
11. OpenRouter backend: `MATRIX_LLM_BACKEND=openrouter`, ücretsiz modeller (`:free` suffix) için
    tier eşlemesi (`MATRIX_MODEL_<HAIKU|SONNET|OPUS>`), rate-limit/latency'e göre otomatik fallback
    sırası: subscription → openrouter-free → rule-only. Operatör isteği 2026-09-12.

Bu 10'dan sonra sistem "çalışıyor gibi görünen" değil, **gerçekten kendini ölçen** hale gelir.
Director (P3) ancak ölçüm altyapısı varken anlamlı; ondan önce yazılırsa gürültüyü orkestre eder.

---

## 10. Tradeoff notları

- **Orkestratörü ilk değil dördüncü faz yaptım.** Bugün orkestratörün okuyacağı güvenilir sinyal yok
  (efficacy, regime, incident). Önce ölçüm.
- **LLM'i hot path'ten çıkarmadım, blend'e aldım.** Çünkü rule-only'nin de PnL'i negatif; LLM'in
  katkısını method-level A/B ile ölçmeden atmak da tutmak da körlemesine.
- **Champion/challenger sermaye maliyeti**: shadow pozisyonlar sermaye saymaz ama CPU/DB yükü artar.
  Retention (P0.5) olmadan yapılmaz.
- **dev_agent'a merge yetkisi** riskli; `FORBIDDEN_PATHS` + test gate + merge sonrası efficacy ile
  sınırlı. İlk 30 gün `auto_pr=true` + Director'un PR'ı test sonucuna göre merge etmesi daha güvenli.
