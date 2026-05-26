"""BIST symbol universe — seed list.

This is the bootstrap universe. The list below covers the BIST 100 plus a
broad swath of the actively traded equities on Borsa Istanbul (banks,
holdings, industrials, REITs, energy, retail, etc.). Yahoo's `.IS` suffix
maps every line here to a ticker yfinance can resolve.

Maintenance: pulled from the public list of BIST-traded equities as of
2026-05. The `index_membership` field is sparse and best-effort — use it as
a hint, not a gate. Refresh by re-running `matrix-bist-symbols --seed`.

Not exhaustive: warrants, ETFs, and bonds are excluded; only common stock
tickers. The `bist_symbols` table is the source of truth at runtime; this
seed only populates it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SymbolSeed:
    symbol: str  # base ticker, no .IS suffix
    name: str | None = None
    sector: str | None = None
    index_membership: str | None = None  # comma-separated: "BIST30,BIST100"


# Trimmed for repo size; covers BIST 100 + heavy-volume mid-caps.
# Add to this list to grow the universe; entries are deduped on insert.
BIST_SEED: tuple[SymbolSeed, ...] = (
    # ------ Banks ------
    SymbolSeed("AKBNK", "Akbank", "Banking", "BIST30,BIST100"),
    SymbolSeed("GARAN", "Garanti BBVA", "Banking", "BIST30,BIST100"),
    SymbolSeed("ISCTR", "İş Bankası C", "Banking", "BIST30,BIST100"),
    SymbolSeed("YKBNK", "Yapı Kredi", "Banking", "BIST30,BIST100"),
    SymbolSeed("HALKB", "Halkbank", "Banking", "BIST100"),
    SymbolSeed("VAKBN", "Vakıfbank", "Banking", "BIST100"),
    SymbolSeed("ALBRK", "Albaraka Türk", "Banking", "BIST100"),
    SymbolSeed("ICBCT", "ICBC Turkey Bank", "Banking", None),
    SymbolSeed("QNBFB", "QNB Finansbank", "Banking", None),
    SymbolSeed("SKBNK", "Şekerbank", "Banking", None),
    SymbolSeed("TSKB", "T. Sınai Kalkınma B.", "Banking", "BIST100"),
    # ------ Holdings & conglomerates ------
    SymbolSeed("KCHOL", "Koç Holding", "Holding", "BIST30,BIST100"),
    SymbolSeed("SAHOL", "Sabancı Holding", "Holding", "BIST30,BIST100"),
    SymbolSeed("DOHOL", "Doğan Holding", "Holding", "BIST100"),
    SymbolSeed("ENKAI", "Enka İnşaat", "Construction", "BIST30,BIST100"),
    SymbolSeed("KOZAL", "Koza Altın", "Mining", "BIST100"),
    SymbolSeed("KOZAA", "Koza Anadolu", "Holding", "BIST100"),
    SymbolSeed("IHLAS", "İhlas Holding", "Holding", None),
    SymbolSeed("YESIL", "Yeşil Yapı", "Construction", None),
    SymbolSeed("GLYHO", "Global Yatırım Holding", "Holding", "BIST100"),
    SymbolSeed("ALARK", "Alarko Holding", "Holding", "BIST100"),
    SymbolSeed("AGHOL", "AG Anadolu Grubu Holding", "Holding", "BIST100"),
    SymbolSeed("TKFEN", "Tekfen Holding", "Holding", "BIST100"),
    SymbolSeed("ECILC", "EİS Eczacıbaşı İlaç", "Pharma", "BIST100"),
    # ------ Industrials / autos / defence ------
    SymbolSeed("ASELS", "Aselsan", "Defence", "BIST30,BIST100"),
    SymbolSeed("OTKAR", "Otokar", "Automotive", "BIST100"),
    SymbolSeed("TOASO", "Tofaş Oto", "Automotive", "BIST30,BIST100"),
    SymbolSeed("FROTO", "Ford Otosan", "Automotive", "BIST30,BIST100"),
    SymbolSeed("DOAS", "Doğuş Otomotiv", "Automotive", "BIST100"),
    SymbolSeed("TTRAK", "Türk Traktör", "Industrial", "BIST100"),
    SymbolSeed("ARCLK", "Arçelik", "Consumer Durables", "BIST30,BIST100"),
    SymbolSeed("VESTL", "Vestel", "Consumer Durables", "BIST100"),
    SymbolSeed("VESBE", "Vestel Beyaz Eşya", "Consumer Durables", "BIST100"),
    SymbolSeed("KORDS", "Kordsa Teknik Tekstil", "Industrial", "BIST100"),
    SymbolSeed("BRSAN", "Borusan Boru", "Industrial", "BIST100"),
    SymbolSeed("BRISA", "Brisa", "Industrial", "BIST100"),
    SymbolSeed("EREGL", "Ereğli Demir Çelik", "Steel", "BIST30,BIST100"),
    SymbolSeed("KRDMD", "Kardemir D", "Steel", "BIST100"),
    SymbolSeed("KRDMA", "Kardemir A", "Steel", None),
    SymbolSeed("KRDMB", "Kardemir B", "Steel", None),
    SymbolSeed("ISDMR", "İskenderun Demir Çelik", "Steel", "BIST100"),
    SymbolSeed("CEMTS", "Çemtaş", "Steel", None),
    SymbolSeed("BURCE", "Burçelik", "Steel", None),
    SymbolSeed("BAGFS", "Bagfaş", "Chemicals", "BIST100"),
    SymbolSeed("HEKTS", "Hektaş", "Chemicals", "BIST100"),
    SymbolSeed("AKSA", "Aksa Akrilik", "Chemicals", "BIST100"),
    SymbolSeed("PETKM", "Petkim", "Chemicals", "BIST30,BIST100"),
    SymbolSeed("TUPRS", "Tüpraş", "Energy", "BIST30,BIST100"),
    SymbolSeed("AKSEN", "Aksa Enerji", "Energy", "BIST100"),
    SymbolSeed("AKENR", "Akenerji", "Energy", "BIST100"),
    SymbolSeed("ZOREN", "Zorlu Enerji", "Energy", "BIST100"),
    SymbolSeed("ODAS", "Odaş Elektrik", "Energy", "BIST100"),
    SymbolSeed("AYGAZ", "Aygaz", "Energy", "BIST100"),
    SymbolSeed("IPEKE", "İpek Doğal Enerji", "Energy", "BIST100"),
    SymbolSeed("TAVHL", "TAV Havalimanları", "Transport", "BIST30,BIST100"),
    SymbolSeed("THYAO", "Türk Hava Yolları", "Transport", "BIST30,BIST100"),
    SymbolSeed("PGSUS", "Pegasus", "Transport", "BIST100"),
    SymbolSeed("CLEBI", "Çelebi Hava Servisi", "Transport", "BIST100"),
    # ------ Telecom & tech ------
    SymbolSeed("TCELL", "Turkcell", "Telecom", "BIST30,BIST100"),
    SymbolSeed("TTKOM", "Türk Telekom", "Telecom", "BIST30,BIST100"),
    SymbolSeed("LOGO", "Logo Yazılım", "Software", "BIST100"),
    SymbolSeed("INDES", "İndeks Bilgisayar", "Tech Distribution", "BIST100"),
    SymbolSeed("ARENA", "Arena Bilgisayar", "Tech Distribution", "BIST100"),
    SymbolSeed("KAREL", "Karel Elektronik", "Tech", None),
    SymbolSeed("DESPC", "Despec", "Tech Distribution", None),
    SymbolSeed("LINK", "Link Bilgisayar", "Tech", None),
    SymbolSeed("ESCOM", "Escort Teknoloji", "Tech", None),
    SymbolSeed("ALCTL", "Alcatel Lucent Teletaş", "Tech", None),
    SymbolSeed("NETAS", "Netaş Telekomünikasyon", "Tech", None),
    # ------ Consumer ------
    SymbolSeed("BIMAS", "BİM Mağazalar", "Retail", "BIST30,BIST100"),
    SymbolSeed("MGROS", "Migros Ticaret", "Retail", "BIST30,BIST100"),
    SymbolSeed("SOKM", "Şok Marketler", "Retail", "BIST100"),
    SymbolSeed("CCOLA", "Coca-Cola İçecek", "Beverages", "BIST30,BIST100"),
    SymbolSeed("AEFES", "Anadolu Efes", "Beverages", "BIST30,BIST100"),
    SymbolSeed("ULKER", "Ülker Bisküvi", "Food", "BIST30,BIST100"),
    SymbolSeed("BANVT", "Banvit", "Food", None),
    SymbolSeed("PINSU", "Pınar Su", "Food", None),
    SymbolSeed("PNSUT", "Pınar Süt", "Food", "BIST100"),
    SymbolSeed("KENT", "Kent Gıda", "Food", None),
    SymbolSeed("TATGD", "TAT Gıda", "Food", "BIST100"),
    SymbolSeed("PETUN", "Pınar Et ve Un", "Food", None),
    SymbolSeed("MAVI", "Mavi Giyim", "Apparel", "BIST100"),
    SymbolSeed("DESA", "Desa Deri", "Apparel", None),
    SymbolSeed("BOSSA", "Bossa", "Textile", None),
    SymbolSeed("YATAS", "Yataş", "Furniture", "BIST100"),
    SymbolSeed("KLMSN", "Klimasan", "Industrial", None),
    # ------ Construction & cement ------
    SymbolSeed("AKCNS", "Akçansa", "Cement", "BIST100"),
    SymbolSeed("CIMSA", "Çimsa", "Cement", "BIST100"),
    SymbolSeed("KONYA", "Konya Çimento", "Cement", None),
    SymbolSeed("BUCIM", "Bursa Çimento", "Cement", None),
    SymbolSeed("OYAKC", "Oyak Çimento", "Cement", "BIST100"),
    SymbolSeed("CEMAS", "Çemaş Döküm", "Industrial", None),
    SymbolSeed("CEMTS", "Çemtaş", "Steel", None),
    SymbolSeed("KUYAS", "Kuyumcukent GYO", "REIT", None),
    SymbolSeed("ISGYO", "İş GYO", "REIT", "BIST100"),
    SymbolSeed("EKGYO", "Emlak Konut GYO", "REIT", "BIST30,BIST100"),
    SymbolSeed("TRGYO", "Torunlar GYO", "REIT", "BIST100"),
    SymbolSeed("HLGYO", "Halk GYO", "REIT", None),
    SymbolSeed("OZKGY", "Özak GYO", "REIT", "BIST100"),
    SymbolSeed("ALGYO", "Alarko GYO", "REIT", None),
    SymbolSeed("AVGYO", "Avrasya GYO", "REIT", None),
    SymbolSeed("DGGYO", "Doğuş GE GYO", "REIT", None),
    SymbolSeed("NUGYO", "Nurol GYO", "REIT", None),
    SymbolSeed("YGGYO", "Yeşil GYO", "REIT", None),
    # ------ Insurance & finance ------
    SymbolSeed("ANSGR", "Anadolu Sigorta", "Insurance", "BIST100"),
    SymbolSeed("AKGRT", "Aksigorta", "Insurance", "BIST100"),
    SymbolSeed("AGESA", "Agesa Hayat", "Insurance", "BIST100"),
    SymbolSeed("RAYSG", "Ray Sigorta", "Insurance", None),
    SymbolSeed("TURSG", "Türkiye Sigorta", "Insurance", "BIST100"),
    SymbolSeed("ATAGY", "Ata GYO", "REIT", None),
    SymbolSeed("ISFIN", "İş Finansal Kiralama", "Finance", None),
    SymbolSeed("ISMEN", "İş Yatırım", "Brokerage", "BIST100"),
    SymbolSeed("OYAKC", "Oyak Çimento", "Cement", "BIST100"),
    SymbolSeed("GSDHO", "GSD Holding", "Holding", None),
    SymbolSeed("CRFSA", "CarrefourSA", "Retail", None),
    # ------ Mining, metals ------
    SymbolSeed("KOZAL", "Koza Altın", "Mining", "BIST100"),
    SymbolSeed("IPEKE", "İpek Doğal Enerji", "Energy", "BIST100"),
    SymbolSeed("KCAER", "Kocaer Çelik", "Steel", None),
    SymbolSeed("DMSAS", "Demisaş Döküm", "Industrial", None),
    SymbolSeed("ASUZU", "Anadolu Isuzu", "Automotive", None),
    SymbolSeed("KARSN", "Karsan Otomotiv", "Automotive", "BIST100"),
    SymbolSeed("TMSN", "Tümosan Motor", "Industrial", None),
    SymbolSeed("KATMR", "Katmerciler Araç Üstü", "Industrial", None),
    SymbolSeed("TUREX", "Tureks Turizm Tic.", "Tourism", None),
    SymbolSeed("MAALT", "Marmaris Altınyunus", "Tourism", None),
    SymbolSeed("AYDEM", "Aydem Enerji", "Energy", "BIST100"),
    SymbolSeed("ENJSA", "Enerjisa Enerji", "Energy", "BIST100"),
    SymbolSeed("EBEBK", "Ebebek Mağazacılık", "Retail", "BIST100"),
    SymbolSeed("MAVI", "Mavi Giyim", "Apparel", "BIST100"),
    SymbolSeed("BIZIM", "Bizim Toptan", "Retail", "BIST100"),
    SymbolSeed("RYSAS", "Reysaş Lojistik", "Logistics", None),
    SymbolSeed("CLEBI", "Çelebi Hava Servisi", "Transport", "BIST100"),
    SymbolSeed("TURGG", "Türker Proje Gayrimenkul", "REIT", None),
    SymbolSeed("ANELE", "Anel Elektrik", "Industrial", None),
    SymbolSeed("SISE", "Şişe Cam", "Glass", "BIST30,BIST100"),
    SymbolSeed("SODA", "Soda Sanayii", "Chemicals", "BIST100"),
    SymbolSeed("TRKCM", "Trakya Cam", "Glass", "BIST100"),
    SymbolSeed("ANACM", "Anadolu Cam", "Glass", None),
)


def deduped_seed() -> tuple[SymbolSeed, ...]:
    """Dedupe the seed list by symbol (in case of accidental duplicates)."""
    seen: set[str] = set()
    out: list[SymbolSeed] = []
    for s in BIST_SEED:
        if s.symbol in seen:
            continue
        seen.add(s.symbol)
        out.append(s)
    return tuple(out)
