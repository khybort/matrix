# Cross-cutting Changes Log

> Append-only. Each entry: date + machine + one-line description of a cross-cutting change.
> Read this on every pull. If you're about to make a change that affects both Python and TS sides, add an entry here BEFORE coding.

---

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

## 2026-09-12 — Otonomi denetimi + planı (`docs/AUTONOMY_PLAN.md`)

- Ajan/tool, memory, self-learning ve insan-bağımlı ops noktaları denetlendi; bulgular dosya:satır
  referanslı. Kritik: 11 stratejide param mutasyonları no-op (dispatcher DB params geçmiyor),
  reflection proposal'ları `asset_class` taşımıyor (BIST → crypto fantom v686), cert eşikleri `.env`
  ile 0 gün / −$50'e gevşetilmiş, dev_agent `merged` sadece DB etiketi (0 gerçek merge), `notify`
  çalışmıyor, retention/backup yok (local DB 97 GB). Sıralı P0-P5 planı ve ilk 10 iş dokümanda.

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
