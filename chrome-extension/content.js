// Reads the Songsterr player: is it playing, which measure the cursor is on,
// and when that measure began. Measures are counted from 0 like the tab file;
// the page labels measure N with the number N and leaves the first one blank.
// Playing is confirmed for a few reads before it flips. When a new bar is
// confirmed, the cursor's place in that bar is kept so a late confirm does not
// pretend the drums are still on beat 1.
(() => {
  const READ_MS = 15;
  const HEARTBEAT_MS = 200;
  const PLAY_CONFIRM = 3;
  const BAR_LEAD = 8;

  let measure = null;
  let playing = false;
  let markAt = Date.now();
  let markFraction = 0;
  let lastSent = 0;
  let stopped = false;
  let nextPlaying = false;
  let playingVotes = PLAY_CONFIRM;

  function cursor() {
    for (const use of document.querySelectorAll('use[href^="#cursor-playhead-"]')) {
      if (use.style.visibility === "hidden") continue;
      const box = use.getBoundingClientRect();
      if (box.width > 0 || box.height > 0) return use;
    }
    return null;
  }

  function readPosition() {
    const head = cursor();
    if (!head) return null;
    const line = head.closest("g[data-line]");
    if (!line) return null;
    const x = head.getBoundingClientRect().left;
    const lineBox = line.getBoundingClientRect();
    const starts = [];
    for (const text of line.querySelectorAll("text")) {
      if (!/number/i.test(text.getAttribute("class") || "")) continue;
      const value = text.textContent.trim();
      if (!/^\d+$/.test(value)) continue;
      starts.push({ m: parseInt(value, 10), x: text.getBoundingClientRect().left - BAR_LEAD });
    }
    starts.sort((a, b) => a.x - b.x);
    let found = null;
    for (let i = 0; i < starts.length; i++) {
      if (starts[i].x <= x + 1) found = i;
    }
    let index, left, right;
    if (found !== null) {
      index = starts[found].m;
      left = starts[found].x;
      right = found + 1 < starts.length ? starts[found + 1].x : lineBox.right;
    } else if (starts.length) {
      index = starts[0].m - 1;
      left = lineBox.left;
      right = starts[0].x;
    } else {
      return null;
    }
    const fraction = right > left ? Math.min(1, Math.max(0, (x - left) / (right - left))) : 0;
    return { measure: index, fraction };
  }

  function isPlaying() {
    const root = document.getElementById("root");
    const flag = root && root.dataset ? root.dataset.playing : undefined;
    if (flag && flag !== "off" && flag !== "false") return true;
    const button = document.getElementById("control-play");
    return !!button && button.getAttribute("aria-pressed") === "true";
  }

  function speed() {
    const button = document.getElementById("control-speed");
    const match = button && (button.textContent || button.getAttribute("title") || "").match(/(\d+)\s*%/);
    return match ? parseInt(match[1], 10) : 100;
  }

  function songId() {
    const match = location.pathname.match(/-s(\d+)(?:t\d+)?\/?$/);
    return match ? parseInt(match[1], 10) : null;
  }

  function apply(command) {
    const button = document.getElementById("control-play");
    if (!button || !command) return;
    if ((command === "play" && !isPlaying()) || (command === "pause" && isPlaying())) {
      button.click();
    }
  }

  function send(now) {
    lastSent = now;
    const state = {
      songId: songId(),
      title: document.title,
      playing,
      measure,
      markAt,
      markFraction,
      speed: speed(),
      sentAt: now,
    };
    try {
      chrome.runtime.sendMessage({ type: "state", state }, (answer) => {
        if (chrome.runtime.lastError || !answer) return;
        if (answer.command) apply(answer.command);
      });
    } catch (error) {
      stopped = true;
    }
  }

  function confirmPlaying(nowPlaying) {
    if (nowPlaying === nextPlaying) {
      playingVotes += 1;
    } else {
      nextPlaying = nowPlaying;
      playingVotes = 1;
    }
    if (playingVotes < PLAY_CONFIRM || nextPlaying === playing) return false;
    playing = nextPlaying;
    return true;
  }

  function tick() {
    if (stopped) return;
    const now = Date.now();
    const position = readPosition();
    const nowPlaying = isPlaying();
    let changed = false;
    if (confirmPlaying(nowPlaying)) {
      markAt = now;
      markFraction = position ? position.fraction : markFraction;
      changed = true;
    }
    if (position && position.measure !== measure) {
      // New bar: keep where the cursor already is. Do not force fraction back to
      // 0, or a late reading leaves the drums behind until the next bar.
      measure = position.measure;
      markAt = now;
      markFraction = position.fraction;
      changed = true;
    } else if (position && !playing && Math.abs(position.fraction - markFraction) > 0.02) {
      markFraction = position.fraction;
      markAt = now;
      changed = true;
    }
    if (changed || now - lastSent >= HEARTBEAT_MS) send(now);
  }

  setInterval(tick, READ_MS);
})();
