import React from "react";
import { FlatList, RefreshControl, SafeAreaView, StyleSheet, Text, View } from "react-native";

import { api } from "../api/client";
import { Badge, Card, EmptyState, ErrorBanner, LoadingView } from "../components/Common";
import { useApiData } from "../hooks/useApiData";
import type { SignalDTO } from "../api/types";
import { formatPrice } from "../format";
import { colors, spacing } from "../theme";

const POLL_MS = 15_000;

export default function SignalsScreen() {
  const signals = useApiData((creds) => api.signals(creds), { pollIntervalMs: POLL_MS });

  if (signals.loading && !signals.data) {
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
        data={signals.data ?? []}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={signals.refreshing} onRefresh={signals.refresh} tintColor={colors.accent} />}
        ListHeaderComponent={
          <>
            <Text style={styles.title}>Signals</Text>
            {signals.error && <ErrorBanner message={signals.error} />}
          </>
        }
        renderItem={({ item }) => <SignalRow signal={item} />}
        ListEmptyComponent={!signals.error ? <EmptyState message="No signals logged yet." /> : null}
      />
    </SafeAreaView>
  );
}

function SignalRow({ signal }: { signal: SignalDTO }) {
  const decision = signal.risk_decision;
  return (
    <Card>
      <View style={styles.rowBetween}>
        <Text style={styles.instrument}>{signal.instrument}</Text>
        <Badge label={signal.direction.toUpperCase()} tone={signal.direction === "long" ? "positive" : "negative"} />
      </View>
      <Text style={styles.meta}>
        {signal.strategy_id} v{signal.strategy_version} · confidence {(signal.confidence_score * 100).toFixed(0)}%
      </Text>
      <Text style={styles.meta}>
        entry {formatPrice(signal.entry)} · SL {formatPrice(signal.stop_loss)} · TP {formatPrice(signal.take_profit)}
      </Text>
      <View style={styles.confluenceWrap}>
        {signal.confluences.map((c, idx) => (
          <View key={idx} style={styles.confluenceChip}>
            <Text style={styles.confluenceText}>{c}</Text>
          </View>
        ))}
      </View>
      {decision && (
        <Badge label={decision.accepted ? "ACCEPTED" : `REJECTED: ${decision.reason}`} tone={decision.accepted ? "positive" : "warning"} />
      )}
      <Text style={styles.timestamp}>{new Date(signal.created_at).toLocaleString()}</Text>
    </Card>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, paddingBottom: spacing.xl * 2 },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700", marginBottom: spacing.md },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  instrument: { color: colors.textPrimary, fontSize: 16, fontWeight: "700" },
  meta: { color: colors.textSecondary, fontSize: 13, marginTop: spacing.xs },
  confluenceWrap: { flexDirection: "row", flexWrap: "wrap", marginTop: spacing.sm, gap: 6 },
  confluenceChip: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 3,
    marginRight: 6,
    marginBottom: 6,
  },
  confluenceText: { color: colors.textSecondary, fontSize: 11 },
  timestamp: { color: colors.textSecondary, fontSize: 11, marginTop: spacing.sm },
});
