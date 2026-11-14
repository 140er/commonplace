const form = document.querySelector('form[action="/app/join"]');

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  const status = form.querySelector("[data-join-status]");
  if (button.disabled) return;
  button.disabled = true;
  status.textContent = "Joining…";
  try {
    // A native form POST sends Origin: null under no-referrer. A same-origin
    // fetch keeps the browser's Origin without exposing the invite or referrer.
    const response = await fetch(form.action, {
      method: "POST",
      mode: "same-origin",
      credentials: "same-origin",
      redirect: "manual",
      body: new URLSearchParams({ confirm: "1" }),
    });
    if (response.type === "opaqueredirect") {
      window.location.assign("/app/");
      return;
    }
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const main = page.querySelector("main.signin");
    if (!main) throw new Error("Unexpected response");
    document.querySelector("main.signin").replaceWith(main);
    document.title = page.title;
  } catch {
    status.textContent = "Could not finish joining. Try again, or open your invite link again.";
    button.disabled = false;
  }
});
