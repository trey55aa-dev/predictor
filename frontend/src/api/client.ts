import type {
  GamePlan,
  GamePrediction,
  ParlaysResponse,
  Performance,
  PlayerProjectionsResponse,
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

export function fetchSystems(): Promise<SchemeFamily[]> {
  return get(`/api/systems`);
}

export function fetchSystemDetail(id: string): Promise<SchemeDetail> {
  return get(`/api/systems/${id}`);
}

export function fetchGamePlan(gameId: string): Promise<GamePlan> {
  return get(`/api/predictions/game/${gameId}/gameplan`);
}

export function fetchParlays(season: number, week: number, legs = 3): Promise<ParlaysResponse> {
  return get(`/api/parlays/week/${season}/${week}?legs=${legs}`);
}

export function fetchPlayerProjections(gameId: string): Promise<PlayerProjectionsResponse> {
  return get(`/api/predictions/game/${gameId}/players`);
}
