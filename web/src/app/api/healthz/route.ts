// Liveness probe for the container HEALTHCHECK. Route Handlers are not cached by default.
export function GET() {
  return Response.json({ status: "ok", service: "web" });
}
