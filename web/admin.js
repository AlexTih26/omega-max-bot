(function () {
  var webApp = window.WebApp || null;
  var initData = "";

  var appRoot = document.getElementById("appRoot");
  var templateList = document.getElementById("templateList");
  var messageField = document.getElementById("messageField");
  var metaLine = document.getElementById("metaLine");
  var previewBox = document.getElementById("previewBox");
  var historyList = document.getElementById("historyList");
  var historyEmpty = document.getElementById("historyEmpty");
  var accessError = document.getElementById("accessError");
  var sendBtn = document.getElementById("sendBtn");
  var confirmSheet = document.getElementById("confirmSheet");
  var confirmBackdrop = document.getElementById("confirmBackdrop");
  var confirmPreview = document.getElementById("confirmPreview");
  var confirmCancel = document.getElementById("confirmCancel");
  var confirmSend = document.getElementById("confirmSend");
  var successScreen = document.getElementById("successScreen");
  var toast = document.getElementById("toast");
  var tabBar = document.getElementById("tabBar");
  var panelAnnounce = document.getElementById("panelAnnounce");
  var panelFleet = document.getElementById("panelFleet");
  var footerAnnounce = document.getElementById("footerAnnounce");
  var footerFleet = document.getElementById("footerFleet");
  var fleetList = document.getElementById("fleetList");
  var fleetEmpty = document.getElementById("fleetEmpty");
  var fleetMeta = document.getElementById("fleetMeta");
  var syncFleetBtn = document.getElementById("syncFleetBtn");
  var addFleetBtn = document.getElementById("addFleetBtn");
  var fleetSheet = document.getElementById("fleetSheet");
  var fleetSheetBackdrop = document.getElementById("fleetSheetBackdrop");
  var fleetSheetTitle = document.getElementById("fleetSheetTitle");
  var fleetSheetHint = document.getElementById("fleetSheetHint");
  var fleetSheetCancel = document.getElementById("fleetSheetCancel");
  var fleetSheetSave = document.getElementById("fleetSheetSave");
  var fleetTail = document.getElementById("fleetTail");
  var fleetName = document.getElementById("fleetName");
  var fleetUid = document.getElementById("fleetUid");
  var fleetVehicle = document.getElementById("fleetVehicle");
  var fleetTaksimoPlate = document.getElementById("fleetTaksimoPlate");
  var fleetResetTrip = document.getElementById("fleetResetTrip");
  var fleetOldReserve = document.getElementById("fleetOldReserve");
  var fleetResetWrap = document.getElementById("fleetResetWrap");
  var fleetReserveWrap = document.getElementById("fleetReserveWrap");
  var fleetTailWrap = document.getElementById("fleetTailWrap");
  var wagonsList = document.getElementById("wagonsList");
  var wagonsEmpty = document.getElementById("wagonsEmpty");
  var wagonsMeta = document.getElementById("wagonsMeta");
  var addWagonBtn = document.getElementById("addWagonBtn");
  var wagonSheet = document.getElementById("wagonSheet");
  var wagonSheetBackdrop = document.getElementById("wagonSheetBackdrop");
  var wagonSheetCancel = document.getElementById("wagonSheetCancel");
  var wagonSheetSave = document.getElementById("wagonSheetSave");
  var wagonNumber = document.getElementById("wagonNumber");
  var wagonStage = document.getElementById("wagonStage");
  var wagonZone = document.getElementById("wagonZone");

  var meta = null;
  var busy = false;
  var activeTemplate = "";
  var currentTab = "announce";
  var fleetSheetMode = "";
  var fleetReserveUid = 0;

  function readInitDataFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var fromQuery = params.get("initData") || params.get("InitData");
    if (fromQuery) return fromQuery;
    if (window.location.hash && window.location.hash.length > 1) {
      var hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      return hash.get("initData") || hash.get("InitData") || "";
    }
    return "";
  }

  function setupBridge() {
    if (!webApp) return false;
    initData = webApp.initData || readInitDataFromUrl();
    if (webApp.ready) webApp.ready();
    if (webApp.expand) webApp.expand();
    if (webApp.disableClosingConfirmation) webApp.disableClosingConfirmation();
    return Boolean(initData);
  }

  function apiHeaders() {
    var h = { "Content-Type": "application/json" };
    if (initData) h["X-Max-Init-Data"] = initData;
    return h;
  }

  function apiFetch(url, options) {
    options = options || {};
    options.headers = Object.assign({}, apiHeaders(), options.headers || {});
    return fetch(url, options);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function haptic(type) {
    if (!webApp || !webApp.HapticFeedback) return;
    if (type === "success" && webApp.HapticFeedback.notificationOccurred) {
      webApp.HapticFeedback.notificationOccurred("success");
    } else if (webApp.HapticFeedback.impactOccurred) {
      webApp.HapticFeedback.impactOccurred("light");
    }
  }

  function showToast(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    toast.classList.add("adm-toast--show");
    setTimeout(function () {
      toast.classList.remove("adm-toast--show");
      setTimeout(function () {
        toast.hidden = true;
      }, 200);
    }, 2200);
  }

  function currentText() {
    return (messageField.value || "").trim();
  }

  function updatePreview() {
    var text = currentText();
    previewBox.textContent = text || "—";
    var len = messageField.value.length;
    var maxLen = (meta && meta.max_length) || 1000;
    var tz = (meta && meta.timezone_label) || "";
    var date = (meta && meta.date_label) || "";
    metaLine.textContent = date + (tz ? " · " + tz : "") + " · " + len + " / " + maxLen + " симв.";
    sendBtn.disabled = busy || !text || !meta;
  }

  function renderTemplates() {
    templateList.innerHTML = "";
    if (!meta || !meta.templates) return;
    meta.templates.forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "adm-chip" + (activeTemplate === item.id ? " adm-chip--active" : "");
      btn.textContent = item.label;
      btn.addEventListener("click", function () {
        activeTemplate = item.id;
        messageField.value = item.text || "";
        renderTemplates();
        updatePreview();
        messageField.focus();
      });
      templateList.appendChild(btn);
    });
  }

  function renderHistory(items) {
    historyList.innerHTML = "";
    if (!items || !items.length) {
      historyEmpty.hidden = false;
      return;
    }
    historyEmpty.hidden = true;
    items.forEach(function (item) {
      var li = document.createElement("li");
      li.className = "adm-history-item";
      li.innerHTML =
        '<span class="adm-history-meta">' +
        escapeHtml(item.at_label || item.at || "") +
        "</span>" +
        escapeHtml(item.text || "");
      historyList.appendChild(li);
    });
  }

  function showAccessError(msg) {
    if (panelAnnounce) panelAnnounce.hidden = true;
    if (panelFleet) panelFleet.hidden = true;
    if (footerAnnounce) footerAnnounce.hidden = true;
    if (footerFleet) footerFleet.hidden = true;
    if (tabBar) tabBar.hidden = true;
    accessError.hidden = false;
    accessError.textContent = msg;
  }

  function switchTab(tab) {
    currentTab = tab;
    if (panelAnnounce) panelAnnounce.hidden = tab !== "announce";
    if (panelFleet) panelFleet.hidden = tab !== "fleet";
    if (footerAnnounce) footerAnnounce.hidden = tab !== "announce";
    if (footerFleet) footerFleet.hidden = tab !== "fleet";
    if (tabBar) {
      tabBar.querySelectorAll(".adm-tab").forEach(function (btn) {
        btn.classList.toggle("adm-tab--active", btn.getAttribute("data-tab") === tab);
      });
    }
    if (tab === "fleet") {
      loadFleet().catch(function () {});
      loadWagons().catch(function () {});
    }
  }

  function renderWagons(data) {
    if (!wagonsList) return;
    var items = (data && data.wagons) || [];
    wagonsList.innerHTML = "";
    if (wagonsMeta) {
      wagonsMeta.textContent =
        "В парке: " + (data.count || items.length) + " / " + (data.max_fleet_wagons || 50) +
        " · конец цикла: " + (data.cycle_destination || "Кодар");
    }
    if (wagonsEmpty) wagonsEmpty.hidden = items.length > 0;
    items.forEach(function (row) {
      var li = document.createElement("li");
      li.className = "adm-fleet-item";
      var slot =
        row.slot_zone && row.slot_index
          ? row.slot_zone + " · слот №" + row.slot_index
          : row.planned_zone
            ? "→ " + row.planned_zone
            : "—";
      li.innerHTML =
        '<div class="adm-fleet-head"><span class="adm-fleet-title">' + escapeHtml(row.number) + "</span>" +
        '<span class="adm-fleet-badge">' + escapeHtml(row.stage_label || row.stage) + "</span></div>" +
        '<p class="adm-fleet-detail">' + escapeHtml(slot) +
        (row.slab_count ? " · блоков " + row.slab_count : "") + "</p>";
      wagonsList.appendChild(li);
    });
  }

  function loadWagons() {
    return apiFetch("/api/admin/wagons")
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, body: body };
        });
      })
      .then(function (res) {
        if (!res.ok) throw new Error((res.body && res.body.error) || "forbidden");
        renderWagons(res.body);
      });
  }

  function openWagonSheet() {
    if (wagonNumber) wagonNumber.value = "";
    if (wagonStage) wagonStage.value = "in_transit";
    if (wagonZone) wagonZone.value = "ТУРАН";
    if (wagonSheet) wagonSheet.hidden = false;
  }

  function closeWagonSheet() {
    if (wagonSheet) wagonSheet.hidden = true;
  }

  function saveWagon() {
    if (busy || !wagonNumber) return;
    var num = (wagonNumber.value || "").replace(/\s+/g, "").trim();
    if (!num) {
      showToast("Укажите номер вагона");
      return;
    }
    busy = true;
    if (wagonSheetSave) wagonSheetSave.disabled = true;
    apiFetch("/api/admin/wagons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        numbers: [num],
        stage: wagonStage ? wagonStage.value : "available",
        planned_zone: wagonZone ? wagonZone.value : "",
      }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, body: body };
        });
      })
      .then(function (res) {
        if (!res.ok) throw new Error((res.body && res.body.notification) || "Не удалось");
        haptic("success");
        showToast(res.body.notification || "Добавлено");
        closeWagonSheet();
        renderWagons(res.body);
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка");
      })
      .finally(function () {
        busy = false;
        if (wagonSheetSave) wagonSheetSave.disabled = false;
      });
  }

  function renderFleet(data) {
    if (!fleetList) return;
    var items = (data && data.items) || [];
    fleetList.innerHTML = "";
    if (fleetMeta) {
      fleetMeta.textContent =
        "В парке: " + (data.active_count || 0) + " · всего в реестре: " + (data.total || items.length);
    }
    fleetEmpty.hidden = items.length > 0;
    items.forEach(function (row) {
      var li = document.createElement("li");
      li.className = "adm-fleet-item" + (row.active ? "" : " adm-fleet-item--off");
      var title =
        row.plate_tail
          ? "…" + row.plate_tail + " · " + (row.name || "—")
          : (row.name || "—") + " · резерв";
      var badge = row.reserve
        ? '<span class="adm-fleet-badge adm-fleet-badge--off">резерв</span>'
        : row.active
          ? '<span class="adm-fleet-badge">' + escapeHtml(row.phase_label || "—") + "</span>"
          : '<span class="adm-fleet-badge adm-fleet-badge--off">снят</span>';
      var detailParts = [row.vehicle, row.taksimo_plate, row.detail].filter(Boolean);
      var uidWarn =
        row.plate_tail && row.active && !row.max_user_id
          ? '<p class="adm-fleet-warn">⚠️ нет MAX id — только Таксimo</p>'
          : row.max_user_id
            ? '<p class="adm-fleet-meta">MAX id ' + row.max_user_id + "</p>"
            : "";
      var actions = "";
      if (row.plate_tail) {
        actions +=
          '<button type="button" class="adm-fleet-btn adm-fleet-btn--primary" data-fleet-form="change" data-tail="' +
          escapeHtml(row.plate_tail) +
          '">Сменить</button>';
        actions +=
          '<button type="button" class="adm-fleet-btn" data-fleet-act="reset_trip" data-tail="' +
          escapeHtml(row.plate_tail) +
          '">Сброс рейса</button>';
        if (row.active) {
          actions +=
            '<button type="button" class="adm-fleet-btn adm-fleet-btn--warn" data-fleet-act="set_active" data-tail="' +
            escapeHtml(row.plate_tail) +
            '" data-active="0">Снять с парка</button>';
        } else if (!row.reserve) {
          actions +=
            '<button type="button" class="adm-fleet-btn" data-fleet-act="set_active" data-tail="' +
            escapeHtml(row.plate_tail) +
            '" data-active="1">Вернуть в парк</button>';
        }
      } else if (row.reserve && row.max_user_id) {
        actions +=
          '<button type="button" class="adm-fleet-btn adm-fleet-btn--primary" data-fleet-form="reserve" data-uid="' +
          row.max_user_id +
          '">Назначить машину</button>';
      }
      li.innerHTML =
        '<div class="adm-fleet-head"><p class="adm-fleet-title">' +
        escapeHtml(title) +
        "</p>" +
        badge +
        "</div>" +
        uidWarn +
        (detailParts.length ? '<p class="adm-fleet-meta">' + escapeHtml(detailParts.join(" · ")) + "</p>" : "") +
        (actions ? '<div class="adm-fleet-actions">' + actions + "</div>" : "");
      li.dataset.row = JSON.stringify(row);
      fleetList.appendChild(li);
    });
  }

  function findFleetRow(tail, uid) {
    if (!fleetList) return null;
    var nodes = fleetList.querySelectorAll("[data-row]");
    for (var i = 0; i < nodes.length; i++) {
      try {
        var row = JSON.parse(nodes[i].dataset.row || "{}");
        if (tail && row.plate_tail === tail) return row;
        if (uid && row.max_user_id === uid) return row;
      } catch (e) {}
    }
    return null;
  }

  function openFleetSheet(mode, row) {
    fleetSheetMode = mode;
    row = row || {};
    fleetReserveUid = 0;
    if (fleetSheet) fleetSheet.hidden = false;

    if (mode === "add") {
      if (fleetSheetTitle) fleetSheetTitle.textContent = "Добавить машину";
      if (fleetSheetHint) fleetSheetHint.textContent = "Появится в Таксimo и реестре. MAX id — когда водитель зарегистрируется.";
      if (fleetTail) { fleetTail.value = ""; fleetTail.disabled = false; }
      if (fleetName) { fleetName.value = ""; fleetName.disabled = false; }
      if (fleetUid) { fleetUid.value = ""; fleetUid.disabled = false; }
      if (fleetVehicle) fleetVehicle.value = "";
      if (fleetTaksimoPlate) fleetTaksimoPlate.value = "";
      if (fleetResetWrap) fleetResetWrap.hidden = true;
      if (fleetReserveWrap) fleetReserveWrap.hidden = true;
    } else if (mode === "reserve") {
      fleetReserveUid = row.max_user_id || 0;
      if (fleetSheetTitle) fleetSheetTitle.textContent = "Назначить машину · " + (row.name || "резерв");
      if (fleetSheetHint) fleetSheetHint.textContent = "MAX id " + fleetReserveUid + " · укажите хвост и номер Таксimo";
      if (fleetTail) { fleetTail.value = ""; fleetTail.disabled = false; }
      if (fleetName) { fleetName.value = row.name || ""; fleetName.disabled = true; }
      if (fleetUid) { fleetUid.value = String(fleetReserveUid); fleetUid.disabled = true; }
      if (fleetVehicle) fleetVehicle.value = row.vehicle || "";
      if (fleetTaksimoPlate) fleetTaksimoPlate.value = row.taksimo_plate || "";
      if (fleetResetWrap) fleetResetWrap.hidden = false;
      if (fleetReserveWrap) fleetReserveWrap.hidden = true;
      if (fleetResetTrip) fleetResetTrip.checked = true;
    } else {
      if (fleetSheetTitle) fleetSheetTitle.textContent = "Сменить · …" + (row.plate_tail || "");
      if (fleetSheetHint) fleetSheetHint.textContent = "Обновит реестр, чат водителей и Таксimo";
      if (fleetTail) { fleetTail.value = row.plate_tail || ""; fleetTail.disabled = true; }
      if (fleetName) { fleetName.value = row.name || ""; fleetName.disabled = false; }
      if (fleetUid) { fleetUid.value = row.max_user_id ? String(row.max_user_id) : ""; fleetUid.disabled = false; }
      if (fleetVehicle) fleetVehicle.value = row.vehicle || "";
      if (fleetTaksimoPlate) fleetTaksimoPlate.value = row.taksimo_plate || "";
      if (fleetResetWrap) fleetResetWrap.hidden = false;
      if (fleetReserveWrap) fleetReserveWrap.hidden = false;
      if (fleetResetTrip) fleetResetTrip.checked = true;
      if (fleetOldReserve) fleetOldReserve.checked = true;
    }
  }

  function closeFleetSheet() {
    if (fleetSheet) fleetSheet.hidden = true;
    fleetSheetMode = "";
    fleetReserveUid = 0;
  }

  function submitFleetSheet() {
    if (!fleetSheetMode || busy) return;
    var tail = (fleetTail && fleetTail.value || "").replace(/\D/g, "");
    var name = (fleetName && fleetName.value || "").trim();
    var uidRaw = (fleetUid && fleetUid.value || "").trim();
    var vehicle = (fleetVehicle && fleetVehicle.value || "").trim();
    var taksimoPlate = (fleetTaksimoPlate && fleetTaksimoPlate.value || "").trim();
    var body = {};

    if (fleetSheetMode === "add") {
      if (!tail || !name || !taksimoPlate) {
        showToast("Хвост, имя и номер Таксimo обязательны");
        return;
      }
      body = {
        action: "add",
        plate_tail: tail,
        name: name,
        vehicle: vehicle,
        taksimo_plate: taksimoPlate,
        max_user_id: uidRaw ? parseInt(uidRaw, 10) : 0,
      };
    } else if (fleetSheetMode === "reserve") {
      if (!tail || !taksimoPlate) {
        showToast("Укажите хвост и номер Таксimo");
        return;
      }
      body = {
        action: "assign_reserve",
        max_user_id: fleetReserveUid,
        plate_tail: tail,
        vehicle: vehicle,
        taksimo_plate: taksimoPlate,
        reset_trip: fleetResetTrip ? fleetResetTrip.checked : true,
      };
    } else {
      if (!tail || !name) {
        showToast("Имя обязательно");
        return;
      }
      body = {
        action: "change",
        plate_tail: tail,
        name: name,
        vehicle: vehicle,
        taksimo_plate: taksimoPlate,
        reset_trip: fleetResetTrip ? fleetResetTrip.checked : true,
        old_to_reserve: fleetOldReserve ? fleetOldReserve.checked : true,
      };
      if (uidRaw !== "") body.max_user_id = parseInt(uidRaw, 10) || 0;
    }

    postFleetAction(body).then(function () {
      closeFleetSheet();
    });
  }

  function loadFleet() {
    return apiFetch("/api/admin/fleet")
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "fleet failed");
          return body;
        });
      })
      .then(renderFleet);
  }

  function postFleetAction(body) {
    if (busy) return Promise.reject(new Error("busy"));
    busy = true;
    return apiFetch("/api/admin/fleet", {
      method: "POST",
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.notification || data.error || "action failed");
          return data;
        });
      })
      .then(function (data) {
        showToast(data.notification || "Готово");
        renderFleet(data);
        haptic("success");
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка");
      })
      .finally(function () {
        busy = false;
      });
  }

  function showSuccess() {
    successScreen.hidden = false;
    appRoot.hidden = true;
    if (footerAnnounce) footerAnnounce.hidden = true;
    if (footerFleet) footerFleet.hidden = true;
    haptic("success");
    setTimeout(function () {
      successScreen.hidden = true;
      appRoot.hidden = false;
      switchTab(currentTab);
      loadHistory();
    }, 1800);
  }

  function loadHistory() {
    return apiFetch("/api/admin/announcements?limit=5")
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "history failed");
          return body;
        });
      })
      .then(function (data) {
        renderHistory(data.items || []);
      })
      .catch(function () {
        historyEmpty.hidden = false;
      });
  }

  function loadProfile() {
    return apiFetch("/api/admin/profile")
      .then(function (r) {
        return r.json().then(function (body) {
          if (r.status === 403) {
            showAccessError("Нет доступа к админ-панели. Откройте из MAX под аккаунтом администратора.");
            throw new Error("forbidden");
          }
          if (!r.ok) throw new Error(body.error || "profile failed");
          return body;
        });
      })
      .then(function (data) {
        meta = data;
        messageField.maxLength = data.max_length || 1000;
        if (data.templates && data.templates.length) {
          activeTemplate = data.templates[0].id;
          messageField.value = data.templates[0].text || "";
        }
        renderTemplates();
        updatePreview();
        sendBtn.disabled = false;
        return loadHistory();
      });
  }

  function openConfirm() {
    var text = currentText();
    if (!text || busy) return;
    confirmPreview.textContent = text;
    confirmSheet.hidden = false;
  }

  function closeConfirm() {
    confirmSheet.hidden = true;
  }

  function submitAnnouncement() {
    var text = currentText();
    if (!text || busy) return;
    busy = true;
    updatePreview();
    confirmSend.disabled = true;
    sendBtn.disabled = true;

    apiFetch("/api/admin/announcements", {
      method: "POST",
      body: JSON.stringify({ text: text }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.notification || body.error || "send failed");
          return body;
        });
      })
      .then(function () {
        closeConfirm();
        showSuccess();
        if (meta && meta.templates && meta.templates.length) {
          var tpl = meta.templates.find(function (t) {
            return t.id === activeTemplate;
          }) || meta.templates[0];
          messageField.value = tpl.text || "";
        } else {
          messageField.value = "";
        }
        updatePreview();
      })
      .catch(function (err) {
        showToast(err.message || "Не удалось отправить");
        haptic("error");
      })
      .finally(function () {
        busy = false;
        confirmSend.disabled = false;
        updatePreview();
      });
  }

  messageField.addEventListener("input", function () {
    activeTemplate = "";
    renderTemplates();
    updatePreview();
  });

  sendBtn.addEventListener("click", openConfirm);
  confirmCancel.addEventListener("click", closeConfirm);
  confirmBackdrop.addEventListener("click", closeConfirm);
  confirmSend.addEventListener("click", submitAnnouncement);

  if (tabBar) {
    tabBar.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-tab]");
      if (!btn) return;
      switchTab(btn.getAttribute("data-tab"));
    });
  }

  if (addWagonBtn) addWagonBtn.addEventListener("click", openWagonSheet);
  if (wagonSheetCancel) wagonSheetCancel.addEventListener("click", closeWagonSheet);
  if (wagonSheetBackdrop) wagonSheetBackdrop.addEventListener("click", closeWagonSheet);
  if (wagonSheetSave) wagonSheetSave.addEventListener("click", saveWagon);

  if (fleetList) {
    fleetList.addEventListener("click", function (e) {
      var formBtn = e.target.closest("[data-fleet-form]");
      if (formBtn && !busy) {
        var mode = formBtn.getAttribute("data-fleet-form");
        if (mode === "change") {
          openFleetSheet("change", findFleetRow(formBtn.getAttribute("data-tail"), 0));
        } else if (mode === "reserve") {
          openFleetSheet("reserve", findFleetRow("", parseInt(formBtn.getAttribute("data-uid") || "0", 10)));
        }
        return;
      }
      var btn = e.target.closest("[data-fleet-act]");
      if (!btn || busy) return;
      var action = btn.getAttribute("data-fleet-act");
      var tail = btn.getAttribute("data-tail") || "";
      var body = { action: action, plate_tail: tail };
      if (action === "set_active") {
        body.active = btn.getAttribute("data-active") === "1";
      }
      postFleetAction(body);
    });
  }

  if (addFleetBtn) {
    addFleetBtn.addEventListener("click", function () {
      openFleetSheet("add");
    });
  }

  if (fleetSheetCancel) fleetSheetCancel.addEventListener("click", closeFleetSheet);
  if (fleetSheetBackdrop) fleetSheetBackdrop.addEventListener("click", closeFleetSheet);
  if (fleetSheetSave) fleetSheetSave.addEventListener("click", submitFleetSheet);

  if (syncFleetBtn) {
    syncFleetBtn.addEventListener("click", function () {
      postFleetAction({ action: "sync" });
    });
  }

  if (!setupBridge()) {
    showAccessError("Откройте панель из бота MAX — кнопка «Админ» или команда /admin.");
  } else {
    loadProfile().catch(function (err) {
      if (err.message !== "forbidden") {
        showAccessError("Не удалось загрузить панель — проверьте связь.");
      }
    });
  }
})();
