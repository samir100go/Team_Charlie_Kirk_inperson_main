// Server-side helpers for talking to core-api. The login token lives in an httpOnly cookie,
// so browser JavaScript never sees it; only these route handlers attach it.
import { cookies } from "next/headers";

export const CORE_API_URL = process.env.CORE_API_URL ?? "http://localhost:8080";
export const TOKEN_COOKIE = "jalani_token";

export async function authHeader(): Promise<Record<string, string>> {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  return token ? { authorization: `Bearer ${token}` } : {};
}

export async function forward(
  path: string,
  init: { method?: string; body?: unknown; auth?: boolean; timeoutMs?: number } = {},
): Promise<Response> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (init.auth) Object.assign(headers, await authHeader());
  try {
    const res = await fetch(`${CORE_API_URL}${path}`, {
      method: init.method ?? "GET",
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
      signal: AbortSignal.timeout(init.timeoutMs ?? 5000),
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return Response.json({ detail: "core-api unreachable" }, { status: 502 });
  }
}
