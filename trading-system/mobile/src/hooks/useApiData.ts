import { useCallback, useEffect, useRef, useState } from "react";

import { useSettings } from "../context/SettingsContext";
import type { ApiCredentials } from "../api/client";

interface UseApiDataOptions {
  pollIntervalMs?: number; // set to poll on a timer in addition to manual refresh
}

interface UseApiDataResult<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refreshing: boolean;
  refresh: () => Promise<void>;
}

/**
 * Fetches from the server on mount, on credential change, and optionally on
 * a poll interval — this is how the client stays in sync with a system that
 * keeps trading whether or not the phone app is in the foreground.
 */
export function useApiData<T>(
  fetcher: (creds: ApiCredentials) => Promise<T>,
  options: UseApiDataOptions = {}
): UseApiDataResult<T> {
  const { serverUrl, apiKey, isConfigured } = useSettings();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(
    async (isRefresh: boolean) => {
      if (!isConfigured) {
        setLoading(false);
        return;
      }
      isRefresh ? setRefreshing(true) : setLoading(true);
      try {
        const result = await fetcherRef.current({ serverUrl, apiKey });
        setData(result);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        isRefresh ? setRefreshing(false) : setLoading(false);
      }
    },
    [serverUrl, apiKey, isConfigured]
  );

  useEffect(() => {
    load(false);
    if (!options.pollIntervalMs) return;
    const id = setInterval(() => load(true), options.pollIntervalMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverUrl, apiKey, isConfigured, options.pollIntervalMs]);

  return { data, error, loading, refreshing, refresh: () => load(true) };
}
