import postgres from "postgres";

const url =
  process.env.DATABASE_URL ?? "postgres://matrix:matrix_dev_only@localhost:5432/matrix";

// Single shared connection pool across HMR reloads
declare global {
  // eslint-disable-next-line no-var
  var __sql: ReturnType<typeof postgres> | undefined;
}

export const sql =
  global.__sql ??
  postgres(url, {
    max: 5,
    idle_timeout: 30,
    connect_timeout: 10,
  });

if (process.env.NODE_ENV !== "production") {
  global.__sql = sql;
}
