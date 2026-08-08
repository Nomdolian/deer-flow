import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { api, type ApiCredentials } from "../api/client";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

/**
 * Requests notification permission, grabs an Expo push token, and registers
 * it with the trading-system backend (Phase 8, item 3: push for new
 * high-confidence signals, position open/close, daily-loss warnings, kill
 * switch, and feed-health issues). Safe to call repeatedly — registration is
 * idempotent on the token.
 */
export async function registerForPushNotifications(creds: ApiCredentials): Promise<string | null> {
  if (!Device.isDevice) {
    return null; // push tokens aren't available on simulators
  }

  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;
  if (status !== "granted") {
    const requested = await Notifications.requestPermissionsAsync();
    status = requested.status;
  }
  if (status !== "granted") {
    return null;
  }

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("default", {
      name: "default",
      importance: Notifications.AndroidImportance.HIGH,
    });
  }

  const projectId = Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
  const tokenResponse = await Notifications.getExpoPushTokenAsync(projectId ? { projectId } : undefined);
  const token = tokenResponse.data;

  await api.registerDevice(creds, token, Platform.OS === "ios" ? "ios" : "android");
  return token;
}
