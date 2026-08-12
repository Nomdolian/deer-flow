import React from "react";
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { colors, radii, spacing } from "../theme";

interface Props {
  children: React.ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches any render error in the app.
 *
 * Without this, one bad component gives you a blank white screen — which on a
 * trading app is worse than a stack trace, because it looks identical to the
 * server being down and gives you nothing to act on or report. This shows what
 * broke and lets you retry, and it says explicitly that the server keeps
 * trading regardless, since that's the first thing you'd want to know.
 */
export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Goes to the Metro/device console, and to any crash reporting added later.
    console.error("Unhandled UI error:", error, info.componentStack);
  }

  private retry = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <View style={styles.container}>
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={styles.title}>The app hit an error</Text>
          <Text style={styles.body}>
            This is a problem in the app on your phone, not in the trading system. The server keeps running and
            managing open positions whether or not this app works.
          </Text>
          <Text style={styles.body}>
            To stop trading without the app: engage the kill switch from the setup page on your computer, or
            close positions in MetaTrader 5 directly.
          </Text>
          <View style={styles.errorBox}>
            <Text style={styles.errorName}>{error.name}</Text>
            <Text style={styles.errorMessage}>{error.message}</Text>
          </View>
          <TouchableOpacity style={styles.button} onPress={this.retry}>
            <Text style={styles.buttonText}>Try again</Text>
          </TouchableOpacity>
        </ScrollView>
      </View>
    );
  }
}
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, paddingTop: spacing.xl * 2 },
  title: { color: colors.textPrimary, fontSize: 22, fontWeight: "700", marginBottom: spacing.md },
  body: { color: colors.textSecondary, fontSize: 14, lineHeight: 21, marginBottom: spacing.md },
  errorBox: {
    backgroundColor: colors.surface,
    borderColor: colors.negative,
    borderWidth: 1,
    borderRadius: radii.md,
    padding: spacing.md,
    marginBottom: spacing.lg,
  },
  errorName: { color: colors.negative, fontSize: 13, fontWeight: "700", marginBottom: spacing.xs },
  errorMessage: { color: colors.textSecondary, fontSize: 12.5, lineHeight: 18 },
  button: {
    backgroundColor: colors.accent,
    borderRadius: radii.md,
    padding: spacing.md,
    alignItems: "center",
  },
  buttonText: { color: "#04121F", fontWeight: "800", fontSize: 15 },
});
