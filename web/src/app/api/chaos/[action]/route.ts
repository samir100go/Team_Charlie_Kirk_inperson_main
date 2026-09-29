import { forward } from "@/lib/core";

// Admin-only failure injection for the resilience demo (core-api enforces the role).
const ACTIONS = new Set([
  "simulator-fault",
  "demand-spike",
  "corrupt-simulator",
  "prediction-outage",
  "clear",
]);

export async function POST(req: Request, ctx: RouteContext<"/api/chaos/[action]">) {
  const { action } = await ctx.params;
  if (!ACTIONS.has(action)) return Response.json({ detail: "not found" }, { status: 404 });
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    // empty body is fine
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) body = {};
  return forward(`/api/v1/chaos/${action}`, { method: "POST", body, auth: true });
}
