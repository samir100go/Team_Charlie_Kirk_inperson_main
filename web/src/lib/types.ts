// Shapes returned by core-api GET /api/v1/state (services/core_api/core_api/decision.py).

export type Risk = "CRITICAL" | "HIGH" | "ELEVATED" | "NORMAL" | "OUTAGE";

export type Anomaly = {
  detected: boolean;
  direction: "spike" | "drop" | null;
  ratio: number;
  z: number;
  since_tick: number | null;
};

export type FuelCell = {
  fuel: string;
  inventory: number;
  capacity: number;
  in_flight: number;
  demand_lph: number;
  hours_to_stockout: number | null;
  hours_range: [number | null, number | null];
  stockout_prob: number | null;
  risk: Risk;
  anomaly: Anomaly | null;
  confidence: number | null;
  demand_24h: { p10: number; p50: number; p90: number } | null;
};

export type Station = {
  id: string;
  name: string;
  region_id: string;
  status: string;
  demand_multiplier: number;
  fuels: FuelCell[];
};

export type RiskSnapshot = {
  hours_p50: number | null;
  hours_early: number | null;
  hours_late: number | null;
  stockout_prob: number;
  horizon_hours: number;
  unmet_p50: number;
  tier: Risk;
};

export type Recommendation = {
  id: string;
  policy: "forecast" | "fallback";
  station_id: string;
  station: string;
  fuel: string;
  depot_id: string;
  depot: string;
  route_id: string;
  transit_ticks: number;
  eta_hours: number;
  quantity: number;
  why: string;
  warning: string | null;
  signals: { name: string; value: string }[];
  constraints: { name: string; value: string; binding: boolean }[];
  impact: { before: RiskSnapshot; after: RiskSnapshot };
  confidence: number | null;
  review_required: boolean;
  review_reasons: string[];
  alternatives: { action: string; after: RiskSnapshot; note: string }[];
};

export type Waiting = {
  station_id: string;
  station: string;
  fuel: string;
  hours_to_stockout: number | null;
  reason: string;
};

export type SimEvent = {
  id: number;
  type: string;
  start_tick: number;
  end_tick: number;
  status: string;
  parameters: Record<string, unknown>;
};

export type Depot = {
  id: string;
  name: string;
  status: string;
  inventory: Record<string, number>;
  dispatch_capacity_per_tick: number;
  dispatch_left: number;
  dispatch_planned: number;
};

export type Allocation = {
  id: number;
  destination_station_id: string;
  source_depot_id: string;
  fuel_type: string;
  quantity: number;
  status: string;
  created_tick: number;
  expected_arrival_tick: number | null;
  failure_reason: string | null;
};

export type Route = {
  id: string;
  source_depot_id: string;
  destination_station_id: string;
  status: string;
  transit_ticks: number;
  max_shipment: number;
};

export type SupplyArrival = {
  id: string;
  depot_id: string;
  fuel_type: string;
  quantity: number;
  planned_tick: number;
  status: string;
  eta_hours: number;
};

export type Component = { name: string; state: "Healthy" | "Degraded" | "Down"; detail: string };

export type Alert = {
  id: number;
  raised_at: string;
  severity: "critical" | "warning" | "info";
  source: string;
  kind: string;
  message: string;
  resolved_at: string | null;
};

export type SystemStatus = {
  components: Component[];
  p95_latency_ms: number | null;
  error_rate: number;
  window_s: number;
  open_alerts: Alert[];
};

export type ResilienceRow = {
  condition: string;
  response: string;
  active: boolean;
  detail: string | null;
};

export type Decision = {
  id: number;
  decided_at: string;
  sim_tick: number;
  station_id: string;
  fuel: string;
  depot_id: string;
  route_id: string;
  quantity: number;
  policy: string;
  confidence: number | null;
  review_required: boolean;
  reviewed: boolean;
  decided_by: string;
  role: string;
  risk_before: RiskSnapshot | null;
  risk_after: RiskSnapshot | null;
  result: string;
  simulator_detail: unknown;
  allocation_id: number | null;
  outcome: string | null;
  outcome_tick: number | null;
};

export type Me = { username: string; role: "operator" | "admin" } | null;

export type World = {
  meta: {
    age_s: number | null;
    simulator_available: boolean;
    error: string | null;
    sim_stale: boolean;
    source: "simulator" | "cache";
    invalid_payload: { endpoint: string; errors: string[] } | null;
    database: { available: boolean; error: string | null; buffered_writes: number } | null;
    prediction_service: {
      available: boolean;
      error: string | null;
      latency_ms: number | null;
      age_s: number | null;
    };
  };
  ready: boolean;
  policy?: "forecast" | "fallback";
  model_version?: string | null;
  forecast_mape?: number | null;
  instance?: { tick: number; sim_time: string; status: string; tick_minutes: number };
  metrics?: { service_level: number; unmet_demand_liters: number; served_demand_liters: number };
  stations?: Station[];
  depots?: Depot[];
  events?: SimEvent[];
  recommendations?: Recommendation[];
  waiting?: Waiting[];
  allocations?: Allocation[];
  routes?: Route[];
  supply?: SupplyArrival[];
};

export const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 0 });
export const pct = (p: number) => `${Math.round(p * 100)}%`;
export const hrs = (h: number | null, horizon = 24) => (h == null ? `>${horizon} h` : `${h} h`);
