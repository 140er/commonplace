export function waitingTotal(bots) {
  const n = (bot) => Number.isSafeInteger(bot?.waiting) && bot.waiting > 0 ? bot.waiting : 0;
  const list = Array.isArray(bots) ? bots : [];
  const general = list.find((bot) => bot?.kind === "general");
  return general ? n(general) : list.reduce((sum, bot) => sum + n(bot), 0);
}
