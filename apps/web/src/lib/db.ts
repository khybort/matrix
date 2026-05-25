import postgres from "postgres";

const sharedUrl =
  process.env.DATABASE_URL ?? "postgres://matrix:matrix_dev_only@postgres:5432/matrix";
const localUrl =
  process.env.LOCAL_DATABASE_URL ?? sharedUrl;

// Pools persisted across HMR reloads
declare global {
  // eslint-disable-next-line no-var
  var __sql_shared: ReturnType<typeof postgres> | undefined;
  // eslint-disable-next-line no-var
  var __sql_local: ReturnType<typeof postgres> | undefined;
}

function makePool(url: string) {
  return postgres(url, {
    max: 5,
    idle_timeout: 30,
    connect_timeout: 10,
  });
}

// SHARED tier (Neon or postgres-shared) — wallet, predictions, lab, graph_signals
export const sql = global.__sql_shared ?? makePool(sharedUrl);

// LOCAL tier (per-PC) — market data, raw_documents, AGE matrix_graph
export const sqlLocal = global.__sql_local ?? makePool(localUrl);

if (process.env.NODE_ENV !== "production") {
  global.__sql_shared = sql;
  global.__sql_local = sqlLocal;
}
