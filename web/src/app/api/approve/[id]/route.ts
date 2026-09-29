import { forward } from "@/lib/core";

export async function POST(req: Request, ctx: RouteContext<"/api/approve/[id]">) {
  const { id } = await ctx.params;
  if (!/^[A-Za-z0-9_.-]{1,120}$/.test(id)) {
    return Response.json({ detail: "invalid recommendation id" }, { status: 422 });
  }
  // Only forward the one field core-api accepts; never pass arbitrary client JSON through.
  let reviewed = false;
  try {
    const body = (await req.json()) as { reviewed?: unknown };
    reviewed = body?.reviewed === true;
  } catch {
    // no body: a plain approve
  }
  return forward(`/api/v1/recommendations/${id}/approve`, {
    method: "POST",
    body: { reviewed },
    auth: true,
    timeoutMs: 8000,
  });
}
