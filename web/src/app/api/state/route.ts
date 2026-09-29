// Proxy to core-api's live world view (server side, so the browser never needs core-api's URL).
const CORE_API_URL = process.env.CORE_API_URL ?? "http://localhost:8080";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const res = await fetch(`${CORE_API_URL}/api/v1/state`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return Response.json({ error: "core-api unreachable" }, { status: 502 });
  }
}
