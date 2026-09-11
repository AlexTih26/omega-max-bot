(function () {
  var webApp = window.WebApp || null;
  var initData = "";
  var busy = false;
  var registry = null;
  var selected = null;

  var siteLabel = document.getElementById("siteLabel");
  var awaitingList = document.getElementById("awaitingList");
  var awaitingEmpty = document.getElementById("awaitingEmpty");
  var detailSection = document.getElementById("detailSection");
  var konturCard = document.getElementById("konturCard");
  var copyBtn = document.getElementById("copyBtn");
  var konturBtn = document.getElementById("konturBtn");
  var undoBtn = document.getElementById("undoBtn");
  var backBtn = document.getElementById("backBtn");
  var historyList = document.getElementById("historyList");
  var outsideMax = document.getElementById("outsideMax");
  var toast = document.getElementById("toast");

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
    }, 2400);
  }

  function blocksHtml(item) {
    var lines = (item.blocks || [])
      .map(function (b) {
        return "<li>" + b.letter + "-" + b.number + "</li>";
      })
      .join("");
    return lines ? "<ul class='rmx-kontur-blocks'>" + lines + "</ul>" : "";
  }

  function renderList() {
    var rows = (registry && registry.awaiting_receipt) || [];
    awaitingList.innerHTML = "";
    awaitingEmpty.hidden = rows.length > 0;
    rows.forEach(function (item) {
      var li = document.createElement("li");
      li.className = "rmx-card";
      li.innerHTML =
        '<div class="rmx-card-head">' +
        '<p class="rmx-card-title">…' +
        item.plate_tail +
        " · " +
        (item.name || "") +
        "</p>" +
        '<p class="rmx-card-status rmx-card-status--wait">' +
        (item.status_label || "Ждёт расписку") +
        "</p>" +
        "</div>" +
        '<p class="rmx-card-meta">' +
        (item.plate || "") +
        "<br>" +
        (item.blocks_short || "") +
        (item.loaded_at ? " · " + item.loaded_at : "") +
        "</p>";
      li.addEventListener("click", function () {
        openDetail(item);
      });
      awaitingList.appendChild(li);
    });

    var history = (registry && registry.history) || [];
    historyList.innerHTML = "";
    history.slice(0, 15).forEach(function (item) {
      var li = document.createElement("li");
      li.className = "rmx-card";
      li.innerHTML =
        '<p class="rmx-card-title">…' +
        item.plate_tail +
        " · " +
        (item.blocks_short || "") +
        "</p>" +
        '<p class="rmx-card-meta">' +
        (item.status_label || "") +
        (item.released_at ? " · " + item.released_at : "") +
        "</p>";
      historyList.appendChild(li);
    });
  }

  function openDetail(item) {
    selected = item;
    detailSection.hidden = false;
    awaitingList.hidden = true;
    awaitingEmpty.hidden = true;
    document.getElementById("historySection").hidden = true;

    konturCard.innerHTML =
      "<p class='rmx-kontur-label'>Данные для Контура</p>" +
      "<p><strong>Водитель:</strong> " +
      (item.name || "—") +
      "</p>" +
      "<p><strong>Гос. номер:</strong> " +
      (item.plate || "—") +
      "</p>" +
      "<p><strong>ТС:</strong> " +
      (item.vehicle || "—") +
      "</p>" +
      "<p><strong>Блоков:</strong> " +
      (item.block_count || "") +
      "</p>" +
      blocksHtml(item);

    konturBtn.hidden = item.status !== "awaiting_receipt";
    undoBtn.hidden = true;
    konturBtn.disabled = false;
  }

  function closeDetail() {
    selected = null;
    detailSection.hidden = true;
    awaitingList.hidden = false;
    document.getElementById("historySection").hidden = false;
    renderList();
  }

  function apiUrl(path) {
    if (!initData) return path;
    var sep = path.indexOf("?") >= 0 ? "&" : "?";
    return path + sep + "initData=" + encodeURIComponent(initData);
  }

  function loadRegistry() {
    return fetch(apiUrl("/api/rumex/accountant/registry"), { headers: apiHeaders() })
      .then(function (r) {
        return r.json().then(function (body) {
          if (r.status === 403) {
            outsideMax.hidden = false;
            throw new Error("forbidden");
          }
          if (!r.ok) throw new Error(body.error || "load failed");
          return body;
        });
      })
      .then(function (data) {
        registry = data;
        if (data.site_label && siteLabel) siteLabel.textContent = data.site_label;
        if (!selected) renderList();
      });
  }

  function postAction(action) {
    if (busy || !selected) return;
    busy = true;
    konturBtn.disabled = true;
    fetch("/api/rumex/accountant/action", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ action: action, loading_id: selected.id }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.notification || body.error || "failed");
          return body;
        });
      })
      .then(function (body) {
        showToast(body.notification || "Готово");
        registry = body.registry || registry;
        closeDetail();
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка");
      })
      .finally(function () {
        busy = false;
        konturBtn.disabled = false;
      });
  }

  function copyForKontur() {
    if (!selected) return;
    fetch(apiUrl("/api/rumex/accountant/copy/" + encodeURIComponent(selected.id)), {
      headers: apiHeaders(),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "copy failed");
          return body.text;
        });
      })
      .then(function (text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          return navigator.clipboard.writeText(text).then(function () {
            showToast("Скопировано.");
          });
        }
        showToast(text.slice(0, 120) + "…");
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка");
      });
  }

  if (copyBtn) copyBtn.addEventListener("click", copyForKontur);
  if (konturBtn) konturBtn.addEventListener("click", function () {
    postAction("kontur");
  });
  if (undoBtn) undoBtn.addEventListener("click", function () {
    postAction("kontur_undo");
  });
  if (backBtn) backBtn.addEventListener("click", closeDetail);

  function boot() {
    if (typeof installPanelFeedback === "function") {
      installPanelFeedback({
        app: "rumex_accountant",
        getHeaders: apiHeaders,
        showToast: showToast,
      });
    }
    if (!setupBridge()) {
      setTimeout(function () {
        if (!setupBridge()) {
          setTimeout(function () {
            if (!setupBridge()) {
              outsideMax.hidden = false;
              return;
            }
            loadRegistry().catch(function () {});
          }, 800);
          return;
        }
        loadRegistry().catch(function () {});
      }, 400);
      return;
    }
    loadRegistry().catch(function () {});
  }

  boot();
})();
