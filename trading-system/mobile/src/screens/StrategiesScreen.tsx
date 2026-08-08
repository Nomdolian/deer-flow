import React from "react";
import { FlatList, RefreshControl, SafeAreaView, StyleSheet, Text, View } from "react-native";

import { api } from "../api/client";
import { Badge, Card, EmptyState, ErrorBanner, LoadingView } from "../components/Common";
import { useApiData } from "../hooks/useApiData";
import type { StrategyDTO } from "../api/types";
import { colors, spacing } from "../theme";

const POLL_MS = 30_000;

export default function StrategiesScreen() {
  const strategies = useApiData((creds) => api.strategies(creds), { pollIntervalMs: POLL_MS });

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
            {strategies.error && <ErrorBanner message={strategies.error} />}
          </>
        }
        renderItem={({ item }) => <StrategyRow strategy={item} />}
        ListEmptyComponent={
          !strategies.error ? <EmptyState message="No strategy versions registered yet — they appear once a signal fires." /> : null
        }
      />
    </SafeAreaView>
  );
}

function StrategyRow({ strategy }: { strategy: StrategyDTO }) {
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
    </Card>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, paddingBottom: spacing.xl * 2 },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700", marginBottom: spacing.md },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  instrument: { color: colors.textPrimary, fontSize: 16, fontWeight: "700" },
  version: { color: colors.textSecondary, fontWeight: "400" },
  meta: { color: colors.textSecondary, fontSize: 13, marginTop: spacing.xs },
});
