const CORE_API_URL = process.env.CORE_API_URL ?? "http://localhost:8080";

export async function POST(req: Request, ctx: RouteContext<"/api/approve/[id]">) {
  const { id } = await ctx.params;
  // Only forward the one field core-api accepts; never pass arbitrary client JSON through.
  let reviewed = false;
  try {
    const body = (await req.json()) as { reviewed?: unknown };
    reviewed = body?.reviewed === true;
  } catch {
    // no body: a plain approve
  }
  try {
    const res = await fetch(
      `${CORE_API_URL}/api/v1/recommendations/${encodeURIComponent(id)}/approve`,
      {
        method: "POST",
        cache: "no-store",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reviewed }),
        signal: AbortSignal.timeout(8000),
      },
    );
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return Response.json({ detail: "core-api unreachable" }, { status: 502 });
  }
}
