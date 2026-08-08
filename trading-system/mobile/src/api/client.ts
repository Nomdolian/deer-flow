import type {
  KillSwitchStateDTO,
  OrderDTO,
  SignalDTO,
  StrategyDTO,
  StrategyPerformanceDTO,
  TradeJournalDTO,
  WatchlistInstrumentDTO,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export interface ApiCredentials {
  serverUrl: string;
  apiKey: string;
}

/**
 * Thin fetch wrapper for the trading-system FastAPI backend. This talks to
 * the always-on server over the network — it never touches broker/exchange
 * credentials or trading logic itself. The phone is a client, same as the PC
 * dashboard; the server keeps trading whether or not this app is open.
 */
async function request<T>(
  creds: ApiCredentials,
  path: string,
  options: { method?: string; body?: unknown; totpCode?: string } = {}
): Promise<T> {
  if (!creds.serverUrl || !creds.apiKey) {
    throw new ApiError(0, "Server not configured — set it up in Settings first");
  }
  const url = `${creds.serverUrl.replace(/\/+$/, "")}${path}`;
  const headers: Record<string, string> = {
    "x-api-key": creds.apiKey,
    "Content-Type": "application/json",
  };
  if (options.totpCode) {
    headers["x-totp-code"] = options.totpCode;
  }

  const response = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText);
    throw new ApiError(response.status, text || response.statusText);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  health: (creds: ApiCredentials) => request<{ status: string }>(creds, "/health"),

  positions: (creds: ApiCredentials) => request<OrderDTO[]>(creds, "/positions"),

  signals: (creds: ApiCredentials) => request<SignalDTO[]>(creds, "/signals"),

  journal: (creds: ApiCredentials, filters?: { strategy_id?: string; instrument?: string }) => {
    const params = new URLSearchParams();
    if (filters?.strategy_id) params.set("strategy_id", filters.strategy_id);
    if (filters?.instrument) params.set("instrument", filters.instrument);
    const qs = params.toString();
    return request<TradeJournalDTO[]>(creds, `/journal${qs ? `?${qs}` : ""}`);
  },

  strategies: (creds: ApiCredentials) => request<StrategyDTO[]>(creds, "/strategies"),

  strategyPerformance: (creds: ApiCredentials, strategyId: string, version: number, instrument: string) =>
    request<StrategyPerformanceDTO>(
      creds,
      `/strategies/${encodeURIComponent(strategyId)}/${version}/performance?instrument=${encodeURIComponent(instrument)}`
    ),

  killSwitchState: (creds: ApiCredentials) => request<KillSwitchStateDTO>(creds, "/killswitch"),

  engageKillSwitch: (creds: ApiCredentials, reason: string) =>
    request<{ engaged: boolean }>(creds, "/killswitch/engage", { method: "POST", body: { reason } }),

  disengageKillSwitch: (creds: ApiCredentials, totpCode: string) =>
    request<{ engaged: boolean }>(creds, "/killswitch/disengage", { method: "POST", totpCode }),

  registerDevice: (creds: ApiCredentials, pushToken: string, platform: "ios" | "android", label?: string) =>
    request<{ id: string }>(creds, "/devices/register", {
      method: "POST",
      body: { push_token: pushToken, platform, label },
    }),

  unregisterDevice: (creds: ApiCredentials, pushToken: string) =>
    request<{ ok: boolean }>(creds, "/devices/unregister", { method: "POST", body: { push_token: pushToken } }),

  watchlist: (creds: ApiCredentials) => request<WatchlistInstrumentDTO[]>(creds, "/watchlist"),

  addToWatchlist: (
    creds: ApiCredentials,
    params: { instrument: string; asset_class: string; timeframe?: string; data_source?: string; enabled?: boolean }
  ) => request<{ id: string; instrument: string; enabled: boolean }>(creds, "/watchlist", { method: "POST", body: params }),

  setWatchlistEnabled: (creds: ApiCredentials, instrument: string, enabled: boolean) =>
    request<{ instrument: string; enabled: boolean }>(creds, `/watchlist/${encodeURIComponent(instrument)}/enabled`, {
      method: "POST",
      body: { enabled },
    }),

  removeFromWatchlist: (creds: ApiCredentials, instrument: string) =>
    request<{ ok: boolean }>(creds, `/watchlist/${encodeURIComponent(instrument)}`, { method: "DELETE" }),
};
