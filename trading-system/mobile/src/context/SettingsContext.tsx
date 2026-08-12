import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  activeBackend,
  clearCredentials,
  loadCredentials,
  saveApiKey,
  saveServerUrl,
  type StorageBackend,
} from "../storage/credentialStore";

interface SettingsState {
  serverUrl: string;
  apiKey: string;
  loaded: boolean;
  isConfigured: boolean;
  /** Where the credentials are kept, so the UI can be honest about it. */
  backend: StorageBackend;
  /** Set if reading or writing storage failed. Surfaced rather than swallowed:
   *  silently forgetting the API key looks like the server going down. */
  storageError: string | null;
  setServerUrl: (value: string) => Promise<void>;
  setApiKey: (value: string) => Promise<void>;
  clear: () => Promise<void>;
}

const SettingsContext = createContext<SettingsState | undefined>(undefined);

export function SettingsProvider({ children }: { children: React.ReactNode }) {
  const [serverUrl, setServerUrlState] = useState("");
  const [apiKey, setApiKeyState] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [storageError, setStorageError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stored = await loadCredentials();
        if (cancelled) return;
        setServerUrlState(stored.serverUrl);
        setApiKeyState(stored.apiKey);
      } catch (err) {
        // Must not leave `loaded` false: the app renders a loading spinner
        // until it flips, so a keychain error used to hang the whole app on a
        // blank screen with no way out.
        if (!cancelled) setStorageError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const setServerUrl = useCallback(async (value: string) => {
    setServerUrlState(value);
    try {
      await saveServerUrl(value);
      setStorageError(null);
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const setApiKey = useCallback(async (value: string) => {
    setApiKeyState(value);
    try {
      await saveApiKey(value);
      setStorageError(null);
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const clear = useCallback(async () => {
    setServerUrlState("");
    setApiKeyState("");
    try {
      await clearCredentials();
      setStorageError(null);
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const value = useMemo<SettingsState>(
    () => ({
      serverUrl,
      apiKey,
      loaded,
      isConfigured: Boolean(serverUrl && apiKey),
      backend: activeBackend(),
      storageError,
      setServerUrl,
      setApiKey,
      clear,
    }),
    [serverUrl, apiKey, loaded, storageError, setServerUrl, setApiKey, clear]
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
