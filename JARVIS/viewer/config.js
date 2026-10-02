// Where the brain (server.py) lives when this page is opened from GitHub Pages.
// Served by server.py itself (localhost or Render)? This is ignored -- same origin is used.
// Only ever point this at an address you've confirmed is yours: the page sends your passphrase
// there. (Confirmed 2026-10-03: JARVIS health check, 401 without passphrase, CORS for rahul9994.github.io.)
// Each device can override it in the Connect dialog (the link icon in the top-left HUD).
window.JARVIS_CONFIG = {
  api: "https://rahul9994-jarvis.onrender.com",
};
