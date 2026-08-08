import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { DarkTheme, NavigationContainer } from "@react-navigation/native";
import React from "react";
import { Text } from "react-native";

import DashboardScreen from "../screens/DashboardScreen";
import JournalScreen from "../screens/JournalScreen";
import KillSwitchScreen from "../screens/KillSwitchScreen";
import SettingsScreen from "../screens/SettingsScreen";
import SignalsScreen from "../screens/SignalsScreen";
import StrategiesScreen from "../screens/StrategiesScreen";
import { colors } from "../theme";

const Tab = createBottomTabNavigator();

const navTheme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    background: colors.background,
    card: colors.surface,
    border: colors.border,
    primary: colors.accent,
    text: colors.textPrimary,
  },
};

const TAB_ICONS: Record<string, string> = {
  Dashboard: "📊",
  Signals: "⚡",
  Journal: "📓",
  Strategies: "🧭",
  "Kill Switch": "⛔",
  Settings: "⚙️",
};

export default function RootNavigator() {
  return (
    <NavigationContainer theme={navTheme}>
      <Tab.Navigator
        screenOptions={({ route }) => ({
          headerShown: false,
          tabBarActiveTintColor: colors.accent,
          tabBarInactiveTintColor: colors.textSecondary,
          tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
          tabBarIcon: () => <Text style={{ fontSize: 18 }}>{TAB_ICONS[route.name]}</Text>,
          tabBarLabelStyle: { fontSize: 10 },
        })}
      >
        <Tab.Screen name="Dashboard" component={DashboardScreen} />
        <Tab.Screen name="Signals" component={SignalsScreen} />
        <Tab.Screen name="Journal" component={JournalScreen} />
        <Tab.Screen name="Strategies" component={StrategiesScreen} />
        <Tab.Screen name="Kill Switch" component={KillSwitchScreen} />
        <Tab.Screen name="Settings" component={SettingsScreen} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}
