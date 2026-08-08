import * as SecureStore from "expo-secure-store";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const SERVER_URL_KEY = "trading_system_server_url";
const API_KEY_KEY = "trading_system_api_key";

interface SettingsState {
  serverUrl: string;
  apiKey: string;
  loaded: boolean;
  isConfigured: boolean;
  setServerUrl: (value: string) => Promise<void>;
  setApiKey: (value: string) => Promise<void>;
  clear: () => Promise<void>;
}

const SettingsContext = createContext<SettingsState | undefined>(undefined);

export function SettingsProvider({ children }: { children: React.ReactNode }) {
  const [serverUrl, setServerUrlState] = useState("");
  const [apiKey, setApiKeyState] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      const [storedUrl, storedKey] = await Promise.all([
        SecureStore.getItemAsync(SERVER_URL_KEY),
        SecureStore.getItemAsync(API_KEY_KEY),
      ]);
      setServerUrlState(storedUrl ?? "");
      setApiKeyState(storedKey ?? "");
      setLoaded(true);
    })();
  }, []);

  const setServerUrl = useCallback(async (value: string) => {
    setServerUrlState(value);
    await SecureStore.setItemAsync(SERVER_URL_KEY, value);
  }, []);

  const setApiKey = useCallback(async (value: string) => {
    setApiKeyState(value);
    await SecureStore.setItemAsync(API_KEY_KEY, value);
  }, []);

  const clear = useCallback(async () => {
    await Promise.all([SecureStore.deleteItemAsync(SERVER_URL_KEY), SecureStore.deleteItemAsync(API_KEY_KEY)]);
    setServerUrlState("");
    setApiKeyState("");
  }, []);

  const value = useMemo<SettingsState>(
    () => ({
      serverUrl,
      apiKey,
      loaded,
      isConfigured: Boolean(serverUrl && apiKey),
      setServerUrl,
      setApiKey,
      clear,
    }),
    [serverUrl, apiKey, loaded, setServerUrl, setApiKey, clear]
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsState {
  const ctx = useContext(SettingsContext);
  if (!ctx) {
    throw new Error("useSettings must be used within a SettingsProvider");
  }
  return ctx;
}
