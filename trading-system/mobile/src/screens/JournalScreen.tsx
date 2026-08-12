import React from "react";
import { FlatList, RefreshControl, SafeAreaView, StyleSheet, Text, View } from "react-native";

import { api } from "../api/client";
import { Badge, Card, EmptyState, ErrorBanner, LoadingView } from "../components/Common";
import { useApiData } from "../hooks/useApiData";
import type { TradeJournalDTO } from "../api/types";
import { formatMoney, formatPrice, formatR, formatSize } from "../format";
import { colors, spacing } from "../theme";

const POLL_MS = 20_000;

export default function JournalScreen() {
  const journal = useApiData((creds) => api.journal(creds), { pollIntervalMs: POLL_MS });

  if (journal.loading && !journal.data) {
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
        data={journal.data ?? []}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={journal.refreshing} onRefresh={journal.refresh} tintColor={colors.accent} />}
        ListHeaderComponent={
          <>
            <Text style={styles.title}>Trade journal</Text>
            {journal.error && <ErrorBanner message={journal.error} />}
          </>
        }
        renderItem={({ item }) => <TradeRow trade={item} />}
        ListEmptyComponent={!journal.error ? <EmptyState message="No trades journaled yet." /> : null}
      />
    </SafeAreaView>
  );
}

function TradeRow({ trade }: { trade: TradeJournalDTO }) {
  const isOpen = trade.closed_at === null;
  const outcomeTone = trade.outcome === "win" ? "positive" : trade.outcome === "loss" ? "negative" : "neutral";
  return (
    <Card>
      <View style={styles.rowBetween}>
        <Text style={styles.instrument}>{trade.instrument}</Text>
        {isOpen ? <Badge label="OPEN" tone="warning" /> : <Badge label={(trade.outcome ?? "—").toUpperCase()} tone={outcomeTone} />}
      </View>
      <Text style={styles.meta}>
        {trade.strategy_id} v{trade.strategy_version} · {trade.direction.toUpperCase()} · size {formatSize(trade.size)}
      </Text>
      <Text style={styles.meta}>
        entry {formatPrice(trade.entry_price)}
        {trade.exit_price !== null ? ` · exit ${formatPrice(trade.exit_price)}` : ""}
        {trade.pnl !== null ? ` · pnl ${formatMoney(trade.pnl, { signed: true })}` : ""}
        {trade.r_multiple !== null ? ` · ${formatR(trade.r_multiple)}` : ""}
      </Text>
      {trade.classification && <Text style={styles.classification}>review: {trade.classification}</Text>}
      <Text style={styles.timestamp}>
        opened {new Date(trade.opened_at).toLocaleString()}
        {trade.closed_at ? ` · closed ${new Date(trade.closed_at).toLocaleString()}` : ""}
      </Text>
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
  classification: { color: colors.accent, fontSize: 12, marginTop: spacing.xs, fontStyle: "italic" },
  timestamp: { color: colors.textSecondary, fontSize: 11, marginTop: spacing.sm },
});
