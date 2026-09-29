import { cookies } from "next/headers";

import { CORE_API_URL, TOKEN_COOKIE } from "@/lib/core";

export async function POST(req: Request) {
  let username = "";
  let password = "";
  try {
    const body = (await req.json()) as { username?: unknown; password?: unknown };
    username = typeof body.username === "string" ? body.username.slice(0, 64) : "";
    password = typeof body.password === "string" ? body.password.slice(0, 256) : "";
  } catch {
    return Response.json({ detail: "invalid body" }, { status: 400 });
  }
  try {
    const res = await fetch(`${CORE_API_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username, password }),
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    const body = await res.json();
    if (!res.ok) return Response.json(body, { status: res.status });
    (await cookies()).set(TOKEN_COOKIE, body.token, {
      httpOnly: true,
      sameSite: "strict",
      path: "/",
      maxAge: 8 * 3600,
    });
    return Response.json({ username: body.username, role: body.role });
  } catch {
    return Response.json({ detail: "core-api unreachable" }, { status: 502 });
  }
}
