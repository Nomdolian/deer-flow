import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

/**
 * Where the server URL and API key live.
 *
 * On iOS and Android this is the device keychain/keystore via
 * expo-secure-store, which is the right home for a key that can halt or resume
 * live trading.
 *
 * On web it cannot be. expo-secure-store ships `export default {}` as its
 * entire web implementation, so calling into it throws
 * "getValueWithKeyAsync is not a function" and takes the whole app down with a
 * blank screen. We fall back to localStorage there — but the backend is
 * reported so the Settings screen can say plainly that browser storage is
 * weaker than the keychain, rather than implying the key is protected when it
 * isn't.
 */

export type StorageBackend = "keychain" | "browser" | "memory";

const SERVER_URL_KEY = "trading_system_server_url";
const API_KEY_KEY = "trading_system_api_key";

/** Last resort if both keychain and localStorage are unavailable (private
 *  browsing modes block localStorage). Credentials then last for the session
 *  only, which is inconvenient but far better than a crash. */
const memory = new Map<string, string>();

const usingSecureStore = Platform.OS !== "web";

function browserStorage(): Storage | null {
  try {
    if (typeof localStorage === "undefined") return null;
    // Touch it: Safari private mode throws on access rather than on read.
    const probe = "__trading_probe__";
    localStorage.setItem(probe, "1");
    localStorage.removeItem(probe);
    return localStorage;
  } catch {
    return null;
  }
}

export function activeBackend(): StorageBackend {
  if (usingSecureStore) return "keychain";
  return browserStorage() ? "browser" : "memory";
}

async function readKey(key: string): Promise<string | null> {
  if (usingSecureStore) return SecureStore.getItemAsync(key);
  const store = browserStorage();
  return store ? store.getItem(key) : (memory.get(key) ?? null);
}

async function writeKey(key: string, value: string): Promise<void> {
  if (usingSecureStore) {
    await SecureStore.setItemAsync(key, value);
    return;
  }
  const store = browserStorage();
  if (store) store.setItem(key, value);
  else memory.set(key, value);
}

async function removeKey(key: string): Promise<void> {
  if (usingSecureStore) {
    await SecureStore.deleteItemAsync(key);
    return;
  }
  const store = browserStorage();
  if (store) store.removeItem(key);
  else memory.delete(key);
}

export interface StoredCredentials {
  serverUrl: string;
  apiKey: string;
}

export async function loadCredentials(): Promise<StoredCredentials> {
  const [serverUrl, apiKey] = await Promise.all([readKey(SERVER_URL_KEY), readKey(API_KEY_KEY)]);
  return { serverUrl: serverUrl ?? "", apiKey: apiKey ?? "" };
}

export async function saveServerUrl(value: string): Promise<void> {
  await writeKey(SERVER_URL_KEY, value);
}

export async function saveApiKey(value: string): Promise<void> {
  await writeKey(API_KEY_KEY, value);
}

export async function clearCredentials(): Promise<void> {
  await Promise.all([removeKey(SERVER_URL_KEY), removeKey(API_KEY_KEY)]);
}
