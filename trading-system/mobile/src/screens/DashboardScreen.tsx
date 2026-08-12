import React from "react";
import { FlatList, RefreshControl, SafeAreaView, StyleSheet, Text, View } from "react-native";

import { api } from "../api/client";
import { Badge, Card, EmptyState, ErrorBanner, LoadingView, SectionTitle } from "../components/Common";
import { useApiData } from "../hooks/useApiData";
import type { OpenPositionDTO } from "../api/types";
import { formatPrice, formatSize } from "../format";
import { colors, spacing } from "../theme";

const POLL_MS = 15_000;

export default function DashboardScreen() {
  const positions = useApiData((creds) => api.positions(creds), { pollIntervalMs: POLL_MS });
  const killSwitch = useApiData((creds) => api.killSwitchState(creds), { pollIntervalMs: POLL_MS });

  if (positions.loading && !positions.data) {
    return (
      <SafeAreaView style={styles.container}>
        <LoadingView />
      </SafeAreaView>
    );
  }

  // No client-side filtering: /positions returns exactly the open set. The
  // previous `status === "filled"` filter counted every trade that ever
  // filled, so long-closed trades showed up here as live exposure.
  const openPositions = positions.data ?? [];

  return (
    <SafeAreaView style={styles.container}>
      <FlatList
        contentContainerStyle={styles.content}
        data={openPositions}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={positions.refreshing} onRefresh={positions.refresh} tintColor={colors.accent} />}
        ListHeaderComponent={
          <>
            <Text style={styles.title}>Dashboard</Text>
            {killSwitch.data?.engaged && (
              <View style={styles.killSwitchBanner}>
                <Text style={styles.killSwitchBannerText}>⛔ KILL SWITCH ENGAGED — new trading halted</Text>
                <Text style={styles.killSwitchBannerReason}>{killSwitch.data.reason}</Text>
              </View>
            )}
            {positions.error && <ErrorBanner message={positions.error} />}
            <SectionTitle>
              Open positions{openPositions.length > 0 ? ` (${openPositions.length})` : ""}
            </SectionTitle>
          </>
        }
        renderItem={({ item }) => <PositionRow position={item} />}
        ListEmptyComponent={
          !positions.error ? <EmptyState message="Flat — nothing open right now. Closed trades are in the Journal tab." /> : null
        }
      />
    </SafeAreaView>
  );
}

function PositionRow({ position }: { position: OpenPositionDTO }) {
  const tone = position.direction === "long" ? "positive" : "negative";
  return (
    <Card>
      <View style={styles.rowBetween}>
        <Text style={styles.instrument}>{position.instrument}</Text>
        <Badge label={position.direction.toUpperCase()} tone={tone} />
      </View>
      <Text style={styles.meta}>
        size {formatSize(position.size)} · entry {formatPrice(position.filled_price)} · SL{" "}
        {formatPrice(position.stop_loss)} · TP {formatPrice(position.take_profit)}
      </Text>
      <Text style={styles.meta}>
        {position.strategy_id} · {position.asset_class}
      </Text>
      <Text style={styles.timestamp}>opened {new Date(position.opened_at).toLocaleString()}</Text>
    </Card>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, paddingBottom: spacing.xl * 2 },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700", marginBottom: spacing.md },
  killSwitchBanner: {
    backgroundColor: "#2A1416",
    borderColor: colors.negative,
    borderWidth: 1,
    borderRadius: 12,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  killSwitchBannerText: { color: colors.negative, fontWeight: "700", fontSize: 14 },
  killSwitchBannerReason: { color: colors.textSecondary, fontSize: 12, marginTop: spacing.xs },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  instrument: { color: colors.textPrimary, fontSize: 16, fontWeight: "700" },
  meta: { color: colors.textSecondary, fontSize: 13, marginTop: spacing.xs },
  timestamp: { color: colors.textSecondary, fontSize: 11, marginTop: spacing.xs },
});
