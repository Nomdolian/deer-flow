import React from "react";
import { FlatList, RefreshControl, SafeAreaView, StyleSheet, Text, View } from "react-native";

import { api } from "../api/client";
import { Badge, Card, EmptyState, ErrorBanner, LoadingView, SectionTitle } from "../components/Common";
import { useApiData } from "../hooks/useApiData";
import type { OrderDTO } from "../api/types";
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

  const openPositions = (positions.data ?? []).filter((o) => o.status === "filled");

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
            <SectionTitle>Recent orders</SectionTitle>
          </>
        }
        renderItem={({ item }) => <OrderRow order={item} />}
        ListEmptyComponent={!positions.error ? <EmptyState message="No orders yet. The server logs one here as soon as it fills." /> : null}
      />
    </SafeAreaView>
  );
}

function OrderRow({ order }: { order: OrderDTO }) {
  const tone = order.direction === "long" ? "positive" : "negative";
  return (
    <Card>
      <View style={styles.rowBetween}>
        <Text style={styles.instrument}>{order.instrument}</Text>
        <Badge label={order.direction.toUpperCase()} tone={tone} />
      </View>
      <Text style={styles.meta}>
        size {order.size.toFixed(2)} · entry {order.filled_price ?? "—"} · SL {order.stop_loss ?? "—"} · TP {order.take_profit ?? "—"}
      </Text>
      <Text style={styles.timestamp}>{new Date(order.created_at).toLocaleString()}</Text>
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
