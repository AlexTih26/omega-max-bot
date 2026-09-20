(function () {
  "use strict";

  var endpoint = "/api/rumex-registry/client-errors";
  var reportCount = 0;
  var maxReports = 10;

  function text(value, limit) {
    return String(value || "")
      .replace(/\b(cookie|token|authorization|bearer|password|passwd|pin)\b\s*[:=]\s*[^\s,;]+/gi, "$1=[скрыто]")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, limit);
  }

  function sourceUrl(value) {
    try {
      var url = new URL(String(value || ""), location.origin);
      return url.origin === location.origin ? url.pathname : "";
    } catch (_error) {
      return "";
    }
  }

  function send(kind, message, source, line, column) {
    if (reportCount >= maxReports || !text(message, 500)) return;
    reportCount += 1;
    var payload = JSON.stringify({
      kind: kind,
      message: text(message, 500),
      source: sourceUrl(source),
      line: Number(line) || 0,
      column: Number(column) || 0
    });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(endpoint, new Blob([payload], { type: "application/json" }));
      return;
    }
    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      credentials: "omit",
      keepalive: true
    }).catch(function () {});
  }

  window.addEventListener("error", function (event) {
    send("error", event.message, event.filename, event.lineno, event.colno);
  });
  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason;
    send("unhandledrejection", reason && reason.message ? reason.message : reason, "", 0, 0);
  });

  var originalError = console.error;
  console.error = function () {
    send("console-error", Array.prototype.map.call(arguments, function (value) {
      return value && value.message ? value.message : value;
    }).join(" "), "", 0, 0);
    return originalError.apply(console, arguments);
  };
})();
