import { escape as e } from "./api.js";

function quantity(value, divisor) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? String(Math.round(number / divisor * 10) / 10) : "0";
}

export function planView(payload) {
  if (!payload?.enabled || !["free", "paid"].includes(payload.plan)) return "";
  const paid = payload.plan === "paid";
  const storage = paid ? "<em>unlimited</em>**" : `${e(quantity(payload.storage.used, 1024 * 1024))} of ${e(quantity(payload.storage.limit, 1024 * 1024))} MB`;
  const voice = paid ? "<em>unlimited</em>**" : `${e(quantity(payload.transcription.used_today, 60))} of ${e(quantity(payload.transcription.limit, 60))} min today`;
  const sync = paid ? "live" : `every ${e(quantity(payload.sync.interval_seconds, 60))} min`;
  return `<section class="settings-section"><h2>${paid ? "Paid" : "Free"}</h2><div class="setting-row"><span>Storage</span><span>${storage}</span></div><div class="setting-row"><span>Voice transcription</span><span>${voice}</span></div><div class="setting-row"><span>Chat sync</span><span>${sync}</span></div>${paid ? "<p>** zolang de server het aankan / as long as the server can handle it</p>" : ""}</section>`;
}
