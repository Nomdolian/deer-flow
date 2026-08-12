import { StatusBar } from "expo-status-bar";
import React from "react";
import { ActivityIndicator, SafeAreaView, StyleSheet, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ErrorBoundary } from "./src/components/ErrorBoundary";
import RootNavigator from "./src/navigation/RootNavigator";
import SettingsScreen from "./src/screens/SettingsScreen";
import { SettingsProvider, useSettings } from "./src/context/SettingsContext";
import { colors } from "./src/theme";

function Gate() {
  const { loaded, isConfigured } = useSettings();

  if (!loaded) {
    return (
      <View style={styles.splash}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  // Nothing else is usable until the server connection is configured — the
  // app has no trading logic of its own to fall back to.
  if (!isConfigured) {
    return (
      <SafeAreaView style={styles.splash}>
        <SettingsScreen />
      </SafeAreaView>
    );
  }

  return <RootNavigator />;
}

export default function App() {
  // The boundary wraps everything including the provider: a storage failure
  // used to take the whole tree down to a blank screen, which on a trading app
  // is indistinguishable from the server being dead.
  return (
    <ErrorBoundary>
      <SafeAreaProvider>
        <SettingsProvider>
          <Gate />
          <StatusBar style="light" />
        </SettingsProvider>
      </SafeAreaProvider>
    </ErrorBoundary>
  );
}

const styles = StyleSheet.create({
  splash: { flex: 1, backgroundColor: colors.background },
});
