// Speech stays on this device. Generation guards ignore a cancelled fetch or utterance.
export function digestSpeech(fetchDigest, changed, failed, browser = window) {
  const synth = browser.speechSynthesis;
  const supported = !!synth && !!browser.SpeechSynthesisUtterance;
  if (supported) synth.getVoices(); // Start loading installed voices before the first press.
  let generation = 0, active = false, text = "", utterance = null;
  const notify = () => changed({ active, text });
  function stop() {
    generation++; active = false; text = "";
    if (utterance) synth.cancel();
    utterance = null; notify();
  }
  async function toggle() {
    if (!supported) return;
    if (active) { stop(); return; }
    const current = ++generation;
    active = true; notify();
    try {
      const digest = await fetchDigest();
      if (current !== generation) return;
      if (!synth.getVoices().length) await new Promise((resolve) => {
        const ready = () => { clearTimeout(timer); synth.removeEventListener("voiceschanged", ready); resolve(); };
        const timer = setTimeout(ready, 1500);
        synth.addEventListener("voiceschanged", ready, { once: true });
      });
      if (current !== generation) return;
      const voices = synth.getVoices().filter((voice) => voice.localService);
      const voice = voices.find((voice) => /^en(?:-|$)/i.test(voice.lang)) || voices[0];
      if (!voice) throw new Error("No local speech voice is installed.");
      utterance = new browser.SpeechSynthesisUtterance(digest.text);
      utterance.voice = voice; utterance.lang = voice.lang;
      utterance.onstart = () => { if (current === generation) { text = digest.text; notify(); } };
      utterance.onend = () => { if (current === generation) stop(); };
      utterance.onerror = () => {
        if (current === generation) { stop(); failed("Could not speak the digest."); }
      };
      synth.speak(utterance);
    } catch (error) {
      if (current === generation) { stop(); failed(error.message); }
    }
  }
  return { supported, toggle, stop };
}
