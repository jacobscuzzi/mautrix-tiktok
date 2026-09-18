// Shared host bridge. One entry point posts everything to the host:
//   - server (Playwright):  page.expose_binding("__bridge_host", ...)
//   - iOS (WKWebView):      WKScriptMessageHandler named "bridgeHost"
// Installs nothing on navigator; only reads. Safe to inject at document-start.
(function () {
  if (window.__bridgePostInstalled) return;
  window.__bridgePostInstalled = true;
  function postToHost(kind, payload) {
    var msg = { kind: kind, payload: payload, ts: Date.now() };
    try {
      if (typeof window.__bridge_host === "function") { window.__bridge_host(msg); return; }
      if (window.webkit && window.webkit.messageHandlers &&
          window.webkit.messageHandlers.bridgeHost) {
        window.webkit.messageHandlers.bridgeHost.postMessage(msg); return;
      }
      (window.__bridgeQueue = window.__bridgeQueue || []).push(msg);
    } catch (e) { /* never throw into the page */ }
  }
  window.__bridgePostToHost = postToHost;
})();
