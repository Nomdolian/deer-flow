import React, { useState } from "react";
import { Alert, SafeAreaView, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";

import { ApiError, api } from "../api/client";
import { Card, ErrorBanner, LoadingView } from "../components/Common";
import { useApiData } from "../hooks/useApiData";
import { useSettings } from "../context/SettingsContext";
import { colors, radii, spacing } from "../theme";

const POLL_MS = 10_000;

export default function KillSwitchScreen() {
  const creds = useSettings();
  const state = useApiData((c) => api.killSwitchState(c), { pollIntervalMs: POLL_MS });
  const [reason, setReason] = useState("manual halt from mobile app");
  const [totpCode, setTotpCode] = useState("");
  const [busy, setBusy] = useState(false);

  const engage = async () => {
    setBusy(true);
    try {
      await api.engageKillSwitch({ serverUrl: creds.serverUrl, apiKey: creds.apiKey }, reason || "manual halt from mobile app");
      await state.refresh();
    } catch (err) {
      Alert.alert("Failed to engage", err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const disengage = async () => {
    if (!totpCode.trim()) {
      Alert.alert("TOTP code required", "Enter the current 6-digit code from your authenticator app to disengage.");
      return;
    }
    setBusy(true);
    try {
      await api.disengageKillSwitch({ serverUrl: creds.serverUrl, apiKey: creds.apiKey }, totpCode.trim());
      setTotpCode("");
      await state.refresh();
    } catch (err) {
      Alert.alert("Failed to disengage", err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (state.loading && !state.data) {
    return (
      <SafeAreaView style={styles.container}>
        <LoadingView />
      </SafeAreaView>
    );
  }

  const engaged = state.data?.engaged ?? false;

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>Kill switch</Text>
        <Text style={styles.subtitle}>
          Engaging halts all NEW order placement across every strategy and asset class immediately. It does NOT close
          existing positions — do that from your broker/exchange directly if needed.
        </Text>

        {state.error && <ErrorBanner message={state.error} />}

        <View style={[styles.statusCard, { borderColor: engaged ? colors.negative : colors.positive }]}>
          <Text style={[styles.statusText, { color: engaged ? colors.negative : colors.positive }]}>
            {engaged ? "⛔ ENGAGED — trading halted" : "✅ Trading active"}
          </Text>
          {engaged && state.data?.reason && <Text style={styles.statusMeta}>Reason: {state.data.reason}</Text>}
          {engaged && state.data?.triggered_by && <Text style={styles.statusMeta}>Triggered by: {state.data.triggered_by}</Text>}
        </View>

        {!engaged ? (
          <Card>
            <Text style={styles.label}>Reason</Text>
            <TextInput style={styles.input} value={reason} onChangeText={setReason} placeholderTextColor={colors.textSecondary} />
            <TouchableOpacity style={styles.engageButton} onPress={engage} disabled={busy}>
              <Text style={styles.engageButtonText}>{busy ? "Engaging…" : "ENGAGE KILL SWITCH"}</Text>
            </TouchableOpacity>
          </Card>
        ) : (
          <Card>
            <Text style={styles.label}>Disengage requires the current TOTP code (2FA), when configured on the server</Text>
            <TextInput
              style={styles.input}
              value={totpCode}
              onChangeText={setTotpCode}
              placeholder="123456"
              placeholderTextColor={colors.textSecondary}
              keyboardType="number-pad"
              maxLength={6}
            />
            <TouchableOpacity style={styles.disengageButton} onPress={disengage} disabled={busy}>
              <Text style={styles.disengageButtonText}>{busy ? "Disengaging…" : "Disengage"}</Text>
            </TouchableOpacity>
          </Card>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700", marginBottom: spacing.xs },
  subtitle: { color: colors.textSecondary, fontSize: 13, marginBottom: spacing.lg, lineHeight: 18 },
  statusCard: {
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: spacing.lg,
    marginBottom: spacing.lg,
    backgroundColor: colors.surface,
  },
  statusText: { fontSize: 16, fontWeight: "700" },
  statusMeta: { color: colors.textSecondary, fontSize: 12, marginTop: spacing.xs },
  label: { color: colors.textSecondary, fontSize: 13, marginBottom: spacing.xs },
  input: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.textPrimary,
    padding: spacing.md,
    fontSize: 14,
    marginBottom: spacing.md,
  },
  engageButton: {
    backgroundColor: colors.negative,
    borderRadius: radii.md,
    padding: spacing.md,
    alignItems: "center",
  },
  engageButtonText: { color: "#2A0000", fontWeight: "800", fontSize: 15 },
  disengageButton: {
    backgroundColor: colors.positive,
    borderRadius: radii.md,
    padding: spacing.md,
    alignItems: "center",
  },
  disengageButtonText: { color: "#04210F", fontWeight: "800", fontSize: 15 },
});
