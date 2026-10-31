// A same-origin module: CSP needs neither blob: nor eval. Output stays silent.
class VoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super(); this.buffer = new Float32Array(2048); this.used = 0; this.recording = true;
    this.port.onmessage = ({ data }) => {
      if (data !== "stop") return;
      this.recording = false;
      if (this.used) this.port.postMessage(this.buffer.slice(0, this.used));
      this.used = 0; this.port.postMessage("stopped");
    };
  }
  process(inputs) {
    const channels = inputs[0];
    if (!this.recording || !channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let sample = 0; for (const channel of channels) sample += channel[i];
      this.buffer[this.used++] = sample / channels.length;
      if (this.used === this.buffer.length) { this.port.postMessage(this.buffer, [this.buffer.buffer]); this.buffer = new Float32Array(2048); this.used = 0; }
    }
    return true;
  }
}
registerProcessor("commonplace-voice", VoiceCapture);
