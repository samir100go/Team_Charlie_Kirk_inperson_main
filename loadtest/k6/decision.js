// Load test of the end-to-end decision path (brief §17):
//   POST /api/v1/recommendations/compute  -> core-api sends the current world to the
//   intelligence service (/v1/predict: 12 forecasts + stockout projections), then runs the
//   capacity-aware allocation planner and returns every recommendation with its explanation.
// A second, lighter scenario polls the dashboard read model (GET /api/v1/state) the way the
// operator console does. VUS sets the decision concurrency.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.CORE_API_URL || "http://core-api:8080";
const VUS = Number(__ENV.VUS || 10);
const DURATION = __ENV.DURATION || "40s";

export const options = {
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  scenarios: {
    decision: {
      executor: "constant-vus",
      vus: VUS,
      duration: DURATION,
      exec: "decision",
      tags: { path: "decision" },
    },
    dashboard: {
      executor: "constant-vus",
      vus: 5,
      duration: DURATION,
      exec: "dashboard",
      tags: { path: "dashboard" },
    },
  },
  thresholds: {
    "http_req_failed{path:decision}": ["rate<0.01"],
    "http_req_duration{path:decision}": ["p(95)<2000"],
    "http_req_duration{path:dashboard}": ["p(95)<1000"],
    "http_reqs{path:decision}": ["count>0"], // exposes the per-path request rate
  },
};

export function decision() {
  const res = http.post(`${BASE}/api/v1/recommendations/compute`, null, {
    tags: { path: "decision" },
  });
  check(res, {
    "decision 200": (r) => r.status === 200,
    "forecast policy": (r) => r.status === 200 && r.json("policy") === "forecast",
  });
}

export function dashboard() {
  const res = http.get(`${BASE}/api/v1/state`, { tags: { path: "dashboard" } });
  check(res, { "state 200": (r) => r.status === 200 });
}
