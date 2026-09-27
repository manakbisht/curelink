// Browser side of the voice game: streams the microphone to the Pipecat
// websocket and plays the bot's audio. Wire format (see app/voice/serializer.py):
//   binary  -> 16-bit mono PCM (16 kHz up, 24 kHz down)
//   text    -> JSON control messages
(() => {
  "use strict";

  const INPUT_RATE = 16000;
  const OUTPUT_RATE = 24000;
  const CHUNK_SAMPLES = 320; // 20 ms at 16 kHz
  // Report playback_done only after the queue has stayed empty this long, so a
  // tiny gap between audio chunks is not mistaken for the end of an utterance.
  const DRAIN_GRACE_MS = 300;

  // Resamples mic audio to 16 kHz (linear interpolation) and emits Int16 chunks.
  const CAPTURE_WORKLET = `
    class Capture extends AudioWorkletProcessor {
      constructor() {
        super();
        this.step = sampleRate / ${INPUT_RATE};
        this.t = 0;
        this.last = 0;
        this.out = new Int16Array(${CHUNK_SAMPLES});
        this.n = 0;
      }
      process(inputs) {
        const input = inputs[0][0];
        if (!input || input.length === 0) return true;
        const len = input.length;
        let t = this.t;
        while (t <= len - 1) {
          const i = Math.floor(t);
          const f = t - i;
          const a = i < 0 ? this.last : input[i];
          const b = i + 1 < len ? input[i + 1] : a;
          const v = Math.max(-1, Math.min(1, a + (b - a) * f));
          this.out[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
          if (this.n === this.out.length) {
            this.port.postMessage(this.out.buffer, [this.out.buffer]);
            this.out = new Int16Array(${CHUNK_SAMPLES});
            this.n = 0;
          }
          t += this.step;
        }
        this.t = t - len;
        this.last = input[len - 1];
        return true;
      }
    }
    registerProcessor("capture", Capture);
  `;

  const PHASE_LABELS = {
    connecting: "Connecting…",
    idle: "Getting ready…",
    presenting: "Listen carefully…",
    interrupted: "You interrupted. Finish speaking and I'll start the round again.",
    listening: "Your turn. Say the words in order.",
    evaluating: "Checking your answer…",
    finished: "Game over",
    off: "Microphone off",
  };

  let call = null;

  class VoiceCall {
    constructor(sessionId, ui) {
      this.sessionId = sessionId;
      this.ui = ui;
      this.sources = new Set();
      this.nextStart = 0;
      this.drainTimer = null;
      this.finished = false;
    }

    async start() {
      this.ui.setPhase("connecting");
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.ctx = new AudioContext();
      const workletUrl = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: "application/javascript" }));
      await this.ctx.audioWorklet.addModule(workletUrl);
      URL.revokeObjectURL(workletUrl);

      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      this.ws = new WebSocket(`${scheme}//${location.host}/ws/sessions/${this.sessionId}`);
      this.ws.binaryType = "arraybuffer";
      this.ws.onmessage = (event) => this.onMessage(event);
      this.ws.onclose = () => this.stop();
      await new Promise((resolve, reject) => {
        this.ws.onopen = resolve;
        this.ws.onerror = () => reject(new Error("Could not connect to the game server."));
      });

      this.mic = this.ctx.createMediaStreamSource(this.stream);
      this.capture = new AudioWorkletNode(this.ctx, "capture");
      this.capture.port.onmessage = (event) => {
        if (this.ws.readyState === WebSocket.OPEN) this.ws.send(event.data);
      };
      this.mic.connect(this.capture);
      // Some browsers only pull audio through nodes that reach the destination.
      const mute = this.ctx.createGain();
      mute.gain.value = 0;
      this.capture.connect(mute).connect(this.ctx.destination);
    }

    onMessage(event) {
      if (event.data instanceof ArrayBuffer) {
        this.play(event.data);
        return;
      }
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case "interrupt":
          this.clearPlayback();
          break;
        case "phase":
          this.finished = msg.phase === "finished";
          this.ui.setPhase(msg.phase);
          this.ui.refresh();
          break;
        case "transcript":
          this.ui.setHeard(msg.text, msg.final);
          break;
        case "result":
          this.ui.refresh();
          break;
        case "error":
          this.ui.setStatus(msg.message);
          break;
      }
    }

    play(buffer) {
      const pcm = new Int16Array(buffer);
      if (pcm.length === 0) return;
      const samples = new Float32Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 0x8000;
      const audio = this.ctx.createBuffer(1, samples.length, OUTPUT_RATE);
      audio.copyToChannel(samples, 0);

      const source = this.ctx.createBufferSource();
      source.buffer = audio;
      source.connect(this.ctx.destination);
      const startAt = Math.max(this.ctx.currentTime + 0.05, this.nextStart);
      source.start(startAt);
      this.nextStart = startAt + audio.duration;

      clearTimeout(this.drainTimer);
      this.sources.add(source);
      source.onended = () => {
        this.sources.delete(source);
        if (this.sources.size === 0) this.drainTimer = setTimeout(() => this.onDrained(), DRAIN_GRACE_MS);
      };
    }

    onDrained() {
      if (this.sources.size > 0) return;
      this.send({ type: "playback_done" });
      if (this.finished) setTimeout(() => this.stop(), 500);
    }

    clearPlayback() {
      clearTimeout(this.drainTimer);
      for (const source of this.sources) {
        source.onended = null;
        try { source.stop(); } catch { /* already stopped */ }
      }
      this.sources.clear();
      this.nextStart = 0;
    }

    send(message) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(message));
    }

    stop() {
      if (this.stopped) return;
      this.stopped = true;
      this.clearPlayback();
      if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close();
      this.stream?.getTracks().forEach((track) => track.stop());
      this.ctx?.close();
      if (call === this) call = null;
      this.ui.onStopped();
    }
  }

  const ui = {
    el: (id) => document.getElementById(id),
    setPhase(phase) {
      const status = this.el("voice-status");
      if (!status) return;
      status.dataset.phase = phase;
      status.textContent = PHASE_LABELS[phase] || phase;
      if (phase === "presenting" || phase === "interrupted") this.setHeard("", true);
    },
    setStatus(text) {
      const status = this.el("voice-status");
      if (status) status.textContent = text;
    },
    setHeard(text, final) {
      const heard = this.el("voice-heard");
      if (heard) heard.textContent = text ? `Heard: “${text}”${final ? "" : "…"}` : "";
    },
    refresh() {
      const state = this.el("game-state");
      if (state && window.htmx) htmx.trigger(state, "refresh");
    },
    onStopped() {
      const connect = this.el("voice-connect");
      const hangup = this.el("voice-hangup");
      if (connect) connect.hidden = false;
      if (hangup) hangup.hidden = true;
      if (this.el("voice-status")?.dataset.phase !== "finished") this.setPhase("off");
      if (this.el("game-state")?.dataset.status !== "ACTIVE") this.el("voice").hidden = true;
    },
  };

  async function connect(button) {
    const game = button.closest("[data-session-id]");
    if (!game || call) return;
    button.disabled = true;
    const current = new VoiceCall(game.dataset.sessionId, ui);
    call = current;
    try {
      await current.start();
      button.hidden = true;
      ui.el("voice-hangup").hidden = false;
    } catch (err) {
      console.error(err);
      current.stop();
      ui.setStatus(err.name === "NotAllowedError" ? "Microphone access was blocked." : err.message);
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener("click", (event) => {
    if (event.target.id === "voice-connect") connect(event.target);
    if (event.target.id === "voice-hangup") call?.stop();
  });

  // When the game ends outside the call ("End game" button, another tab), hang up.
  // If the bot ended it, let it finish speaking; the call stops itself after that.
  document.addEventListener("htmx:afterSwap", () => {
    const state = ui.el("game-state");
    const voice = ui.el("voice");
    if (!state || !voice || state.dataset.status === "ACTIVE") return;
    if (!call) voice.hidden = true;
    else if (!call.finished) call.stop();
  });
})();
