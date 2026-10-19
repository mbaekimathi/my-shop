(() => {
  let ctx = null;

  const getCtx = () => {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    if (!ctx) {
      try {
        ctx = new AC();
      } catch (_err) {
        return null;
      }
    }
    if (ctx.state === "suspended") {
      ctx.resume().catch(() => {});
    }
    return ctx;
  };

  // Autoplay policies only allow audio after a user gesture — prime the
  // context on the first interaction so later notifications can play.
  ["pointerdown", "keydown", "touchstart"].forEach((evt) => {
    window.addEventListener(evt, () => getCtx(), { once: true, passive: true });
  });

  const TONES = {
    success: [
      { f: 523.25, t: 0, d: 0.12 },
      { f: 659.25, t: 0.11, d: 0.12 },
      { f: 783.99, t: 0.22, d: 0.18 },
    ],
    warning: [
      { f: 587.33, t: 0, d: 0.12 },
      { f: 880.0, t: 0.12, d: 0.18 },
    ],
    error: [
      { f: 440.0, t: 0, d: 0.14 },
      { f: 349.23, t: 0.14, d: 0.22 },
    ],
  };

  const play = (tone = "warning") => {
    const audio = getCtx();
    if (!audio) return;
    const notes = TONES[tone] || TONES.warning;
    const start = audio.currentTime + 0.02;
    notes.forEach(({ f, t, d }) => {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = "sine";
      osc.frequency.value = f;
      gain.gain.setValueAtTime(0.0001, start + t);
      gain.gain.exponentialRampToValueAtTime(0.2, start + t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + t + d);
      osc.connect(gain).connect(audio.destination);
      osc.start(start + t);
      osc.stop(start + t + d + 0.05);
    });
  };

  window.MyShopNotifySound = { play };
})();
