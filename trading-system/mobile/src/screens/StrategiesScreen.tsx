import React, { useState } from "react";
import {
  Alert,
  FlatList,
  RefreshControl,
  SafeAreaView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { ApiError, api } from "../api/client";
import { Badge, Card, EmptyState, ErrorBanner, LoadingView } from "../components/Common";
import { useApiData } from "../hooks/useApiData";
import { useSettings } from "../context/SettingsContext";
import type { StrategyDTO } from "../api/types";
import { colors, radii, spacing } from "../theme";

const POLL_MS = 30_000;

export default function StrategiesScreen() {
  const strategies = useApiData((creds) => api.strategies(creds), { pollIntervalMs: POLL_MS });
  const pausedCount = (strategies.data ?? []).filter((s) => s.is_paused).length;

  if (strategies.loading && !strategies.data) {
    return (
      <SafeAreaView style={styles.container}>
        <LoadingView />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <FlatList
        contentContainerStyle={styles.content}
        data={strategies.data ?? []}
        keyExtractor={(item) => `${item.strategy_id}-${item.version}`}
        refreshControl={<RefreshControl refreshing={strategies.refreshing} onRefresh={strategies.refresh} tintColor={colors.accent} />}
        ListHeaderComponent={
          <>
            <Text style={styles.title}>Strategies</Text>
            {pausedCount > 0 && (
              <Text style={styles.pausedNotice}>
                {pausedCount} paused — a paused strategy takes no new trades and will not un-pause itself. Review why,
                then resume it below.
              </Text>
            )}
            {strategies.error && <ErrorBanner message={strategies.error} />}
          </>
        }
        renderItem={({ item }) => <StrategyRow strategy={item} onResumed={strategies.refresh} />}
        ListEmptyComponent={
          !strategies.error ? <EmptyState message="No strategy versions registered yet — they appear once a signal fires." /> : null
        }
      />
    </SafeAreaView>
  );
}

function StrategyRow({ strategy, onResumed }: { strategy: StrategyDTO; onResumed: () => void | Promise<void> }) {
  const creds = useSettings();
  const [totpCode, setTotpCode] = useState("");
  const [busy, setBusy] = useState(false);

  const resume = async () => {
    if (!totpCode.trim()) {
      Alert.alert("TOTP code required", "Enter the current 6-digit code from your authenticator app to resume trading.");
      return;
    }
    setBusy(true);
    try {
      await api.resumeStrategy(
        { serverUrl: creds.serverUrl, apiKey: creds.apiKey },
        strategy.strategy_id,
        strategy.version,
        totpCode.trim()
      );
      setTotpCode("");
      await onResumed();
    } catch (err) {
      Alert.alert("Failed to resume", err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <View style={styles.rowBetween}>
        <Text style={styles.instrument}>
          {strategy.strategy_id} <Text style={styles.version}>v{strategy.version}</Text>
        </Text>
        {strategy.is_paused ? <Badge label="PAUSED" tone="negative" /> : <Badge label="ACTIVE" tone="positive" />}
      </View>
      <Text style={styles.meta}>{strategy.asset_class}</Text>
      <Text style={styles.meta}>
        size multiplier {strategy.size_multiplier.toFixed(2)}x · consecutive losses {strategy.consecutive_losses}
      </Text>
      {(strategy.backtest_expectancy_r !== null || strategy.backtest_win_rate !== null) && (
        <Text style={styles.meta}>
          backtest baseline: expectancy {strategy.backtest_expectancy_r?.toFixed(2) ?? "—"}R · win rate{" "}
          {strategy.backtest_win_rate !== null ? `${(strategy.backtest_win_rate * 100).toFixed(0)}%` : "—"}
        </Text>
      )}
      {strategy.is_paused && (
        <View style={styles.resumeBlock}>
          <Text style={styles.resumeLabel}>
            Resume requires the current TOTP code — it re-enables risk-taking after an automatic halt.
          </Text>
          <TextInput
            style={styles.input}
            value={totpCode}
            onChangeText={setTotpCode}
            placeholder="123456"
            placeholderTextColor={colors.textSecondary}
            keyboardType="number-pad"
            maxLength={6}
          />
          <TouchableOpacity style={styles.resumeButton} onPress={resume} disabled={busy}>
            <Text style={styles.resumeButtonText}>{busy ? "Resuming…" : "Resume trading"}</Text>
          </TouchableOpacity>
        </View>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, paddingBottom: spacing.xl * 2 },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700", marginBottom: spacing.md },
  pausedNotice: { color: colors.negative, fontSize: 13, lineHeight: 18, marginBottom: spacing.md },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  instrument: { color: colors.textPrimary, fontSize: 16, fontWeight: "700" },
  version: { color: colors.textSecondary, fontWeight: "400" },
  meta: { color: colors.textSecondary, fontSize: 13, marginTop: spacing.xs },
  resumeBlock: { marginTop: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: spacing.md },
  resumeLabel: { color: colors.textSecondary, fontSize: 12, lineHeight: 17, marginBottom: spacing.xs },
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
  resumeButton: { backgroundColor: colors.positive, borderRadius: radii.md, padding: spacing.md, alignItems: "center" },
  resumeButtonText: { color: "#04210F", fontWeight: "800", fontSize: 15 },
});
