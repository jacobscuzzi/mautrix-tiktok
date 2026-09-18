// Tap the frontier websocket by wrapping WebSocket. Reads frames only; does not
// alter navigator or any fingerprinted property. Foreground "live while the app
// is open" mode for the iOS host. On the server we prefer Playwright's native
// page.on("websocket"); this file exists for the WKWebView host where that is
// unavailable.
(function () {
  if (window.__bridgeWsHooked) return;
  window.__bridgeWsHooked = true;
  var post = window.__bridgePostToHost || function () {};
  var Orig = window.WebSocket;
  function Hooked(url, protocols) {
    var ws = protocols === undefined ? new Orig(url) : new Orig(url, protocols);
    if (String(url).indexOf("im-ws.tiktok.com") !== -1) {
      ws.addEventListener("message", function (ev) {
        var d = ev.data;
        if (d instanceof Blob) {
          d.arrayBuffer().then(function (b) { post("ws_in", { url: String(url), b64: _b64(b) }); });
        } else if (d instanceof ArrayBuffer) {
          post("ws_in", { url: String(url), b64: _b64(d) });
        } else {
          post("ws_in", { url: String(url), text: String(d) });
        }
      });
    }
    return ws;
  }
  function _b64(buf) {
    var bytes = new Uint8Array(buf), bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }
  Hooked.prototype = Orig.prototype;
  Object.setPrototypeOf(Hooked, Orig);
  window.WebSocket = Hooked;
})();
