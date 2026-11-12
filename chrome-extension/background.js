// Forwards the page's player state to the finder at 127.0.0.1 and hands back
// its reply. The page itself cannot reach 127.0.0.1 because of its own rules.
const FINDER = "http://127.0.0.1:47391/songsterr";

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (!message || message.type !== "state") {
    return false;
  }
  fetch(FINDER, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message.state),
  })
    .then((response) => response.json())
    .then((answer) => reply({ connected: true, command: answer.command || null }))
    .catch(() => reply({ connected: false, command: null }));
  return true;
});
