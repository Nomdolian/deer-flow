import { StatusBar } from "expo-status-bar";
import React from "react";
import { ActivityIndicator, SafeAreaView, StyleSheet, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

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
  return (
    <SafeAreaProvider>
      <SettingsProvider>
        <Gate />
        <StatusBar style="light" />
      </SettingsProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  splash: { flex: 1, backgroundColor: colors.background },
});
