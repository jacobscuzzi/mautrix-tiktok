// Observe DM-related fetch responses (read-only) and forward their shapes to the
// host. Wraps window.fetch without changing navigator. TikTok's webmssdk already
// wraps fetch to SIGN it; we wrap the outer call to OBSERVE it. We never modify
// the request, so signing is unaffected.
(function () {
  if (window.__bridgeFetchTapped) return;
  window.__bridgeFetchTapped = true;
  var post = window.__bridgePostToHost || function () {};
  var DM = /(im-api\.|\/api\/im\/|spotlight|conversation|message|token\/beat)/i;
  var orig = window.fetch;
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var p = orig.apply(this, arguments);
    if (DM.test(url)) {
      p.then(function (r) {
        r.clone().text().then(function (t) {
          post("fetch", { url: url, status: r.status, body: t.slice(0, 200000) });
        }).catch(function () {});
      }).catch(function () {});
    }
    return p;
  };
})();
