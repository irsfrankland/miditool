/* Drum-only preview. Guitar and bass notes are not in the embedded data. */
(function () {
  const songs = new Map();
  document.querySelectorAll("script.drum-data").forEach((node) => {
    songs.set(node.dataset.song, JSON.parse(node.textContent));
  });

  let context = null;
  let noise = null;
  let token = 0;
  let timer = 0;
  let frame = 0;
  let cursor = null;
  let active = [];
  let activeButton = null;

  function audio() {
    if (!context) context = new AudioContext();
    return context;
  }

  function noiseBuffer(ctx) {
    if (noise) return noise;
    const length = ctx.sampleRate;
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) data[i] = Math.random() * 2 - 1;
    noise = buffer;
    return buffer;
  }

  function tone(ctx, when, type, freq, attack, release, peak, freqEnd) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, when);
    if (freqEnd) {
      osc.frequency.exponentialRampToValueAtTime(Math.max(30, freqEnd), when + attack + release);
    }
    gain.gain.setValueAtTime(0.0001, when);
    gain.gain.exponentialRampToValueAtTime(Math.max(0.001, peak), when + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + attack + release);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(when);
    osc.stop(when + attack + release + 0.03);
    active.push(osc);
  }

  function burst(ctx, when, duration, peak, highpass) {
    const source = ctx.createBufferSource();
    const filter = ctx.createBiquadFilter();
    const gain = ctx.createGain();
    source.buffer = noiseBuffer(ctx);
    filter.type = "highpass";
    filter.frequency.value = highpass;
    gain.gain.setValueAtTime(Math.max(0.001, peak), when);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + duration);
    source.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);
    source.start(when);
    source.stop(when + duration);
    active.push(source);
  }

  function hit(ctx, when, note, velocity) {
    const level = Math.max(0.15, velocity / 127);
    if (note === 35 || note === 36) {
      tone(ctx, when, "sine", 170, 0.005, 0.18, 0.85 * level, 45);
      return;
    }
    if (note === 38 || note === 40) {
      tone(ctx, when, "triangle", 190, 0.004, 0.12, 0.35 * level, 120);
      burst(ctx, when, 0.16, 0.45 * level, 800);
      return;
    }
    if (note === 37 || note === 39 || note === 31) {
      burst(ctx, when, note === 39 ? 0.08 : 0.04, 0.4 * level, 1200);
      return;
    }
    if (note === 42 || note === 44) {
      burst(ctx, when, 0.045, 0.28 * level, 5000);
      return;
    }
    if (note === 46 || note === 32) {
      burst(ctx, when, 0.28, 0.24 * level, 4000);
      return;
    }
    if (note >= 41 && note <= 50 && note !== 42 && note !== 44 && note !== 46 && note !== 49) {
      const pitch = { 41: 90, 43: 110, 45: 140, 47: 170, 48: 200, 50: 240 }[note] || 150;
      tone(ctx, when, "sine", pitch, 0.004, 0.2, 0.45 * level, pitch * 0.7);
      return;
    }
    if (note === 49 || note === 51 || note === 52 || note === 53 || note === 55 || note === 57 || note === 59) {
      burst(ctx, when, note === 51 || note === 53 || note === 59 ? 0.7 : 0.45, 0.22 * level, 3500);
      return;
    }
    if (note === 56) {
      tone(ctx, when, "square", 587, 0.002, 0.14, 0.18 * level);
      tone(ctx, when, "square", 845, 0.002, 0.12, 0.12 * level);
      return;
    }
    if (note === 26 || note === 34) {
      tone(ctx, when, "sine", 1661, 0.002, 0.4, 0.32 * level);
      tone(ctx, when, "sine", 2489, 0.002, 0.25, 0.12 * level);
      return;
    }
    if (note === 25 || note === 33) {
      tone(ctx, when, "square", 1200, 0.001, 0.04, 0.2 * level);
      return;
    }
    if (note === 83 || note === 84) {
      tone(ctx, when, "sine", 2637, 0.001, 0.18, 0.16 * level);
      tone(ctx, when, "sine", 3136, 0.001, 0.14, 0.1 * level);
      tone(ctx, when, "sine", 3951, 0.001, 0.1, 0.06 * level);
      return;
    }
    if (note === 69 || note === 70 || note === 82) {
      burst(ctx, when, 0.05, 0.22 * level, 6000);
      return;
    }
    const freq = 440 * Math.pow(2, (note - 69) / 12);
    tone(ctx, when, "sine", freq, 0.004, 0.12, 0.25 * level);
  }

  function clearUi() {
    document.querySelectorAll("button.play").forEach((button) => {
      button.setAttribute("aria-pressed", "false");
    });
    document.querySelectorAll("tr.is-playing").forEach((row) => row.classList.remove("is-playing"));
    document.querySelectorAll("[data-status-for]").forEach((node) => {
      node.textContent = "";
    });
    document.querySelectorAll(".playhead").forEach((node) => node.setAttribute("visibility", "hidden"));
    activeButton = null;
  }

  function halt() {
    token += 1;
    window.clearTimeout(timer);
    window.cancelAnimationFrame(frame);
    cursor = null;
    active.forEach((node) => {
      try {
        node.stop();
      } catch (error) {
        /* already stopped */
      }
    });
    active = [];
    clearUi();
  }

  function barAt(bars, time) {
    let index = 0;
    for (let i = 0; i < bars.length - 1; i += 1) {
      if (bars[i + 1] <= time) index = i + 1;
      else break;
    }
    if (index >= bars.length - 1) index = Math.max(0, bars.length - 2);
    return index;
  }

  function drawPlayhead(songId, time) {
    const song = songs.get(songId);
    const head = document.querySelector(`#${CSS.escape(songId)} .playhead`);
    if (!song || !head) return;
    const bars = song.bars;
    const index = barAt(bars, time);
    const barStart = bars[index];
    const barEnd = bars[index + 1];
    const fraction = barEnd > barStart ? (time - barStart) / (barEnd - barStart) : 0;
    const pad = Number(head.dataset.pad);
    const step = Number(head.dataset.step);
    const x = pad + index * step + Math.min(1, Math.max(0, fraction)) * step;
    const shade = head.querySelector(".playhead-shade");
    const line = head.querySelector(".playhead-line");
    const cap = head.querySelector(".playhead-cap");
    shade.setAttribute("x", String(pad + index * step));
    line.setAttribute("x1", String(x));
    line.setAttribute("x2", String(x));
    cap.setAttribute("transform", `translate(${x} ${cap.dataset.y})`);
    head.setAttribute("visibility", "visible");

    const barNumber = index + 1;
    if (cursor && cursor.bar !== barNumber) {
      cursor.bar = barNumber;
      const status = document.querySelector(`[data-status-for="${songId}"]`);
      if (status) {
        status.textContent = `${cursor.loop ? "Looping" : "Playing"} ${cursor.label} · bar ${barNumber}`;
      }
    }

    const scroller = head.closest(".chart-scroll");
    if (!scroller) return;
    const view = scroller.clientWidth;
    if (!cursor.seen) {
      scroller.scrollLeft = Math.max(0, x - 48);
      cursor.seen = true;
      return;
    }
    const margin = 72;
    const left = scroller.scrollLeft;
    if (x < left + margin || x > left + view - margin) {
      const desired = Math.max(0, x - view * 0.35);
      scroller.scrollLeft = left + (desired - left) * 0.4;
    }
  }

  function track() {
    frame = window.requestAnimationFrame(track);
    if (!cursor || cursor.run !== token || !context) return;
    const duration = cursor.endMs - cursor.startMs;
    let elapsed = (context.currentTime - cursor.origin) * 1000;
    if (!Number.isFinite(elapsed) || elapsed < 0) return;
    if (cursor.loop) elapsed %= duration;
    else if (elapsed > duration) elapsed = duration;
    drawPlayhead(cursor.songId, cursor.startMs + elapsed);
  }

  async function schedule(songId, startBar, endBar, loop, label, button) {
    const song = songs.get(songId);
    if (!song) return;
    halt();
    const run = token;
    const ctx = audio();
    if (ctx.state === "suspended") await ctx.resume();
    if (run !== token) return;
    const startMs = song.bars[Math.max(0, startBar - 1)] || 0;
    const endIndex = Math.min(song.bars.length - 1, endBar);
    const endMs = song.bars[endIndex];
    const duration = Math.max(0, endMs - startMs);
    if (!duration) return;
    const notes = song.events.filter((event) => event[0] >= startMs && event[0] < endMs);
    const origin = ctx.currentTime + 0.05;
    cursor = {
      songId,
      origin,
      startMs,
      endMs,
      run,
      label,
      loop,
      bar: 0,
      seen: false,
    };
    frame = window.requestAnimationFrame(track);
    activeButton = button;
    button.setAttribute("aria-pressed", "true");
    const row = button.closest("tr");
    if (row) row.classList.add("is-playing");
    const status = document.querySelector(`[data-status-for="${songId}"]`);
    if (status) status.textContent = `${loop ? "Looping" : "Playing"} ${label}`;

    function pass(passOrigin) {
      if (run !== token) return;
      if (cursor && cursor.run === run) cursor.origin = passOrigin;
      notes.forEach((event) => hit(ctx, passOrigin + (event[0] - startMs) / 1000, event[1], event[2]));
      if (!loop) {
        timer = window.setTimeout(() => {
          if (run === token) halt();
        }, duration + 80);
        return;
      }
      timer = window.setTimeout(() => pass(passOrigin + duration / 1000), Math.max(40, duration - 80));
    }

    pass(origin);
  }

  document.addEventListener("click", (event) => {
    const stop = event.target.closest("button.stop");
    if (stop) {
      halt();
      return;
    }
    const button = event.target.closest("button.play");
    if (!button) return;
    if (button === activeButton) {
      halt();
      return;
    }
    schedule(
      button.dataset.song,
      Number(button.dataset.start),
      Number(button.dataset.end),
      button.dataset.loop === "1",
      button.dataset.label,
      button
    );
  });
})();
