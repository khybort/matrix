"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export type AssetClass = "crypto" | "bist" | string;

const NAV = [
  { href: "/",           label: "Trades",     num: "01" },
  { href: "/strategies", label: "Strategies", num: "02" },
  { href: "/research",   label: "Research",   num: "03" },
  { href: "/markets",    label: "Markets",    num: "04" },
] as const;

// ────────────────────────────────────────────────────────────────────────
// Sidebar — terminal-strip nav with phosphor accent
// ────────────────────────────────────────────────────────────────────────

export function Sidebar({ equity, totalPct, circuitTripped, now }: {
  equity?: number;
  totalPct?: number;
  circuitTripped?: boolean;
  now?: string;
}) {
  const pathname = usePathname();
  return (
    <aside
      className="hidden md:flex flex-col w-[208px] shrink-0 sticky top-0 h-screen"
      style={{
        background: "var(--bg-deep)",
        borderRight: "1px solid var(--rule)",
      }}
    >
      {/* Brand mark */}
      <div className="px-5 pt-5 pb-4">
        <Link href="/" className="block">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10px] tracker accent">▦</span>
            <span className="text-base" style={{ letterSpacing: "0.06em" }}>matrix</span>
          </div>
          <div className="serif-i text-[12px] dim mt-0.5">trading terminal</div>
        </Link>
      </div>

      {/* Equity ticker block */}
      {equity != null && totalPct != null && (
        <div className="px-5 pb-4">
          <div className="text-[9.5px] tracker dim mb-1">Equity</div>
          <div className="headline text-[28px] text-[var(--ink)]">
            {fmtMoney(equity)}
          </div>
          <div className="flex items-center gap-2 mt-1">
            <span className={`text-[11px] tnum ${totalPct >= 0 ? "pos" : "neg"}`}>
              {totalPct >= 0 ? "+" : ""}{totalPct.toFixed(3)}%
            </span>
            {circuitTripped !== undefined && (
              circuitTripped ? (
                <span className="flex items-center gap-1.5 text-[9px] tracker-sm neg">
                  <span className="pulse-sq inline-block w-1.5 h-1.5 bg-current" />
                  Tripped
                </span>
              ) : (
                <span className="flex items-center gap-1.5 text-[9px] tracker-sm pos">
                  <span className="pulse-sq inline-block w-1.5 h-1.5 bg-current" />
                  Live
                </span>
              )
            )}
          </div>
        </div>
      )}

      <div className="mx-5 mb-3" style={{ borderTop: "1px solid var(--rule)" }} />

      {/* Nav */}
      <nav className="flex-1 px-2">
        {NAV.map((n) => {
          const active = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
          return (
            <Link key={n.href} href={n.href} className={`nav-row ${active ? "active" : ""}`}>
              <span className="num">{n.num}</span>
              <span>{n.label}</span>
              <span className="lead" />
              {active && <span className="accent text-[10px]">▸</span>}
            </Link>
          );
        })}
      </nav>

      {/* Teletype footer */}
      {now && (
        <div className="px-5 py-4" style={{ borderTop: "1px solid var(--rule)" }}>
          <div className="teletype">
            <span className="arrow">▸ </span>
            <span>{formatClock(new Date(now))}</span>
          </div>
          <div className="teletype mt-1">
            <span className="arrow">← </span>
            <span>/api/dashboard</span>
          </div>
        </div>
      )}
    </aside>
  );
}

// ────────────────────────────────────────────────────────────────────────
// PageShell — full bleed shell with sidebar slot
// ────────────────────────────────────────────────────────────────────────

export function PageShell({ sidebar, children }: {
  sidebar: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen flex" style={{ background: "var(--bg)" }}>
      {sidebar}
      <main className="flex-1 min-w-0">
        <div className="max-w-[1400px] mx-auto px-6 lg:px-10 py-8 space-y-7">
          {children}
        </div>
      </main>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// Panel — bracketed frame with title strip
// ────────────────────────────────────────────────────────────────────────

export function Panel({ title, action, children, className = "", tight = false, plain = false }: {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  tight?: boolean;
  plain?: boolean;
}) {
  return (
    <section className={`frame ${className}`}>
      {title && (
        <header className="frame-title">
          <span className="frame-title-marker" />
          <span className="frame-title-label">{title}</span>
          <span className="frame-title-rule" />
          {action && <span className="text-[10px] dim tracker-sm">{action}</span>}
        </header>
      )}
      <div className={plain ? "" : tight ? "frame-body-tight" : "frame-body"}>
        {children}
      </div>
    </section>
  );
}

// ────────────────────────────────────────────────────────────────────────
// MarketHeader — ledger strip with dotted leader + row count
// ────────────────────────────────────────────────────────────────────────

export function MarketHeader({ label, count, tone }: {
  label: string;
  count?: number;
  tone: "crypto" | "bist";
}) {
  const color = tone === "bist" ? "var(--bist)" : "var(--crypto)";
  return (
    <div className="flex items-center gap-3 mb-3 mt-1">
      <span className="ledger" style={{ ["--bar" as never]: color }}>
        <span className="glyph" />
        {label}
      </span>
      <span
        className="flex-1 h-[1px]"
        style={{
          background:
            "repeating-linear-gradient(90deg, var(--rule-2) 0 2px, transparent 2px 6px)",
        }}
      />
      {count !== undefined && (
        <span className="text-[10px] tracker-sm dim tnum">{count} rows</span>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// AssetBadge — minimal chip
// ────────────────────────────────────────────────────────────────────────

export function AssetBadge({ cls }: { cls?: string }) {
  const label = cls ?? "crypto";
  const color = label === "bist" ? "var(--bist)" : "var(--crypto)";
  return (
    <span
      className="chip tnum"
      style={{ color, borderColor: color, opacity: 0.85 }}
    >
      {label}
    </span>
  );
}

// ────────────────────────────────────────────────────────────────────────
// Stat — label/value stack with hairline cap
// ────────────────────────────────────────────────────────────────────────

export function Stat({ label, value, sub, valueClass = "", subClass = "dim" }: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
  subClass?: string;
}) {
  return (
    <div className="pt-1.5" style={{ borderTop: "1px solid var(--rule)" }}>
      <div className="text-[9.5px] tracker dim">{label}</div>
      <div className={`text-[15px] tnum mt-0.5 ${valueClass}`}>{value}</div>
      {sub && <div className={`text-[10px] tnum ${subClass}`}>{sub}</div>}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// EmptyHint — dotted-leader prose
// ────────────────────────────────────────────────────────────────────────

export function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 py-2">
      <span className="text-[10px] tracker dim">∅ empty</span>
      <span
        className="flex-1 h-[1px]"
        style={{
          background:
            "repeating-linear-gradient(90deg, var(--rule) 0 2px, transparent 2px 5px)",
        }}
      />
      <span className="serif-i text-[12px] muted">{children}</span>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// Formatters
// ────────────────────────────────────────────────────────────────────────

export function fmtUsd(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(n);
}

export function fmtMoney(n: number): string {
  // Compact serif-friendly money — no $ glyph (looks awkward in serif italic)
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

export function formatClock(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function formatDuration(ms: number): string {
  if (ms < 0) ms = 0;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${s % 60 ? ` ${s % 60}s` : ""}`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

export function splitByMarket<T extends { asset_class?: string }>(rows: T[]): {
  crypto: T[]; bist: T[];
} {
  const crypto: T[] = [];
  const bist: T[] = [];
  for (const r of rows) {
    if (r.asset_class === "bist") bist.push(r);
    else crypto.push(r);
  }
  return { crypto, bist };
}
