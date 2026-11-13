import postgres from "postgres";
import { getEnv } from "./env";

// One pool per server process. The pool connects as the database owner; every user-facing
// query runs inside withRls(), which drops to the `authenticated` role and sets the request
// JWT claims so Postgres row-level security enforces tenant isolation and roles.
const globalForDb = globalThis as unknown as { __gccSql?: postgres.Sql };

export function sql(): postgres.Sql {
  if (!globalForDb.__gccSql) {
    globalForDb.__gccSql = postgres(getEnv().databaseUrl, {
      max: Number(process.env.DATABASE_POOL_MAX || 10),
      idle_timeout: 30,
      connect_timeout: 10,
      onnotice: () => {},
    });
  }
  return globalForDb.__gccSql;
}

export type Tx = postgres.TransactionSql;

export interface RlsIdentity {
  userId: string;
  tenantId: string;
}

export async function withRls<T>(who: RlsIdentity, fn: (tx: Tx) => Promise<T>): Promise<T> {
  const claims = JSON.stringify({ sub: who.userId, tenant_id: who.tenantId, role: "authenticated" });
  const result = await sql().begin(async (tx) => {
    await tx`select set_config('request.jwt.claims', ${claims}, true)`;
    await tx`set local role authenticated`;
    return fn(tx);
  });
  return result as T;
}
