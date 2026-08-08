import React, { useState } from "react";
import { Alert, SafeAreaView, ScrollView, StyleSheet, Switch, Text, TextInput, TouchableOpacity, View } from "react-native";

import { ApiError, api } from "../api/client";
import { Card, SectionTitle } from "../components/Common";
import { useSettings } from "../context/SettingsContext";
import { registerForPushNotifications } from "../notifications/register";
import { colors, radii, spacing } from "../theme";

export default function SettingsScreen() {
  const { serverUrl, apiKey, isConfigured, setServerUrl, setApiKey, clear } = useSettings();
  const [urlInput, setUrlInput] = useState(serverUrl);
  const [keyInput, setKeyInput] = useState(apiKey);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [pushEnabled, setPushEnabled] = useState(false);
  const [registeringPush, setRegisteringPush] = useState(false);

  const save = async () => {
    await setServerUrl(urlInput.trim());
    await setApiKey(keyInput.trim());
    setTestResult(null);
    Alert.alert("Saved", "Server settings updated.");
  };

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      await api.health({ serverUrl: urlInput.trim(), apiKey: keyInput.trim() });
      setTestResult("Connected ✓");
    } catch (err) {
      setTestResult(err instanceof ApiError ? `Failed (HTTP ${err.status}): ${err.message}` : `Failed: ${String(err)}`);
    } finally {
      setTesting(false);
    }
  };

  const togglePush = async (value: boolean) => {
    if (!value) {
      setPushEnabled(false);
      return;
    }
    setRegisteringPush(true);
    try {
      const token = await registerForPushNotifications({ serverUrl, apiKey });
      if (token) {
        setPushEnabled(true);
      } else {
        Alert.alert("Push notifications unavailable", "Permission was denied, or this is a simulator without push support.");
      }
    } catch (err) {
      Alert.alert("Registration failed", err instanceof Error ? err.message : String(err));
    } finally {
      setRegisteringPush(false);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>Server connection</Text>
        <Text style={styles.subtitle}>
          Point this app at your always-on trading-system server. The server keeps trading whether or not this app
          is open — this app is a control surface, not the execution engine.
        </Text>

        <Card>
          <Text style={styles.label}>Server URL</Text>
          <TextInput
            style={styles.input}
            placeholder="https://your-vps.example.com"
            placeholderTextColor={colors.textSecondary}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            value={urlInput}
            onChangeText={setUrlInput}
          />
          <Text style={styles.label}>API key</Text>
          <TextInput
            style={styles.input}
            placeholder="matches API_AUTH_SECRET on the server"
            placeholderTextColor={colors.textSecondary}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
            value={keyInput}
            onChangeText={setKeyInput}
          />

          <TouchableOpacity style={styles.primaryButton} onPress={save}>
            <Text style={styles.primaryButtonText}>Save</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.secondaryButton} onPress={testConnection} disabled={testing}>
            <Text style={styles.secondaryButtonText}>{testing ? "Testing…" : "Test connection"}</Text>
          </TouchableOpacity>
          {testResult ? <Text style={styles.testResult}>{testResult}</Text> : null}
        </Card>

        {isConfigured && (
          <>
            <SectionTitle>Push notifications</SectionTitle>
            <Card>
              <View style={styles.row}>
                <Text style={styles.label}>New signals, fills, kill switch, feed issues</Text>
                <Switch value={pushEnabled} onValueChange={togglePush} disabled={registeringPush} />
              </View>
            </Card>

            <SectionTitle>Danger zone</SectionTitle>
            <Card>
              <TouchableOpacity
                style={styles.dangerButton}
                onPress={() => {
                  setUrlInput("");
                  setKeyInput("");
                  clear();
                }}
              >
                <Text style={styles.dangerButtonText}>Forget server credentials</Text>
              </TouchableOpacity>
            </Card>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg },
  title: { color: colors.textPrimary, fontSize: 22, fontWeight: "700", marginBottom: spacing.xs },
  subtitle: { color: colors.textSecondary, fontSize: 13, marginBottom: spacing.lg, lineHeight: 18 },
  label: { color: colors.textSecondary, fontSize: 13, marginBottom: spacing.xs, marginTop: spacing.sm },
  input: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.textPrimary,
    padding: spacing.md,
    fontSize: 14,
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: radii.md,
    padding: spacing.md,
    alignItems: "center",
    marginTop: spacing.lg,
  },
  primaryButtonText: { color: "#04101F", fontWeight: "700", fontSize: 15 },
  secondaryButton: { padding: spacing.md, alignItems: "center", marginTop: spacing.sm },
  secondaryButtonText: { color: colors.accent, fontWeight: "600" },
  testResult: { color: colors.textSecondary, textAlign: "center", marginTop: spacing.xs, fontSize: 13 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  dangerButton: { padding: spacing.md, alignItems: "center" },
  dangerButtonText: { color: colors.negative, fontWeight: "600" },
});
