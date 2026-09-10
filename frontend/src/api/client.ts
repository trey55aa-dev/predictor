import type {
  AccuracyComparison,
  CalibrationAdjustment,
  GameBreakdown,
  GamePlan,
  GamePrediction,
  ParlaysResponse,
  Performance,
  PlayerProjectionsResponse,
  SimPlayerPropsResponse,
  SlipEvaluation,
  SlipLegInput,
  SchemeDetail,
  SchemeFamily,
} from "../types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`Request to ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function fetchPredictionsForWeek(season: number, week: number): Promise<GamePrediction[]> {
  return get(`/api/predictions/week/${season}/${week}`);
}

export function fetchPerformance(season?: number): Promise<Performance> {
  const query = season ? `?season=${season}` : "";
  return get(`/api/model/performance${query}`);
}

export function fetchAccuracyComparison(): Promise<AccuracyComparison> {
  return get(`/api/model/accuracy-comparison`);
}

export function fetchSystems(): Promise<SchemeFamily[]> {
  return get(`/api/systems`);
}

export function fetchSystemDetail(id: string): Promise<SchemeDetail> {
  return get(`/api/systems/${id}`);
}

export function fetchGamePlan(gameId: string): Promise<GamePlan> {
  return get(`/api/predictions/game/${gameId}/gameplan`);
}

export function fetchSimPlayerProps(gameId: string): Promise<SimPlayerPropsResponse> {
  return get(`/api/predictions/game/${gameId}/sim-player-props`);
}

export function fetchGameBreakdown(gameId: string): Promise<GameBreakdown> {
  return get(`/api/predictions/game/${gameId}/breakdown`);
}

export async function evaluateSlip(legs: SlipLegInput[]): Promise<SlipEvaluation> {
  const res = await fetch(`${BASE_URL}/api/slip/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      legs: legs.map(({ key: _key, ...rest }) => rest),
    }),
  });
  if (!res.ok) {
    throw new Error(`Request to /api/slip/evaluate failed: ${res.status}`);
  }
  return res.json() as Promise<SlipEvaluation>;
}

export function fetchParlays(season: number, week: number, legs = 3): Promise<ParlaysResponse> {
  return get(`/api/parlays/week/${season}/${week}?legs=${legs}`);
}

export function fetchPlayerProjections(gameId: string): Promise<PlayerProjectionsResponse> {
  return get(`/api/predictions/game/${gameId}/players`);
}

export function fetchCalibrationHistory(): Promise<CalibrationAdjustment[]> {
  return get(`/api/model/calibration-history`);
}
