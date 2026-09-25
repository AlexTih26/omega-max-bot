(function () {
  "use strict";

  var installPrompt = null;
  var installButton = document.getElementById("installAppButton");

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
    navigator.serviceWorker.register("/taksimo-new/sw.js", { scope: "/taksimo-new/" }).catch(function () {
      // Приложение остаётся доступно как защищённый веб-кабинет.
    });
  });
})();
