(function () {
  "use strict";

  var installButton = document.getElementById("installAppButton");
  var installPrompt = null;

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    installPrompt = event;
    if (installButton) installButton.hidden = false;
  });

  window.addEventListener("appinstalled", function () {
    installPrompt = null;
    if (installButton) installButton.hidden = true;
  });

  if (installButton) {
    installButton.addEventListener("click", function () {
      if (!installPrompt) return;
      installPrompt.prompt();
      installPrompt.userChoice.finally(function () {
        installPrompt = null;
        installButton.hidden = true;
      });
    });
  }

  if (!("serviceWorker" in navigator)) return;

  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/rumex-app-sw.js", { scope: "/" }).catch(function () {
      // PWA is an optional app shell; the protected web cabinets remain available.
    });
  });
})();
