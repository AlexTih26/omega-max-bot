(function () {
  var webApp = window.WebApp || null;
  var initData = "";
  var searchQ = document.getElementById("searchQ");
  var results = document.getElementById("results");
  var empty = document.getElementById("empty");
  var outsideMax = document.getElementById("outsideMax");
  var timer = null;

  function readInitDataFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var fromQuery = params.get("initData") || params.get("InitData") || params.get("tgWebAppData");
    if (fromQuery) return fromQuery;
    if (window.location.hash && window.location.hash.length > 1) {
      var hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      return hash.get("initData") || hash.get("InitData") || hash.get("tgWebAppData") || "";
    }
    return "";
  }

  function syncInitData() {
    if (webApp && webApp.initData) {
      initData = webApp.initData;
    }
    if (!initData) {
      initData = readInitDataFromUrl();
    }
    return Boolean(initData);
  }

  function apiHeaders() {
    syncInitData();
    var h = {};
    if (initData) h["X-Max-Init-Data"] = initData;
    return h;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function setupBridge() {
    if (!webApp) return false;
    try {
      if (webApp.ready) webApp.ready();
      if (webApp.expand) webApp.expand();
    } catch (e) {}
    return syncInitData();
  }

  function renderSlab(s) {
    var li = document.createElement("li");
    li.className = "tf-card";
    var place = s.place || (s.pos_x && s.pos_y ? s.pos_x + "/" + s.pos_y : "—");
    if (s.suffix) place += " (" + s.suffix + ")";
    var title = s.label || (s.letter && s.number ? s.letter + " " + s.number : "—");
    var meta = [
      s.unload_date || "",
      s.vehicle_plate || "",
      s.platform_zone ? "площадка " + s.platform_zone : "",
      s.wagon_number ? "вагон " + s.wagon_number : "",
      s.loading_date ? "погр. " + s.loading_date : "",
      s.on_yard ? "" : "не на площадке",
    ].filter(Boolean).join(" · ");
    li.innerHTML =
      "<p class='tf-card-title'>" + esc(title) + " → " + esc(place) + "</p>" +
      "<p class='tf-card-meta'>" + esc(meta) + "</p>";
    return li;
  }

  function renderData(data) {
    results.innerHTML = "";
    var items = data.results || [];
    empty.hidden = items.length > 0;
    if (data.type === "wagon" && items.length) {
      var head = document.createElement("li");
      head.className = "tf-card";
      head.innerHTML =
        "<p class='tf-card-head'>Вагон " + esc(data.wagon) + " · блоков: " + items.length + "</p>";
      results.appendChild(head);
    }
    items.forEach(function (s) {
      results.appendChild(renderSlab(s));
    });
  }

  function runSearch(q) {
    if (!q) {
      results.innerHTML = "";
      empty.hidden = true;
      return;
    }
    fetch("/api/taksimo-find/search?q=" + encodeURIComponent(q), { headers: apiHeaders() })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "search failed");
          return body;
        });
      })
      .then(renderData)
      .catch(function () {
        results.innerHTML = "";
        empty.hidden = false;
        empty.textContent = "Ошибка поиска — проверьте связь";
      });
  }

  searchQ.addEventListener("input", function () {
    clearTimeout(timer);
    var q = searchQ.value.trim();
    timer = setTimeout(function () {
      runSearch(q);
    }, 320);
  });

  function boot() {
    if (!setupBridge()) {
      setTimeout(function () {
        if (!syncInitData()) {
          outsideMax.hidden = false;
          searchQ.disabled = true;
          return;
        }
        searchQ.disabled = false;
        searchQ.focus();
      }, 400);
      return;
    }
    searchQ.focus();
  }

  boot();
})();
