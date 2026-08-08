import React, { useMemo, useState } from "react";
import { Alert, RefreshControl, SafeAreaView, ScrollView, StyleSheet, Switch, Text, TouchableOpacity, View } from "react-native";

import { ApiError, api } from "../api/client";
import { Card, ErrorBanner, LoadingView, SectionTitle } from "../components/Common";
import { useApiData } from "../hooks/useApiData";
import { useSettings } from "../context/SettingsContext";
import type { WatchlistInstrumentDTO } from "../api/types";
import { colors, spacing } from "../theme";

const POLL_MS = 20_000;

// A curated catalog matching what AlphaVantageProvider actually serves live —
// see app/data/providers/alpha_vantage_provider.py. Indices/commodities route
// through an ETF proxy there (e.g. SPX500 -> SPY), noted inline.
const CATALOG: { label: string; note?: string; instruments: { instrument: string; assetClass: string }[] }[] = [
  {
    label: "Forex",
    instruments: [
      { instrument: "EURUSD", assetClass: "forex" },
      { instrument: "GBPUSD", assetClass: "forex" },
      { instrument: "USDJPY", assetClass: "forex" },
      { instrument: "AUDUSD", assetClass: "forex" },
    ],
  },
  {
    label: "Metals",
    instruments: [
      { instrument: "XAUUSD", assetClass: "metals" },
      { instrument: "XAGUSD", assetClass: "metals" },
    ],
  },
  {
    label: "Indices",
    note: "live via ETF proxy (SPX500→SPY, NAS100→QQQ, US30→DIA, US2000→IWM) — not the literal index print",
    instruments: [
      { instrument: "SPX500", assetClass: "indices" },
      { instrument: "NAS100", assetClass: "indices" },
      { instrument: "US30", assetClass: "indices" },
      { instrument: "US2000", assetClass: "indices" },
    ],
  },
  {
    label: "Commodities",
    note: "live via ETF proxy (WTI→USO, NATURAL_GAS→UNG)",
    instruments: [
      { instrument: "WTI", assetClass: "commodities" },
      { instrument: "NATURAL_GAS", assetClass: "commodities" },
    ],
  },
  {
    label: "Crypto",
    note: "the only genuinely 24/7 asset class here",
    instruments: [
      { instrument: "BTCUSD", assetClass: "crypto_major" },
      { instrument: "ETHUSD", assetClass: "crypto_major" },
      { instrument: "DOGEUSD", assetClass: "crypto_meme" },
    ],
  },
  {
    label: "Stocks",
    note: "trades only NYSE/Nasdaq regular hours",
    instruments: [
      { instrument: "AAPL", assetClass: "stocks" },
      { instrument: "MSFT", assetClass: "stocks" },
      { instrument: "TSLA", assetClass: "stocks" },
    ],
  },
];

export default function AssetsScreen() {
  const settings = useSettings();
  const creds = { serverUrl: settings.serverUrl, apiKey: settings.apiKey };
  const watchlist = useApiData((c) => api.watchlist(c), { pollIntervalMs: POLL_MS });
  const [busy, setBusy] = useState<string | null>(null);

  const byInstrument = useMemo(() => {
    const map = new Map<string, WatchlistInstrumentDTO>();
    for (const row of watchlist.data ?? []) map.set(row.instrument, row);
    return map;
  }, [watchlist.data]);

  const toggle = async (instrument: string, assetClass: string) => {
    setBusy(instrument);
    try {
      const existing = byInstrument.get(instrument);
      if (existing) {
        await api.setWatchlistEnabled(creds, instrument, !existing.enabled);
      } else {
        await api.addToWatchlist(creds, {
          instrument,
          asset_class: assetClass,
          timeframe: "D1",
          data_source: "alphavantage",
          enabled: true,
        });
      }
      await watchlist.refresh();
    } catch (err) {
      Alert.alert("Couldn't update watchlist", err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (instrument: string) => {
    setBusy(instrument);
    try {
      await api.removeFromWatchlist(creds, instrument);
      await watchlist.refresh();
    } catch (err) {
      Alert.alert("Couldn't remove", err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  if (watchlist.loading && !watchlist.data) {
    return (
      <SafeAreaView style={styles.container}>
        <LoadingView />
      </SafeAreaView>
    );
  }

  const activeCount = (watchlist.data ?? []).filter((w) => w.enabled).length;

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={watchlist.refreshing} onRefresh={watchlist.refresh} tintColor={colors.accent} />}
      >
        <Text style={styles.title}>Assets</Text>
        <Text style={styles.subtitle}>
          Pick what the system trades autonomously. Enabling one doesn't place a trade by itself — it just lets the
          risk-managed pipeline evaluate it every cycle. Position sizing and stop-losses manage risk per trade; no
          selection here guarantees a winning trade.
        </Text>
        {watchlist.error && <ErrorBanner message={watchlist.error} />}

        <View style={styles.summaryCard}>
          <Text style={styles.summaryText}>
            {activeCount} asset{activeCount === 1 ? "" : "s"} active
          </Text>
        </View>

        {CATALOG.map((group) => (
          <View key={group.label}>
            <SectionTitle>{group.label}</SectionTitle>
            {group.note && <Text style={styles.groupNote}>{group.note}</Text>}
            {group.instruments.map(({ instrument, assetClass }) => {
              const row = byInstrument.get(instrument);
              const enabled = row?.enabled ?? false;
              return (
                <Card key={instrument} style={styles.assetCard}>
                  <View style={styles.assetRow}>
                    <View style={styles.assetInfo}>
                      <Text style={styles.instrument}>{instrument}</Text>
                      {row && <Text style={styles.meta}>{row.timeframe} · {row.data_source}</Text>}
                    </View>
                    <View style={styles.assetActions}>
                      {row && (
                        <TouchableOpacity onPress={() => remove(instrument)} disabled={busy === instrument} hitSlop={8}>
                          <Text style={styles.removeText}>Remove</Text>
                        </TouchableOpacity>
                      )}
                      <Switch
                        value={enabled}
                        onValueChange={() => toggle(instrument, assetClass)}
                        disabled={busy === instrument}
                      />
                    </View>
                  </View>
                </Card>
              );
            })}
          </View>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, paddingBottom: spacing.xl * 2 },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700", marginBottom: spacing.xs },
  subtitle: { color: colors.textSecondary, fontSize: 13, lineHeight: 18, marginBottom: spacing.md },
  summaryCard: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  summaryText: { color: colors.accent, fontWeight: "700", fontSize: 13 },
  groupNote: { color: colors.textSecondary, fontSize: 11.5, fontStyle: "italic", marginBottom: spacing.xs, marginTop: -4 },
  assetCard: { paddingVertical: spacing.sm },
  assetRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  assetInfo: { flexShrink: 1 },
  instrument: { color: colors.textPrimary, fontSize: 15, fontWeight: "700" },
  meta: { color: colors.textSecondary, fontSize: 12, marginTop: 2 },
  assetActions: { flexDirection: "row", alignItems: "center", gap: 14 },
  removeText: { color: colors.negative, fontSize: 12, fontWeight: "600" },
});
