/* Text stays in server-escaped <pre> elements; never render imported Markdown as HTML. */
const deck = document.body.dataset.deck;
const base = `/api/skill-decks/${encodeURIComponent(deck)}`;
let receipt = null;

async function post(action, value) {
  const response = await fetch(`${base}/${action}`, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}

post("preview", {}).then(result => {
  receipt = result.receipt;
  document.querySelector("#target").textContent = `Install into ${result.workspace}.`;
  document.querySelector("#confirm").disabled = false;
  document.querySelector("#add").disabled = false;
}).catch(error => {
  document.querySelector("#target").textContent = error.message === "unauthorized"
    ? "Sign in as a workspace owner to install." : error.message;
});

document.querySelector("#install").addEventListener("submit", async event => {
  event.preventDefault();
  const result = document.querySelector("#install-result");
  const button = document.querySelector("#add");
  if (!receipt || !document.querySelector("#confirm").checked) return;
  button.disabled = true;
  try {
    const installed = await post("install", { receipt, confirm: true });
    result.textContent = `Installed ${installed.skills.length} skills as quoted data.`;
  } catch (error) {
    result.textContent = error.message;
    button.disabled = false;
  }
});

document.querySelector("#report").addEventListener("submit", async event => {
  event.preventDefault();
  const result = document.querySelector("#report-result");
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    const report = await post("report", { reason: document.querySelector("#reason").value });
    result.textContent = `${report.notice} Reference: ${report.report_id}`;
  } catch (error) {
    result.textContent = error.message === "unauthorized" ? "Sign in to send a report." : error.message;
  } finally {
    button.disabled = false;
  }
});
