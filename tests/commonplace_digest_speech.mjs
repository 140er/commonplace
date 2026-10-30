import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const source = await readFile(new URL("../commonplace/web/static/digest.js", import.meta.url), "utf8");
const { digestSpeech } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
let spoken, cancelled = 0, state, error, resolve;
const local = { localService: true, lang: "en-US" };
const browser = {
  SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
  speechSynthesis: {
    getVoices: () => [{ localService: false, lang: "en-GB" }, local],
    speak: (utterance) => { spoken = utterance; utterance.onstart(); },
    cancel: () => { cancelled++; },
  },
};
const speech = digestSpeech(() => Promise.resolve({ text: 'Café "yes" <script>' }), (s) => { state = s; }, (e) => { error = e; }, browser);
assert.equal(speech.supported, true);
await speech.toggle();
assert.equal(spoken.voice, local);
assert.equal(state.text, 'Café "yes" <script>');
const previous = spoken;
await speech.toggle();
assert.equal(cancelled, 1);
assert.deepEqual(state, { active: false, text: "" });
await speech.toggle();
previous.onend(); // An old callback cannot clear the new captions.
assert.equal(state.active, true);
spoken.onend();
assert.deepEqual(state, { active: false, text: "" });
await speech.toggle();
spoken.onerror();
assert.equal(error, "Could not speak the digest.");
assert.equal(state.active, false);
const pending = digestSpeech(() => new Promise((r) => { resolve = r; }), (s) => { state = s; }, assert.fail, browser);
const fetch = pending.toggle();
await pending.toggle();
spoken = null;
resolve({ text: "Cancelled" });
await fetch;
assert.equal(spoken, null);
assert.equal(state.active, false);
const broken = digestSpeech(() => Promise.reject(new Error("Offline")), () => {}, (e) => { error = e; }, browser);
await broken.toggle();
assert.equal(error, "Offline");
browser.speechSynthesis.getVoices = () => [{ localService: false, lang: "en-US" }];
await speech.toggle();
assert.equal(error, "No local speech voice is installed.");
assert.equal(spoken, null);
// Some browsers load voices only after the first request.
let voicesChanged;
browser.speechSynthesis.getVoices = () => [];
browser.speechSynthesis.addEventListener = (_, callback) => { voicesChanged = callback; };
browser.speechSynthesis.removeEventListener = () => {};
const loading = speech.toggle();
await Promise.resolve();
browser.speechSynthesis.getVoices = () => [local];
voicesChanged();
await loading;
assert.equal(spoken.voice, local);
speech.stop();
const missing = digestSpeech(assert.fail, () => {}, assert.fail, {});
assert.equal(missing.supported, false);
await missing.toggle();
missing.stop();
