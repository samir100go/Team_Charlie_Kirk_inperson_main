import { forward } from "@/lib/core";

export const dynamic = "force-dynamic";

// Anonymous visitors are normal (the dashboard is read-only for them): answer null, not 401.
export async function GET() {
  const res = await forward("/api/v1/auth/me", { auth: true });
  return res.status === 401 ? Response.json(null) : res;
}
