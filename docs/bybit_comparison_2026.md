# Matrix vs Bybit Trading Bot — Gap Analizi & Roadmap

> Tarih: 2026-05-26
> Kaynak: bybit.com/en/tradingbot, learn.bybit.com, help-center makaleleri
> Amaç: Bybit'in iyi yaptıklarını çıkar, Matrix'te ne eksik/iyileşmeli görüş, fazlara hizalı yol haritası kur.

---

## 1. Bybit Trading Bot — ne sunuyor?

### Bot tipleri (built-in, no-code)
| Bot | Mantık | Pazar |
|---|---|---|
| **Spot Grid** | Referans fiyatın altında BUY, üstünde SELL eşit aralıklı (range-bound mean-reversion) | Spot |
| **Futures Grid** | Aynı mantık + leverage; Long / Short / Neutral varyantları | Perp |
| **Futures Grid Pricing** | Arithmetic (eşit fiyat farkı) veya Geometric (eşit oran) | Perp |
| **DCA** | Sabit aralıklarla sabit miktar alım (averaging in) | Spot |
| **Futures Martingale** | Kayıp pozisyona ekleme (averaging down) | Perp |
| **Combo Bots** | Birden fazla bot'u zincirleme | Hepsi |

### Risk yönetimi
- TP ratio max 500%, SL ratio max 100% (per-bot)
- **Equity Trailing Stop** (recent feature) — portföy seviyesinde trailing dd kontrolü
- Per-tip 50 bot limiti

### AI / suggestions
- **AI Strategy Suggester** — geçmiş veriden parametre öner; sınıflandırma: *High Yield* / *Stable* (düşük dd) / *High Frequency*
- **TradeGPT** — ChatGPT üzerine LLM asistanı; strateji öneri, soru-cevap
- **Aurora AI Trading Bot** — AI-driven karar verici (ayrı ürün)

### Backtest
- AI Strategy suggester historical data üzerinden öneri yapıyor; full kullanıcı-erişimli ayrı bir backtest UI'ı yok (suggester'a entegre)

### Copy trading & social
- **Leaderboard** günlük 10:00 UTC update; 24h realized PnL + ROI
- **Master Trader** profili — followerlar otomatik mirror trade
- **Bot Squad vs Human** turnuvalar, ödül havuzları
- **Sosyal paylaşım** — Telegram, Twitter, Facebook, WhatsApp tek tıkla
- 10M kullanıcı kitlesi

### UX
- Code-free, mobile + web
- Pre-configured templates
- Kullanıcı tek bot tek pair seçer; Combo ile zincirler

---

## 2. Matrix bugünkü durum

### Strateji üreticileri (concurrent, multi-strategy)
| Modül | Mantık | Durum |
|---|---|---|
| matrix_agent | LLM-based (Haiku 4.5), multi-feature blend | ✅ aktif, v2 |
| trade_flow_imbalance | Trade volume buy-share threshold mean-reversion | ✅ aktif |
| funding_reversion | Funding rate uç değerinde reversion | ⚠️ threshold'a varmıyor (calm market) |
| oi_delta | OI sıçraması + fiyat onayı | ⚠️ threshold'a varmıyor |
| bist_gap_fade / volume_breakout / news_event | BIST equities | ⚠️ TR market saatleri dışı |

### Risk yönetimi
- `max_position_pct=2%`, `daily_loss_circuit_pct=5%`, `LIVE_CAPITAL_CAP_USD`
- `max_concurrent_positions=5`
- `paper_trade_certificate` Phase 5 gate (bu session)
- Composite gate `services/execution/safety.should_submit_live` (bu session)
- Bug-induced rapid trade için yok ⚠️
- Equity trailing stop yok ⚠️

### AI
- Reflection servisi: outcomes → mutation_proposal (rule + LLM)
- LLM-based agent (Claude Haiku 4.5)
- Context graph (Apache AGE: 86 doc, 9 asset, 258 mentions)
- Strategy parameter mutation otomatik versioning (`strategy_configs`)

### Backtest
- **Yok** — sadece live paper-trade engine (`services/backtest`)
- Historical bar üzerinden ileri tarihli simulation kapasitesi henüz yok
- AI Strategy suggester benzeri öneri kanalı yok

### UX
- Web dashboard (next.js) — equity curve, predictions, outcomes, mutation_proposals, lab leaderboard, graph signals, cert panel (bu session)
- Strateji oluşturma UI'ı **yok** — kod yazılıyor
- Copy trading, mobile, sosyal özellikler **yok**

### Sistem temelleri (Bybit'te yok)
- Self-improvement loop (reflection → mutation → cert) — Bybit static
- Multi-source signal engine (news graph + market microstructure + LLM)
- Long-term graph memory (Apache AGE)
- Code-enforced safety gates (FORBIDDEN_PATHS, paper_trade_certificate)
- Node-based dağıtık çalışma (multi-PC)
- Strateji versioning + audit log

---

## 3. Karşılaştırma matrisi

| Boyut | Bybit | Matrix | Kazanan |
|---|---|---|---|
| Bot tip çeşitliliği | Grid/DCA/Martingale/Combo | 4 custom + 3 BIST sinyal modülü | Bybit (bilinen şablonlar) |
| AI strateji öneri | TradeGPT + AI Strategy | Yok | **Bybit** |
| Backtest | Historical suggester | Yok | **Bybit** |
| Self-improvement | Yok | reflection → mutation → cert versioning | **Matrix** |
| Multi-strategy concurrent | Tek bot per pair | N strateji aynı anda predict | **Matrix** |
| Multi-market | Spot+Perp (Bybit) | Bybit + BIST kod path'i | **Matrix** (mimari) |
| Risk: TP/SL | Per-bot TP/SL + trailing stop | Position cap + circuit breaker, no trailing stop, no per-trade SL | Bybit (granular) |
| Risk: gate | Yok | paper_trade_certificate code-enforced | **Matrix** |
| Copy trading | Tam ekosistem | Yok | Bybit (kapsam dışı bizim için) |
| Sosyal/topluluk | 10M kullanıcı | Yok | Bybit |
| Live execution | Production | Phase 5 (henüz hayır) | Bybit (zaman) |
| UI / non-dev erişim | No-code mobile+web | Code-only + dashboard | Bybit |
| Strateji audit log | Yok | mutation_proposals + strategy_configs versions | **Matrix** |
| Context awareness | Yok (haber/event entegrasyonu kullanıcı vekil) | Apache AGE graph + news ingestion | **Matrix** |
| Custom signal mühendisliği | Sabit şablon (Grid/DCA) | Açık Python modülleri | **Matrix** |

---

## 4. Bybit'ten alınacak iyi fikirler (entegrasyon önceliği)

### A. Grid + DCA strateji modülleri (Phase 3.5)
Neden: Grid bot regime-agnostic, range-bound çoğu market'te çalışır. Şu an matrix_agent + trade_flow + funding + oi_delta'nın tümü direksiyona dayalı (mean-reversion / momentum). Grid bot çeşitlilik katar.

Effort: Düşük. `services/strategy/modules/grid.py` + `dca.py` — mevcut altyapı reusable.

### B. Historical backtest engine (Phase 3.5)
Neden: Strateji parametresi tune ederken 24h forward paper bekleyemeyiz. Bir gün geçmişe karşı simülasyon iki saatte cevap verir. AI-suggester'ın tabanı.

Effort: Orta. market_bars zaten yüklü. `services/backtest/historical.py` — bar üzerinden sentetik prediction → simulated outcome.

### C. AI Strategy Suggester (Phase 4.5)
Neden: Bybit'in en güçlü "moat"larından biri. "Bana BTC için akıllı bir bot kur" → AI parametre üretsin + backtest gösterssin.

Effort: Orta-yüksek. Backtest engine bittikten sonra üzerine kuruluyor. LLM (Claude Haiku) ön-rastgele param sample → backtest grid → top-N göster.

### D. Equity Trailing Stop (Phase 5 risk-add)
Neden: Daily loss circuit (-5%) günü bitirip kapatır. Trailing stop dinamik — kâr biriktirdikçe stop yukarı kayar. Maksimum drawdown'u kısıtlar.

Effort: Düşük. `wallets.equity_trailing_stop_pct` kolonu + check function.

### E. Strateji templating + leaderboard (Phase 6)
Neden: "İlk başlayan kullanıcı" UX'i. Hangi config çalıştı, hangisi battı — public şeffaflık.

Effort: Düşük (dashboard'a tablo eklenir). Genome data zaten var (labs).

### F. Per-trade TP/SL (Phase 5)
Neden: Şu an horizon-based exit; o sırada fiyat ne ise satılır. TP/SL pozisyon açıldıktan sonra fiyat hedeflerine ulaşınca kapatır — çoğu durumda daha iyi PnL.

Effort: Orta. `predictions` tablosuna `tp_pct`, `sl_pct` kolonları + paper_trade.close_due_positions'a fiyat-tetikli early exit.

### G. TradeGPT-equivalent chat asistanı (Phase 6+)
Neden: User-facing chat bot — "BTC long açar mıydın bugün, niye?". Dev_agent var ama o code-editing için. Trading copilot ayrı bir özellik.

Effort: Yüksek. Chat UI + tool calls'le DB sorgu + matrix-agent decide işlevini export.

### H. Bybit live connector (Phase 5)
Neden: Roadmap'in temel adımı; paper-trade certificate granted olunca live'a geçmek için.

Effort: Yüksek (broker API, order types, fill handling, websocket). Composite gate hazır.

### I. Mobile / push notifications (Phase 7+)
Neden: Telefondan PnL ve circuit alarmı görme. Bybit native mobile uygulama.

Effort: Yüksek. Telegram bot ile büyük çoğunluğu sağlanabilir (Phase 6 light).

---

## 5. Bybit'ten ALMAYACAĞIMIZ şeyler (Matrix avantajı koru)

| Bybit özellik | Almayalım çünkü |
|---|---|
| Copy trading ekosistemi | Bizim ürünümüz para kazanan motor, social layer değil. Sapma yaratır. |
| Public leaderboard / turnuva | Strateji "public ölünce" arbitraj sızıntısı. Internal leaderboard zaten labs'da var. |
| Pre-configured templates only | Custom signal mühendisliğimizi öldürür. Template **ek** olabilir ama base yapı kod kalmalı. |
| GUI-only config | Kod-first arch self-improvement loop için zorunlu (reflection mutation yazar). |
| Aggressive marketing AI ("Aurora") | Cilali ürünler kullanıcı bekleyişini şişirir; bizim approach: önce ölç, sonra anlat. |
| Martingale (averaging down losses) | Risk profili kötü — bilinen capital-killer pattern. Kendi reflection'umuz da bunu reddetmeli. |

---

## 6. Matrix-uyarlı roadmap (önerilen sıra)

### Phase 3.5 — Strateji çeşitliliği & backtest (1-2 hafta)
**Gate**: Historical backtest engine BTCUSDT 30 günlük data üzerinde çalışsın + min 2 yeni strateji modülü canlıda prediction üretsin.

- [ ] `services/strategy/modules/grid.py` — Spot grid mantığı, market_bars üzerinden working
- [ ] `services/strategy/modules/dca.py` — Recurring buy, simple long-bias accumulator
- [ ] `services/backtest/historical.py` — market_bars + sentetik prediction → simulated outcomes, idempotent re-run
- [ ] `make backtest STRATEGY=... DAYS=...` CLI
- [ ] Backtest sonuçları → `lab_experiments` tablosuna entegre (mevcut yapıdan reuse)

### Phase 4.5 — AI Strategy Suggester (2-3 hafta)
**Gate**: Kullanıcı "BTC, $100 risk, conservative" derse 3 farklı bot config + her birinin geçmiş 30g backtest grafiği gelsin.

- [ ] Param-space sampling kodu (random + bayesian)
- [ ] Backtest grid runner → top-K seç
- [ ] LLM (Haiku) → human-readable rationale + risk uyarısı per config
- [ ] Dashboard'da "Suggest a bot" formu

### Phase 5 — Live execution (4 hafta, paper-cert sonrası)
**Gate**: Bybit testnet'te 60g paper-cert geçmiş strateji canlıya çıksın, 30g circuit breaker tetiklemeden ayakta kalsın.

- [ ] `services/execution/bybit_connector.py` — testnet/mainnet REST + WS order submission
- [ ] Composite gate'i tüm submit path'lerden çağır (zaten hazır)
- [ ] `wallets.equity_trailing_stop_pct` kolonu + check
- [ ] Per-trade TP/SL (predictions kolonları + paper_trade closer'a tetik)
- [ ] Rate limiter (rapid trade bug detection)
- [ ] Manual approval UI dashboard'da (bir butonla testnet → mainnet flip)

### Phase 6 — UX & yaygınlaştırma (Phase 5 + 1 ay)
**Gate**: Dış kullanıcı (1-2 alpha tester) sistemi kullanabilsin.

- [ ] Strategy creation wizard (no-code, dashboard)
- [ ] Strategy templates ("Conservative DCA", "Aggressive Grid", "Hybrid")
- [ ] Telegram bot — PnL bildirim + circuit alarm + komutla pozisyon görüntü
- [ ] Public-facing read-only sayfa (sanitized — actual sizes/keys gizli)
- [ ] Bülten ürünü (orijinal Phase 6 zaten)

### Phase 7+ — Genişleme (Months 6+)
- [ ] Mobile / native app (opsiyonel)
- [ ] TradeGPT-equivalent trader copilot (chat UI)
- [ ] Çoklu broker (Binance, OKX)
- [ ] Quant-as-a-service (kullanıcı kendi strateji modülünü Python ile yazsın, izole çalışsın)

---

## 7. Hemen yapılması gereken küçük iyileştirmeler (this week, low effort)

| # | İş | Effort | Etki |
|---|---|---|---|
| 1 | `wallets.equity_trailing_stop_pct` migration + check | 1 saat | Drawdown koruması |
| 2 | `predictions.tp_pct`, `predictions.sl_pct` (nullable) | 1 saat | Strateji modülleri opt-in TP/SL |
| 3 | Grid bot iskelet `services/strategy/modules/grid.py` | 2 saat | Çeşitlilik |
| 4 | Historical backtest CLI (basit, tek strateji) | 4 saat | Hızlı geri besleme |
| 5 | Dashboard: per-strategy 7-day win rate kıyas | 2 saat | Performance görünürlük |
| 6 | Auto-grant cert pipeline (eligible olunca ✓) | 3 saat | Phase 5 path açılır |

---

## 8. Strateji notu

Matrix'in **gerçek farkı** Bybit'le mukayese değil; Bybit "kullanıcı seçer, bot çalıştırır" pasif platform. Matrix "kendi kendini iyileştiren, çoklu sinyalden anlamlandıran, bağlamla beslenen otonom motor". Bybit'in iyi fikirlerini al — *aktif* bir motorun *daha iyi* karar vermesi için.

Roadmap'in özü: önümüzdeki 2-4 haftada **backtest + strateji çeşitliliği** odak — self-improvement loop'unun beslendiği verinin kalitesi + çeşitliliği bunlar olmadan plato yapacak.
