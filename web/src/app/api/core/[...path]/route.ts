import { forward } from "@/lib/core";

export const dynamic = "force-dynamic";

// Read-only views the console polls; anything else is not proxied.
const ALLOWED = new Set(["system/status", "resilience", "alerts", "decisions"]);

export async function GET(_req: Request, ctx: RouteContext<"/api/core/[...path]">) {
  const path = (await ctx.params).path.join("/");
  if (!ALLOWED.has(path)) return Response.json({ detail: "not found" }, { status: 404 });
  return forward(`/api/v1/${path}`);
}
