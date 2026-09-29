import { cookies } from "next/headers";

import { TOKEN_COOKIE } from "@/lib/core";

export async function POST() {
  (await cookies()).delete(TOKEN_COOKIE);
  return Response.json({ ok: true });
}
