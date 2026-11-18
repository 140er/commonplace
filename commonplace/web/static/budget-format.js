// Plain words for money, windows and budget scopes. Shared by the budgets page and Needs me.
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const day = (stamp) => String(stamp || "").slice(0, 10);

// Whole units with at least two decimals; micros are integers, so no floating-point rounding.
export function formatMicros(micros) {
  const value = micros === null || micros === undefined ? NaN : Number(micros);
  if (!Number.isSafeInteger(value) || value < 0) return "unknown";
  const whole = Math.floor(value / 1_000_000), fraction = String(value % 1_000_000).padStart(6, "0").replace(/0{1,4}$/, "");
  return `${whole}.${fraction.padEnd(2, "0")}`;
}

// What the owner typed, as micros: a positive amount with at most six decimals. Anything else is null.
export function parseAmount(text) {
  const match = /^(\d{1,9})(?:\.(\d{1,6}))?$/.exec(String(text ?? "").trim());
  if (!match) return null;
  const micros = Number(match[1]) * 1_000_000 + Number((match[2] || "").padEnd(6, "0"));
  return micros > 0 ? micros : null;
}

export function windowText(window) {
  const start = String(window?.start || ""), kind = window?.window;
  const month = MONTHS[Number(start.slice(5, 7)) - 1];
  if (window?.custom) return `${day(start)} to ${day(window?.end)} (UTC, end excluded)`;
  if (kind === "calendar_month" && month) return `${month} ${start.slice(0, 4)} (UTC)`;
  if (kind === "calendar_week" && start) return `The week from Monday ${day(start)} (UTC)`;
  if (kind === "calendar_day" && start) return `${day(start)} (UTC)`;
  if (kind === "rolling_days" && window?.days) return `The last ${window.days} days (UTC)`;
  return start ? `${day(start)} to ${day(window?.end)} (UTC)` : "the current window";
}

export function scopeLabel(scope, names = {}) {
  const key = scope?.key ?? "";
  if (scope?.kind === "workspace") return "The workspace";
  if (scope?.kind === "agent") return `Agent ${names[key] || key}`;
  if (scope?.kind === "project") return `Project ${key}`;
  if (scope?.kind === "card") return `Card #${key}`;
  return "An unknown scope";
}

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
export const unknownCostText = (count) => `${plural(count, "run", "runs")} without a cost, not counted`;
export const otherCurrencyText = (count, currency) => `${plural(count, "run", "runs")} in a currency other than ${currency}, not counted`;
export const runningText = (count) => `${plural(count, "run is", "runs are")} still running and will finish`;

export const STATES = { ok: "OK", warning: "Warning", limit: "Over budget" };
export const badgeText = (entry) => `${STATES[entry?.state] || "Unknown"} · ${Number.isFinite(entry?.percent) ? entry.percent : "?"}%`;

// The one-line meaning of a hard stop, shown before the owner confirms.
export const HARD_STOP = "New starts are refused; running work finishes.";

// The Needs me row of a budget: a title, one line of detail and where to review it.
export function budgetNeed(item, names = {}) {
  const who = scopeLabel(item?.scope, names);
  const over = (item?.reasons || []).includes("limit");
  const parts = [];
  if (Number.isFinite(item?.spent_micros) && Number.isFinite(item?.limit_micros) && item?.currency) parts.push(`${formatMicros(item.spent_micros)} of ${formatMicros(item.limit_micros)} ${item.currency} spent in ${windowText(item.window)}`);
  if (over) parts.push(HARD_STOP);
  if (over && item?.running_runs) parts.push(runningText(item.running_runs) + ".");
  if (item?.unknown_cost_runs) parts.push(unknownCostText(item.unknown_cost_runs) + ".");
  return {
    title: over ? `${who} has used its whole budget` : `${who} is at ${Number.isFinite(item?.percent) ? item.percent : "?"}% of its budget`,
    detail: parts.join(" ") || String(item?.why || "Open spend and budgets to see why."),
    href: "/app/budgets.html",
  };
}
