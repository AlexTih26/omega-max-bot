(function () {
  function apiFetch(url, options) {
    options = options || {};
    options.credentials = "same-origin";
    return fetch(url, options).then(function (r) {
      if (r.status === 401) {
        location.href = "/taksimo-login.html?next=" + encodeURIComponent(location.pathname);
        return Promise.reject(new Error("auth"));
      }
      return r;
    });
  }

  var meta = {
    letters: ["A", "B", "C", "D", "E", "F", "K"],
    suffixes: ["", "к", "а", "тк", "скол"],
    platform_zones: ["ХРАНЕНИЯ", "ГРУЗОВОЙ", "ТУРАН", "В ПУТИ"],
    material_units: ["шт", "кг", "м", "пачка", "рулон"],
    grid_x: 13,
    grid_y: 25,
    max_slabs_per_cell: 4,
    max_wagon_slabs: 9,
    vehicles: [],
    stats: { on_yard: 0, on_wagon: 0, in_transit: 0 },
  };
  var YARD_CELL_PX = 40;
  var wagonPlanData = null;
  var activeWagonSlot = null;
  var HISTORY_PAGE_SIZE = 50;
  var historyOffset = 0;
  var historyTotal = 0;
  var historyLoading = false;
  var wagonCardCatalog = [];
  var activeWagonCardNumber = "";
  var wagonCardSearchTimer = null;
  var materialsSnapshot = null;
  var activeMaterialWagonNumber = "";

  function yardCellPx() {
    var w = window.innerWidth || 0;
    if (w >= 1024) return 44;
    if (w >= 768) return 42;
    return 40;
  }
  var selectedVehicleId = null;
  var pickTarget = null;
  var editingSessionId = null;
  var editingRevision = null;
  var draftSessionId = null;
  var canDelete = true;
  var canKodar = false;
  var pickSlabField = null;
  var DRAFT_CACHE_KEY = "taksimo_unload_draft_v1";
  var yardSnapshot = null;
  var yardPollTimer = null;
  var activeTab = "unload";
  var lastSaveAction = null;
  var YARD_POLL_MS = 20000;

  var panels = {
    unload: document.getElementById("panelUnload"),
    yard: document.getElementById("panelYard"),
    search: document.getElementById("panelSearch"),
    history: document.getElementById("panelHistory"),
    wagons: document.getElementById("panelWagons"),
    materials: document.getElementById("panelMaterials"),
    export: document.getElementById("panelExport"),
  };

  function $(id) {
    return document.getElementById(id);
  }

  function toast(msg) {
    var el = $("toast");
    el.textContent = msg;
    el.hidden = false;
    el.classList.add("tk-toast--show");
    setTimeout(function () {
      el.classList.remove("tk-toast--show");
      setTimeout(function () {
        el.hidden = true;
      }, 200);
    }, 2400);
  }

  function todayIso() {
    return new Date().toISOString().slice(0, 10);
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function craneMinutes() {
    var s = $("craneStart").value;
    var e = $("craneEnd").value;
    if (!s || !e) return null;
    var a = s.split(":").map(Number);
    var b = e.split(":").map(Number);
    var mins = b[0] * 60 + b[1] - (a[0] * 60 + a[1]);
    return mins > 0 ? mins : null;
  }

  function suffixOptions(selected) {
    return meta.suffixes
      .map(function (s) {
        return '<option value="' + esc(s) + '"' + (s === selected ? " selected" : "") + ">" + (s || "—") + "</option>";
      })
      .join("");
  }

  function platformOptions(selected) {
    var zones = (meta.platform_zones || ["ХРАНЕНИЯ", "ГРУЗОВОЙ", "ТУРАН", "В ПУТИ"]).slice();
    if (selected && zones.indexOf(selected) < 0) {
      zones.push(selected);
    }
    return zones
      .map(function (z) {
        var short =
          z === "ХРАНЕНИЯ" ? "Склад" :
          z === "В ПУТИ" ? "Путь" :
          z === "В КОДАР" ? "→ Кодар" :
          z === "БТС ВОСТОК" ? "БТС Восток" :
          z;
        return '<option value="' + esc(z) + '"' + (z === selected ? " selected" : "") + ">" + esc(short) + "</option>";
      })
      .join("");
  }

  function needsCoords(zone) {
    return zone === "ХРАНЕНИЯ";
  }

  function usesDefaultWagon(zone) {
    return zone === "ГРУЗОВОЙ" || zone === "ТУРАН";
  }

  function needsWagon(zone) {
    return usesDefaultWagon(zone) || zone === "В КОДАР";
  }

  function updatePlatformHint() {
    var z = $("defaultPlatform").value;
    var hints = {
      "ХРАНЕНИЯ": "ХРАНЕНИЯ: блок на площадке, укажите X/Y.",
      "В ПУТИ": "В ПУТИ: блок едет, координаты не нужны — только буква и номер.",
      "ГРУЗОВОЙ": "ГРУЗОВОЙ: с крана в вагон — укажите номер вагона в строке или «вагон по умолчанию».",
      "ТУРАН": "ТУРАН: с крана в вагон — укажите номер вагона в строке или «вагон по умолчанию».",
    };
    $("platformHint").textContent = hints[z] || "";
    refreshDefaultWagonSelect();
  }

  function wagonOptionsForZone(zone, selected) {
    selected = (selected || "").trim();
    var options = ['<option value="">—</option>'];
    var seen = {};
    var list = (wagonPlanData && wagonPlanData.wagons_in_zone && wagonPlanData.wagons_in_zone[zone]) || [];
    list.forEach(function (num) {
      num = (num || "").trim();
      if (!num || seen[num]) return;
      seen[num] = true;
      options.push(
        '<option value="' + esc(num) + '"' + (num === selected ? " selected" : "") + ">" + esc(num) + "</option>"
      );
    });
    var fleet = (wagonPlanData && (wagonPlanData.fleet || wagonPlanData.wagon_pool)) || [];
    fleet.forEach(function (w) {
      var num = (typeof w === "string" ? w : w.number || "").trim();
      if (!num || seen[num]) return;
      var planned = (typeof w === "object" ? w.planned_zone : "") || "";
      if (planned && planned !== zone) return;
      seen[num] = true;
      if (num === selected) {
        var hint = "";
        if (typeof w === "object") {
          if (w.planned_zone && w.planned_zone !== zone) return;
          if (w.stage === "returning") hint = " · порожн.";
        }
        options.push(
          '<option value="' + esc(num) + '"' + (num === selected ? " selected" : "") + ">" + esc(num) + esc(hint) + "</option>"
        );
        return;
      }
      var hint2 = "";
      if (typeof w === "object" && w.stage === "returning") hint2 = " · порожн.";
      options.push(
        '<option value="' + esc(num) + '"' + (num === selected ? " selected" : "") + ">" + esc(num) + esc(hint2) + "</option>"
      );
    });
    if (selected && !seen[selected]) {
      options.push(
        '<option value="' + esc(selected) + '" selected>' + esc(selected) + " (вне слота)</option>"
      );
    }
    return options.join("");
  }

  function slotWagonOptionsForZone(zone, selected) {
    selected = (selected || "").trim();
    var options = ['<option value="">— вагон из слота —</option>'];
    var seen = {};
    var list = (wagonPlanData && wagonPlanData.wagons_in_zone && wagonPlanData.wagons_in_zone[zone]) || [];
    list.forEach(function (num) {
      num = (num || "").trim();
      if (!num || seen[num]) return;
      seen[num] = true;
      options.push(
        '<option value="' + esc(num) + '"' + (num === selected ? " selected" : "") + ">" + esc(num) + "</option>"
      );
    });
    if (selected && !seen[selected]) {
      options.push(
        '<option value="' + esc(selected) + '" selected>' + esc(selected) + " (вне слота)</option>"
      );
    }
    return options.join("");
  }

  function refreshDefaultWagonSelect() {
    var row = $("defaultWagonRow");
    var sel = $("defaultWagon");
    if (!row || !sel) return;
    var zone = $("defaultPlatform").value;
    if (!usesDefaultWagon(zone)) {
      row.hidden = true;
      sel.innerHTML = '<option value="">— выберите вагон —</option>';
      return;
    }
    row.hidden = false;
    var current = sel.value || "";
    sel.innerHTML =
      '<option value="">— выберите вагон —</option>' +
      wagonOptionsForZone(zone, current).replace('<option value="">—</option>', "");
  }

  function refreshRowWagonSelect(row) {
    var sel = row.querySelector('[data-field="wagon_number"]');
    if (!sel) return;
    var zone = row.querySelector('[data-field="platform_zone"]').value;
    var current = sel.value || "";
    sel.innerHTML = wagonOptionsForZone(zone, current);
  }

  function refreshAllRowWagonSelects() {
    document.querySelectorAll("#slabRows .tk-slab-row").forEach(refreshRowWagonSelect);
    refreshDefaultWagonSelect();
  }

  function syncRowPlatformFields(row) {
    var z = row.querySelector('[data-field="platform_zone"]').value;
    var xIn = row.querySelector('[data-field="pos_x"]');
    var yIn = row.querySelector('[data-field="pos_y"]');
    var wagonSel = row.querySelector('[data-field="wagon_number"]');
    if (!needsCoords(z)) {
      xIn.value = "";
      yIn.value = "";
      xIn.disabled = true;
      yIn.disabled = true;
    } else {
      xIn.disabled = false;
      yIn.disabled = false;
      xIn.max = String(gridMaxX());
      yIn.max = String(gridMaxY());
    }
    if (wagonSel) {
      var needW = needsWagon(z);
      wagonSel.disabled = !needW;
      wagonSel.classList.toggle("tk-slab-wagon--off", !needW);
      if (!needW) {
        wagonSel.value = "";
      } else {
        refreshRowWagonSelect(row);
        if (
          usesDefaultWagon(z) &&
          !wagonSel.value &&
          $("defaultWagon") &&
          $("defaultWagon").value
        ) {
          wagonSel.value = $("defaultWagon").value;
        }
      }
    }
  }

  function applyDefaultPlatformToRows() {
    var zone = $("defaultPlatform").value;
    document.querySelectorAll("#slabRows .tk-slab-row").forEach(function (row) {
      var sel = row.querySelector('[data-field="platform_zone"]');
      if (sel) sel.value = zone;
      syncRowPlatformFields(row);
    });
    updatePlatformHint();
    updateAllPlacementHints();
  }

  function syncAllGridLimits() {
    var maxX = gridMaxX();
    var maxY = gridMaxY();
    var xIn = $("slabX");
    var yIn = $("slabY");
    if (xIn) {
      xIn.min = "1";
      xIn.max = String(maxX);
    }
    if (yIn) {
      yIn.min = "1";
      yIn.max = String(maxY);
    }
    document.querySelectorAll('#slabRows [data-field="pos_x"]').forEach(function (el) {
      el.min = "1";
      el.max = String(maxX);
    });
    document.querySelectorAll('#slabRows [data-field="pos_y"]').forEach(function (el) {
      el.min = "1";
      el.max = String(maxY);
    });
  }

  function refreshSlabSheetFields() {
    syncAllGridLimits();
    var zone = $("slabPlatform").value;
    var needXY = needsCoords(zone);
    $("slabX").required = needXY;
    $("slabY").required = needXY;
    $("slabWagonRow").hidden = !needsWagon(zone);
    if (needsWagon(zone)) {
      var wagonSel = $("slabWagon");
      var current = wagonSel.value || "";
      wagonSel.innerHTML = slotWagonOptionsForZone(zone, current);
    } else {
      $("slabWagon").innerHTML = '<option value="">— вагон из слота —</option>';
      $("slabWagon").value = "";
    }
  }

  function slabSlotWagons(zone) {
    return ((wagonPlanData && wagonPlanData.wagons_in_zone && wagonPlanData.wagons_in_zone[zone]) || [])
      .map(function (num) { return (num || "").trim(); })
      .filter(Boolean);
  }

  function handleSlabPlatformChange() {
    var platformSel = $("slabPlatform");
    var prevZone = (platformSel.dataset.prevZone || "").trim() || "ХРАНЕНИЯ";
    var nextZone = (platformSel.value || "").trim();
    if (!needsWagon(nextZone)) {
      refreshSlabSheetFields();
      platformSel.dataset.prevZone = nextZone;
      return;
    }
    var slotWagons = slabSlotWagons(nextZone);
    if (!slotWagons.length) {
      alert(nextZone + ": нет вагонов в слотах");
      platformSel.value = prevZone;
      refreshSlabSheetFields();
      return;
    }
    if (prevZone !== nextZone) {
      var movingFromGround = needsCoords(prevZone) || !!($("slabX").value || $("slabY").value);
      var message = movingFromGround
        ? nextZone + ": перенести с земли в вагон? X/Y очистятся, затем выберите вагон из слота."
        : nextZone + ": выберите вагон из слота.";
      if (!confirm(message)) {
        platformSel.value = prevZone;
        refreshSlabSheetFields();
        return;
      }
      $("slabX").value = "";
      $("slabY").value = "";
      $("slabWagon").value = "";
    }
    refreshSlabSheetFields();
    platformSel.dataset.prevZone = nextZone;
    if (!$("slabWagon").value) {
      $("slabWagon").focus();
      toast(nextZone + ": выберите вагон");
    }
  }

  function formatDbTime(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return d.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatDurationShort(seconds) {
    if (seconds == null || !isFinite(seconds)) return "—";
    var totalMinutes = Math.max(0, Math.round(seconds / 60));
    var days = Math.floor(totalMinutes / (60 * 24));
    var hours = Math.floor((totalMinutes % (60 * 24)) / 60);
    var minutes = totalMinutes % 60;
    var parts = [];
    if (days) parts.push(days + " д");
    if (hours) parts.push(hours + " ч");
    if (!days && minutes) parts.push(minutes + " мин");
    if (!parts.length) parts.push("0 мин");
    return parts.join(" ");
  }

  function formatQty(value) {
    var num = Number(value || 0);
    if (!isFinite(num)) return "0";
    return Math.abs(num - Math.round(num)) < 0.001
      ? String(Math.round(num))
      : num.toFixed(3).replace(/\.?0+$/, "");
  }

  function wagonStageBadgeClass(stage) {
    return (
      "tk-wagon-card-badge tk-wagon-card-badge--" +
      (stage || "history").replace(/[^a-z_]/gi, "")
    );
  }

  function renderDbStatus(db) {
    db = db || meta.db || {};
    var el = $("dbStatus");
    if (!el) return;
    var change = formatDbTime(db.last_change_ts);
    var backup = db.last_backup_ts ? formatDbTime(db.last_backup_ts) : "—";
    el.textContent = "База обновлена: " + change + " · бэкап: " + backup;
  }

  function renderStats(stats) {
    stats = stats || meta.stats || {};
    $("statYard").textContent = stats.on_yard != null ? stats.on_yard : 0;
    $("statWagon").textContent = stats.on_wagon != null ? stats.on_wagon : 0;
    var kodar = stats.on_kodar != null ? stats.on_kodar : 0;
    var bts = stats.at_bts_vostok != null ? stats.at_bts_vostok : 0;
    $("statKodar").textContent = kodar;
    $("statBts").textContent = bts;
    var kodarWrap = $("statKodarWrap");
    if (kodarWrap) kodarWrap.hidden = kodar <= 0;
    var btsWrap = $("statBtsWrap");
    if (btsWrap) btsWrap.hidden = false;
  }

  function bindHeaderStats() {
    var statBtsWrap = $("statBtsWrap");
    if (statBtsWrap) {
      statBtsWrap.classList.add("tk-stat--click");
      statBtsWrap.setAttribute("role", "button");
      statBtsWrap.setAttribute("tabindex", "0");
      statBtsWrap.addEventListener("click", function () { setTab("wagons"); });
      statBtsWrap.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setTab("wagons");
        }
      });
    }
    var statKodarWrap = $("statKodarWrap");
    if (statKodarWrap) {
      statKodarWrap.classList.add("tk-stat--click");
      statKodarWrap.setAttribute("role", "button");
      statKodarWrap.setAttribute("tabindex", "0");
      statKodarWrap.addEventListener("click", function () {
        setTab("yard");
        setTimeout(function () {
          var el = $("wagonInTransitWrap");
          if (el && !el.hidden) el.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 400);
      });
      statKodarWrap.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          statKodarWrap.click();
        }
      });
    }
    var statWagonWrap = $("statWagonWrap");
    if (statWagonWrap) {
      statWagonWrap.classList.add("tk-stat--click");
      statWagonWrap.setAttribute("role", "button");
      statWagonWrap.setAttribute("tabindex", "0");
      statWagonWrap.addEventListener("click", function () {
        setTab("yard");
        setTimeout(function () {
          var el = $("wagonPlan");
          if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 400);
      });
    }
  }

  function openAppMenu() {
    var sheet = $("appMenuSheet");
    if (!sheet) return;
    sheet.hidden = false;
    var userEl = $("currentUser");
    var menuUser = $("menuUser");
    if (menuUser && userEl && !userEl.hidden) {
      menuUser.textContent = userEl.textContent;
      menuUser.hidden = false;
    }
  }

  function closeAppMenu() {
    var sheet = $("appMenuSheet");
    if (sheet) sheet.hidden = true;
  }

  function dispatchStatusLabel(status) {
    return status === "in_transit" ? "в пути" : status === "delivered" ? "у БТС Восток" : status || "";
  }

  function formatVehicleBlockLines(blocks, prefix) {
    prefix = prefix || "· ";
    var grouped = {};
    var order = [];
    var unknown = [];
    (blocks || []).forEach(function (b) {
      var label = (b && b.label) || "";
      if (!label) return;
      var plate = ((b && b.vehicle_plate) || "").trim();
      if (!plate) {
        unknown.push(label);
        return;
      }
      if (!grouped[plate]) {
        grouped[plate] = [];
        order.push(plate);
      }
      grouped[plate].push(label);
    });
    if (!order.length && !unknown.length) return ["—"];
    var lines = order.map(function (plate) {
      return prefix + plate + " — " + grouped[plate].join(", ");
    });
    if (unknown.length) {
      lines.push(prefix + "без машины — " + unknown.join(", "));
    }
    return lines;
  }

  function confirmKodarReceived(dispatchId, wagonNumber) {
    if (!canKodar) return Promise.resolve();
    if (!confirm("Кодар получил вагон " + wagonNumber + "?\nПлиты будут отмечены выгруженными у БТС Восток.")) {
      return Promise.resolve();
    }
    return apiFetch("/api/taksimo/wagons/kodar-received", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dispatch_id: dispatchId, wagon_number: wagonNumber }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Кодар получил · БТС Восток");
        loadWagonPlan();
        loadStats();
        loadWagonCards();
        loadWagonHistory();
      })
      .catch(function (err) { alert(err.message); });
  }

  function renderWagonCardCatalog(items) {
    var list = $("wagonCardList");
    if (!list) return;
    list.innerHTML = "";
    if (!(items || []).length) {
      list.innerHTML = "<li class='tk-card'><div class='tk-card-meta'>Вагоны не найдены</div></li>";
      return;
    }
    items.forEach(function (wagon) {
      var li = document.createElement("li");
      li.className = "tk-card tk-wagon-card-item";
      if (wagon.number === activeWagonCardNumber) {
        li.classList.add("tk-wagon-card-item--active");
      }
      li.dataset.wagonNumber = wagon.number;
      li.innerHTML =
        '<div class="tk-wagon-card-top">' +
        '<div class="tk-card-title">Вагон ' + esc(wagon.number) + "</div>" +
        '<span class="' + wagonStageBadgeClass(wagon.stage) + '">' + esc(wagon.stage_label || wagon.stage || "—") + "</span>" +
        "</div>" +
        '<div class="tk-card-meta">' + esc(wagon.location_label || "—") + "</div>" +
        '<div class="tk-card-meta">Плит сейчас: ' + esc(wagon.current_slab_count || 0) +
        " · рейсов: " + esc(wagon.total_trips || 0) +
        (wagon.last_event_at_label ? " · " + esc(wagon.last_event_at_label) : "") +
        "</div>";
      li.addEventListener("click", function () {
        loadWagonCard(wagon.number, { scroll: false });
      });
      list.appendChild(li);
    });
  }

  function showWagonCardEmpty(message) {
    var box = $("wagonCardDetail");
    if (!box) return;
    box.hidden = false;
    box.innerHTML = "<h4>Карточка вагона</h4><p>" + esc(message) + "</p>";
  }

  function loadWagonCards(options) {
    options = options || {};
    var input = $("wagonCardSearch");
    var query =
      options.query != null
        ? String(options.query).trim()
        : input
          ? input.value.trim()
          : "";
    var list = $("wagonCardList");
    if (list) {
      list.innerHTML = "<li class='tk-card'><div class='tk-card-meta'>Загрузка вагонов…</div></li>";
    }
    return apiFetch(
      "/api/taksimo/wagons/catalog?limit=80&q=" + encodeURIComponent(query)
    )
      .then(function (r) { return r.json(); })
      .then(function (data) {
        wagonCardCatalog = data.wagons || [];
        if (!wagonCardCatalog.length) {
          activeWagonCardNumber = "";
          renderWagonCardCatalog([]);
          showWagonCardEmpty(query ? "Совпадений нет" : "История вагонов пока пуста");
          return null;
        }
        var nextNumber = activeWagonCardNumber;
        if (
          !nextNumber ||
          !wagonCardCatalog.some(function (item) { return item.number === nextNumber; })
        ) {
          nextNumber = wagonCardCatalog[0].number;
        }
        activeWagonCardNumber = nextNumber;
        renderWagonCardCatalog(wagonCardCatalog);
        return loadWagonCard(nextNumber, { scroll: false });
      })
      .catch(function () {
        if (list) {
          list.innerHTML = "<li class='tk-card'><div class='tk-card-meta'>Не удалось загрузить вагоны</div></li>";
        }
      });
  }

  function loadWagonCard(wagonNumber, options) {
    options = options || {};
    if (!wagonNumber) return Promise.resolve();
    activeWagonCardNumber = wagonNumber;
    renderWagonCardCatalog(wagonCardCatalog);
    var box = $("wagonCardDetail");
    if (box) {
      box.hidden = false;
      box.innerHTML = "<h4>Вагон " + esc(wagonNumber) + "</h4><p>Загрузка карточки…</p>";
    }
    return apiFetch("/api/taksimo/wagons/card/" + encodeURIComponent(wagonNumber))
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка загрузки вагона");
        var wagon = res.data.wagon;
        if (!wagon || !box) return;
        var currentSlabs = wagon.current_slabs || [];
        var currentSlabHtml = currentSlabs.length
          ? "<ul class='tk-list'>" +
            currentSlabs.map(function (item) {
              var metaLine = [];
              if (item.vehicle_plate) metaLine.push(item.vehicle_plate);
              if (item.loading_date) metaLine.push(item.loading_date);
              return (
                "<li class='tk-card'>" +
                "<div class='tk-card-title'>" + esc(item.label || "—") + "</div>" +
                (metaLine.length ? "<div class='tk-card-meta'>" + esc(metaLine.join(" · ")) + "</div>" : "") +
                "</li>"
              );
            }).join("") +
            "</ul>"
          : "<p class='tk-card-meta'>Сейчас на вагоне плит нет.</p>";
        var history = wagon.history || [];
        var tripsHtml = history.length
          ? "<ul class='tk-list'>" +
            history.map(function (trip) {
              var statusClass =
                trip.status === "in_transit"
                  ? "tk-dispatch-badge--transit"
                  : "tk-dispatch-badge--done";
              return (
                "<li class='tk-card tk-card--click tk-wagon-card-trip' data-trip-id='" + trip.id + "'>" +
                "<div class='tk-wagon-card-top'>" +
                "<div class='tk-card-title'>Рейс №" + esc(trip.id) + " · " + esc(trip.slot_zone || "—") + " №" + esc(trip.slot_index || "—") + "</div>" +
                "<span class='tk-dispatch-badge " + statusClass + "'>" + esc(trip.status_label || dispatchStatusLabel(trip.status)) + "</span>" +
                "</div>" +
                "<div class='tk-card-meta'>" +
                esc((trip.block_labels || []).join(", ") || "Без плит") +
                "</div>" +
                "<div class='tk-card-meta'>" +
                "Старт: " + esc(trip.dispatched_at_label || "—") +
                (trip.received_at_label ? " · БТС: " + esc(trip.received_at_label) : "") +
                " · этап: " + esc(formatDurationShort(trip.trip_seconds)) +
                "</div>" +
                "</li>"
              );
            }).join("") +
            "</ul>"
          : "<p class='tk-card-meta'>Рейсов пока нет.</p>";
        box.hidden = false;
        box.innerHTML =
          "<h4>Вагон " + esc(wagon.number) + "</h4>" +
          "<p><span class='" + wagonStageBadgeClass(wagon.stage) + "'>" + esc(wagon.stage_label || "—") + "</span></p>" +
          "<p>Где стоит: <strong>" + esc(wagon.location_label || "—") + "</strong><br>" +
          "Этап с: " + esc(wagon.current_stage_started_at_label || "—") +
          " · прошло " + esc(formatDurationShort(wagon.current_stage_age_seconds)) +
          ((wagon.vehicle_plates || []).length ? "<br>Машины: " + esc((wagon.vehicle_plates || []).join(", ")) : "") +
          (wagon.planned_zone ? "<br>План тупика: " + esc(wagon.planned_zone) : "") +
          "</p>" +
          '<div class="tk-wagon-card-stats">' +
          '<div class="tk-wagon-card-stat"><b>' + esc(wagon.current_slab_count || 0) + '</b><span>плит сейчас</span></div>' +
          '<div class="tk-wagon-card-stat"><b>' + esc(wagon.total_trips || 0) + '</b><span>кругов всего</span></div>' +
          '<div class="tk-wagon-card-stat"><b>' + esc(wagon.delivered_trips || 0) + '</b><span>закрыто у БТС</span></div>' +
          "</div>" +
          (
            wagon.current_dispatch && wagon.current_dispatch.status === "in_transit" && canKodar
              ? '<div class="tk-actions"><button type="button" class="tk-btn tk-btn--primary" id="wagonCardKodarBtn">Кодар получил</button></div>'
              : ""
          ) +
          '<h5 class="tk-wagon-card-subtitle">Текущий состав плит</h5>' +
          currentSlabHtml +
          '<h5 class="tk-wagon-card-subtitle">История рейсов</h5>' +
          tripsHtml;
        var wagonCardKodarBtn = $("wagonCardKodarBtn");
        if (wagonCardKodarBtn && wagon.current_dispatch) {
          wagonCardKodarBtn.addEventListener("click", function () {
            confirmKodarReceived(wagon.current_dispatch.id, wagon.number);
          });
        }
        box.querySelectorAll("[data-trip-id]").forEach(function (el) {
          el.addEventListener("click", function () {
            var tripId = parseInt(el.dataset.tripId, 10);
            var trip = history.find(function (item) { return item.id === tripId; });
            if (trip) showWagonDispatchDetail(trip);
          });
        });
        if (options.scroll !== false) {
          box.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      })
      .catch(function (err) {
        showWagonCardEmpty(err.message || "Не удалось открыть вагон");
      });
  }

  function materialUnitOptions(selected) {
    var units = (meta.material_units || ["шт", "кг", "м", "пачка", "рулон"]).slice();
    if (selected && units.indexOf(selected) < 0) units.push(selected);
    return units.map(function (unit) {
      return '<option value="' + esc(unit) + '"' + (unit === selected ? " selected" : "") + ">" + esc(unit) + "</option>";
    }).join("");
  }

  function renderMaterialSummary(data) {
    var box = $("materialSummary");
    if (!box) return;
    var summary = (data && data.summary) || {};
    box.innerHTML =
      '<div class="tk-material-metric"><b>' + esc(summary.material_count || 0) + '</b><span>материалов</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.low_stock_count || 0) + '</b><span>ниже минимума</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.ready_wagons || 0) + '</b><span>вагонов обеспечено</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.overall_wagons_left != null ? summary.overall_wagons_left : "—") + '</b><span>хватит ещё на вагонов</span></div>';
  }

  function renderMaterialList(materials) {
    var box = $("materialList");
    if (!box) return;
    box.innerHTML = "";
    if (!(materials || []).length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>Материалов пока нет. Добавьте первую позицию.</div></div>";
      return;
    }
    materials.forEach(function (item) {
      var card = document.createElement("div");
      card.className = "tk-card tk-material-card";
      card.dataset.materialId = String(item.id);
      var warnClass = item.low_stock ? " tk-material-low" : "";
      card.innerHTML =
        '<div class="tk-material-card-top">' +
        '<div class="tk-card-title">' + esc(item.name) + '</div>' +
        '<div class="tk-card-meta' + warnClass + '">' +
        (item.low_stock ? "ниже минимума" : "норма на вагон") +
        ": " + esc(formatQty(item.norm_per_wagon)) + " " + esc(item.unit) +
        '</div></div>' +
        '<div class="tk-material-grid">' +
        '<div class="tk-material-metric"><b>' + esc(formatQty(item.on_hand)) + '</b><span>физически, ' + esc(item.unit) + '</span></div>' +
        '<div class="tk-material-metric"><b>' + esc(formatQty(item.reserved)) + '</b><span>в резерве</span></div>' +
        '<div class="tk-material-metric"><b>' + esc(formatQty(item.available)) + '</b><span>свободно</span></div>' +
        '<div class="tk-material-metric"><b>' + esc(item.available_wagons != null ? item.available_wagons : "—") + '</b><span>вагонов по норме</span></div>' +
        '</div>' +
        '<div class="tk-row2">' +
        '<div><label class="tk-label">Ед. изм.</label><select class="tk-select" data-field="unit">' + materialUnitOptions(item.unit) + '</select></div>' +
        '<div><label class="tk-label">Мин. остаток</label><input class="tk-input" data-field="min_level" type="number" min="0" step="0.001" value="' + esc(item.min_level) + '"></div>' +
        '</div>' +
        '<div class="tk-row2">' +
        '<div><label class="tk-label">Название</label><input class="tk-input" data-field="name" value="' + esc(item.name) + '"></div>' +
        '<div><label class="tk-label">Норма на вагон</label><input class="tk-input" data-field="norm_per_wagon" type="number" min="0" step="0.001" value="' + esc(item.norm_per_wagon) + '"></div>' +
        '</div>' +
        '<div class="tk-material-inline-actions">' +
        '<input class="tk-input" data-field="receipt_qty" type="number" min="0" step="0.001" placeholder="Приход: количество">' +
        '<button type="button" class="tk-btn" data-act="receipt">Приход</button>' +
        '<button type="button" class="tk-btn tk-btn--primary" data-act="save">Сохранить</button>' +
        '</div>';
      card.querySelector('[data-act="save"]').addEventListener("click", function () {
        saveMaterialItem(item.id, card);
      });
      card.querySelector('[data-act="receipt"]').addEventListener("click", function () {
        addMaterialReceipt(item.id, card);
      });
      box.appendChild(card);
    });
  }

  function refreshMaterialSelectors() {
    if ($("materialUnit")) {
      $("materialUnit").innerHTML = materialUnitOptions(($("materialUnit").value || "шт"));
    }
    var materials = (materialsSnapshot && materialsSnapshot.materials) || [];
    var wagons = (materialsSnapshot && materialsSnapshot.wagons) || [];
    if ($("materialReserveMaterial")) {
      $("materialReserveMaterial").innerHTML =
        '<option value="">— выберите материал —</option>' +
        materials.map(function (item) {
          return '<option value="' + item.id + '">' + esc(item.name) + " · свободно " + esc(formatQty(item.available)) + " " + esc(item.unit) + '</option>';
        }).join("");
    }
    if ($("materialReserveWagon")) {
      $("materialReserveWagon").innerHTML =
        '<option value="">— выберите вагон —</option>' +
        wagons.map(function (wagon) {
          return '<option value="' + esc(wagon.number) + '">' + esc(wagon.number + " · " + (wagon.stage_label || "")) + '</option>';
        }).join("");
    }
  }

  function renderMaterialWagons(wagons) {
    var box = $("materialWagonList");
    if (!box) return;
    box.innerHTML = "";
    if (!(wagons || []).length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>Активных вагонов для мастера пока нет.</div></div>";
      return;
    }
    wagons.forEach(function (wagon) {
      var card = document.createElement("div");
      card.className = "tk-card tk-card--click";
      if (wagon.is_ready) card.classList.add("tk-material-wagon-card--ready");
      else if (wagon.shortage_count > 0) card.classList.add("tk-material-wagon-card--warn");
      card.innerHTML =
        '<div class="tk-material-card-top">' +
        '<div class="tk-card-title">Вагон ' + esc(wagon.number) + '</div>' +
        '<div class="tk-card-meta">' + esc(wagon.stage_label || "—") + '</div>' +
        '</div>' +
        '<div class="tk-card-meta">' + esc(wagon.location_label || "—") + '</div>' +
        '<div class="tk-card-meta">Резервов: ' + esc(wagon.reserved_item_count || 0) +
        " · дефицит: " + esc(wagon.shortage_count || 0) +
        (wagon.shortage_names && wagon.shortage_names.length ? " · " + esc(wagon.shortage_names.join(", ")) : "") +
        '</div>';
      card.addEventListener("click", function () {
        loadMaterialWagon(wagon.number);
      });
      box.appendChild(card);
    });
  }

  function loadMaterialsOverview(options) {
    options = options || {};
    return apiFetch("/api/taksimo/materials/overview")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        materialsSnapshot = data;
        meta.material_units = data.units || meta.material_units;
        renderMaterialSummary(data);
        renderMaterialList(data.materials || []);
        renderMaterialWagons(data.wagons || []);
        refreshMaterialSelectors();
        if (activeMaterialWagonNumber && !options.skipDetailRefresh) {
          return loadMaterialWagon(activeMaterialWagonNumber, { scroll: false });
        }
      })
      .catch(function (err) {
        var box = $("materialList");
        if (box) box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>Не удалось загрузить расходники</div></div>";
        if (err && err.message && err.message !== "auth") alert(err.message);
      });
  }

  function saveMaterialItem(materialId, root) {
    var body = {
      name: root.querySelector('[data-field="name"]').value.trim(),
      unit: root.querySelector('[data-field="unit"]').value,
      min_level: root.querySelector('[data-field="min_level"]').value || 0,
      norm_per_wagon: root.querySelector('[data-field="norm_per_wagon"]').value || 0,
    };
    apiFetch("/api/taksimo/materials/items/" + materialId, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Материал сохранён");
        loadMaterialsOverview();
      })
      .catch(function (err) { alert(err.message || "Ошибка сохранения"); });
  }

  function addMaterialReceipt(materialId, root) {
    var qtyEl = root.querySelector('[data-field="receipt_qty"]');
    var quantity = qtyEl ? qtyEl.value : "";
    apiFetch("/api/taksimo/materials/receipt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ material_id: materialId, quantity: quantity }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        if (qtyEl) qtyEl.value = "";
        toast("Приход сохранён");
        loadMaterialsOverview();
      })
      .catch(function (err) { alert(err.message || "Ошибка прихода"); });
  }

  function createMaterialItem() {
    var body = {
      name: $("materialName").value.trim(),
      unit: $("materialUnit").value,
      min_level: $("materialMin").value || 0,
      norm_per_wagon: $("materialNorm").value || 0,
    };
    apiFetch("/api/taksimo/materials/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        $("materialName").value = "";
        $("materialMin").value = "";
        $("materialNorm").value = "";
        $("materialUnit").value = "шт";
        toast("Материал добавлен");
        loadMaterialsOverview();
      })
      .catch(function (err) { alert(err.message || "Ошибка добавления"); });
  }

  function reserveMaterialForWagonUi() {
    var body = {
      wagon_number: $("materialReserveWagon").value,
      material_id: $("materialReserveMaterial").value,
      quantity: $("materialReserveQty").value,
      note: $("materialReserveNote").value.trim(),
    };
    apiFetch("/api/taksimo/materials/reserve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        activeMaterialWagonNumber = body.wagon_number;
        $("materialReserveQty").value = "";
        $("materialReserveNote").value = "";
        toast("Резерв создан");
        loadMaterialsOverview({ skipDetailRefresh: true }).then(function () {
          loadMaterialWagon(body.wagon_number, { scroll: true });
        });
      })
      .catch(function (err) { alert(err.message || "Ошибка резерва"); });
  }

  function loadMaterialWagon(wagonNumber, options) {
    options = options || {};
    if (!wagonNumber) return Promise.resolve();
    activeMaterialWagonNumber = wagonNumber;
    var box = $("materialWagonDetail");
    if (box) {
      box.hidden = false;
      box.innerHTML = "<h4>Вагон " + esc(wagonNumber) + "</h4><p>Загрузка расходников…</p>";
    }
    return apiFetch("/api/taksimo/materials/wagons/" + encodeURIComponent(wagonNumber))
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        var wagon = res.data.wagon;
        if (!box || !wagon) return;
        var items = wagon.items || [];
        var rowsHtml = items.length
          ? "<ul class='tk-list'>" +
            items.map(function (item) {
              return (
                "<li class='tk-card'>" +
                "<div class='tk-material-card-top'><div class='tk-card-title'>" + esc(item.name) + "</div>" +
                "<div class='tk-card-meta'>норма " + esc(formatQty(item.norm_per_wagon)) + " " + esc(item.unit) + "</div></div>" +
                "<div class='tk-card-meta'>резерв: " + esc(formatQty(item.reserved_qty)) + " · факт уже ушёл: " + esc(formatQty(item.consumed_qty)) + " · дефицит: " + esc(formatQty(item.shortage_qty)) + " " + esc(item.unit) + "</div>" +
                '<label class="tk-label">Факт списания</label>' +
                '<input class="tk-input" data-actual-material="' + item.material_id + '" type="number" min="0" step="0.001" value="' + esc(item.actual_default_qty) + '">' +
                "</li>"
              );
            }).join("") +
            "</ul>"
          : "<p class='tk-card-meta'>По этому вагону ещё нет нормативов или резервов.</p>";
        box.hidden = false;
        box.innerHTML =
          "<h4>Вагон " + esc(wagon.wagon_number) + "</h4>" +
          "<p>" + esc((wagon.wagon && wagon.wagon.stage_label) || "—") +
          ((wagon.wagon && wagon.wagon.location_label) ? " · " + esc(wagon.wagon.location_label) : "") +
          "<br>Резервов: " + esc(wagon.reserved_item_count || 0) +
          " · дефицит: " + esc(wagon.shortage_count || 0) +
          "</p>" +
          itemsHtmlSafe(rowsHtml) +
          (wagon.has_reserves
            ? '<div class="tk-actions"><button type="button" class="tk-btn tk-btn--primary" id="materialFinalizeBtn">Списать факт по вагону</button></div>'
            : "");
        var finalizeBtn = $("materialFinalizeBtn");
        if (finalizeBtn) {
          finalizeBtn.addEventListener("click", function () {
            finalizeMaterialWagon(wagon.wagon_number, items);
          });
        }
        if (options.scroll !== false) {
          box.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      })
      .catch(function (err) {
        if (box) {
          box.hidden = false;
          box.innerHTML = "<h4>Расходники вагона</h4><p>" + esc(err.message || "Не удалось загрузить вагон") + "</p>";
        }
      });
  }

  function itemsHtmlSafe(html) {
    return html;
  }

  function finalizeMaterialWagon(wagonNumber, items) {
    var payload = {
      items: (items || []).map(function (item) {
        var input = document.querySelector('[data-actual-material="' + item.material_id + '"]');
        return {
          material_id: item.material_id,
          actual_qty: input ? input.value : item.actual_default_qty,
        };
      }),
    };
    apiFetch("/api/taksimo/materials/wagons/" + encodeURIComponent(wagonNumber) + "/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Факт расхода списан");
        loadMaterialsOverview({ skipDetailRefresh: true }).then(function () {
          loadMaterialWagon(wagonNumber, { scroll: false });
        });
      })
      .catch(function (err) { alert(err.message || "Ошибка списания"); });
  }

  function loadWagonHistory() {
    return apiFetch("/api/taksimo/wagons/history?limit=80")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var list = $("wagonHistoryList");
        if (!list) return;
        list.innerHTML = "";
        var items = data.dispatches || [];
        if (!items.length) {
          list.innerHTML = "<li class='tk-card'><div class='tk-card-meta'>Рейсов вагонов пока нет</div></li>";
          return;
        }
        items.forEach(function (d) {
          var li = document.createElement("li");
          li.className = "tk-card tk-card--click";
          var badgeClass =
            d.status === "in_transit" ? "tk-dispatch-badge--transit" : "tk-dispatch-badge--done";
          var when =
            d.status === "delivered" && d.received_at_label
              ? d.received_at_label
              : d.dispatched_at_label || "";
          li.innerHTML =
            "<div class='tk-card-title'>Вагон " + esc(d.wagon_number) +
            " · " + esc(d.slot_zone || "") + " №" + d.slot_index +
            "<span class='tk-dispatch-badge " + badgeClass + "'>" +
            esc(dispatchStatusLabel(d.status)) + "</span></div>" +
            "<div class='tk-card-meta'>" + esc((d.block_labels || []).join(", ")) +
            (when ? " · " + esc(when) : "") +
            (d.dispatched_by ? " · " + esc(d.dispatched_by) : "") +
            "</div>";
          li.addEventListener("click", function () { showWagonDispatchDetail(d); });
          list.appendChild(li);
        });
      })
      .catch(function () {});
  }

  function showWagonDispatchDetail(d) {
    var box = $("wagonHistoryDetail");
    if (!box || !d) return;
    box.hidden = false;
    var blocks = (d.block_labels || []).join(", ");
    var blockRows = d.blocks || (d.block_labels || []).map(function (label) {
      return { label: label, vehicle_plate: "" };
    });
    var vehicleLines = formatVehicleBlockLines(blockRows);
    var html =
      "<h4>Вагон " + esc(d.wagon_number) + " · рейс №" + d.id + "</h4>" +
      "<p>Слот " + esc(d.slot_zone || "—") + " №" + d.slot_index + "<br>" +
      "Статус: <strong>" + esc(dispatchStatusLabel(d.status)) + "</strong><br>" +
      "Заказчик: " + esc(d.customer || "БТС Восток") + "<br>" +
      "Отправлен: " + esc(d.dispatched_at_label || "—") +
      (d.dispatched_by ? " · " + esc(d.dispatched_by) : "") + "<br>";
    if (d.received_at_label) {
      html += "Принят в Кодаре: " + esc(d.received_at_label) +
        (d.received_by ? " · " + esc(d.received_by) : "") + "<br>";
    }
    html += "</p><p><strong>Блоки (" + (d.slab_count || 9) + "):</strong><br>" +
      esc(blocks) + "</p>" +
      "<p><strong>Машины:</strong><br>" +
      vehicleLines.map(function (line) { return esc(line); }).join("<br>") +
      "</p>";
    if (d.status === "in_transit" && canKodar) {
      html +=
        '<div class="tk-actions">' +
        '<button type="button" class="tk-btn tk-btn--primary" id="wagonHistoryKodarBtn">Кодар получил</button>' +
        "</div>";
    }
    box.innerHTML = html;
    var kodarBtn = $("wagonHistoryKodarBtn");
    if (kodarBtn) {
      kodarBtn.addEventListener("click", function () {
        confirmKodarReceived(d.id, d.wagon_number).then(function () {
          box.hidden = true;
        });
      });
    }
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function loadStats() {
    return apiFetch("/api/taksimo/stats")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        meta.stats = data;
        meta.db = data.db || meta.db;
        renderStats(data);
        renderDbStatus(data.db);
      })
      .catch(function () {});
  }

  function letterOptions(selected) {
    return meta.letters
      .map(function (l) {
        return '<option value="' + l + '"' + (l === selected ? " selected" : "") + ">" + l + "</option>";
      })
      .join("");
  }

  function selectedVehicleInfo() {
    if (!selectedVehicleId) return null;
    for (var i = 0; i < meta.vehicles.length; i++) {
      if (meta.vehicles[i].id === selectedVehicleId) return meta.vehicles[i];
    }
    return null;
  }

  function updateVehicleBanner() {
    var el = $("vehicleBanner");
    if (!el) return;
    var v = selectedVehicleInfo();
    if (!v) {
      el.className = "tk-vehicle-banner tk-vehicle-banner--warn";
      el.textContent = "⚠️ Нажмите машину — без выбора сохранять нельзя";
      return;
    }
    el.className = "tk-vehicle-banner tk-vehicle-banner--ok";
    el.textContent = "Выгрузка: " + (v.plate || "—") + " · " + (v.driver || "—");
  }

  function confirmVehicleForSave() {
    var v = selectedVehicleInfo();
    if (!v) {
      alert("Сначала выберите машину — нажмите карточку с номером и водителем.");
      updateVehicleBanner();
      return false;
    }
    var typed = ($("driver").value || "").trim();
    var regDriver = (v.driver || "").trim();
    var msg =
      "Сохранить приём для:\n\n" +
      (v.plate || "—") +
      "\n" +
      (regDriver || typed || "—") +
      "\n\nВерно?";
    if (typed && regDriver && typed !== regDriver) {
      msg +=
        "\n\n⚠️ В поле «Водитель» указано: " +
        typed +
        "\nПроверьте, что выбрана нужная машина.";
    }
    return confirm(msg);
  }

  function renderVehicles() {
    var box = $("vehicleList");
    box.innerHTML = "";
    meta.vehicles.forEach(function (v) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tk-vehicle" + (selectedVehicleId === v.id ? " tk-vehicle--active" : "");
      btn.innerHTML =
        '<div class="tk-vehicle-plate">' + esc(v.plate) + "</div>" +
        '<div class="tk-vehicle-driver">' + esc(v.driver || v.brand || "—") + "</div>";
      btn.addEventListener("click", function () {
        selectedVehicleId = v.id;
        $("driver").value = v.driver || "";
        renderVehicles();
        updateVehicleBanner();
        cacheDraftForm();
      });
      box.appendChild(btn);
    });
    updateVehicleBanner();
  }

  function gridMaxX() {
    return meta.grid_x || meta.default_grid_x || 13;
  }

  function gridMaxY() {
    return meta.grid_y || meta.default_grid_y || 25;
  }

  function maxSlabsPerCell() {
    return meta.max_slabs_per_cell || 4;
  }

  function countExternalInCell(x, y) {
    var key = x + "/" + y;
    var list = (yardSnapshot && yardSnapshot.cells && yardSnapshot.cells[key]) || [];
    var sid = activeSessionId();
    return list.filter(function (s) {
      return !(sid && s.session_id === sid);
    }).length;
  }

  function formSlabsByCell() {
    var byCell = {};
    collectSlabs().forEach(function (s) {
      if (!needsCoords(s.platform_zone)) return;
      if (!s.pos_x || !s.pos_y) return;
      var key = s.pos_x + "/" + s.pos_y;
      byCell[key] = (byCell[key] || 0) + 1;
    });
    return byCell;
  }

  function cellPlacementMessage(x, y, formCount) {
    var maxCell = maxSlabsPerCell();
    var external = countExternalInCell(x, y);
    var total = external + formCount;
    if (formCount > maxCell) {
      return {
        level: "warn",
        text: "Ячейка " + x + "/" + y + ": в форме " + formCount + " плит — максимум " + maxCell,
      };
    }
    if (total > maxCell) {
      return {
        level: "warn",
        text:
          "Ячейка " + x + "/" + y + ": другие выгрузки " + external + ", вы " + formCount +
          " — всего " + total + ", максимум " + maxCell,
      };
    }
    if (formCount > 0) {
      var free = maxCell - total;
      return {
        level: free === 0 ? "ok" : "ok",
        text:
          "Ячейка " + x + "/" + y + ": на площадке " + external + ", вы " + formCount +
          (free > 0 ? ", останется " + free : ", ячейка заполнится"),
      };
    }
    return null;
  }

  function updateRowPlacementHint(row) {
    if (!row) return;
    var wrap = row.closest(".tk-slab-row-wrap");
    if (!wrap) return;
    var hint = wrap.querySelector(".tk-cell-hint");
    if (!hint) return;
    var zone = row.querySelector('[data-field="platform_zone"]').value;
    var pos_x = row.querySelector('[data-field="pos_x"]').value.trim();
    var pos_y = row.querySelector('[data-field="pos_y"]').value.trim();
    var number = row.querySelector('[data-field="number"]').value.trim();
    var wagon = (row.querySelector('[data-field="wagon_number"]') || {}).value || "";
    wagon = wagon.trim();
    if (needsWagon(zone) && number && number !== "0" && !wagon) {
      hint.hidden = false;
      hint.textContent = zone + ": укажите номер вагона";
      hint.className = "tk-cell-hint tk-cell-hint--warn";
      return;
    }
    if (!needsCoords(zone) || !pos_x || !pos_y || !number || number === "0") {
      hint.hidden = true;
      hint.textContent = "";
      hint.className = "tk-cell-hint";
      return;
    }
    var x = parseInt(pos_x, 10);
    var y = parseInt(pos_y, 10);
    if (!x || !y) {
      hint.hidden = true;
      return;
    }
    var byCell = formSlabsByCell();
    var msg = cellPlacementMessage(x, y, byCell[x + "/" + y] || 0);
    if (!msg) {
      hint.hidden = true;
      return;
    }
    hint.hidden = false;
    hint.textContent = msg.text;
    hint.className = "tk-cell-hint" + (msg.level === "warn" ? " tk-cell-hint--warn" : " tk-cell-hint--ok");
  }

  function updateAllPlacementHints() {
    document.querySelectorAll("#slabRows .tk-slab-row").forEach(updateRowPlacementHint);
  }

  function validatePlacementBeforeSave(slabs) {
    var maxCell = maxSlabsPerCell();
    var byCell = {};
    var errors = [];

    slabs.forEach(function (s) {
      if (needsWagon(s.platform_zone) && !(s.wagon_number || "").trim()) {
        errors.push(s.letter + s.number + ": для " + s.platform_zone + " укажите номер вагона");
      }
    });

    slabs.forEach(function (s) {
      if (!needsCoords(s.platform_zone)) return;
      if (!s.pos_x || !s.pos_y) return;
      var key = s.pos_x + "/" + s.pos_y;
      byCell[key] = (byCell[key] || 0) + 1;
    });

    Object.keys(byCell).forEach(function (key) {
      var parts = key.split("/");
      var x = parseInt(parts[0], 10);
      var y = parseInt(parts[1], 10);
      var formCount = byCell[key];
      if (formCount > maxCell) {
        errors.push("Ячейка " + key + ": в этой выгрузке " + formCount + " плит — максимум " + maxCell);
      }
      var external = countExternalInCell(x, y);
      if (external + formCount > maxCell) {
        errors.push(
          "Ячейка " + key + ": другие выгрузки " + external + ", вы добавляете " + formCount +
          " — всего " + (external + formCount) + ", максимум " + maxCell
        );
      }
    });
    return errors;
  }

  function findDuplicateSlabInDraft(slabs) {
    var seen = {};
    for (var i = 0; i < (slabs || []).length; i += 1) {
      var item = slabs[i] || {};
      var letter = String(item.letter || "").trim().toUpperCase();
      var number = String(item.number || "").trim();
      if (!letter || !number) continue;
      var key = letter + "|" + number;
      if (seen[key] != null) {
        return {
          label: letter + number,
          firstRow: seen[key] + 1,
          secondRow: i + 1,
        };
      }
      seen[key] = i;
    }
    return null;
  }

  function setSaveStatus(state, message) {
    var el = $("saveStatus");
    if (!el) return;
    if (state === "idle") {
      el.hidden = true;
      el.textContent = "";
      el.className = "tk-save-status";
      return;
    }
    el.hidden = false;
    el.className = "tk-save-status tk-save-status--" + state;
    if (state === "saving") {
      el.textContent = message || "Сохраняем…";
      return;
    }
    if (state === "error") {
      el.innerHTML =
        esc(message || "Не удалось сохранить.") +
        ' <button type="button" class="tk-link-btn" id="saveRetryBtn">Повторить</button>';
      var retryBtn = $("saveRetryBtn");
      if (retryBtn) {
        retryBtn.addEventListener("click", function () {
          if (lastSaveAction) saveUnload(lastSaveAction);
        });
      }
    }
  }

  function isNetworkError(err) {
    if (!err) return true;
    var msg = String(err.message || err);
    return (
      err.name === "TypeError" ||
      msg === "Failed to fetch" ||
      msg.indexOf("NetworkError") >= 0 ||
      msg.indexOf("Load failed") >= 0
    );
  }

  function startYardPoll() {
    stopYardPoll();
    var badge = $("yardLiveBadge");
    if (badge) badge.hidden = false;
    yardPollTimer = setInterval(function () {
      if (document.hidden || activeTab !== "yard") return;
      refreshYardPanel(true);
    }, YARD_POLL_MS);
  }

  function stopYardPoll() {
    if (yardPollTimer) {
      clearInterval(yardPollTimer);
      yardPollTimer = null;
    }
    var badge = $("yardLiveBadge");
    if (badge) badge.hidden = true;
  }

  function bindPlacementHintListeners(row) {
    ["pos_x", "pos_y", "number", "platform_zone"].forEach(function (field) {
      var input = row.querySelector('[data-field="' + field + '"]');
      if (!input) return;
      input.addEventListener("input", function () {
        updateAllPlacementHints();
      });
      input.addEventListener("change", function () {
        updateAllPlacementHints();
      });
    });
  }

  function buildSlabRow(letter, data) {
    data = data || {};
    var wrap = document.createElement("div");
    wrap.className = "tk-slab-row-wrap";
    var row = document.createElement("div");
    row.className = "tk-slab-row";
    if (data.id) row.dataset.id = data.id;
    row.innerHTML =
      '<select class="tk-select" data-field="letter">' +
      letterOptions(data.letter || letter) +
      "</select>" +
      '<input class="tk-input" type="text" inputmode="numeric" placeholder="номер" data-field="number" value="' +
      esc(data.number || "") +
      '">' +
      '<input class="tk-input" type="number" min="1" max="' + gridMaxX() + '" placeholder="X" data-field="pos_x" value="' +
      (data.pos_x || "") +
      '">' +
      '<input class="tk-input" type="number" min="1" max="' + gridMaxY() + '" placeholder="Y" data-field="pos_y" value="' +
      (data.pos_y || "") +
      '">' +
      '<select class="tk-select" data-field="suffix">' +
      suffixOptions(data.suffix || "") +
      "</select>" +
      '<select class="tk-select" data-field="platform_zone">' +
      platformOptions(data.platform_zone || $("defaultPlatform").value || "ХРАНЕНИЯ") +
      "</select>" +
      '<select class="tk-select tk-slab-wagon" data-field="wagon_number">' +
      wagonOptionsForZone(
        data.platform_zone || $("defaultPlatform").value || "ХРАНЕНИЯ",
        data.wagon_number ||
          ($("defaultWagon") && usesDefaultWagon(data.platform_zone || $("defaultPlatform").value) && $("defaultWagon").value) ||
          ""
      ) +
      "</select>";
    syncRowPlatformFields(row);
    row.querySelector('[data-field="platform_zone"]').addEventListener("change", function () {
      syncRowPlatformFields(row);
      updateAllPlacementHints();
    });
    row.querySelector('[data-field="wagon_number"]').addEventListener("change", function () {
      updateAllPlacementHints();
    });
    row.querySelector('[data-field="pos_x"]').addEventListener("focus", function () {
      pickTarget = row;
      pickSlabField = "pos";
      setTab("yard");
    });
    var hint = document.createElement("p");
    hint.className = "tk-cell-hint";
    hint.hidden = true;
    wrap.appendChild(row);
    wrap.appendChild(hint);
    bindPlacementHintListeners(row);
    ["wagon_number"].forEach(function (field) {
      var input = row.querySelector('[data-field="' + field + '"]');
      if (!input) return;
      input.addEventListener("input", function () {
        updateAllPlacementHints();
      });
      input.addEventListener("change", function () {
        updateAllPlacementHints();
      });
    });
    updateRowPlacementHint(row);
    return wrap;
  }

  function renderSlabRows(presetSlabs) {
    var box = $("slabRows");
    box.innerHTML = "";
    if (presetSlabs && presetSlabs.length) {
      presetSlabs.forEach(function (sl) {
        box.appendChild(buildSlabRow(sl.letter, sl));
      });
      box.appendChild(buildSlabRow("A", {}));
    } else {
      meta.letters.forEach(function (letter) {
        box.appendChild(buildSlabRow(letter, { letter: letter }));
      });
    }
    syncAllGridLimits();
    updateAllPlacementHints();
    updatePlatformHint();
  }

  function collectSlabs() {
    var slabs = [];
    var defaultZone = $("defaultPlatform").value || "ХРАНЕНИЯ";
    document.querySelectorAll("#slabRows .tk-slab-row").forEach(function (row) {
      var letter = row.querySelector('[data-field="letter"]').value.trim().toUpperCase();
      var number = row.querySelector('[data-field="number"]').value.trim();
      var pos_x = row.querySelector('[data-field="pos_x"]').value.trim();
      var pos_y = row.querySelector('[data-field="pos_y"]').value.trim();
      var suffix = row.querySelector('[data-field="suffix"]').value;
      var zone = row.querySelector('[data-field="platform_zone"]').value || defaultZone;
      var wagon_number = (row.querySelector('[data-field="wagon_number"]') || {}).value || "";
      wagon_number = wagon_number.trim();
      if (!number || number === "0") return;
      if (needsCoords(zone) && (!pos_x || !pos_y)) return;
      if (needsWagon(zone) && !wagon_number) return;
      var item = {
        letter: letter,
        number: number,
        suffix: suffix,
        platform_zone: zone,
        wagon_number: wagon_number,
        pos_x: needsCoords(zone) ? parseInt(pos_x, 10) : 0,
        pos_y: needsCoords(zone) ? parseInt(pos_y, 10) : 0,
      };
      if (row.dataset.id) item.id = parseInt(row.dataset.id, 10);
      slabs.push(item);
    });
    return slabs;
  }

  function showDraftBanner(id) {
    draftSessionId = id;
    $("draftBanner").hidden = false;
    $("draftSessionId").textContent = id;
  }

  function hideDraftBanner() {
    draftSessionId = null;
    $("draftBanner").hidden = true;
  }

  function activeSessionId() {
    return editingSessionId || draftSessionId;
  }

  function cacheDraftForm() {
    try {
      localStorage.setItem(
        DRAFT_CACHE_KEY,
        JSON.stringify({
          session_id: activeSessionId(),
          revision: editingRevision,
          unload_date: $("unloadDate").value,
          trn: $("trn").value,
          driver: $("driver").value,
          crane_start: $("craneStart").value,
          crane_end: $("craneEnd").value,
          notes: $("notes").value,
          vehicle_id: selectedVehicleId,
          default_platform: $("defaultPlatform").value,
          slabs: collectSlabs(),
        })
      );
    } catch (e) {}
  }

  function restoreDraftCache() {
    try {
      var raw = localStorage.getItem(DRAFT_CACHE_KEY);
      if (!raw) return;
      var data = JSON.parse(raw);
      if (!data || !data.slabs || !data.slabs.length) return;
      if (activeSessionId()) return;
      $("unloadDate").value = data.unload_date || todayIso();
      $("trn").value = data.trn || "";
      $("driver").value = data.driver || "";
      $("craneStart").value = data.crane_start || "";
      $("craneEnd").value = data.crane_end || "";
      $("notes").value = data.notes || "";
      if (data.default_platform) $("defaultPlatform").value = data.default_platform;
      /* Не восстанавливаем машину из кэша — после обновления страницы выбираете заново */
      selectedVehicleId = null;
      $("driver").value = "";
      renderVehicles();
      renderSlabRows(data.slabs);
      if (data.session_id) showDraftBanner(data.session_id);
      if (data.revision) editingRevision = data.revision;
      updatePlatformHint();
      toast("Черновик плит восстановлен — нажмите нужную машину");
    } catch (e) {}
  }

  function clearDraftCache() {
    try {
      localStorage.removeItem(DRAFT_CACHE_KEY);
    } catch (e) {}
  }

  function resetEditMode() {
    editingSessionId = null;
    editingRevision = null;
    hideDraftBanner();
    $("editBanner").hidden = true;
    renderSlabRows();
  }

  function startEditSession(s) {
    editingSessionId = s.id;
    editingRevision = s.revision || 1;
    if (s.status === "draft") {
      showDraftBanner(s.id);
      $("editBanner").hidden = true;
    } else {
      hideDraftBanner();
      $("editBanner").hidden = false;
      $("editSessionId").textContent = s.id;
    }
    $("unloadDate").value = s.unload_date;
    $("trn").value = s.trn || "";
    $("driver").value = s.driver || "";
    $("craneStart").value = s.crane_start || "";
    $("craneEnd").value = s.crane_end || "";
    $("notes").value = s.notes || "";
    if (s.slabs && s.slabs.length) {
      $("defaultPlatform").value = s.slabs[0].platform_zone || "ХРАНЕНИЯ";
      updatePlatformHint();
    }
    selectedVehicleId = s.vehicle_id || (s.vehicle ? s.vehicle.id : null);
    renderVehicles();
    renderSlabRows(s.slabs || []);
    updateVehicleBanner();
    setTab("unload");
    window.scrollTo(0, 0);
  }

  function fillSlabFromPick(x, y) {
    document.querySelectorAll(".tk-yard-cell").forEach(function (c) {
      c.classList.remove("tk-yard-cell--pick");
    });
    if (pickSlabField === "sheet") {
      $("slabX").value = x;
      $("slabY").value = y;
      pickSlabField = null;
      $("slabSheet").hidden = false;
      toast("Место " + x + "/" + y);
      return;
    }
    if (!pickTarget) return;
    pickTarget.querySelector('[data-field="pos_x"]').value = x;
    pickTarget.querySelector('[data-field="pos_y"]').value = y;
    var pickedRow = pickTarget;
    pickTarget = null;
    setTab("unload");
    updateRowPlacementHint(pickedRow);
    updateAllPlacementHints();
    toast("Место " + x + "/" + y);
  }

  function renderYard(data) {
    var grid = $("yardGrid");
    grid.innerHTML = "";
    grid.style.gridTemplateColumns = "repeat(" + data.grid_x + ", " + yardCellPx() + "px)";
    var cells = data.cells || {};
    var picking = pickTarget || pickSlabField;

    for (var y = 1; y <= data.grid_y; y++) {
      for (var x = data.grid_x; x >= 1; x--) {
        var key = x + "/" + y;
        var list = cells[key] || [];
        var cell = document.createElement("button");
        cell.type = "button";
        var maxCell = meta.max_slabs_per_cell || 4;
        cell.className = "tk-yard-cell" + (list.length ? " tk-yard-cell--busy" : "");
        if (list.length >= maxCell) cell.classList.add("tk-yard-cell--full");
        if (picking) cell.classList.add("tk-yard-cell--pick");
        cell.dataset.x = x;
        cell.dataset.y = y;
        if (list.length) {
          var countBadge = list.length > 1
            ? "<span class='tk-yard-count'>" + list.length + "</span>"
            : "";
          cell.innerHTML =
            countBadge +
            "<span class='tk-yard-slab'>" +
            esc(list.slice(0, 2).map(function (s) { return s.letter + s.number; }).join(" ")) +
            "</span><span class='tk-yard-place'>" + x + "/" + y + "</span>";
        } else {
          cell.textContent = x + "/" + y;
        }
        cell.addEventListener("click", function () {
          var cx = parseInt(this.dataset.x, 10);
          var cy = parseInt(this.dataset.y, 10);
          var key = cx + "/" + cy;
          var atCell = (cells[key] || []).length;
          var maxCell = meta.max_slabs_per_cell || 4;
          if (pickTarget || pickSlabField) {
            if (atCell >= maxCell) {
              alert("Ячейка " + cx + "/" + cy + ": максимум " + maxCell + " плиты");
              return;
            }
            fillSlabFromPick(cx, cy);
            return;
          }
          showYardDetail(cells[cx + "/" + cy] || [], cx, cy);
        });
        grid.appendChild(cell);
      }
    }
  }

  function isKodarLockedZone(zone) {
    return zone === "В КОДАР" || zone === "БТС ВОСТОК";
  }

  function slabActionButtons(s, onDone) {
    if (isKodarLockedZone(s.platform_zone)) {
      return '<span class="tk-card-meta">цикл Кодар</span>';
    }
    return (
      '<button type="button" class="tk-mini-btn" data-act="edit" data-id="' + s.id + '">Изменить</button>'
    );
  }

  function bindSlabEditButtons(root) {
    root.querySelectorAll('[data-act="edit"]').forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        openSlabSheet(parseInt(btn.dataset.id, 10));
      });
    });
  }

  function showYardDetail(list, x, y) {
    var box = $("yardDetail");
    if (!list.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    var html = "<h4>Место " + x + "/" + y + "</h4><ul class='tk-list'>";
    list.forEach(function (s) {
      html +=
        "<li class='tk-card tk-slab-item'><div><div class='tk-card-title'>" +
        esc(s.label) + (s.suffix ? " · " + esc(s.suffix) : "") +
        "</div><div class='tk-card-meta'>" + esc(s.unload_date || "") + "</div></div>" +
        slabActionButtons(s) + "</li>";
    });
    html += "</ul>";
    box.innerHTML = html;
    bindSlabEditButtons(box);
  }

  function setTab(name) {
    activeTab = name;
    closeAppMenu();
    document.querySelectorAll(".tk-tab").forEach(function (btn) {
      btn.classList.toggle("tk-tab--active", btn.dataset.tab === name);
    });
    Object.keys(panels).forEach(function (k) {
      if (panels[k]) panels[k].hidden = k !== name;
    });
    if (name === "yard") {
      refreshYardPanel();
      startYardPoll();
    } else {
      stopYardPoll();
    }
    if (name === "history") loadHistory(true);
    if (name === "wagons") {
      $("wagonHistoryDetail").hidden = true;
      loadWagonCards();
      loadWagonHistory();
    }
    if (name === "materials") {
      loadMaterialsOverview();
    }
    loadStats();
    if (name === "search") {
      var sq = $("searchQ");
      if (sq) {
        sq.focus();
        if (sq.value.trim()) sq.dispatchEvent(new Event("input"));
      }
    }
  }

  function applyKodarUi() {
    updateWagonKodarButtons(activeWagonSlot);
    if (wagonPlanData) renderKodarInTransit(wagonPlanData);
  }

  function applyDeleteUi() {
    var slabBtn = $("slabDelete");
    if (slabBtn) {
      slabBtn.disabled = !canDelete;
      slabBtn.title = canDelete ? "" : "Удаление — только у оператора 1";
    }
  }

  function loadCurrentUser() {
    return apiFetch("/api/taksimo/auth/me")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var el = $("currentUser");
        if (data.user) {
          canDelete = data.can_delete !== false;
          canKodar = data.can_kodar === true;
          if (el) {
        el.textContent = data.user;
        el.hidden = false;
          }
        }
        applyDeleteUi();
        applyKodarUi();
      })
      .catch(function () {
        applyDeleteUi();
        applyKodarUi();
      });
  }

  function loadMeta() {
    loadCurrentUser();
    loadStats();
    return apiFetch("/api/taksimo/meta").then(function (r) { return r.json(); }).then(function (data) {
      meta = Object.assign(meta, data);
      syncAllGridLimits();
      $("slabPlatform").innerHTML = platformOptions("ХРАНЕНИЯ");
      meta.db = data.db || meta.db;
      renderStats(data.stats);
      renderDbStatus(data.db);
      renderVehicles();
      renderSlabRows();
      var letterSel = $("slabLetter");
      letterSel.innerHTML = letterOptions("A");
      $("slabSuffix").innerHTML = suffixOptions("");
      if ($("materialUnit")) {
        $("materialUnit").innerHTML = materialUnitOptions("шт");
      }
    });
  }

  function loadYard(silent) {
    return apiFetch("/api/taksimo/yard")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        yardSnapshot = data;
        renderYard(data);
        if (!silent) updateAllPlacementHints();
        return data;
      });
  }

  function renderWagonPlan(data) {
    wagonPlanData = data || null;
    var box = $("wagonPlan");
    if (!box) return;
    box.innerHTML = "";
    var deadEnds = (data && data.dead_ends) || {};
    ["ТУРАН", "ГРУЗОВОЙ"].forEach(function (zone) {
      var section = document.createElement("div");
      section.className = "tk-dead-end";
      section.innerHTML = '<h4 class="tk-dead-end-title">' + esc(zone) + " · до 10 вагонов</h4>";
      var bar = document.createElement("div");
      bar.className = "tk-fleet-extras-bar";
      bar.innerHTML =
        '<p class="tk-fleet-extras-bar-title">' +
        esc(zone) +
        ' · допы по тупику · целые кольца вычтены</p>' +
        '<button type="button" class="tk-hint-star-btn tk-hint-star-btn--fleet" aria-label="Допы по тупику ' +
        esc(zone) +
        '">★★</button>';
      bar.querySelector("button").addEventListener("click", function (e) {
        e.stopPropagation();
        openFleetExtrasSheet(zone);
      });
      section.appendChild(bar);
      var slots = document.createElement("div");
      slots.className = "tk-wagon-slots";
      (deadEnds[zone] || []).forEach(function (slot) {
        slot.zone = zone;
        var card = document.createElement("div");
        card.className = "tk-wagon-slot";
        if (!slot.wagon_number) card.classList.add("tk-wagon-slot--empty");
        if (slot.slab_count) card.classList.add("tk-wagon-slot--busy");
        if (slot.is_complete) card.classList.add("tk-wagon-slot--complete");
        if (slot.logistics && slot.logistics.show_hint_star) card.classList.add("tk-wagon-slot--hint");
        card.dataset.slotId = String(slot.id);
        card.setAttribute("role", "button");
        card.tabIndex = 0;
        var blockList = (slot.slabs || []).map(function (s) {
          return s.letter + s.number;
        });
        var blocks = blockList.join(" ");
        var expected = (slot.expected_blocks || []).filter(function (item) {
          return blockList.indexOf(item) === -1;
        }).join(" ");
        var count = slot.slab_count || 0;
        var maxSlabs = slot.max_slabs || slotMaxSlabs(slot);
        var schemeBadge = slot.scheme_label
          ? '<span class="tk-wagon-slot-scheme">' + esc(slot.scheme_label) + "</span>"
          : "";
        var starBtn =
          slot.logistics && slot.logistics.show_hint_star
            ? '<button type="button" class="tk-hint-star-btn" aria-label="Подсказка по вагону">★</button>'
            : "";
        var inlineHints =
          slot.logistics && slot.logistics.show_hint_star
            ? '<div class="tk-wagon-slot-hints" hidden>' + wagonHintsHtml(slot.logistics) + "</div>"
            : "";
        card.innerHTML =
          starBtn +
          '<div class="tk-wagon-slot-head">' +
          '<span class="tk-wagon-slot-no">№' + slot.slot_index + "</span>" +
          '<span class="tk-wagon-slot-num">' + esc(slot.wagon_number || "+ вагон") + schemeBadge + "</span>" +
          '<span class="tk-wagon-count">' + count + "/" + maxSlabs + "</span>" +
          "</div>" +
          '<div class="tk-wagon-blocks">' + esc(blocks || "пусто") + "</div>" +
          (expected ? '<div class="tk-wagon-blocks">→ ' + esc(expected) + "</div>" : "") +
          inlineHints;
        var starEl = card.querySelector(".tk-hint-star-btn");
        if (starEl) {
          starEl.addEventListener("click", function (e) {
            e.stopPropagation();
            var hintsEl = card.querySelector(".tk-wagon-slot-hints");
            if (!hintsEl) return;
            var open = hintsEl.hidden;
            document.querySelectorAll(".tk-wagon-slot-hints").forEach(function (el) {
              el.hidden = true;
            });
            hintsEl.hidden = !open;
          });
        }
        card.addEventListener("click", function () {
          document.querySelectorAll(".tk-wagon-slot-hints").forEach(function (el) {
            el.hidden = true;
          });
          toggleWagonSlotSheet(slot);
        });
        card.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggleWagonSlotSheet(slot);
          }
        });
        slots.appendChild(card);
      });
      section.appendChild(slots);
      box.appendChild(section);
    });
    renderFleetPlannedZones(data);
    renderKodarInTransit(data);
  }

  function renderFleetPlannedZones(data) {
    var box = $("wagonFleetList");
    if (!box) return;
    box.innerHTML = "";
    var fleet = (data && (data.fleet || data.wagon_pool)) || [];
    var items = fleet.filter(function (w) {
      return w && (w.stage === "returning" || w.stage === "available");
    });
    if (!items.length) {
      box.innerHTML = '<p class="tk-yard-legend">Нет свободных вагонов в парке.</p>';
      return;
    }
    items.forEach(function (w) {
      var row = document.createElement("div");
      row.className = "tk-fleet-row";
      var stage = w.stage_label || w.stage || "";
      row.innerHTML =
        '<span class="tk-fleet-num">' + esc(w.number) + "</span>" +
        '<span class="tk-fleet-stage">' + esc(stage) + "</span>" +
        '<label class="tk-fleet-zone-label">Тупик<select class="tk-input tk-fleet-zone" data-wagon="' +
        esc(w.number) +
        '">' +
        '<option value="">—</option>' +
        '<option value="ТУРАН"' +
        (w.planned_zone === "ТУРАН" ? " selected" : "") +
        ">ТУРАН</option>" +
        '<option value="ГРУЗОВОЙ"' +
        (w.planned_zone === "ГРУЗОВОЙ" ? " selected" : "") +
        ">ГРУЗОВОЙ</option>" +
        "</select></label>";
      row.querySelector("select").addEventListener("change", function (e) {
        var zone = e.target.value;
        if (!zone) return;
        saveWagonPlannedZone(w.number, zone, e.target);
      });
      box.appendChild(row);
    });
  }

  function saveWagonPlannedZone(number, zone, selectEl) {
    apiFetch("/api/taksimo/wagons/pool/" + encodeURIComponent(number) + "/planned-zone", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ planned_zone: zone }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Тупик " + zone + " для " + number);
        loadWagonPlan().then(refreshAllRowWagonSelects);
      })
      .catch(function (err) {
        alert(err.message);
        if (selectEl) loadWagonPlan();
      });
  }

  function renderKodarInTransit(data) {
    var wrap = $("wagonInTransitWrap");
    var box = $("wagonInTransit");
    if (!wrap || !box) return;
    var list = (data && data.kodar_in_transit) || [];
    if (!list.length) {
      wrap.hidden = true;
      box.innerHTML = "";
      return;
    }
    wrap.hidden = false;
    box.innerHTML = "";
    list.forEach(function (d) {
      var card = document.createElement("div");
      card.className = "tk-in-transit-card";
      var blocks = (d.block_labels || []).join(", ");
      card.innerHTML =
        "<div><strong>" + esc(d.wagon_number) + "</strong> · " + esc(d.dispatched_at_label || "") +
        "</div><div class='tk-card-meta'>" + esc(blocks) + "</div>";
      if (canKodar) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "tk-btn tk-btn--primary";
        btn.textContent = "Кодар получил";
        btn.addEventListener("click", function () {
          confirmKodarReceived(d.id, d.wagon_number);
        });
        card.appendChild(btn);
      }
      box.appendChild(card);
    });
  }

  function wagonHintsHtml(log) {
    if (!log || !log.hints || !log.hints.length) return "";
    return (
      '<ul class="tk-wagon-logistics-list">' +
      log.hints
        .map(function (h) {
          return "<li>" + esc(h) + "</li>";
        })
        .join("") +
      "</ul>"
    );
  }

  function wagonRingComplete(slot) {
    if (!slot) return false;
    if (slot.ring_complete === true) return true;
    return !!(slot.logistics && slot.logistics.is_complete);
  }

  function slotMaxSlabs(slot) {
    if (!slot) return 9;
    return slot.max_slabs || (slot.logistics && slot.logistics.max_slabs) || 9;
  }

  var pendingWagonSlotSave = null;

  function slotAssignOptions(slot) {
    var zone = slot.zone || "";
    var assign = (wagonPlanData && wagonPlanData.assignable && wagonPlanData.assignable[zone]) || [];
    var slotAssign = assign.find(function (a) { return a.slot_id === slot.id; });
    return (slotAssign && slotAssign.options) || [];
  }

  function wagonOptionLabel(w) {
    var s = w.number || "";
    if (w.planned_zone) s += " →" + w.planned_zone;
    if (w.stage === "returning") s += " (порожн.)";
    return s;
  }

  function closeWagonSlotSheet() {
    $("wagonSlotSheet").hidden = true;
    activeWagonSlot = null;
    updateWagonSlotActiveUi();
  }

  function updateWagonSlotActiveUi() {
    var openId = activeWagonSlot && !$("wagonSlotSheet").hidden ? String(activeWagonSlot.id) : "";
    document.querySelectorAll(".tk-wagon-slot").forEach(function (btn) {
      btn.classList.toggle("tk-wagon-slot--active", openId && btn.dataset.slotId === openId);
    });
  }

  function toggleWagonSlotSheet(slot) {
    if (!$("wagonSlotSheet").hidden && activeWagonSlot && activeWagonSlot.id === slot.id) {
      closeWagonSlotSheet();
      return;
    }
    openWagonSlotSheet(slot);
  }

  function findWagonSlot(slotId) {
    if (!wagonPlanData || !wagonPlanData.dead_ends) return null;
    var zones = ["ТУРАН", "ГРУЗОВОЙ"];
    for (var zi = 0; zi < zones.length; zi++) {
      var list = wagonPlanData.dead_ends[zones[zi]] || [];
      for (var i = 0; i < list.length; i++) {
        if (list[i].id === slotId) {
          var slot = list[i];
          slot.zone = zones[zi];
          return slot;
        }
      }
    }
    return null;
  }

  function renderWagonSlotSlabs(slot) {
    var list = $("wagonSlotSlabs");
    var empty = $("wagonSlotSlabsEmpty");
    var wrap = $("wagonSlotSlabsWrap");
    if (!list || !wrap) return;
    if (!slot || !slot.wagon_number) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    list.innerHTML = "";
    var slabs = slot.slabs || [];
    if (!slabs.length) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    slabs.forEach(function (s) {
      var li = document.createElement("li");
      li.className = "tk-card tk-slab-item";
      var meta = [];
      if (s.unload_date) meta.push(s.unload_date);
      if (s.vehicle_plate) meta.push(s.vehicle_plate);
      if (s.platform_zone) meta.push(s.platform_zone);
      li.innerHTML =
        "<div><div class='tk-card-title'>" + esc(s.label || (s.letter + " " + s.number)) +
        (s.suffix ? " · " + esc(s.suffix) : "") +
        "</div><div class='tk-card-meta'>" + esc(meta.join(" · ")) + "</div></div>" +
        slabActionButtons(s);
      list.appendChild(li);
    });
    bindSlabEditButtons(list);
  }

  function renderWagonSlotLogistics(slot) {
    var box = $("wagonSlotLogistics");
    if (!box) return;
    var log = slot && slot.logistics;
    if (!log || !log.hints || !log.hints.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    var title = (slot && slot.scheme_label) || "Схема 1";
    if (log.scheme_code === "scheme2") title = "Схема 2 (K)";
    if (log.scheme_code === "scheme3") title = "Схема 3 (A–F + ящик)";
    box.innerHTML =
      '<p class="tk-wagon-logistics-title"><span class="tk-hint-star" aria-hidden="true">★</span> ' +
      esc(title) + "</p><ul class='tk-wagon-logistics-list'>" +
      log.hints.map(function (h) {
        return "<li>" + esc(h) + "</li>";
      }).join("") +
      "</ul>";
  }

  function openWagonSlotSheet(slot, opts) {
    opts = opts || {};
    activeWagonSlot = slot;
    $("wagonSlotSheet").hidden = false;
    updateWagonSlotActiveUi();
    $("wagonSlotTitle").textContent = slot.zone + " · слот №" + slot.slot_index;
    if (slot.wagon_number) {
      $("wagonSlotTitle").textContent += " · " + slot.wagon_number;
    }
    $("wagonSlotHint").textContent = slot.wagon_number
      ? "Вагон " +
        slot.wagon_number +
        (slot.scheme_label ? " · " + slot.scheme_label : "") +
        (slot.slab_count
          ? " · блоков " + slot.slab_count + "/" + slotMaxSlabs(slot)
          : " · пусто")
      : "Пустой слот — выберите вагон из парка";
    var schemeEl = $("wagonSlotScheme");
    if (schemeEl) {
      if (slot.wagon_number && slot.scheme_label) {
        schemeEl.hidden = false;
        schemeEl.textContent =
          "Схема: " +
          slot.scheme_label +
          (slot.has_box ? " · с ящиком" : "") +
          " · максимум " +
          slotMaxSlabs(slot) +
          " блоков";
      } else {
        schemeEl.hidden = true;
        schemeEl.textContent = "";
      }
    }
    renderWagonSlotSlabs(slot);
    renderWagonSlotLogistics(slot);
    if (!opts.keepSettings) {
      $("wagonSlotExpected").value = (slot.expected_blocks || []).join(", ");
      var sel = $("wagonSlotPick");
      var pickOpts = ['<option value="">— пусто —</option>'];
      slotAssignOptions(slot).forEach(function (w) {
        var num = w.number || "";
        pickOpts.push(
          '<option value="' + esc(num) + '"' + (num === slot.wagon_number ? " selected" : "") + ">" +
          esc(wagonOptionLabel(w)) + "</option>"
        );
      });
      if (slot.wagon_number && !slotAssignOptions(slot).some(function (w) { return w.number === slot.wagon_number; })) {
        pickOpts.push(
          '<option value="' + esc(slot.wagon_number) + '" selected>' + esc(slot.wagon_number) + "</option>"
        );
      }
      sel.innerHTML = pickOpts.join("");
    }
    updateWagonKodarButtons(slot);
  }

  function closeWagonSchemeSheet() {
    $("wagonSchemeSheet").hidden = true;
    pendingWagonSlotSave = null;
  }

  function openWagonSchemeSheet() {
    var pending = pendingWagonSlotSave;
    var hint = $("wagonSchemeHint");
    if (hint && pending) {
      hint.textContent =
        "Вагон " + pending.wagon + " — выберите схему погрузки перед постановкой в слот.";
    }
    $("wagonSchemeSheet").hidden = false;
  }

  function doSaveWagonSlot(wagon, expected, schemeCode, clearSlot) {
    if (!activeWagonSlot) return;
    var body = { wagon_number: wagon, expected_blocks: expected };
    if (schemeCode) body.scheme_code = schemeCode;
    apiFetch("/api/taksimo/wagons/slots/" + activeWagonSlot.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        closeWagonSchemeSheet();
        toast(clearSlot ? "Слот освобождён" : "Слот обновлён");
        var slotId = activeWagonSlot.id;
        loadWagonPlan().then(function () {
          var slot = findWagonSlot(slotId);
          if (slot) openWagonSlotSheet(slot, { keepSettings: true });
          else closeWagonSlotSheet();
        });
        loadStats();
        refreshAllRowWagonSelects();
      })
      .catch(function (err) { alert(err.message); });
  }

  function saveWagonSlot(clearSlot) {
    if (!activeWagonSlot) return;
    var wagon = clearSlot ? "" : ($("wagonSlotPick") && $("wagonSlotPick").value || "").trim();
    var expectedRaw = ($("wagonSlotExpected") && $("wagonSlotExpected").value || "").trim();
    var expected = expectedRaw.split(/[,;\s]+/).map(function (x) { return x.trim(); }).filter(Boolean);
    if (clearSlot) {
      doSaveWagonSlot("", expected, null, true);
      return;
    }
    var prevWagon = (activeWagonSlot.wagon_number || "").trim();
    if (wagon && wagon !== prevWagon) {
      pendingWagonSlotSave = { wagon: wagon, expected: expected };
      openWagonSchemeSheet();
      return;
    }
    doSaveWagonSlot(wagon, expected, null, false);
  }

  function updateWagonKodarButtons(slot) {
    var dispatchBtn = $("wagonDispatchKodar");
    if (!dispatchBtn) return;
    if (!canKodar || !slot || !slot.wagon_number) {
      dispatchBtn.hidden = true;
      return;
    }
    dispatchBtn.hidden = false;
    var ringOk = wagonRingComplete(slot);
    var maxSlabs = slotMaxSlabs(slot);
    var fullCount = slot.slab_count === maxSlabs;
    dispatchBtn.classList.remove("tk-btn--primary", "tk-btn--warn", "tk-btn--blocked");
    var schemeLabel = slot.scheme_label || "Схема 1";
    if (ringOk) {
      dispatchBtn.disabled = false;
      dispatchBtn.classList.add("tk-btn--primary");
      dispatchBtn.textContent = "В Кодар";
      dispatchBtn.title =
        "Отправить полный комплект " + maxSlabs + "/" + maxSlabs + " в Кодар (" + schemeLabel + ")";
    } else if (fullCount) {
      dispatchBtn.disabled = false;
      dispatchBtn.classList.add("tk-btn--blocked");
      dispatchBtn.textContent = "В Кодар";
      var missing = (slot.logistics && slot.logistics.ring_letters_missing) || [];
      var missHint =
        missing.length ? " — не хватает: " + missing.join(", ") : " — комплект не соответствует схеме";
      dispatchBtn.title = schemeLabel + missHint + ". На Кодар не отправлять";
    } else {
      dispatchBtn.disabled = true;
      dispatchBtn.classList.add("tk-btn--blocked");
      dispatchBtn.textContent = "В Кодар";
      dispatchBtn.title =
        "Нужно " + maxSlabs + "/" + maxSlabs + " блоков (" + schemeLabel + ") перед отправкой в Кодар";
    }
  }

  function dispatchWagonToKodar() {
    if (!canKodar || !activeWagonSlot) return;
    var maxSlabs = slotMaxSlabs(activeWagonSlot);
    var ringOk = wagonRingComplete(activeWagonSlot);
    if (activeWagonSlot.slab_count !== maxSlabs) {
      alert("Нужно " + maxSlabs + "/" + maxSlabs + " блоков перед отправкой в Кодар");
      return;
    }
    if (ringOk) {
      if (
        !confirm(
          "Отправить вагон " +
            activeWagonSlot.wagon_number +
            " в Кодар?\nСлот освободится, в чат уйдёт сообщение."
        )
      ) {
        return;
      }
    }
    apiFetch("/api/taksimo/wagons/slots/" + activeWagonSlot.id + "/dispatch-kodar", {
      method: "POST",
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok && res.data && res.data.blocked) {
          toast("На Кодар нельзя — уведомление в чат менеджеров");
          updateWagonKodarButtons(activeWagonSlot);
          return;
        }
        if (!res.ok) throw new Error((res.data && res.data.error) || "Ошибка");
        toast("Вагон отправлен в Кодар");
        closeWagonSlotSheet();
        loadWagonPlan();
        loadStats();
        loadYard(true);
      })
      .catch(function (err) { alert(err.message); });
  }

  function fleetExtrasHintsHtml(extras) {
    var hints = (extras && extras.hints) || [];
    if (!hints.length) {
      return '<p class="tk-yard-legend">Нет целых колец — допы появятся после закрытия кольца A–K на вагоне.</p>';
    }
    return (
      '<ul class="tk-wagon-logistics-list">' +
      hints
        .map(function (h) {
          return "<li>" + esc(h) + "</li>";
        })
        .join("") +
      "</ul>"
    );
  }

  function closeFleetExtrasSheet() {
    var sheet = $("fleetExtrasSheet");
    if (sheet) sheet.hidden = true;
  }

  function openFleetExtrasSheet(zone) {
    closeAppMenu();
    var sheet = $("fleetExtrasSheet");
    var body = $("fleetExtrasBody");
    var title = $("fleetExtrasTitle");
    var hint = $("fleetExtrasHint");
    if (!sheet || !body) return;
    zone = zone || "ТУРАН";
    if (title) title.textContent = "★★ Допы · " + zone;
    if (hint) hint.textContent = zone + ": целые кольца вычтены — остаток допы A–F и счётчик K.";
    sheet.hidden = false;
    body.innerHTML = "<p class='tk-yard-legend'>Считаем допы…</p>";
    apiFetch("/api/taksimo/wagons/fleet-extras?zone=" + encodeURIComponent(zone))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        body.innerHTML = fleetExtrasHintsHtml(data.extras);
      })
      .catch(function () {
        body.innerHTML = "<p class='tk-yard-legend'>Не удалось загрузить допы.</p>";
      });
  }

  function syncOpenWagonSlotSheet() {
    if (!activeWagonSlot || $("wagonSlotSheet").hidden) return;
    var slot = findWagonSlot(activeWagonSlot.id);
    if (slot) {
      activeWagonSlot = slot;
      openWagonSlotSheet(slot, { keepSettings: true });
    }
  }

  function refreshYardPanel(silent) {
    return Promise.all([loadYard(silent), loadWagonPlan()]).then(function () {
      syncOpenWagonSlotSheet();
    });
  }

  function loadWagonPlan() {
    return apiFetch("/api/taksimo/wagons/plan")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderWagonPlan(data);
        if (activeTab === "materials") loadMaterialsOverview({ skipDetailRefresh: true });
        return data;
      })
      .catch(function () {});
  }

  function appendHistorySessions(sessions) {
      var list = $("historyList");
    if (!list) return;
    (sessions || []).forEach(function (s) {
        var li = document.createElement("li");
        li.className = "tk-card tk-card--click";
      li.dataset.sessionId = String(s.id);
        var statusMark = s.status === "draft" ? " · черновик" : "";
        li.innerHTML =
          "<div class='tk-card-title'>" + esc(s.unload_date) + " · ТРН " + esc(s.trn || "—") + " · №" + s.id + statusMark + "</div>" +
          "<div class='tk-card-meta'>" + esc(s.vehicle_plate || s.driver || "") + " · плит: " + s.slab_count +
        (s.wagon_numbers ? " · вагон " + esc(s.wagon_numbers) : "") +
          (s.operator ? " · " + esc(s.operator) : "") +
          (s.crane_minutes ? " · кран " + s.crane_minutes + " мин" : "") + "</div>";
        li.addEventListener("click", function () { loadSessionDetail(s.id); });
        list.appendChild(li);
      });
  }

  function updateHistoryMoreUi(data) {
    var wrap = $("historyMoreWrap");
    var meta = $("historyCountMeta");
    var btn = $("historyLoadMore");
    if (!wrap || !meta || !btn) return;
    var shown = $("historyList") ? $("historyList").children.length : 0;
    historyTotal = data.total != null ? data.total : shown;
    if (shown <= 0) {
      wrap.hidden = true;
      meta.textContent = "";
      return;
    }
    meta.textContent = "Показано " + shown + " из " + historyTotal;
    if (data.has_more) {
      wrap.hidden = false;
      btn.hidden = false;
      btn.disabled = false;
      btn.textContent = "Показать ещё";
    } else {
      wrap.hidden = false;
      btn.hidden = true;
      if (shown < historyTotal) {
        meta.textContent = "Показано " + shown + " из " + historyTotal;
      } else if (historyTotal > HISTORY_PAGE_SIZE) {
        meta.textContent = "Все " + historyTotal + " записей загружены";
      } else {
        meta.textContent = "Записей: " + historyTotal;
      }
    }
  }

  function loadHistory(reset) {
    if (historyLoading) return Promise.resolve();
    if (reset !== false) {
      historyOffset = 0;
      var list = $("historyList");
      if (list) {
        list.innerHTML =
          "<li class='tk-card'><div class='tk-card-meta'>Загрузка журнала…</div></li>";
      }
      var detail = $("historyDetail");
      if (detail) detail.hidden = true;
    }
    historyLoading = true;
    var btn = $("historyLoadMore");
    if (btn && historyOffset > 0) {
      btn.disabled = true;
      btn.textContent = "Загрузка…";
    }
    return apiFetch(
      "/api/taksimo/sessions?limit=" + HISTORY_PAGE_SIZE + "&offset=" + historyOffset
    )
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var sessions = data.sessions || [];
        if (historyOffset === 0) {
          var listEl = $("historyList");
          if (listEl) listEl.innerHTML = "";
        }
        appendHistorySessions(sessions);
        historyOffset += sessions.length;
        if (!sessions.length && historyOffset === 0) {
          $("historyList").innerHTML =
            "<li class='tk-card'><div class='tk-card-meta'>Выгрузок пока нет</div></li>";
          $("historyMoreWrap").hidden = true;
          return;
        }
        updateHistoryMoreUi(data);
      })
      .catch(function (err) {
        if (historyOffset === 0 && $("historyList")) {
          $("historyList").innerHTML =
            "<li class='tk-card'><div class='tk-card-meta'>Не удалось загрузить журнал</div></li>";
        }
        if (err && err.message !== "auth") alert(err.message || "Ошибка загрузки журнала");
        if (btn) {
          btn.disabled = false;
          btn.textContent = "Показать ещё";
        }
      })
      .finally(function () {
        historyLoading = false;
      });
  }

  function loadMoreHistory() {
    if (historyLoading) return;
    loadHistory(false);
  }

  function loadSessionDetail(id) {
    apiFetch("/api/taksimo/sessions/" + id).then(function (r) { return r.json(); }).then(function (data) {
      var s = data.session;
      if (!s) return;
      var box = $("historyDetail");
      box.hidden = false;
      var html =
        "<h4>Выгрузка №" + s.id + " · " + esc(s.unload_date) + "</h4>" +
        "<p>" + esc(s.vehicle ? s.vehicle.plate : "") + " · " + esc(s.driver) +
        (s.operator ? " · " + esc(s.operator) : "") +
        (s.wagon_numbers ? "<br>Вагон: " + esc(s.wagon_numbers) : "") +
        "<br>Кран " + esc(s.crane_start) + "–" + esc(s.crane_end) +
        (s.crane_minutes ? " (" + s.crane_minutes + " мин)" : "") + "</p>" +
        '<div class="tk-actions">' +
        '<button type="button" class="tk-btn tk-btn--primary" id="btnEditSession">Редактировать</button>' +
        '<button type="button" class="tk-btn tk-btn--danger" id="btnDeleteSession"' +
        (canDelete ? "" : ' disabled title="Удаление — только у оператора 1"') +
        ">Удалить выгрузку</button>" +
        "</div><ul class='tk-list'>";
      (s.slabs || []).forEach(function (sl) {
        html +=
          "<li class='tk-card tk-slab-item'><div><div class='tk-card-title'>" +
          esc(sl.label) + " → " + esc(sl.location || sl.place) + (sl.suffix ? esc(sl.suffix) : "") +
          (sl.wagon_number && sl.platform_zone !== "БТС ВОСТОК" ? " · вагон " + esc(sl.wagon_number) : "") +
          "</div></div>" + slabActionButtons(sl) + "</li>";
      });
      html += "</ul>";
      box.innerHTML = html;
      bindSlabEditButtons(box);
      $("btnEditSession").addEventListener("click", function () { startEditSession(s); });
      $("btnDeleteSession").addEventListener("click", function () {
        if (!canDelete) return;
        if (!confirm("Удалить выгрузку №" + s.id + " и все плиты?")) return;
        apiFetch("/api/taksimo/sessions/" + s.id, { method: "DELETE" })
          .then(function (r) {
            return r.json().then(function (body) {
              if (!r.ok) throw new Error(body.error || "Ошибка удаления");
              return body;
            });
          })
          .then(function () {
            toast("Выгрузка удалена");
            box.hidden = true;
            loadHistory();
            refreshYardPanel(true);
          })
          .catch(function (err) { alert(err.message || "Ошибка удаления"); });
      });
      box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  function openSlabSheet(slabId, preset) {
    preset = preset || {};
    $("slabSheet").hidden = false;
    $("slabEditId").value = slabId || "";
    if (slabId) {
      apiFetch("/api/taksimo/slabs/" + slabId).then(function (r) { return r.json(); }).then(function (data) {
        var sl = data.slab;
        $("slabSheetTitle").textContent = "Плита " + sl.label;
        $("slabLetter").innerHTML = letterOptions(sl.letter);
        $("slabNumber").value = sl.number;
        $("slabX").value = sl.pos_x;
        $("slabY").value = sl.pos_y;
        $("slabSuffix").innerHTML = suffixOptions(sl.suffix || "");
        $("slabPlatform").innerHTML = platformOptions(sl.platform_zone || "ХРАНЕНИЯ");
        $("slabWagon").innerHTML = slotWagonOptionsForZone(sl.platform_zone || "ХРАНЕНИЯ", sl.wagon_number || "");
        $("slabWagon").value = sl.wagon_number || "";
        $("slabLoading").value = sl.loading_date || "";
        refreshSlabSheetFields();
        $("slabPlatform").dataset.prevZone = $("slabPlatform").value || "ХРАНЕНИЯ";
      });
    } else {
      $("slabSheetTitle").textContent = "Новая координата";
      $("slabLetter").innerHTML = letterOptions(preset.letter || "A");
      $("slabNumber").value = preset.number || "";
      $("slabX").value = preset.pos_x || "";
      $("slabY").value = preset.pos_y || "";
      $("slabSuffix").innerHTML = suffixOptions("");
      $("slabPlatform").innerHTML = platformOptions("ХРАНЕНИЯ");
      $("slabWagon").innerHTML = '<option value="">— вагон из слота —</option>';
      $("slabWagon").value = "";
      $("slabLoading").value = "";
      refreshSlabSheetFields();
      $("slabPlatform").dataset.prevZone = $("slabPlatform").value || "ХРАНЕНИЯ";
    }
  }

  $("slabPlatform").addEventListener("change", handleSlabPlatformChange);
  $("defaultPlatform").addEventListener("change", applyDefaultPlatformToRows);
  $("defaultWagon").addEventListener("change", function () {
    var w = this.value;
    if (!w) return;
    document.querySelectorAll("#slabRows .tk-slab-row").forEach(function (row) {
      var zone = row.querySelector('[data-field="platform_zone"]').value;
      if (!usesDefaultWagon(zone)) return;
      var ws = row.querySelector('[data-field="wagon_number"]');
      if (ws && !ws.disabled) ws.value = w;
    });
    updateAllPlacementHints();
  });
  $("wagonCardSearch").addEventListener("input", function () {
    clearTimeout(wagonCardSearchTimer);
    wagonCardSearchTimer = setTimeout(function () {
      activeWagonCardNumber = "";
      loadWagonCards();
    }, 180);
  });
  $("materialCreateBtn").addEventListener("click", createMaterialItem);
  $("materialReserveBtn").addEventListener("click", reserveMaterialForWagonUi);

  function closeSlabSheet() {
    $("slabSheet").hidden = true;
    pickSlabField = null;
  }

  $("slabSheetBackdrop").addEventListener("click", closeSlabSheet);
  $("slabCancel").addEventListener("click", closeSlabSheet);

  $("slabX").addEventListener("focus", function () {
    pickSlabField = "sheet";
    setTab("yard");
  });

  $("slabForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var id = $("slabEditId").value;
    if (!id) return;
    var zone = $("slabPlatform").value;
    if (needsWagon(zone) && !$("slabWagon").value.trim()) {
      alert(zone + ": выберите вагон из слота");
      return;
    }
    var body = {
      letter: $("slabLetter").value,
      number: $("slabNumber").value.trim(),
      pos_x: needsCoords(zone) ? parseInt($("slabX").value, 10) : 0,
      pos_y: needsCoords(zone) ? parseInt($("slabY").value, 10) : 0,
      suffix: $("slabSuffix").value,
      platform_zone: zone,
      wagon_number: $("slabWagon").value.trim(),
      loading_date: $("slabLoading").value,
    };
    apiFetch("/api/taksimo/slabs/" + id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Плита обновлена");
        closeSlabSheet();
        refreshYardPanel();
        loadHistory();
        loadStats();
        apiFetch("/api/taksimo/slabs/" + id).then(function (r) { return r.json(); }).then(function (d) {
          if (d.slab && d.slab.session_id && !$("historyDetail").hidden) {
            loadSessionDetail(d.slab.session_id);
          }
        });
      })
      .catch(function (err) { alert(err.message); });
  });

  $("slabOffYard").addEventListener("click", function () {
    var id = $("slabEditId").value;
    if (!id || !confirm("Убрать плиту с площадки (останется в журнале)?")) return;
    apiFetch("/api/taksimo/slabs/" + id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        letter: $("slabLetter").value,
        number: $("slabNumber").value.trim(),
        pos_x: 0,
        pos_y: 0,
        suffix: $("slabSuffix").value,
        platform_zone: "В ПУТИ",
      }),
    }).then(function () {
      toast("Убрано с площадки");
      closeSlabSheet();
      refreshYardPanel();
      loadStats();
    });
  });

  $("slabDelete").addEventListener("click", function () {
    if (!canDelete) return;
    var id = $("slabEditId").value;
    if (!id || !confirm("Удалить плиту навсегда?")) return;
    apiFetch("/api/taksimo/slabs/" + id, { method: "DELETE" })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "Ошибка удаления");
          return body;
        });
      })
      .then(function () {
      toast("Плита удалена");
      closeSlabSheet();
        refreshYardPanel();
      loadHistory();
        loadStats();
      $("historyDetail").hidden = true;
      })
      .catch(function (err) { alert(err.message || "Ошибка удаления"); });
  });

  document.querySelectorAll(".tk-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTab(btn.dataset.tab);
    });
  });

  $("cancelEdit").addEventListener("click", function () {
    resetEditMode();
    $("unloadForm").reset();
    updatePlatformHint();
    $("unloadDate").value = todayIso();
    toast("Редактирование отменено");
  });

  function saveUnload(action) {
    lastSaveAction = action;
    if (!confirmVehicleForSave()) {
      return Promise.resolve();
    }
    var slabs = collectSlabs();
    if (!slabs.length) {
      alert(
        "Заполните хотя бы одну плиту: буква и номер. " +
          "Для ХРАНЕНИЯ укажите X/Y. Для В ПУТИ координаты не нужны. " +
          "Для ГРУЗОВОЙ/ТУРАН укажите номер вагона в строке или «вагон по умолчанию»."
      );
      return Promise.resolve();
    }
    if (action === "complete") {
      if (!$("craneStart").value || !$("craneEnd").value) {
        alert("Укажите время крана: «кран с» и «кран до» (*)");
        return Promise.resolve();
      }
    }
    var placementErrors = validatePlacementBeforeSave(slabs);
    if (placementErrors.length) {
      updateAllPlacementHints();
      alert(placementErrors.join("\n"));
      return Promise.resolve();
    }
    var duplicateDraft = findDuplicateSlabInDraft(slabs);
    if (duplicateDraft) {
      alert(
        "Плита " + duplicateDraft.label +
        " повторяется в текущей форме: строки " +
        duplicateDraft.firstRow + " и " + duplicateDraft.secondRow +
        ". Исправьте номер до сохранения."
      );
      return Promise.resolve();
    }
    var payload = {
      action: action,
      unload_date: $("unloadDate").value,
      trn: $("trn").value,
      vehicle_id: selectedVehicleId,
      driver: $("driver").value,
      crane_start: $("craneStart").value,
      crane_end: $("craneEnd").value,
      crane_minutes: craneMinutes(),
      notes: $("notes").value,
      slabs: slabs,
    };
    var sid = activeSessionId();
    if (sid) payload.revision = editingRevision;
    var url = sid ? "/api/taksimo/sessions/" + sid : "/api/taksimo/sessions";
    var method = sid ? "PUT" : "POST";
    $("saveDraftBtn").disabled = true;
    $("completeBtn").disabled = true;
    setSaveStatus("saving", "Сохраняем…");
    return apiFetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          return { ok: r.ok, status: r.status, data: d };
        });
      })
      .then(function (res) {
        if (res.status === 409) {
          setSaveStatus("idle");
          alert(res.data.error || "Выгрузку изменил другой пользователь. Откройте журнал заново.");
          if (sid) loadSessionDetail(sid);
          return;
        }
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        setSaveStatus("idle");
        var session = res.data.session;
        editingRevision = session.revision;
        if (action === "complete") {
          resetEditMode();
          $("unloadForm").reset();
          $("unloadDate").value = todayIso();
          renderSlabRows();
          clearDraftCache();
          toast("Приём завершён №" + session.id);
        } else {
          editingSessionId = null;
          showDraftBanner(session.id);
          renderSlabRows(session.slabs || []);
          cacheDraftForm();
          toast("Черновик сохранён №" + session.id);
        }
        refreshYardPanel(true);
        loadHistory();
        loadStats();
      })
      .catch(function (err) {
        if (err && err.message === "auth") return;
        if (isNetworkError(err)) {
          setSaveStatus("error", "Нет связи с сервером. Данные в форме сохранены локально.");
          return;
        }
        setSaveStatus("idle");
        alert(err.message || "Ошибка сохранения");
      })
      .finally(function () {
        $("saveDraftBtn").disabled = false;
        $("completeBtn").disabled = false;
      });
  }

  $("saveDraftBtn").addEventListener("click", function () {
    saveUnload("draft");
  });

  $("unloadForm").addEventListener("submit", function (e) {
    e.preventDefault();
    saveUnload("complete");
  });

  $("unloadForm").addEventListener("input", function () {
    cacheDraftForm();
    updateAllPlacementHints();
  });

  document.addEventListener("visibilitychange", function () {
    if (activeTab !== "yard") return;
    if (document.hidden) {
      stopYardPoll();
      return;
    }
    refreshYardPanel(true);
    startYardPoll();
  });

  var searchTimer;
  $("searchQ").addEventListener("input", function () {
    clearTimeout(searchTimer);
    var q = $("searchQ").value.trim();
    if (!q.length) { $("searchResults").innerHTML = ""; return; }
    searchTimer = setTimeout(function () {
      apiFetch("/api/taksimo/search?q=" + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (data) {
        var list = $("searchResults");
        list.innerHTML = "";
        if (!(data.results || []).length) {
          list.innerHTML = "<li class='tk-card'><div class='tk-card-meta'>Ничего не найдено</div></li>";
          return;
        }
        if (data.type === "wagon") {
          var head = document.createElement("li");
          head.className = "tk-card";
          head.innerHTML = "<div class='tk-card-title'>Вагон " + esc(data.wagon) + " · блоков: " + data.results.length + "</div>";
          list.appendChild(head);
        }
        (data.results || []).forEach(function (s) {
          var li = document.createElement("li");
          li.className = "tk-card tk-slab-item";
          var wagon = (s.wagon_number || "").trim();
          var metaLine = esc(s.unload_date) + " · " + esc(s.vehicle_plate || "") +
            (s.location ? " · " + esc(s.location) : s.platform_zone ? " · " + esc(s.platform_zone) : "") +
            (wagon && s.platform_zone !== "БТС ВОСТОК" ? " · вагон " + esc(wagon) : "") +
            (s.loading_date ? " · погр. " + esc(s.loading_date) : "");
          li.innerHTML =
            "<div><div class='tk-card-title'>" + esc(s.label) + " → " + esc(s.place) +
            (s.suffix ? " (" + esc(s.suffix) + ")" : "") + "</div>" +
            "<div class='tk-card-meta'>" + metaLine + "</div></div>" +
            slabActionButtons(s);
          bindSlabEditButtons(li);
          list.appendChild(li);
        });
      });
    }, 300);
  });

  updatePlatformHint();
  $("unloadDate").value = todayIso();
  $("exportDate").value = todayIso();
  $("exportToday").href = "/api/taksimo/export/registry.xlsx?date=" + todayIso();
  $("exportByDate").addEventListener("click", function (e) {
    var d = $("exportDate").value;
    if (!d) { e.preventDefault(); return; }
    this.href = "/api/taksimo/export/registry.xlsx?date=" + encodeURIComponent(d);
  });

  $("historyLoadMore").addEventListener("click", loadMoreHistory);

  $("wagonSlotBackdrop").addEventListener("click", closeWagonSlotSheet);
  $("wagonSlotClose").addEventListener("click", closeWagonSlotSheet);
  $("wagonSlotSave").addEventListener("click", function () { saveWagonSlot(false); });
  $("wagonSlotClear").addEventListener("click", function () {
    if (!confirm("Освободить слот? Вагон уберётся с тупика.")) return;
    saveWagonSlot(true);
  });
  $("wagonDispatchKodar").addEventListener("click", dispatchWagonToKodar);

  $("wagonSchemeBackdrop").addEventListener("click", closeWagonSchemeSheet);
  $("wagonSchemeClose").addEventListener("click", closeWagonSchemeSheet);
  document.querySelectorAll(".tk-scheme-btn[data-scheme]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var pending = pendingWagonSlotSave;
      if (!pending || !activeWagonSlot) return;
      doSaveWagonSlot(pending.wagon, pending.expected, btn.dataset.scheme, false);
    });
  });

  $("fleetExtrasBackdrop").addEventListener("click", closeFleetExtrasSheet);
  $("fleetExtrasClose").addEventListener("click", closeFleetExtrasSheet);
  var menuFleetExtras = $("menuFleetExtras");
  if (menuFleetExtras) {
    menuFleetExtras.addEventListener("click", function () { openFleetExtrasSheet("ТУРАН"); });
  }

  $("appMenuOpen").addEventListener("click", openAppMenu);
  $("appMenuClose").addEventListener("click", closeAppMenu);
  $("appMenuBackdrop").addEventListener("click", closeAppMenu);
  document.querySelectorAll(".tk-menu-item[data-go]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTab(btn.dataset.go);
    });
  });

  bindHeaderStats();

  var yardResizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(yardResizeTimer);
    yardResizeTimer = setTimeout(function () {
      if (activeTab === "yard" && yardSnapshot) renderYard(yardSnapshot);
    }, 150);
  });

  loadMeta().then(function () {
    refreshYardPanel(true);
    restoreDraftCache();
  });
})();
