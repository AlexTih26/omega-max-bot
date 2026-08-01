(function () {
  var webApp = window.WebApp || null;
  var initData = "";
  var busy = false;

  var siteLabel = document.getElementById("siteLabel");
  var plateTail = document.getElementById("plateTail");
  var docsBtn = document.getElementById("docsBtn");
  var outsideMax = document.getElementById("outsideMax");
  var toast = document.getElementById("toast");
  var infoBtn = document.getElementById("infoBtn");
  var infoPanel = document.getElementById("infoPanel");

  function apiHeaders() {
    var h = { "Content-Type": "application/json" };
    if (initData) h["X-Max-Init-Data"] = initData;
    return h;
  }

  function setupBridge() {
    if (!webApp) return false;
    initData = webApp.initData || "";
    try {
      if (webApp.ready) webApp.ready();
      if (webApp.expand) webApp.expand();
    } catch (e) {}
    return Boolean(initData);
  }

  function showToast(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    toast.classList.add("rmx-toast--show");
    setTimeout(function () {
      toast.classList.remove("rmx-toast--show");
      setTimeout(function () {
        toast.hidden = true;
      }, 200);
    }, 2200);
  }

  function normalizeTail(value) {
    return String(value || "").replace(/\D/g, "").slice(-6);
  }

  function loadProfile() {
    return fetch("/api/rumex/registry", { headers: apiHeaders() })
      .then(function (r) {
        return r.json().then(function (body) {
          if (r.status === 403) {
            outsideMax.hidden = false;
            outsideMax.textContent = "Панель Румекс — только для диспетчера завода.";
            throw new Error("forbidden");
          }
          if (!r.ok) throw new Error(body.error || "profile failed");
          return body;
        });
      })
      .then(function (data) {
        if (data.site_label && siteLabel) siteLabel.textContent = data.site_label;
      });
  }

  function postDocuments() {
    if (busy) return;
    var tail = normalizeTail(plateTail && plateTail.value);
    if (!tail) {
      showToast("Введите хвост номера");
      if (plateTail) plateTail.focus();
      return;
    }
    busy = true;
    if (docsBtn) docsBtn.disabled = true;
    fetch("/api/rumex/action", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ action: "documents", plate_tail: tail }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.notification || body.error || "action failed");
          return body;
        });
      })
      .then(function (body) {
        showToast(body.notification || "Готово");
        if (plateTail) plateTail.value = "";
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка");
      })
      .finally(function () {
        busy = false;
        if (docsBtn) docsBtn.disabled = false;
      });
  }

  if (docsBtn) docsBtn.addEventListener("click", postDocuments);
  if (plateTail) {
    plateTail.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        postDocuments();
      }
    });
  }

  if (infoBtn && infoPanel) {
    infoBtn.addEventListener("click", function () {
      var open = infoBtn.getAttribute("aria-expanded") === "true";
      infoBtn.setAttribute("aria-expanded", open ? "false" : "true");
      infoPanel.hidden = open;
    });
  }

  function boot() {
    if (typeof installPanelFeedback === "function") {
      installPanelFeedback({
        app: "rumex",
        getHeaders: apiHeaders,
        showToast: showToast,
      });
    }

    if (!setupBridge()) {
      setTimeout(function () {
        if (!setupBridge()) {
          outsideMax.hidden = false;
          return;
        }
        loadProfile().catch(function () {});
      }, 400);
      return;
    }
    loadProfile().catch(function () {});
  }

  boot();
})();
