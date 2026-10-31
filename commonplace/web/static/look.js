const looks = ["a", "b", "c"];

export function resolveLook(search, remembered = null) {
  const params = new URLSearchParams(search);
  const value = params.has("look") ? params.get("look") : remembered;
  return looks.includes(value) ? value : null;
}

if (typeof document !== "undefined") {
  let remembered = null;
  try { remembered = localStorage.getItem("commonplace-look"); } catch {}
  const look = resolveLook(location.search, remembered);
  if (new URLSearchParams(location.search).has("look")) {
    try {
      if (look) localStorage.setItem("commonplace-look", look);
      else localStorage.removeItem("commonplace-look");
    } catch {}
  }
  if (look) document.documentElement.dataset.look = look;
  else delete document.documentElement.dataset.look;
}
