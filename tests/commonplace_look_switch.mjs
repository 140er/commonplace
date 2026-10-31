import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const source = await readFile(new URL("../commonplace/web/static/look.js", import.meta.url), "utf8");
const { resolveLook } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
assert.equal(resolveLook(""), null);
for (const look of ["a", "b", "c"]) {
  assert.equal(resolveLook(`?look=${look}`, "b"), look);
  assert.equal(resolveLook("?next=/app/", look), look);
}
for (const invalid of ["default", "", "A", "unknown", "toString", "__proto__"]) {
  assert.equal(resolveLook(`?look=${invalid}`, "a"), null);
  assert.equal(resolveLook("", invalid), null);
}

function browser(search, remembered, blocked = false) {
  const saved = new Map(remembered ? [["commonplace-look", remembered]] : []);
  const context = { URLSearchParams, location: { search }, document: { documentElement: { dataset: {} } } };
  Object.defineProperty(context, "localStorage", { get() {
    if (blocked === "access") throw Error("Storage blocked");
    return {
      getItem(key) { if (blocked) throw Error(); return saved.get(key); },
      setItem(key, value) { if (blocked) throw Error(); saved.set(key, value); },
      removeItem(key) { if (blocked) throw Error(); saved.delete(key); },
    };
  } });
  runInNewContext(source.replace("export function", "function"), context);
  return { look: context.document.documentElement.dataset.look, saved };
}
assert.equal(browser("", null).look, undefined);
assert.equal(browser("", "c").look, "c");
for (const look of ["a", "b", "c"]) {
  const result = browser(`?look=${look}`, "a");
  assert.equal(result.look, look);
  assert.equal(result.saved.get("commonplace-look"), look);
}
for (const value of ["default", "", "unknown"]) {
  const result = browser(`?look=${value}`, "b");
  assert.equal(result.look, undefined);
  assert.equal(result.saved.has("commonplace-look"), false);
}
for (const blocked of [true, "access"]) {
  assert.equal(browser("?look=a", "c", blocked).look, "a");
  assert.equal(browser("?look=default", "c", blocked).look, undefined);
  assert.equal(browser("", "c", blocked).look, undefined);
}
console.log("look resolution, persistence, clearing and storage failures passed");
