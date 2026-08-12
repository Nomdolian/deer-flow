/**
 * Number formatting for the screens.
 *
 * The API returns full float precision, and pasting that straight into the UI
 * gives you "exit 114816.33996419217" on a phone screen — technically accurate
 * and useless to read. Instrument prices need different precision depending on
 * magnitude: 5 decimals for a currency pair, 2 for an index or a Bitcoin CFD.
 */

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const magnitude = Math.abs(value);
  // FX majors quote to 5 (or 3 for JPY pairs); anything priced above ~100 is an
  // index, metal or crypto CFD, where 2 is what the broker itself shows.
  const decimals = magnitude >= 100 ? 2 : 5;
  return value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function formatMoney(value: number | null | undefined, { signed = false } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const body = Math.abs(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (!signed) return value < 0 ? `-${body}` : body;
  return `${value < 0 ? "-" : "+"}${body}`;
}

export function formatR(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}R`;
}

/** Lot/unit sizes span a huge range — 0.01 lots of BTC up to thousands of
 *  currency units — so a fixed 2 decimals prints "0.00" for a real position. */
export function formatSize(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return value.toPrecision(3).replace(/0+$/, "").replace(/\.$/, "");
}
