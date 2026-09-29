const CORE_API_URL = process.env.CORE_API_URL ?? "http://localhost:8080";

export async function POST(_req: Request, ctx: RouteContext<"/api/approve/[id]">) {
  const { id } = await ctx.params;
  try {
    const res = await fetch(
      `${CORE_API_URL}/api/v1/recommendations/${encodeURIComponent(id)}/approve`,
      { method: "POST", cache: "no-store", signal: AbortSignal.timeout(8000) },
    );
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return Response.json({ detail: "core-api unreachable" }, { status: 502 });
  }
}
