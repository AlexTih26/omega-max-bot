(function () {
  "use strict";

  var API = "/api/taksimo-new";
  var state = { operator: null, permissions: {}, activeTab: "today", searchTimer: null };
  var statusLabels = {
    expected: "Ожидается",
    draft: "Черновик",
    in_progress: "В работе",
    confirmed: "Подтверждена",
    discrepancy: "Расхождение",
    forming: "Формируется",
    loaded: "Загружен в тупиках, в вагонах",
    in_transit: "В пути",
    at_kodar: "В Кодаре",
    unloaded_bts_east: "Выгружен у БТС Восток",
    returned_empty: "Вернулся порожним"
  };
  var eventLabels = {
    intake_expected_imported: "Получена ожидаемая приёмка из служебного моста",
    intake_draft_created: "Создана ручная ожидаемая приёмка",
    intake_locked: "Приёмка взята в работу",
    intake_arrived: "Машина прибыла на площадку",
    intake_crane_started: "Кран начал работу",
    intake_crane_ended: "Кран завершил работу",
    intake_confirmed: "Приёмка подтверждена",
    intake_discrepancy: "Приёмка подтверждена с расхождением",
    intake_cancelled: "Оператор 1 отменил подтверждённую приёмку",
    block_loaded_to_wagon: "Блок загружен в вагон",
    wagon_loaded: "Загрузка вагона зафиксирована",
    wagon_dispatched: "Вагон отправлен",
    wagon_arrived_kodar: "Вагон прибыл в Кодар",
    wagon_unloaded_bts_east: "Вагон выгружен у БТС Восток",
    wagon_returned_empty: "Вагон вернулся порожним",
    wagon_position_assigned: "Вагон поставлен в тупик",
    wagon_position_released: "Вагон освободил слот тупика",
    operation_corrected: "Создана корректировка подтверждённой операции",
    operation_cancelled: "Оператор 1 отменил подтверждённую операцию"
  };
  var tripStatusLabels = {
    draft: "Черновик",
    ttn_issued: "Номер ТТН выдан",
    printed: "ТТН напечатана",
    er_sent: "ЭР отправлена",
    er_returned: "ЭР вернулась",
    cancelled: "Отменён"
  };
  var receiptStatusLabels = {
    draft: "Черновик",
    sent: "Отправлена в ЭДО",
    returned: "Вернулась",
    cancelled: "Отменена"
  };
  var tripBlockLetters = ["A", "B", "C", "D", "E", "F", "K"];
  var operatorTripStatuses = { printed: true, er_sent: true, er_returned: true };

  var currentUser = document.getElementById("currentUser");
  var appError = document.getElementById("appError");
  var toast = document.getElementById("toast");
  var modal = document.getElementById("intakeModal");
  var modalTitle = document.getElementById("intakeModalTitle");
  var modalContent = document.getElementById("intakeModalContent");
  var wagonModal = document.getElementById("wagonModal");
  var wagonModalTitle = document.getElementById("wagonModalTitle");
  var wagonModalContent = document.getElementById("wagonModalContent");

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function setError(message) {
    appError.textContent = message || "";
    appError.hidden = !message;
  }

  function showToast(message) {
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(function () { toast.hidden = true; }, 3200);
  }

  function formatDate(value) {
    if (!value) return "—";
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }

  function statusBadge(status) {
    return element("span", "tn-badge tn-badge--" + String(status || "").replace(/[^a-z_]/g, ""), statusLabels[status] || status || "—");
  }

  function request(path, options) {
    var config = Object.assign({ credentials: "same-origin" }, options || {});
    return fetch(API + path, config).then(function (response) {
      return response.text().then(function (text) {
        var body = {};
        try { body = text ? JSON.parse(text) : {}; } catch (_) { body = {}; }
        if (response.status === 401) {
          var next = "/taksimo-new/index.html";
          if (!location.pathname.endsWith("/")) next = location.pathname;
          location.replace("/taksimo-new/login.html?next=" + encodeURIComponent(next));
          throw new Error("Требуется вход");
        }
        if (!response.ok) throw new Error(body.error || "Сервер вернул ошибку");
        return body;
      });
    });
  }

  function jsonRequest(path, method, payload) {
    return request(path, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload)
    });
  }

  var roleLabels = {
    admin: "Администратор",
    dispatcher: "Диспетчер",
    accountant1: "Бухгалтер 1",
    accountant2: "Бухгалтер 2",
    accountant3: "Бухгалтер 3",
    operator1: "Оператор 1",
    operator2: "Оператор 2",
    operator3: "Оператор 3"
  };

  function isOperator1() {
    return state.operator && state.operator.role === "operator1";
  }

  function isAdmin() {
    return state.operator && state.operator.role === "admin";
  }

  function hasRole() {
    var allowed = Array.prototype.slice.call(arguments);
    return Boolean(state.operator) && allowed.indexOf(state.operator.role) !== -1;
  }

  function showPanel(name) {
    state.activeTab = name;
    document.querySelectorAll("[data-panel]").forEach(function (panel) {
      panel.hidden = panel.getAttribute("data-panel") !== name;
    });
    document.querySelectorAll(".tn-tab").forEach(function (tab) {
      tab.setAttribute("aria-selected", String(tab.getAttribute("data-tab") === name));
    });
    loadPanel(name);
  }

  function intakeMeta(intake) {
    var parts = [];
    if (intake.ttn_number) parts.push("ТТН " + intake.ttn_number);
    if (intake.vehicle_plate) parts.push(intake.vehicle_plate);
    if (intake.driver_name) parts.push(intake.driver_name);
    parts.push("ожидается: " + intake.expected_blocks_count);
    if (intake.source_system === "rumex") parts.push("из служебного моста РУМЕКС");
    if (intake.cancelled) parts.push("отменена: " + (intake.cancellation_reason || "причина указана в журнале"));
    return parts.join(" · ");
  }

  function makeIntakeCard(intake) {
    var card = element("article", "tn-card");
    var row = element("div", "tn-card-title-row");
    var heading = element("div");
    heading.appendChild(element("p", "tn-card-title", intake.ttn_number || intake.source_reference || "Приёмка без номера"));
    heading.appendChild(element("p", "tn-card-meta", intakeMeta(intake)));
    row.appendChild(heading);
    row.appendChild(statusBadge(intake.cancelled ? "discrepancy" : intake.status));
    card.appendChild(row);
    var time = intake.planned_arrival_at || intake.created_at;
    if (time) card.appendChild(element("p", "tn-card-meta", "Время: " + formatDate(time)));
    var actions = element("div", "tn-card-actions");
    var open = element("button", "tn-button tn-button--secondary", "Открыть");
    open.type = "button";
    open.addEventListener("click", function () { openIntake(intake.public_id); });
    actions.appendChild(open);
    card.appendChild(actions);
    return card;
  }

  function legacyBadge() {
    return element("span", "tn-badge tn-badge--legacy", "старая");
  }

  function makeLegacySessionCard(session) {
    var card = element("article", "tn-card tn-card--legacy");
    var row = element("div", "tn-card-title-row");
    row.appendChild(element("p", "tn-card-title", "Сессия #" + session.id + " (старая Таксимо)"));
    row.appendChild(legacyBadge());
    card.appendChild(row);
    var meta = [];
    if (session.unload_date) meta.push(session.unload_date);
    if (session.trn) meta.push("ТРН " + session.trn);
    if (session.driver) meta.push("водитель: " + session.driver);
    if (session.status) meta.push("статус: " + session.status);
    card.appendChild(element("p", "tn-card-meta", meta.join(" · ") || "—"));
    return card;
  }

  function renderIntakes(target, intakes, emptyNode) {
    clear(target);
    emptyNode.hidden = intakes.length > 0;
    intakes.forEach(function (intake) { target.appendChild(makeIntakeCard(intake)); });
  }

  function statCard(value, label) {
    var card = element("article", "tn-stat");
    card.appendChild(element("p", "tn-stat-value", value));
    card.appendChild(element("p", "tn-stat-label", label));
    return card;
  }

  function loadDashboard() {
    return request("/dashboard").then(function (data) {
      setError("");
      document.getElementById("todayTimezone").textContent = "Рабочая зона: " + (data.timezone || "UTC");
      var stats = document.getElementById("dashboardStats");
      clear(stats);
      var intakes = data.intakes || {};
      var wagons = data.wagons || {};
      stats.appendChild(statCard((intakes.expected || 0) + (intakes.in_progress || 0), "ожидают или в работе"));
      stats.appendChild(statCard(data.yard_blocks || 0, "блоков на площадке"));
      stats.appendChild(statCard(wagons.forming || 0, "вагонов формируется"));
      stats.appendChild(statCard(wagons.in_transit || 0, "вагонов в пути"));
      stats.appendChild(statCard((wagons.at_kodar || 0) + (wagons.unloaded_bts_east || 0), "вагонов в Кодаре или выгружено"));
      stats.appendChild(statCard(data.pending_integrations || 0, "неподтверждённых сообщений моста"));
      var legacy = data.legacy || {};
      stats.appendChild(statCard(legacy.yard_blocks || 0, "плит в старой Таксимо"));
      stats.appendChild(statCard(legacy.wagons || 0, "вагонов в старой Таксимо"));
      stats.appendChild(statCard(legacy.sessions || 0, "сессий в старой Таксимо"));
      renderIntakes(document.getElementById("expectedIntakes"), data.expected_intakes || [], document.getElementById("expectedEmpty"));
    });
  }

  function loadIntakes() {
    return request("/intakes?limit=100").then(function (data) {
      var intakes = data.intakes || [];
      var sessions = data.legacy_sessions || [];
      renderIntakes(document.getElementById("intakesList"), intakes, document.getElementById("intakesEmpty"));
      sessions.slice(0, 30).forEach(function (session) {
        document.getElementById("intakesList").appendChild(makeLegacySessionCard(session));
      });
      document.getElementById("intakesEmpty").hidden = (intakes.length + Math.min(sessions.length, 30)) > 0;
    });
  }

  function locationText(block) {
    if (block.current_location_kind === "wagon") return "вагон " + (block.wagon_number || "—");
    return "площадка X=" + block.yard_x + ", Y=" + block.yard_y + (block.yard_slot ? ", место " + block.yard_slot : "");
  }

  function loadYard() {
    return request("/yard").then(function (data) {
      var blocks = data.blocks || [];
      var legacySlabs = data.legacy_slabs || [];
      var freshByCell = {};
      var legacyByCell = {};
      blocks.forEach(function (block) {
        var key = block.yard_x + ":" + block.yard_y;
        (freshByCell[key] = freshByCell[key] || []).push(block);
      });
      legacySlabs.forEach(function (slab) {
        var key = slab.yard_x + ":" + slab.yard_y;
        (legacyByCell[key] = legacyByCell[key] || []).push(slab);
      });
      var grid = document.getElementById("yardGrid");
      clear(grid);
      for (var y = 1; y <= 25; y += 1) {
        for (var x = 1; x <= 13; x += 1) {
          var key = x + ":" + y;
          var fresh = freshByCell[key] || [];
          var legacyItems = legacyByCell[key] || [];
          var total = fresh.length + legacyItems.length;
          var legacyOnly = total > 0 && fresh.length === 0;
          var hasDraft = fresh.some(function (item) { return item.draft; });
          var cell = element("button", "tn-yard-cell" + (total ? " tn-yard-cell--occupied" + (legacyOnly ? " tn-yard-cell--legacy" : "") + (hasDraft ? " tn-yard-cell--draft" : "") : ""));
          cell.type = "button";
          cell.textContent = x + "/" + y;
          var labels = fresh.map(function (item) { return item.block_type + " " + item.block_number + (item.draft ? " (черновик)" : ""); })
            .concat(legacyItems.map(function (item) { return item.block_type + " " + item.block_number + " (старая)"; }));
          cell.title = labels.length ? labels.join(", ") : "Свободная ячейка";
          if (total) cell.appendChild(element("span", "tn-yard-cell-count", String(total)));
          grid.appendChild(cell);
        }
      }
      var note = document.getElementById("yardLegacyNote");
      if (note) {
        note.hidden = legacySlabs.length === 0;
        note.textContent = "Плит из старой Таксимо на площадке: " + legacySlabs.length;
      }
      var list = document.getElementById("yardBlocks");
      clear(list);
      blocks.forEach(function (block) {
        var card = element("article", "tn-card");
        card.appendChild(element("p", "tn-card-title", "#" + block.id + " · " + block.block_type + " " + block.block_number));
        card.appendChild(element("p", "tn-card-meta", locationText(block) + (block.draft ? " · черновик" : "") + (block.condition_code === "damage" ? " · повреждение" : "")));
        list.appendChild(card);
      });
    });
  }

  function loadWagons() {
    return request("/wagons").then(function (data) {
      var wagons = data.wagons || [];
      var legacyWagons = data.legacy_wagons || [];
      var legacySlots = data.legacy_slots || [];
      var legacyByWagon = {};
      legacySlots.forEach(function (slot) {
        if (slot.wagon_number) legacyByWagon[slot.wagon_number] = slot;
      });
      var list = document.getElementById("wagonsList");
      clear(list);
      wagons.forEach(function (wagon) {
        var card = element("article", "tn-card");
        var row = element("div", "tn-card-title-row");
        row.appendChild(element("p", "tn-card-title", "Вагон " + wagon.wagon_number));
        row.appendChild(statusBadge(wagon.status));
        card.appendChild(row);
        var position = wagon.dead_end_code ? (wagon.dead_end_code + " · слот " + wagon.slot_index) : "позиция не задана";
        card.appendChild(element("p", "tn-card-meta", "Блоков: " + wagon.blocks_count + " · " + position + " · создан: " + formatDate(wagon.created_at)));
        if (wagon.loaded_at) card.appendChild(element("p", "tn-card-meta", "Загрузка: " + formatDate(wagon.loaded_at)));
        if (wagon.dispatched_at) card.appendChild(element("p", "tn-card-meta", "Отправлен: " + formatDate(wagon.dispatched_at)));
        if (wagon.arrived_kodar_at) card.appendChild(element("p", "tn-card-meta", "Кодар: " + formatDate(wagon.arrived_kodar_at)));
        if (wagon.unloaded_bts_east_at) card.appendChild(element("p", "tn-card-meta", "БТС Восток: " + formatDate(wagon.unloaded_bts_east_at)));
        if (isOperator1()) appendWagonAction(card, wagon);
        card.addEventListener("click", function () { openWagonHistory(wagon.wagon_number); });
        list.appendChild(card);
      });
      legacyWagons.forEach(function (wagon) {
        var slot = legacyByWagon[wagon.number];
        var card = element("article", "tn-card tn-card--legacy");
        var row = element("div", "tn-card-title-row");
        row.appendChild(element("p", "tn-card-title", "Вагон " + wagon.number));
        row.appendChild(legacyBadge());
        card.appendChild(row);
        var meta = [];
        if (slot) meta.push(slot.zone + " · слот " + slot.slot_index);
        if (wagon.stage) meta.push("этап: " + wagon.stage);
        card.appendChild(element("p", "tn-card-meta", meta.join(" · ") || "—"));
        card.addEventListener("click", function () { openWagonHistory(wagon.number); });
        list.appendChild(card);
      });
      document.getElementById("wagonsEmpty").hidden = (wagons.length + legacyWagons.length) > 0;
    });
  }

  var kindLabels = { block: "Блок", wagon: "Вагон", intake: "ТТН/рейс", slab: "Плита (старая)", vehicle: "Машина" };

  function resultMeta(item) {
    if (item.kind === "block") return locationText(item) + (item.ttn_number ? " · ТТН " + item.ttn_number : "");
    if (item.kind === "wagon") return "статус: " + (item.status || item.stage || "—") + (item.planned_zone ? " · зона " + item.planned_zone : "");
    if (item.kind === "intake") return [item.vehicle_plate, item.driver_name, item.expected_blocks_count ? "блоков " + item.expected_blocks_count : ""].filter(Boolean).join(" · ");
    if (item.kind === "vehicle") return [item.brand, item.driver].filter(Boolean).join(" · ");
    if (item.kind === "slab") return [item.platform_zone ? "зона " + item.platform_zone : "", item.wagon_number ? "вагон " + item.wagon_number : "на площадке"].filter(Boolean).join(" · ");
    return "";
  }

  function runSearch() {
    var input = document.getElementById("searchInput");
    var query = input.value.trim();
    var kind = document.getElementById("searchKind").value;
    var results = document.getElementById("searchResults");
    var empty = document.getElementById("searchEmpty");
    clear(results);
    empty.hidden = true;
    if (!query) return Promise.resolve();
    var url = "/search?q=" + encodeURIComponent(query) + "&limit=50" + (kind ? "&kind=" + encodeURIComponent(kind) : "");
    return request(url).then(function (data) {
      var items = data.results || [];
      empty.hidden = items.length > 0;
      items.forEach(function (item) {
        var card = element("article", "tn-card");
        var row = element("div", "tn-card-title-row");
        row.appendChild(element("p", "tn-card-title", item.label || "#" + item.id));
        row.appendChild(element("span", "tn-badge tn-badge--soft", kindLabels[item.kind] || item.kind || "—"));
        card.appendChild(row);
        var meta = resultMeta(item);
        if (meta) card.appendChild(element("p", "tn-card-meta", meta));
        results.appendChild(card);
      });
    });
  }

  function payloadText(payload) {
    if (!payload || typeof payload !== "object") return "";
    var parts = [];
    if (payload.reason) parts.push("Причина: " + payload.reason);
    if (payload.wagon_number) parts.push("Вагон: " + payload.wagon_number);
    if (payload.block) parts.push("Блок: " + payload.block);
    if (payload.external_reference) parts.push("Внешний номер: " + payload.external_reference);
    return parts.join(" · ");
  }

  function loadEvents() {
    return request("/events?limit=100").then(function (data) {
      var events = data.events || [];
      var list = document.getElementById("eventsList");
      clear(list);
      document.getElementById("eventsEmpty").hidden = events.length > 0;
      events.forEach(function (event) {
        var item = element("li");
        item.appendChild(element("time", "", formatDate(event.occurred_at)));
        item.appendChild(element("strong", "", eventLabels[event.event_type] || event.event_type));
        var subject = [event.actor_name, event.subject_public_id].filter(Boolean).join(" · ");
        if (subject) item.appendChild(element("p", "", subject));
        var detail = payloadText(event.payload);
        if (detail) item.appendChild(element("p", "", detail));
        list.appendChild(item);
      });
    });
  }

  function loadReport() {
    var date = document.getElementById("reportDate").value;
    return request("/reports/summary" + (date ? "?date=" + encodeURIComponent(date) : "")).then(function (data) {
      var stats = document.getElementById("reportStats");
      clear(stats);
      stats.appendChild(statCard(data.confirmed_intakes || 0, "подтверждённых приёмок"));
      stats.appendChild(statCard(data.received_blocks || 0, "принятых блоков"));
      stats.appendChild(statCard(data.missing_blocks || 0, "недостача"));
      stats.appendChild(statCard(data.damaged_blocks || 0, "повреждений"));
      var list = document.getElementById("reportIntakes");
      clear(list);
      (data.intakes || []).filter(function (intake) {
        return intake.status === "confirmed" || intake.status === "discrepancy";
      }).forEach(function (intake) { list.appendChild(makeIntakeCard(intake)); });
    });
  }

  function loadCatalog() {
    return Promise.all([
      request("/catalog/vehicles"),
      request("/catalog/wagons"),
      request("/catalog/slots"),
      request("/catalog/dead-ends")
    ]).then(function (results) {
      var vehicles = results[0].vehicles || [];
      var wagons = results[1].wagons || [];
      var slots = results[2].slots || [];
      var deadEnds = results[3].dead_ends || [];
      var positionByWagon = {};
      slots.forEach(function (slot) { if (slot.wagon_number) positionByWagon[slot.wagon_number] = slot; });
      renderCatalogVehicles(vehicles);
      renderCatalogWagons(wagons, positionByWagon);
      renderCatalogDeadEnds(deadEnds);
      setError("");
    });
  }

  function renderCatalogVehicles(vehicles) {
    var list = document.getElementById("catalogVehicles");
    clear(list);
    document.getElementById("catalogVehiclesEmpty").hidden = vehicles.length > 0;
    vehicles.forEach(function (vehicle) {
      var card = element("article", "tn-card");
      card.appendChild(element("p", "tn-card-title", vehicle.plate || "—"));
      card.appendChild(element("p", "tn-card-meta", (vehicle.brand || "марка не указана") + " · " + (vehicle.driver || "водитель не назначен")));
      list.appendChild(card);
    });
  }

  function renderCatalogWagons(wagons, positionByWagon) {
    var list = document.getElementById("catalogWagons");
    clear(list);
    document.getElementById("catalogWagonsEmpty").hidden = wagons.length > 0;
    wagons.forEach(function (wagon) {
      var card = element("article", "tn-card");
      card.appendChild(element("p", "tn-card-title", "Вагон " + (wagon.number || "—")));
      var position = positionByWagon[wagon.number];
      var parts = [];
      if (wagon.stage) parts.push("стадия: " + wagon.stage);
      var zone = position ? position.zone : (wagon.planned_zone || null);
      if (zone) parts.push("зона: " + zone);
      if (position) parts.push("слот " + position.slot_index);
      card.appendChild(element("p", "tn-card-meta", parts.length ? parts.join(" · ") : "позиция не задана"));
      card.addEventListener("click", function () { openWagonHistory(wagon.number); });
      list.appendChild(card);
    });
  }

  function renderCatalogDeadEnds(deadEnds) {
    var wrap = document.getElementById("catalogDeadEnds");
    clear(wrap);
    deadEnds.forEach(function (deadEnd) {
      var box = element("section", "tn-dead-end");
      var head = element("div", "tn-dead-end-head");
      head.appendChild(element("p", "tn-dead-end-name", deadEnd.name || "Тупик"));
      head.appendChild(element("p", "tn-dead-end-meta", "слотов: " + (deadEnd.slots || 10)));
      box.appendChild(head);
      var grid = element("div", "tn-slot-grid");
      (deadEnd.positions || []).forEach(function (position) {
        var occupied = Boolean(position.wagon_number);
        var cell = element("div", "tn-slot" + (occupied ? " tn-slot--occupied" : ""));
        cell.textContent = occupied ? position.wagon_number : String(position.slot_index);
        cell.title = "Слот " + position.slot_index + (occupied ? " · вагон " + position.wagon_number : " · свободен");
        grid.appendChild(cell);
      });
      box.appendChild(grid);
      wrap.appendChild(box);
    });
  }

  function adminCard(title, meta, onEdit) {
    var card = element("article", "tn-card");
    card.appendChild(element("p", "tn-card-title", title));
    if (meta) card.appendChild(element("p", "tn-card-meta", meta));
    if (onEdit) {
      var actions = element("div", "tn-card-actions");
      var button = element("button", "tn-button tn-button--secondary", "Изменить");
      button.type = "button";
      button.addEventListener("click", onEdit);
      actions.appendChild(button);
      card.appendChild(actions);
    }
    return card;
  }

  function renderAdminBlockTypes(types) {
    var list = document.getElementById("adminBlockTypes");
    clear(list);
    types.forEach(function (item) {
      var card = adminCard("Плита " + item.letter, "вес: " + item.weight_kg + " кг · стоимость: " + item.price_rub + " ₽", function () {
        var weight = window.prompt("Вес плиты " + item.letter + ", кг", item.weight_kg);
        if (weight === null) return;
        var price = window.prompt("Стоимость плиты " + item.letter + ", ₽", item.price_rub);
        if (price === null) return;
        jsonRequest("/admin/block-types/" + encodeURIComponent(item.letter), "POST", { weight_kg: weight, price_rub: price })
          .then(function () { showToast("Справочник плит обновлён."); return loadAdmin(); })
          .catch(function (error) { showToast(error.message || "Не удалось сохранить"); });
      });
      list.appendChild(card);
    });
  }

  function renderAdminWagonSchemes(schemes) {
    var list = document.getElementById("adminWagonSchemes");
    clear(list);
    schemes.forEach(function (item) {
      list.appendChild(adminCard(
        item.label || item.code,
        "максимум плит: " + item.max_blocks + (item.allowed_letters ? " · буквы: " + item.allowed_letters : "")
      ));
    });
  }

  function renderAdminCarriers(carriers) {
    var list = document.getElementById("adminCarriers");
    clear(list);
    carriers.forEach(function (item) {
      list.appendChild(adminCard(
        item.legal_form + " " + item.name,
        "ИНН " + item.inn + (item.active ? "" : " · отключён"),
        function () { fillAdminCarrierForm(item); }
      ));
    });
  }

  function renderAdminDrivers(drivers) {
    var list = document.getElementById("adminDrivers");
    clear(list);
    drivers.forEach(function (item) {
      var meta = [item.phone || "телефон не указан", item.license_number ? "права " + item.license_number : "права не указаны"];
      if (!item.active) meta.push("отключён");
      list.appendChild(adminCard(item.full_name, meta.join(" · "), function () { fillAdminDriverForm(item); }));
    });
  }

  function renderAdminVehicles(vehicles) {
    var list = document.getElementById("adminVehicles");
    clear(list);
    vehicles.forEach(function (item) {
      var meta = [item.carrier_name || "перевозчик не указан", item.default_driver_name ? "водитель: " + item.default_driver_name : "водитель не назначен"];
      if (!item.active) meta.push("отключена");
      list.appendChild(adminCard(item.plate, meta.join(" · "), function () { fillAdminVehicleForm(item); }));
    });
  }

  function renderAdminWagons(wagons) {
    var list = document.getElementById("adminWagons");
    clear(list);
    wagons.forEach(function (item) {
      var meta = [item.scheme_label || "схема не задана"];
      if (item.current_dead_end) meta.push("тупик " + item.current_dead_end + ", слот " + item.current_slot);
      if (!item.active) meta.push("отключён");
      list.appendChild(adminCard("Вагон " + item.wagon_number, meta.join(" · "), function () { fillAdminWagonForm(item); }));
    });
  }

  function renderAdminAccounts(accounts) {
    var list = document.getElementById("adminAccounts");
    clear(list);
    accounts.forEach(function (item) {
      list.appendChild(adminCard(
        item.display_name || item.role_code,
        (roleLabels[item.role_code] || item.role_code) + (item.login ? " · вход: " + item.login : "") + (item.active ? "" : " · отключена")
      ));
    });
  }

  function renderAdminActions(actions) {
    var list = document.getElementById("adminActions");
    clear(list);
    actions.forEach(function (item) {
      var card = element("article", "tn-card");
      card.appendChild(element("p", "tn-card-title", (item.actor_name || roleLabels[item.role_code] || item.role_code) + " · " + item.action));
      var meta = [formatDate(item.created_at)];
      if (item.subject_type) meta.push(item.subject_type + (item.subject_id ? " " + item.subject_id : ""));
      card.appendChild(element("p", "tn-card-meta", meta.join(" · ")));
      list.appendChild(card);
    });
  }

  function fillAdminCarrierForm(item) {
    var form = document.getElementById("adminCarrierForm");
    form.elements.id.value = item.id;
    form.elements.legal_form.value = item.legal_form;
    form.elements.name.value = item.name;
    form.elements.inn.value = item.inn;
    form.elements.active.checked = Boolean(item.active);
  }

  function fillAdminDriverForm(item) {
    var form = document.getElementById("adminDriverForm");
    form.elements.id.value = item.id;
    form.elements.full_name.value = item.full_name;
    form.elements.phone.value = item.phone || "";
    form.elements.license_number.value = item.license_number || "";
    form.elements.active.checked = Boolean(item.active);
  }

  function fillAdminVehicleForm(item) {
    var form = document.getElementById("adminVehicleForm");
    form.elements.id.value = item.id;
    form.elements.plate.value = item.plate;
    form.elements.carrier_id.value = item.carrier_id || "";
    form.elements.default_driver_id.value = item.default_driver_id || "";
    form.elements.note.value = item.note || "";
    form.elements.active.checked = Boolean(item.active);
  }

  function fillAdminWagonForm(item) {
    var form = document.getElementById("adminWagonForm");
    form.elements.id.value = item.id;
    form.elements.wagon_number.value = item.wagon_number;
    form.elements.scheme_code.value = item.scheme_code || "";
    form.elements.note.value = item.note || "";
    form.elements.active.checked = Boolean(item.active);
  }

  function fillAdminSelects(catalogs) {
    var carrierSelect = document.getElementById("adminVehicleForm").elements.carrier_id;
    var driverSelect = document.getElementById("adminVehicleForm").elements.default_driver_id;
    var schemeSelect = document.getElementById("adminWagonForm").elements.scheme_code;
    var carrierValue = carrierSelect.value;
    var driverValue = driverSelect.value;
    while (carrierSelect.options.length > 1) carrierSelect.remove(1);
    while (driverSelect.options.length > 1) driverSelect.remove(1);
    while (schemeSelect.options.length > 1) schemeSelect.remove(1);
    (catalogs.carriers || []).forEach(function (item) {
      carrierSelect.appendChild(new Option(item.legal_form + " " + item.name, item.id));
    });
    (catalogs.drivers || []).forEach(function (item) {
      driverSelect.appendChild(new Option(item.full_name, item.id));
    });
    (catalogs.wagon_schemes || []).forEach(function (item) {
      schemeSelect.appendChild(new Option(item.label || item.code, item.code));
    });
    carrierSelect.value = carrierValue;
    driverSelect.value = driverValue;
  }

  function loadAdmin() {
    return Promise.all([
      request("/admin/catalogs"),
      request("/admin/accounts"),
      request("/admin/actions?limit=50")
    ]).then(function (results) {
      var catalogs = results[0] || {};
      fillAdminSelects(catalogs);
      renderAdminBlockTypes(catalogs.block_types || []);
      renderAdminWagonSchemes(catalogs.wagon_schemes || []);
      renderAdminCarriers(catalogs.carriers || []);
      renderAdminDrivers(catalogs.drivers || []);
      renderAdminVehicles(catalogs.vehicles || []);
      renderAdminWagons(catalogs.wagon_fleet || []);
      renderAdminAccounts(results[1].accounts || []);
      renderAdminActions(results[2].actions || []);
      setError("");
    });
  }

  function adminFormPayload(form, fields) {
    var payload = {};
    fields.forEach(function (name) {
      var field = form.elements[name];
      if (!field) return;
      if (field.type === "checkbox") payload[name] = field.checked;
      else if (field.value !== "") payload[name] = field.value.trim ? field.value.trim() : field.value;
    });
    return payload;
  }

  function submitAdminForm(event, path, fields, message) {
    event.preventDefault();
    var form = event.currentTarget;
    if (!form.checkValidity()) { form.reportValidity(); return; }
    var button = form.querySelector("button[type='submit']");
    button.disabled = true;
    jsonRequest(path, "POST", adminFormPayload(form, fields))
      .then(function () { form.reset(); form.elements.id.value = ""; showToast(message); return loadAdmin(); })
      .catch(function (error) { showToast(error.message || "Не удалось сохранить"); })
      .finally(function () { button.disabled = false; });
  }

  function tripStatusBadge(status) {
    return element("span", "tn-badge tn-badge--" + String(status || "").replace(/[^a-z_]/g, ""), tripStatusLabels[status] || status || "—");
  }

  function receiptStatusBadge(status) {
    return element("span", "tn-badge tn-badge--" + String(status || "").replace(/[^a-z_]/g, ""), receiptStatusLabels[status] || status || "—");
  }

  function makeTripBlockRow() {
    var row = element("div", "tn-form-grid");
    var letterField = element("label", "tn-field", "Буква *");
    var select = element("select");
    tripBlockLetters.forEach(function (letter) {
      var option = element("option", "", letter);
      option.value = letter;
      select.appendChild(option);
    });
    select.name = "letter";
    letterField.appendChild(select);
    row.appendChild(letterField);
    var numberField = element("label", "tn-field", "Номер блока *");
    var numberInput = element("input");
    numberInput.name = "number";
    numberInput.maxLength = 40;
    numberInput.required = true;
    numberField.appendChild(numberInput);
    row.appendChild(numberField);
    var suffixField = element("label", "tn-field", "Суффикс");
    var suffixInput = element("input");
    suffixInput.name = "suffix";
    suffixInput.maxLength = 8;
    suffixField.appendChild(suffixInput);
    row.appendChild(suffixField);
    var weightField = element("label", "tn-field", "Вес, кг");
    var weightInput = element("input");
    weightInput.name = "weight_kg";
    weightInput.type = "number";
    weightInput.min = "1";
    weightField.appendChild(weightInput);
    row.appendChild(weightField);
    var priceField = element("label", "tn-field", "Стоимость, ₽");
    var priceInput = element("input");
    priceInput.name = "price_rub";
    priceInput.type = "number";
    priceInput.min = "0";
    priceInput.step = "0.01";
    priceField.appendChild(priceInput);
    row.appendChild(priceField);
    var remove = element("button", "tn-button tn-button--secondary", "Убрать");
    remove.type = "button";
    remove.addEventListener("click", function () {
      if (row.parentNode) row.parentNode.removeChild(row);
      syncTripBlockButtons();
    });
    row.appendChild(remove);
    return row;
  }

  function syncTripBlockButtons() {
    var list = document.getElementById("tripBlocksList");
    var rows = list ? list.querySelectorAll(".tn-form-grid") : [];
    var addButton = document.getElementById("addTripBlockButton");
    if (addButton) addButton.disabled = rows.length >= 6;
    rows.forEach(function (row) {
      var remove = row.querySelector("button");
      if (remove) remove.disabled = rows.length <= 3;
    });
  }

  function ensureTripBlockRows() {
    var list = document.getElementById("tripBlocksList");
    if (!list) return;
    while (list.querySelectorAll(".tn-form-grid").length < 3) list.appendChild(makeTripBlockRow());
    syncTripBlockButtons();
  }

  function addTripBlockRow() {
    var list = document.getElementById("tripBlocksList");
    if (!list) return;
    if (list.querySelectorAll(".tn-form-grid").length >= 6) { showToast("Максимум 6 блоков в рейсе."); return; }
    list.appendChild(makeTripBlockRow());
    syncTripBlockButtons();
  }

  function submitTripForm(event) {
    event.preventDefault();
    var form = event.currentTarget;
    if (!form.checkValidity()) { form.reportValidity(); return; }
    var rows = Array.from(form.querySelectorAll("#tripBlocksList .tn-form-grid"));
    if (rows.length < 3) { showToast("Минимум 3 блока в рейсе."); return; }
    var blocks = rows.map(function (row) {
      var get = function (name) {
        var el = row.querySelector("[name='" + name + "']");
        return el ? el.value.trim() : "";
      };
      return {
        letter: get("letter"),
        number: get("number"),
        suffix: get("suffix"),
        weight_kg: get("weight_kg"),
        price_rub: get("price_rub")
      };
    });
    var payload = {
      vehicle_plate: form.elements.vehicle_plate.value.trim(),
      trip_date: form.elements.trip_date.value || null,
      blocks: blocks
    };
    var button = form.querySelector("button[type='submit']");
    button.disabled = true;
    jsonRequest("/trips", "POST", payload)
      .then(function () {
        form.reset();
        ensureTripBlockRows();
        showToast("Рейс создан.");
        return loadTrips();
      })
      .catch(function (error) { showToast(error.message || "Не удалось создать рейс"); })
      .finally(function () { button.disabled = false; });
  }

  function issueTripTtn(trip, button) {
    button.disabled = true;
    jsonRequest("/trips/" + encodeURIComponent(trip.public_id) + "/ttn-number", "POST", {})
      .then(function () { showToast("Номер ТТН выдан."); return loadTrips(); })
      .catch(function (error) { showToast(error.message || "Не удалось выдать номер ТТН"); button.disabled = false; });
  }

  function printTripTtn(trip, button) {
    button.disabled = true;
    jsonRequest("/trips/" + encodeURIComponent(trip.public_id) + "/ttn-copies", "POST", {})
      .then(function () { showToast("ТТН сформирована — 4 экземпляра."); return loadTrips(); })
      .catch(function (error) { showToast(error.message || "Не удалось сформировать ТТН"); button.disabled = false; });
  }

  function showTripCopies(trip) {
    request("/trips/" + encodeURIComponent(trip.public_id) + "/ttn-copies").then(function (data) {
      var copies = data.copies || [];
      if (!copies.length) { showToast("Экземпляры ещё не сформированы."); return; }
      showToast(copies.map(function (copy) { return "копия " + copy.copy_number; }).join(" · "));
    }).catch(function (error) { showToast(error.message || "Не удалось загрузить экземпляры"); });
  }

  function makeTripCard(trip) {
    var card = element("article", "tn-card");
    var row = element("div", "tn-card-title-row");
    var heading = element("div");
    heading.appendChild(element("p", "tn-card-title", trip.ttn_number || "номер не выдан"));
    var meta = [];
    if (trip.vehicle_plate) meta.push(trip.vehicle_plate);
    if (trip.driver_name) meta.push(trip.driver_name);
    if (trip.carrier_name) meta.push(trip.carrier_name);
    meta.push("блоков: " + trip.block_count);
    meta.push("вес: " + trip.total_weight_kg + " кг");
    heading.appendChild(element("p", "tn-card-meta", meta.join(" · ")));
    row.appendChild(heading);
    row.appendChild(tripStatusBadge(trip.status));
    card.appendChild(row);
    if (trip.trip_date) card.appendChild(element("p", "tn-card-meta", "Дата: " + trip.trip_date));
    var actions = element("div", "tn-card-actions");
    if (trip.status === "draft") {
      var ttnButton = element("button", "tn-button tn-button--primary", "Выдать номер ТТН");
      ttnButton.type = "button";
      ttnButton.addEventListener("click", function () { issueTripTtn(trip, ttnButton); });
      actions.appendChild(ttnButton);
    }
    if (trip.status === "ttn_issued") {
      var printButton = element("button", "tn-button tn-button--primary", "Напечатать 4 экземпляра");
      printButton.type = "button";
      printButton.addEventListener("click", function () { printTripTtn(trip, printButton); });
      actions.appendChild(printButton);
    }
    if (trip.ttn_number) {
      for (var n = 1; n <= 4; n += 1) {
        var link = element("a", "tn-button tn-button--secondary", "Скачать " + n);
        link.href = API + "/trips/" + encodeURIComponent(trip.public_id) + "/ttn-copies/" + n;
        link.setAttribute("download", "");
        actions.appendChild(link);
      }
      var show = element("button", "tn-button tn-button--secondary", "Показать экземпляры");
      show.type = "button";
      show.addEventListener("click", function () { showTripCopies(trip); });
      actions.appendChild(show);
    }
    card.appendChild(actions);
    return card;
  }

  function renderTrips(target, trips, emptyNode) {
    clear(target);
    emptyNode.hidden = trips.length > 0;
    trips.forEach(function (trip) { target.appendChild(makeTripCard(trip)); });
  }

  function loadTrips() {
    return request("/trips?limit=50").then(function (data) {
      var trips = data.trips || [];
      renderTrips(document.getElementById("tripsList"), trips, document.getElementById("tripsEmpty"));
      setError("");
    });
  }

  function makeReceiptCard(receipt) {
    var card = element("article", "tn-card");
    var row = element("div", "tn-card-title-row");
    var heading = element("div");
    heading.appendChild(element("p", "tn-card-title", receipt.ttn_number || "Расписка без номера"));
    var meta = [];
    if (receipt.vehicle_plate) meta.push(receipt.vehicle_plate);
    if (receipt.driver_name) meta.push(receipt.driver_name);
    if (receipt.carrier_name) meta.push(receipt.carrier_name);
    if (receipt.carrier_inn) meta.push("ИНН " + receipt.carrier_inn);
    if (receipt.blocks_summary) meta.push(receipt.blocks_summary);
    meta.push("вес: " + receipt.total_weight_kg + " кг");
    heading.appendChild(element("p", "tn-card-meta", meta.join(" · ")));
    row.appendChild(heading);
    row.appendChild(receiptStatusBadge(receipt.status));
    card.appendChild(row);
    var timeParts = [];
    if (receipt.sent_at) timeParts.push("отправлена: " + formatDate(receipt.sent_at));
    if (receipt.returned_at) timeParts.push("вернулась: " + formatDate(receipt.returned_at));
    if (receipt.external_reference) timeParts.push("ЭДО: " + receipt.external_reference);
    if (timeParts.length) card.appendChild(element("p", "tn-card-meta", timeParts.join(" · ")));
    var actions = element("div", "tn-card-actions");
    if (receipt.status === "draft") {
      var sendButton = element("button", "tn-button tn-button--primary", "Отметить отправленной");
      sendButton.type = "button";
      sendButton.addEventListener("click", function () { markReceiptSent(receipt, sendButton); });
      actions.appendChild(sendButton);
    }
    if (receipt.status === "sent") {
      var returnButton = element("button", "tn-button tn-button--primary", "Отметить вернувшейся");
      returnButton.type = "button";
      returnButton.addEventListener("click", function () { markReceiptReturned(receipt, returnButton); });
      actions.appendChild(returnButton);
    }
    var blocksButton = element("button", "tn-button tn-button--secondary", "Расшифровка блоков");
    blocksButton.type = "button";
    blocksButton.addEventListener("click", function () { showReceiptBlocks(receipt); });
    actions.appendChild(blocksButton);
    card.appendChild(actions);
    return card;
  }

  function markReceiptSent(receipt, button) {
    var reference = window.prompt("Ссылка или номер в ЭДО:", "");
    if (reference === null) return;
    button.disabled = true;
    jsonRequest("/receipts/" + encodeURIComponent(receipt.public_id) + "/send", "POST", { external_reference: reference.trim() })
      .then(function () { showToast("Расписка отмечена отправленной."); return loadReceipts(); })
      .catch(function (error) { showToast(error.message || "Не удалось отметить отправку"); button.disabled = false; });
  }

  function markReceiptReturned(receipt, button) {
    button.disabled = true;
    jsonRequest("/receipts/" + encodeURIComponent(receipt.public_id) + "/return", "POST", {})
      .then(function () { showToast("Расписка отмечена вернувшейся."); return loadReceipts(); })
      .catch(function (error) { showToast(error.message || "Не удалось отметить возврат"); button.disabled = false; });
  }

  function showReceiptBlocks(receipt) {
    request("/receipts/" + encodeURIComponent(receipt.public_id) + "/blocks").then(function (data) {
      showToast(data.blocks || "Расшифровка блоков пустая.");
    }).catch(function (error) { showToast(error.message || "Не удалось получить расшифровку"); });
  }

  function fillReceiptTripSelect(trips) {
    var select = document.getElementById("receiptForm").elements.trip_public_id;
    var current = select.value;
    while (select.options.length > 1) select.remove(1);
    trips.filter(function (trip) { return trip.status !== "draft"; }).forEach(function (trip) {
      var label = (trip.ttn_number || trip.public_id) + " · " + (trip.vehicle_plate || "") + (trip.driver_name ? " · " + trip.driver_name : "");
      select.appendChild(new Option(label, trip.public_id));
    });
    select.value = current;
  }

  function renderReceipts(target, receipts, emptyNode) {
    clear(target);
    emptyNode.hidden = receipts.length > 0;
    receipts.forEach(function (receipt) { target.appendChild(makeReceiptCard(receipt)); });
  }

  function loadReceipts() {
    return Promise.all([
      request("/receipts?limit=50"),
      request("/trips?limit=50")
    ]).then(function (results) {
      var receipts = results[0].receipts || [];
      var trips = results[1].trips || [];
      renderReceipts(document.getElementById("receiptsList"), receipts, document.getElementById("receiptsEmpty"));
      fillReceiptTripSelect(trips);
      setError("");
    });
  }

  function submitReceiptForm(event) {
    event.preventDefault();
    var form = event.currentTarget;
    if (!form.checkValidity()) { form.reportValidity(); return; }
    var button = form.querySelector("button[type='submit']");
    button.disabled = true;
    jsonRequest("/receipts", "POST", {
      trip_public_id: form.elements.trip_public_id.value,
      note: form.elements.note.value.trim()
    })
      .then(function () { form.reset(); showToast("Расписка создана."); return loadReceipts(); })
      .catch(function (error) { showToast(error.message || "Не удалось создать расписку"); })
      .finally(function () { button.disabled = false; });
  }

  function loadOperatorPanel() {
    return request("/trips?limit=50").then(function (data) {
      var trips = (data.trips || []).filter(function (trip) { return operatorTripStatuses[trip.status] === true; });
      var list = document.getElementById("operatorTripsList");
      clear(list);
      document.getElementById("operatorTripsEmpty").hidden = trips.length > 0;
      trips.forEach(function (trip) {
        var card = element("article", "tn-card");
        var row = element("div", "tn-card-title-row");
        var heading = element("div");
        heading.appendChild(element("p", "tn-card-title", trip.ttn_number || "номер не выдан"));
        var meta = [];
        if (trip.vehicle_plate) meta.push(trip.vehicle_plate);
        if (trip.driver_name) meta.push(trip.driver_name);
        if (trip.carrier_name) meta.push(trip.carrier_name);
        meta.push("блоков: " + trip.block_count);
        meta.push("вес: " + trip.total_weight_kg + " кг");
        heading.appendChild(element("p", "tn-card-meta", meta.join(" · ")));
        row.appendChild(heading);
        row.appendChild(tripStatusBadge(trip.status));
        card.appendChild(row);
        list.appendChild(card);
      });
      setError("");
    });
  }

  function loadPanel(name) {
    var loaders = { today: loadDashboard, reception: loadIntakes, yard: loadYard, wagons: loadWagons, journal: loadEvents, reports: loadReport, catalog: loadCatalog, admin: loadAdmin, trips: loadTrips, receipts: loadReceipts, operator: loadOperatorPanel };
    if (!loaders[name]) return;
    loaders[name]().catch(function (error) { setError(error.message || "Не удалось загрузить данные"); });
  }

  function closeModal() {
    modal.hidden = true;
    clear(modalContent);
    wagonModal.hidden = true;
    clear(wagonModalContent);
  }

  function openWagonHistory(number) {
    wagonModal.hidden = false;
    wagonModalTitle.textContent = "Вагон " + number;
    clear(wagonModalContent);
    wagonModalContent.appendChild(element("p", "tn-help", "Загрузка…"));
    request("/wagons/" + encodeURIComponent(number) + "/history").then(function (data) {
      renderWagonHistory(data.history);
    }).catch(function (error) {
      wagonModal.hidden = true;
      showToast(error.message || "Не удалось открыть историю вагона");
    });
  }

  function renderWagonHistory(history) {
    clear(wagonModalContent);
    var wagon = history.wagon;
    if (wagon) {
      var statusCard = element("section", "tn-form-card");
      statusCard.appendChild(element("p", "tn-card-title", "Этап: " + (statusLabels[wagon.status] || wagon.status)));
      var meta = [];
      if (wagon.loaded_at) meta.push("загрузка " + formatDate(wagon.loaded_at));
      if (wagon.dispatched_at) meta.push("отправлен " + formatDate(wagon.dispatched_at));
      if (wagon.arrived_kodar_at) meta.push("Кодар " + formatDate(wagon.arrived_kodar_at));
      if (wagon.unloaded_bts_east_at) meta.push("БТС Восток " + formatDate(wagon.unloaded_bts_east_at));
      if (wagon.returned_empty_at) meta.push("порожний " + formatDate(wagon.returned_empty_at));
      statusCard.appendChild(element("p", "tn-card-meta", meta.join(" · ") || "цикл ещё не начат"));
      wagonModalContent.appendChild(statusCard);
    }
    var circles = element("section", "tn-section");
    circles.appendChild(element("h3", "", "Круги (история отправок)"));
    var trips = history.trips || [];
    circles.appendChild(element("p", "tn-help", "Сходил кругов: " + trips.length));
    var tripsList = element("div", "tn-card-list");
    if (!trips.length) tripsList.appendChild(element("p", "tn-empty", "В истории старой Таксимо отправок нет."));
    trips.forEach(function (trip) {
      var card = element("article", "tn-card tn-card--legacy");
      card.appendChild(element("p", "tn-card-title", (trip.customer || "—") + " · плит " + (trip.slab_count || 0)));
      var m = [];
      if (trip.slot_zone) m.push(trip.slot_zone + " · слот " + trip.slot_index);
      if (trip.status) m.push("этап: " + trip.status);
      if (trip.return_status) m.push("возврат: " + trip.return_status);
      if (trip.dispatched_at) m.push("отправка " + formatDate(trip.dispatched_at));
      if (trip.received_at) m.push("возврат " + formatDate(trip.received_at));
      card.appendChild(element("p", "tn-card-meta", m.join(" · ") || "—"));
      var blocks = trip.blocks || [];
      if (blocks.length) {
        var labels = blocks.map(function (b) { return b.letter + " " + b.number; });
        card.appendChild(element("p", "tn-card-meta", "Блоки: " + labels.join(", ")));
      }
      tripsList.appendChild(card);
    });
    circles.appendChild(tripsList);
    wagonModalContent.appendChild(circles);
    var loads = history.loads || [];
    var loadsSection = element("section", "tn-section");
    loadsSection.appendChild(element("h3", "", "Блоки в вагоне (" + loads.length + ")"));
    var loadsList = element("div", "tn-card-list");
    if (!loads.length) loadsList.appendChild(element("p", "tn-empty", "Сейчас блоков нет."));
    loads.forEach(function (load) {
      var card = element("article", "tn-card");
      card.appendChild(element("p", "tn-card-title", load.block_type + " " + load.block_number));
      card.appendChild(element("p", "tn-card-meta", "загружен " + formatDate(load.loaded_at) + " · " + load.weight_kg + " кг"));
      loadsList.appendChild(card);
    });
    loadsSection.appendChild(loadsList);
    wagonModalContent.appendChild(loadsSection);
  }

  function openIntake(publicId) {
    modal.hidden = false;
    modalTitle.textContent = "Загрузка…";
    clear(modalContent);
    request("/intakes/" + encodeURIComponent(publicId)).then(function (data) {
      renderIntakeModal(data.intake);
    }).catch(function (error) {
      closeModal();
      showToast(error.message || "Не удалось открыть приёмку");
    });
  }

  function intakeSummary(intake) {
    var summary = element("div", "tn-card");
    summary.appendChild(element("p", "tn-card-title", intake.ttn_number || intake.source_reference || "Приёмка без номера"));
    summary.appendChild(element("p", "tn-card-meta", intakeMeta(intake)));
    summary.appendChild(element("p", "tn-card-meta", "Статус: " + (intake.cancelled ? "отменена" : (statusLabels[intake.status] || intake.status))));
    return summary;
  }

  function renderReadOnlyLines(lines) {
    if (!lines || !lines.length) return null;
    var section = element("section", "tn-card-list");
    lines.forEach(function (line) {
      var card = element("article", "tn-card");
      card.appendChild(element("p", "tn-card-title", line.block_type + " " + line.block_number));
      var parts = [line.receipt_state, line.condition_code === "damage" ? "повреждение" : "", line.yard_x ? "X=" + line.yard_x + ", Y=" + line.yard_y : "", line.discrepancy_note || ""].filter(Boolean);
      card.appendChild(element("p", "tn-card-meta", parts.join(" · ")));
      section.appendChild(card);
    });
    return section;
  }

  function renderIntakeModal(intake) {
    clear(modalContent);
    modalTitle.textContent = intake.ttn_number || intake.source_reference || "Приёмка";
    modalContent.appendChild(intakeSummary(intake));
    var docsWrap = element("div", "tn-intake-docs");
    modalContent.appendChild(docsWrap);
    request("/intakes/" + encodeURIComponent(intake.public_id) + "/documents").then(function (data) {
      var docs = data.documents || [];
      if (!docs.length) return;
      docsWrap.appendChild(element("p", "tn-help", "Копии ТТН:"));
      var row = element("div", "tn-intake-docs-row");
      docs.forEach(function (doc) {
        var btn = element("a", "tn-button tn-button--secondary", "Скачать · копия " + doc.copy_number);
        btn.href = API + "/intakes/" + encodeURIComponent(intake.public_id) + "/documents/" + doc.copy_number;
        btn.setAttribute("download", doc.filename || "");
        row.appendChild(btn);
      });
      docsWrap.appendChild(row);
    }).catch(function () {});
    var isFinal = ["confirmed", "discrepancy"].includes(intake.status) || intake.cancelled;
    if (isFinal) {
      var lines = renderReadOnlyLines(intake.lines);
      if (lines) modalContent.appendChild(lines);
      if (isOperator1() && !intake.cancelled) {
        var cancel = element("button", "tn-button tn-button--danger", "Отменить подтверждённую приёмку");
        cancel.type = "button";
        cancel.addEventListener("click", function () { cancelIntake(intake.public_id, cancel); });
        modalContent.appendChild(cancel);
      }
      return;
    }
    if (intake.status !== "in_progress" || Number(intake.locked_by_operator_id || 0) !== Number(state.operator.id)) {
      var claim = element("button", "tn-button tn-button--primary", "Взять в работу на 20 минут");
      claim.type = "button";
      claim.addEventListener("click", function () {
        claim.disabled = true;
        jsonRequest("/intakes/" + encodeURIComponent(intake.public_id) + "/claim", "POST", {}).then(function (data) {
          renderIntakeModal(data.intake);
          loadDashboard().catch(function () {});
          loadIntakes().catch(function () {});
        }).catch(function (error) { showToast(error.message || "Не удалось взять приёмку в работу"); claim.disabled = false; });
      });
      modalContent.appendChild(claim);
      return;
    }
    if (intake.source_system === "rumex" && !intake.arrived_at) {
      modalContent.appendChild(intakeFactAction(intake, "arrival", "Зафиксировать прибытие машины", "Прибытие машины зафиксировано."));
      return;
    }
    if (intake.source_system === "rumex" && !intake.crane_started_at) {
      modalContent.appendChild(intakeFactAction(intake, "crane-started", "Зафиксировать начало работы крана", "Начало работы крана зафиксировано."));
      return;
    }
    if (intake.source_system === "rumex" && !intake.crane_ended_at) {
      modalContent.appendChild(intakeFactAction(intake, "crane-ended", "Зафиксировать окончание работы крана", "Окончание работы крана зафиксировано."));
      return;
    }
    modalContent.appendChild(receiptForm(intake));
  }

  function intakeFactAction(intake, endpoint, label, successText) {
    var section = element("section", "tn-form-card");
    section.appendChild(element("h3", "", "Физический факт"));
    section.appendChild(element("p", "tn-help", "Этот факт будет записан отдельно и не изменит ЭР или ТТН РУМЕКС."));
    var button = element("button", "tn-button tn-button--primary", label);
    button.type = "button";
    button.addEventListener("click", function () {
      if (!window.confirm(label + "? Запись останется в журнале.")) return;
      button.disabled = true;
      jsonRequest("/intakes/" + encodeURIComponent(intake.public_id) + "/" + endpoint, "POST", {})
        .then(function (data) {
          showToast(successText);
          renderIntakeModal(data.intake);
          loadDashboard().catch(function () {});
          loadIntakes().catch(function () {});
        })
        .catch(function (error) { showToast(error.message || "Не удалось зафиксировать факт"); button.disabled = false; });
    });
    section.appendChild(button);
    return section;
  }

  function lineField(label, type, name, value, options) {
    var field = element("label", "tn-field", label);
    var control;
    options = options || {};
    if (type === "select") {
      control = element("select");
      (options.items || []).forEach(function (item) {
        var option = element("option", "", item.label);
        option.value = item.value;
        option.selected = item.value === value;
        control.appendChild(option);
      });
    } else if (type === "textarea") {
      control = element("textarea");
      control.value = value || "";
    } else {
      control = element("input");
      control.type = type;
      control.value = value === undefined || value === null ? "" : value;
    }
    control.name = name;
    if (options.required) control.required = true;
    if (options.readOnly) control.readOnly = true;
    if (options.min !== undefined) control.min = String(options.min);
    if (options.max !== undefined) control.max = String(options.max);
    if (options.maxLength) control.maxLength = options.maxLength;
    field.appendChild(control);
    return field;
  }

  function loadWagonPicker(onPick, selected) {
    var wrap = element("div", "tn-wagon-picker");
    var parkPanel = element("div", "tn-wagon-picker-park");
    parkPanel.hidden = true;
    wrap.appendChild(parkPanel);

    function isSelected(position) {
      return selected && String(position.wagon_number) === String(selected.wagon_number)
        && position.dead_end_code === selected.dead_end_code && Number(position.slot_index) === Number(selected.slot_index);
    }

    function pickButton(position) {
      var btn = element("button", "tn-wagon-pick" + (isSelected(position) ? " tn-wagon-pick--sel" : ""));
      btn.type = "button";
      btn.textContent = "Вагон " + position.wagon_number;
      btn.addEventListener("click", function () {
        wrap.querySelectorAll(".tn-wagon-pick").forEach(function (b) { b.classList.remove("tn-wagon-pick--sel"); });
        btn.classList.add("tn-wagon-pick--sel");
        parkPanel.hidden = true;
        onPick(position);
      });
      return btn;
    }

    function openPark(targetCell, deadEnd, slotIndex, parkWagons) {
      clear(parkPanel);
      parkPanel.hidden = false;
      parkPanel.appendChild(element("p", "tn-help", "Выберите вагон в " + deadEnd.name + " · слот " + slotIndex + ":"));
      var row = element("div", "tn-wagon-picker-row");
      parkWagons.forEach(function (w) {
        var btn = element("button", "tn-wagon-pick", "Вагон " + w.wagon_number);
        btn.type = "button";
        if (w.stage || w.status) btn.title = "этап: " + (w.stage || w.status);
        btn.addEventListener("click", function () {
          var position = { wagon_number: w.wagon_number, dead_end_code: deadEnd.code, slot_index: slotIndex };
          targetCell.textContent = "Вагон " + w.wagon_number;
          targetCell.classList.add("tn-slot--occupied");
          targetCell.classList.remove("tn-slot--empty");
          targetCell.title = deadEnd.name + " · слот " + slotIndex + " · вагон " + w.wagon_number;
          clear(parkPanel);
          parkPanel.hidden = true;
          wrap.querySelectorAll(".tn-wagon-pick").forEach(function (b) { b.classList.remove("tn-wagon-pick--sel"); });
          if (targetCell.classList) targetCell.classList.add("tn-wagon-pick--sel");
          onPick(position);
        });
        row.appendChild(btn);
      });
      parkPanel.appendChild(row);
    }

    Promise.all([request("/catalog/dead-ends"), request("/catalog/wagons")]).then(function (results) {
      var occupied = {};
      (results[0].dead_ends || []).forEach(function (deadEnd) {
        (deadEnd.positions || []).forEach(function (position) {
          if (position.wagon_number) occupied[position.wagon_number] = true;
        });
      });
      var parkWagons = (results[1].wagons || []).filter(function (w) {
        return w.active !== false && w.number && !occupied[w.number];
      }).map(function (w) {
        return { wagon_number: w.number, stage: w.stage || "" };
      });
      (results[0].free_wagons || []).forEach(function (wagon) {
        if (wagon.wagon_number && !occupied[wagon.wagon_number]) parkWagons.push(wagon);
      });
      (results[0].dead_ends || []).forEach(function (deadEnd) {
        var box = element("section", "tn-wagon-picker-dead-end");
        box.appendChild(element("p", "tn-wagon-picker-title", deadEnd.name || "Тупик"));
        var grid = element("div", "tn-slot-grid");
        (deadEnd.positions || []).forEach(function (pos) {
          if (pos.wagon_number) {
            var cell = pickButton({
              wagon_number: pos.wagon_number, dead_end_code: deadEnd.code, slot_index: pos.slot_index
            });
            cell.title = (deadEnd.name || "Тупик") + " · слот " + pos.slot_index + " · вагон " + pos.wagon_number;
            grid.appendChild(cell);
          } else {
            (function (slotIndex) {
              var cell = element("button", "tn-slot tn-slot--empty");
              cell.type = "button";
              cell.textContent = "+" + slotIndex;
              cell.title = "Слот " + slotIndex + " · пусто — выбрать вагон из парка";
              cell.addEventListener("click", function () {
                openPark(cell, deadEnd, slotIndex, parkWagons);
              });
              grid.appendChild(cell);
            })(pos.slot_index);
          }
        });
        box.appendChild(grid);
        wrap.appendChild(box);
      });
      var free = results[0].free_wagons || [];
      if (free.length) {
        var freeBox = element("div", "tn-wagon-picker-free");
        freeBox.appendChild(element("p", "tn-help", "Вагоны без тупика (новый контур):"));
        var row = element("div", "tn-wagon-picker-row");
        free.forEach(function (wagon) { row.appendChild(element("span", "tn-help", "Вагон " + wagon.wagon_number)); });
        freeBox.appendChild(row);
        wrap.appendChild(freeBox);
      }
    }).catch(function () {});
    return wrap;
  }

  function loadVacantSlotPicker(wagonNumber, onPick, selected) {
    var wrap = element("div", "tn-wagon-picker");
    var selectedButton = null;

    function isSelected(position) {
      return selected && position.dead_end_code === selected.dead_end_code
        && Number(position.slot_index) === Number(selected.slot_index);
    }

    request("/catalog/dead-ends").then(function (data) {
      (data.dead_ends || []).forEach(function (deadEnd) {
        var box = element("section", "tn-wagon-picker-dead-end");
        box.appendChild(element("p", "tn-wagon-picker-title", deadEnd.name || "Тупик"));
        var grid = element("div", "tn-slot-grid");
        (deadEnd.positions || []).forEach(function (position) {
          var occupied = Boolean(position.wagon_number);
          var cell = element(
            "button",
            "tn-slot" + (occupied ? " tn-slot--occupied" : " tn-slot--empty") + (isSelected(position) ? " tn-wagon-pick--sel" : "")
          );
          cell.type = "button";
          cell.disabled = occupied;
          cell.textContent = occupied ? position.wagon_number : String(position.slot_index);
          cell.title = occupied
            ? "Слот " + position.slot_index + " · занят вагоном " + position.wagon_number
            : "Слот " + position.slot_index + " · поставить вагон " + wagonNumber;
          if (!occupied) {
            cell.addEventListener("click", function () {
              if (selectedButton) selectedButton.classList.remove("tn-wagon-pick--sel");
              cell.classList.add("tn-wagon-pick--sel");
              selectedButton = cell;
              onPick({ dead_end_code: deadEnd.code, slot_index: position.slot_index });
            });
          }
          if (isSelected(position)) selectedButton = cell;
          grid.appendChild(cell);
        });
        box.appendChild(grid);
        wrap.appendChild(box);
      });
    }).catch(function () {});
    return wrap;
  }

  function makeYardPicker(onPick, selX, selY) {
    var wrap = element("div", "tn-yard-picker");
    var grid = element("div", "tn-yard-picker-grid");
    var selected = null;
    for (var yy = 1; yy <= 25; yy += 1) {
      for (var xx = 1; xx <= 13; xx += 1) {
        (function (px, py) {
          var cell = element("button", "tn-yard-picker-cell" + (px === selX && py === selY ? " tn-yard-picker-cell--sel" : ""));
          cell.type = "button";
          cell.textContent = px + "/" + py;
          cell.title = px + "/" + py;
          cell.addEventListener("click", function () {
            if (selected) selected.classList.remove("tn-yard-picker-cell--sel");
            cell.classList.add("tn-yard-picker-cell--sel");
            selected = cell;
            onPick(px, py);
          });
          grid.appendChild(cell);
        })(xx, yy);
      }
    }
    wrap.appendChild(grid);
    return wrap;
  }

  function makeReceiptLine(data, isExpected) {
    var line = element("section", "tn-block-card");
    var head = element("button", "tn-block-head");
    head.type = "button";
    head.setAttribute("aria-expanded", "false");
    var label = data.block_type && data.block_number ? (data.block_type + " " + data.block_number) : "Новый блок";
    head.appendChild(element("span", "tn-block-id", label));
    var summary = element("span", "tn-block-summary", isExpected ? "ожидается" : "новый");
    head.appendChild(summary);
    head.appendChild(element("span", "tn-block-chevron", "▾"));

    var body = element("div", "tn-block-body");
    body.hidden = true;
    body.appendChild(lineField("Тип *", "text", "block_type", data.block_type || "", { required: true, readOnly: isExpected, maxLength: 30 }));
    body.appendChild(lineField("Номер *", "text", "block_number", data.block_number || "", { required: true, readOnly: isExpected, maxLength: 80 }));
    body.appendChild(lineField("Наименование", "text", "product_name", data.product_name || "", { maxLength: 200 }));
    body.appendChild(lineField("Вес, кг", "number", "weight_kg", data.weight_kg || "", { min: 1 }));

    var receipt = lineField("Статус *", "select", "receipt_state", data.receipt_state || "received", {
      items: [{ value: "received", label: "Принят" }, { value: "missing", label: "Недостача" }, { value: "damaged", label: "Повреждён" }]
    });
    var condition = lineField("Состояние *", "select", "condition_code", data.condition_code || "ok", {
      items: [{ value: "ok", label: "Без повреждений" }, { value: "damage", label: "Есть повреждение" }]
    });
    var note = lineField("Примечание", "textarea", "discrepancy_note", data.discrepancy_note || "", { maxLength: 500 });
    body.appendChild(receipt);
    body.appendChild(condition);
    body.appendChild(note);

    var placeWrap = element("div", "tn-block-place");
    placeWrap.appendChild(element("p", "tn-help", "Куда сохранить блок:"));
    var placeRow = element("div", "tn-place-row");
    var yardRadio = element("input"); yardRadio.type = "radio"; yardRadio.name = "place"; yardRadio.value = "yard"; yardRadio.checked = true;
    var yardLabel = element("label", "tn-place-option"); yardLabel.appendChild(yardRadio); yardLabel.appendChild(document.createTextNode("Площадка"));
    var wagonRadio = element("input"); wagonRadio.type = "radio"; wagonRadio.name = "place"; wagonRadio.value = "wagon";
    var wagonLabel = element("label", "tn-place-option"); wagonLabel.appendChild(wagonRadio); wagonLabel.appendChild(document.createTextNode("Вагон"));
    placeRow.appendChild(yardLabel); placeRow.appendChild(wagonLabel);
    placeWrap.appendChild(placeRow);

    var x = lineField("Координата X", "number", "yard_x", data.yard_x || "", { min: 1, max: 13 });
    var y = lineField("Координата Y", "number", "yard_y", data.yard_y || "", { min: 1, max: 25 });
    x.hidden = true; y.hidden = true;
    var xControl = x.querySelector("input");
    var yControl = y.querySelector("input");
    var yardPicker = makeYardPicker(function (px, py) { xControl.value = px; yControl.value = py; updateLine(); }, data.yard_x || 0, data.yard_y || 0);
    var wagonNumber = element("input"); wagonNumber.type = "hidden"; wagonNumber.name = "wagon_number"; wagonNumber.value = data.wagon_number || "";
    var deadEndCode = element("input"); deadEndCode.type = "hidden"; deadEndCode.name = "dead_end_code"; deadEndCode.value = data.dead_end_code || "";
    var slotIndex = element("input"); slotIndex.type = "hidden"; slotIndex.name = "slot_index"; slotIndex.value = data.slot_index || "";
    var wagonField = element("div", "tn-wagon-picker-field");
    wagonField.appendChild(wagonNumber);
    wagonField.appendChild(deadEndCode);
    wagonField.appendChild(slotIndex);
    wagonField.appendChild(element("p", "tn-help", "Выберите вагон тапом в тупике:"));
    wagonField.appendChild(loadWagonPicker(function (position) {
      wagonNumber.value = position.wagon_number;
      deadEndCode.value = position.dead_end_code;
      slotIndex.value = position.slot_index;
      updateLine();
    }, data.wagon_number ? {
      wagon_number: data.wagon_number,
      dead_end_code: data.dead_end_code,
      slot_index: data.slot_index
    } : null));
    wagonField.hidden = true;
    placeWrap.appendChild(yardPicker); placeWrap.appendChild(x); placeWrap.appendChild(y); placeWrap.appendChild(wagonField);
    body.appendChild(placeWrap);
    var saveBtn = element("button", "tn-button tn-button--primary tn-block-save", "Сохранить блок");
    saveBtn.type = "button";
    saveBtn.addEventListener("click", function () { saveBlock(); });
    body.appendChild(saveBtn);

    head.addEventListener("click", function () {
      var open = body.hidden;
      body.hidden = !open;
      head.setAttribute("aria-expanded", String(open));
      head.querySelector(".tn-block-chevron").textContent = open ? "▴" : "▾";
    });

    var receiptControl = receipt.querySelector("select");
    var conditionControl = condition.querySelector("select");
    var noteControl = note.querySelector("textarea");
    function updateLine() {
      var missing = receiptControl.value === "missing";
      xControl.disabled = missing; yControl.disabled = missing;
      xControl.required = !missing && yardRadio.checked;
      yControl.required = !missing && yardRadio.checked;
      // вагон задаётся тапом по сетке тупиков, обязательность проверяется при отправке
      noteControl.required = missing || receiptControl.value === "damaged" || conditionControl.value === "damage";
      if (receiptControl.value === "damaged") conditionControl.value = "damage";
      var s = [];
      if (missing) s.push("недостача");
      else if (yardRadio.checked) {
        var vx = xControl.value, vy = yControl.value;
        s.push(vx && vy ? ("X=" + vx + ", Y=" + vy) : "выберите место");
      } else {
        s.push(wagonNumber.value && deadEndCode.value && slotIndex.value
          ? ("вагон " + wagonNumber.value + " · слот " + slotIndex.value)
          : "выберите вагон и слот");
      }
      summary.textContent = s.join(" ") || "—";
    }
    function updatePlace() {
      yardPicker.hidden = !yardRadio.checked;
      wagonField.hidden = !wagonRadio.checked;
      updateLine();
    }
    function saveBlock() {
      var missing = receiptControl.value === "missing";
      if (!missing && yardRadio.checked && (!xControl.value || !yControl.value)) {
        body.hidden = false;
        showToast("Выберите место блока на площадке — тап по ячейке.");
        return;
      }
      if (!missing && wagonRadio.checked && (!wagonNumber.value || !deadEndCode.value || !slotIndex.value)) {
        body.hidden = false;
        showToast("Выберите вагон и обязательный слот — тап по сетке тупиков.");
        return;
      }
      if ((missing || receiptControl.value === "damaged" || conditionControl.value === "damage") && !noteControl.value.trim()) {
        body.hidden = false;
        showToast("Для недостачи или повреждения обязательно укажите примечание.");
        return;
      }
      line.dataset.saved = "1";
      body.hidden = true;
      head.setAttribute("aria-expanded", "false");
      head.querySelector(".tn-block-chevron").textContent = "▾";
      head.querySelector(".tn-block-id").textContent = "✓ " + label;
      showToast("Блок сохранён: " + label + ".");
    }
    yardRadio.addEventListener("change", updatePlace);
    wagonRadio.addEventListener("change", updatePlace);
    receiptControl.addEventListener("change", updateLine);
    conditionControl.addEventListener("change", updateLine);
    xControl.addEventListener("input", updateLine);
    yControl.addEventListener("input", updateLine);
    updatePlace();
    if (data._saved) {
      if (data.wagon_number) { wagonRadio.checked = true; yardRadio.checked = false; }
      updatePlace();
      line.dataset.saved = "1";
      body.hidden = true;
      head.querySelector(".tn-block-chevron").textContent = "▾";
      head.querySelector(".tn-block-id").textContent = "✓ " + label;
    }
    line.appendChild(head);
    line.appendChild(body);
    return line;
  }

  function receiptForm(intake) {
    var form = element("form", "tn-form-card");
    form.appendChild(element("h3", "", "Подтвердить фактическую приёмку"));
    form.appendChild(element("p", "tn-help", "После подтверждения строки, блоки и журнал не изменяются. Для недостачи, повреждения и незаявленного блока обязательно укажите примечание."));
    var linesWrap = element("div", "tn-intake-lines");
    var expected = intake.source_system === "rumex" ? (intake.expected_lines || []) : [];
    var savedByKey = {};
    (intake.lines || []).forEach(function (line) {
      savedByKey[line.block_type + "|" + line.block_number] = line;
    });
    var count = expected.length || Number(intake.expected_blocks_count || 0);
    for (var index = 0; index < count; index += 1) {
      var expectedLine = expected[index] || {};
      var saved = savedByKey[expectedLine.block_type + "|" + expectedLine.block_number];
      var base = saved ? {
        block_type: expectedLine.block_type, block_number: expectedLine.block_number,
        product_name: saved.product_name || expectedLine.product_name || "",
        weight_kg: saved.weight_kg || expectedLine.weight_kg || "",
        receipt_state: saved.receipt_state, condition_code: saved.condition_code,
        discrepancy_note: saved.discrepancy_note || "",
        yard_x: saved.yard_x, yard_y: saved.yard_y,
        wagon_number: saved.block_wagon_number || saved.wagon_number || "",
        dead_end_code: saved.dead_end_code || "", slot_index: saved.slot_index || "",
        _saved: true
      } : expectedLine;
      linesWrap.appendChild(makeReceiptLine(base, expected.length > 0));
    }
    form.appendChild(linesWrap);
    if (expected.length) {
      var addExtra = element("button", "tn-button tn-button--secondary", "Добавить незаявленный блок");
      addExtra.type = "button";
      addExtra.addEventListener("click", function () { linesWrap.appendChild(makeReceiptLine({}, false)); });
      form.appendChild(addExtra);
    }
    var actions = element("div", "tn-form-actions");
    var draftBtn = element("button", "tn-button tn-button--secondary", "Сохранить черновик");
    draftBtn.type = "button";
    draftBtn.addEventListener("click", function () {
      var lines = Array.from(linesWrap.querySelectorAll(".tn-block-card"))
        .filter(function (card) { return card.dataset.saved === "1"; })
        .map(function (card) {
          var get = function (name) { var el = card.querySelector("[name='" + name + "']"); return el ? el.value.trim() : ""; };
          var place = card.querySelector("input[name='place']:checked");
          var useWagon = place && place.value === "wagon";
          return {
            block_type: get("block_type"), block_number: get("block_number"), product_name: get("product_name"),
            weight_kg: get("weight_kg") || null, receipt_state: get("receipt_state"), condition_code: get("condition_code"),
            discrepancy_note: get("discrepancy_note"),
            yard_x: useWagon ? null : (get("yard_x") || null),
            yard_y: useWagon ? null : (get("yard_y") || null),
            wagon_number: useWagon ? get("wagon_number") : "",
            dead_end_code: useWagon ? get("dead_end_code") : "",
            slot_index: useWagon ? get("slot_index") : ""
          };
        });
      if (!lines.length) { showToast("Сначала сохраните хотя бы один блок кнопкой «Сохранить блок»."); return; }
      draftBtn.disabled = true;
      jsonRequest("/intakes/" + encodeURIComponent(intake.public_id) + "/draft", "POST", { lines: lines })
        .then(function () {
          showToast("Черновик сохранён — блоки видны на площадке.");
          loadYard().catch(function () {});
        })
        .catch(function (error) { showToast(error.message || "Не удалось сохранить черновик"); })
        .finally(function () { draftBtn.disabled = false; });
    });
    var confirm = element("button", "tn-button tn-button--primary", "Подтвердить приёмку");
    confirm.type = "submit";
    actions.appendChild(confirm);
    form.appendChild(actions);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!form.checkValidity()) { form.reportValidity(); return; }
      var invalidWagon = Array.from(linesWrap.querySelectorAll(".tn-block-card")).some(function (card) {
        var place = card.querySelector("input[name='place']:checked");
        if (!place || place.value !== "wagon") return false;
        var wagon = card.querySelector("input[name='wagon_number']");
        var deadEnd = card.querySelector("input[name='dead_end_code']");
        var slot = card.querySelector("input[name='slot_index']");
        return !(wagon && wagon.value.trim() && deadEnd && deadEnd.value.trim() && slot && slot.value.trim());
      });
      if (invalidWagon) { showToast("Для каждого блока выберите вагон и обязательный слот тупика или переключите на площадку."); return; }
      var unsaved = Array.from(linesWrap.querySelectorAll(".tn-block-card")).filter(function (card) {
        return card.dataset.saved !== "1";
      });
      if (unsaved.length) {
        unsaved[0].querySelector(".tn-block-body").hidden = false;
        showToast("Сохраните каждый блок кнопкой «Сохранить блок», затем подтвердите приёмку.");
        return;
      }
      var lines = Array.from(linesWrap.querySelectorAll(".tn-block-card")).map(function (card) {
        var get = function (name) { var el = card.querySelector("[name='" + name + "']"); return el ? el.value.trim() : ""; };
        var place = card.querySelector("input[name='place']:checked");
        var useWagon = place && place.value === "wagon";
        return {
          block_type: get("block_type"), block_number: get("block_number"), product_name: get("product_name"),
          weight_kg: get("weight_kg") || null, receipt_state: get("receipt_state"), condition_code: get("condition_code"),
          discrepancy_note: get("discrepancy_note"),
          yard_x: useWagon ? null : (get("yard_x") || null),
          yard_y: useWagon ? null : (get("yard_y") || null),
          wagon_number: useWagon ? get("wagon_number") : "",
          dead_end_code: useWagon ? get("dead_end_code") : "",
          slot_index: useWagon ? get("slot_index") : ""
        };
      });
      confirm.disabled = true;
      jsonRequest("/intakes/" + encodeURIComponent(intake.public_id) + "/confirm", "POST", { lines: lines })
        .then(function (data) {
          showToast(data.intake.status === "discrepancy" ? "Приёмка зафиксирована с расхождением." : "Приёмка подтверждена.");
          renderIntakeModal(data.intake);
          loadDashboard().catch(function () {});
          loadIntakes().catch(function () {});
        })
        .catch(function (error) { showToast(error.message || "Не удалось подтвердить приёмку"); confirm.disabled = false; });
    });
    return form;
  }

  function cancelIntake(publicId, button) {
    var reason = window.prompt("Укажите обязательную причину отмены подтверждённой приёмки:");
    if (reason === null) return;
    if (!reason.trim()) { showToast("Причина отмены обязательна."); return; }
    button.disabled = true;
    jsonRequest("/intakes/" + encodeURIComponent(publicId) + "/cancel", "POST", { reason: reason.trim() })
      .then(function (data) {
        showToast("Отмена зафиксирована в журнале.");
        renderIntakeModal(data.intake);
        loadDashboard().catch(function () {});
        loadIntakes().catch(function () {});
      })
      .catch(function (error) { showToast(error.message || "Не удалось отменить приёмку"); button.disabled = false; });
  }

  function wagonTransition(number, endpoint, question, successText, button) {
    if (!window.confirm(question + " Вагон " + number + " останется в журнале.")) return;
    button.disabled = true;
    jsonRequest("/wagons/" + encodeURIComponent(number) + "/" + endpoint, "POST", {})
      .then(function () { showToast(successText); return Promise.all([loadWagons(), loadDashboard()]); })
      .catch(function (error) { showToast(error.message || "Не удалось зафиксировать этап вагона"); button.disabled = false; });
  }

  function returnWagonEmpty(wagon) {
    wagonModal.hidden = false;
    wagonModalTitle.textContent = "Возврат порожнего вагона " + wagon.wagon_number;
    clear(wagonModalContent);
    wagonModalContent.appendChild(element("p", "tn-help", "Выберите свободный обязательный слот новой площадки для возврата вагона."));
    var selected = null;
    var choice = element("p", "tn-help", "Слот ещё не выбран.");
    wagonModalContent.appendChild(choice);
    wagonModalContent.appendChild(loadVacantSlotPicker(wagon.wagon_number, function (position) {
      selected = position;
      choice.textContent = "Выбрано: " + position.dead_end_code + " · слот " + position.slot_index + ".";
    }));
    var actions = element("div", "tn-card-actions");
    var cancel = element("button", "tn-button tn-button--secondary", "Отмена");
    cancel.type = "button";
    cancel.addEventListener("click", closeModal);
    var confirm = element("button", "tn-button tn-button--primary", "Подтвердить возврат");
    confirm.type = "button";
    confirm.addEventListener("click", function () {
      if (!selected) { showToast("Выберите свободный слот новой площадки."); return; }
      if (!window.confirm("Зафиксировать возврат порожнего вагона " + wagon.wagon_number + " в выбранный слот?")) return;
      confirm.disabled = true;
      jsonRequest("/wagons/" + encodeURIComponent(wagon.wagon_number) + "/returned-empty", "POST", selected)
        .then(function () {
          closeModal();
          showToast("Возврат порожнего вагона зафиксирован.");
          return Promise.all([loadWagons(), loadDashboard()]);
        })
        .catch(function (error) { showToast(error.message || "Не удалось зафиксировать возврат вагона"); confirm.disabled = false; });
    });
    actions.appendChild(cancel);
    actions.appendChild(confirm);
    wagonModalContent.appendChild(actions);
  }

  function markWagonLoaded(wagon, button) {
    if (wagon.dead_end_code && wagon.slot_index) {
      wagonTransition(wagon.wagon_number, "loaded", "Зафиксировать загрузку?", "Загрузка вагона зафиксирована.", button);
      return;
    }
    wagonModal.hidden = false;
    wagonModalTitle.textContent = "Позиция вагона " + wagon.wagon_number;
    clear(wagonModalContent);
    wagonModalContent.appendChild(element("p", "tn-help", "Перед фиксацией загрузки выберите обязательный свободный слот новой площадки."));
    var selected = null;
    var choice = element("p", "tn-help", "Слот ещё не выбран.");
    wagonModalContent.appendChild(choice);
    wagonModalContent.appendChild(loadVacantSlotPicker(wagon.wagon_number, function (position) {
      selected = position;
      choice.textContent = "Выбрано: " + position.dead_end_code + " · слот " + position.slot_index + ".";
    }));
    var actions = element("div", "tn-card-actions");
    var cancel = element("button", "tn-button tn-button--secondary", "Отмена");
    cancel.type = "button";
    cancel.addEventListener("click", closeModal);
    var confirm = element("button", "tn-button tn-button--primary", "Указать слот и зафиксировать загрузку");
    confirm.type = "button";
    confirm.addEventListener("click", function () {
      if (!selected) { showToast("Выберите свободный слот новой площадки."); return; }
      if (!window.confirm("Указать выбранный слот и зафиксировать загрузку вагона " + wagon.wagon_number + "?")) return;
      confirm.disabled = true;
      jsonRequest("/wagons/" + encodeURIComponent(wagon.wagon_number) + "/loaded", "POST", selected)
        .then(function () {
          closeModal();
          showToast("Позиция и загрузка вагона зафиксированы.");
          return Promise.all([loadWagons(), loadDashboard()]);
        })
        .catch(function (error) { showToast(error.message || "Не удалось зафиксировать загрузку вагона"); confirm.disabled = false; });
    });
    actions.appendChild(cancel);
    actions.appendChild(confirm);
    wagonModalContent.appendChild(actions);
  }

  function appendWagonAction(card, wagon) {
    if (wagon.status === "unloaded_bts_east") {
      var returnActions = element("div", "tn-card-actions");
      var returnButton = element("button", "tn-button tn-button--primary", "Вернуть порожним в слот");
      returnButton.type = "button";
      returnButton.addEventListener("click", function (event) {
        event.stopPropagation();
        returnWagonEmpty(wagon);
      });
      returnActions.appendChild(returnButton);
      card.appendChild(returnActions);
      return;
    }
    if (wagon.status === "forming") {
      var loadActions = element("div", "tn-card-actions");
      var loadButton = element(
        "button",
        "tn-button tn-button--primary",
        wagon.dead_end_code ? "Зафиксировать загрузку" : "Указать слот и зафиксировать загрузку"
      );
      loadButton.type = "button";
      loadButton.addEventListener("click", function (event) {
        event.stopPropagation();
        markWagonLoaded(wagon, loadButton);
      });
      loadActions.appendChild(loadButton);
      card.appendChild(loadActions);
      return;
    }
    var actionsByStatus = {
      loaded: { endpoint: "dispatch", label: "Отправить в путь", question: "Отправить в путь?", success: "Вагон отправлен в путь." },
      in_transit: { endpoint: "arrived-kodar", label: "Подтвердить прибытие в Кодар", question: "Подтвердить прибытие в Кодар?", success: "Прибытие в Кодар зафиксировано." },
      at_kodar: { endpoint: "unloaded-bts-east", label: "Подтвердить выгрузку у БТС Восток", question: "Подтвердить выгрузку у БТС Восток?", success: "Выгрузка у БТС Восток зафиксирована." }
    };
    var action = actionsByStatus[wagon.status];
    if (!action) return;
    var actions = element("div", "tn-card-actions");
    var button = element("button", "tn-button tn-button--primary", action.label);
    button.type = "button";
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      wagonTransition(wagon.wagon_number, action.endpoint, action.question, action.success, button);
    });
    actions.appendChild(button);
    card.appendChild(actions);
  }

  function submitManualIntake(event) {
    event.preventDefault();
    var form = event.currentTarget;
    if (!form.checkValidity()) { form.reportValidity(); return; }
    var fields = form.elements;
    var planned = fields.planned_arrival_at.value;
    var payload = {
      expected_blocks_count: Number(fields.expected_blocks_count.value),
      ttn_number: fields.ttn_number.value.trim(), vehicle_plate: fields.vehicle_plate.value.trim(),
      driver_name: fields.driver_name.value.trim(), planned_arrival_at: planned ? new Date(planned).toISOString() : null
    };
    var button = form.querySelector("button[type='submit']");
    button.disabled = true;
    jsonRequest("/intakes", "POST", payload)
      .then(function () {
        form.reset();
        document.getElementById("manualIntakeDetails").open = false;
        showToast("Ожидаемая приёмка создана.");
        return Promise.all([loadIntakes(), loadDashboard()]);
      })
      .catch(function (error) { showToast(error.message || "Не удалось создать приёмку"); })
      .finally(function () { button.disabled = false; });
  }

  function submitWagonLoad(event) {
    event.preventDefault();
    var form = event.currentTarget;
    if (!form.checkValidity()) { form.reportValidity(); return; }
    var button = form.querySelector("button[type='submit']");
    button.disabled = true;
    jsonRequest("/wagons/load", "POST", {
      block_id: Number(form.elements.block_id.value),
      wagon_number: form.elements.wagon_number.value.trim(),
      dead_end_code: form.elements.dead_end_code.value,
      slot_index: Number(form.elements.slot_index.value)
    })
      .then(function () { form.reset(); showToast("Блок загружен в вагон."); return Promise.all([loadWagons(), loadYard()]); })
      .catch(function (error) { showToast(error.message || "Не удалось загрузить блок"); })
      .finally(function () { button.disabled = false; });
  }

  function submitCorrection(event) {
    event.preventDefault();
    var form = event.currentTarget;
    if (!form.checkValidity()) { form.reportValidity(); return; }
    var details = form.elements.details.value.trim();
    var button = form.querySelector("button[type='submit']");
    button.disabled = true;
    jsonRequest("/corrections", "POST", {
      subject_type: form.elements.subject_type.value, subject_id: form.elements.subject_id.value.trim(),
      reason: form.elements.reason.value.trim(), details: details ? { note: details } : {}
    })
      .then(function () { form.reset(); showToast("Корректировка зафиксирована отдельным фактом."); return loadEvents(); })
      .catch(function (error) { showToast(error.message || "Не удалось создать корректировку"); })
      .finally(function () { button.disabled = false; });
  }

  function submitOperationCancellation(event) {
    event.preventDefault();
    var form = event.currentTarget;
    if (!form.checkValidity()) { form.reportValidity(); return; }
    var button = form.querySelector("button[type='submit']");
    button.disabled = true;
    jsonRequest(
      "/operations/" + encodeURIComponent(form.elements.subject_type.value) + "/" + encodeURIComponent(form.elements.subject_id.value.trim()) + "/cancel",
      "POST", { reason: form.elements.reason.value.trim() }
    )
      .then(function () { form.reset(); showToast("Отмена зафиксирована отдельным фактом."); return Promise.all([loadEvents(), loadWagons(), loadDashboard()]); })
      .catch(function (error) { showToast(error.message || "Не удалось отменить операцию"); })
      .finally(function () { button.disabled = false; });
  }

  function loadIdentity() {
    return request("/auth/me").then(function (data) {
      state.operator = data.operator;
      state.permissions = data.permissions || {};
      currentUser.textContent = data.operator.name + " · " + (roleLabels[data.operator.role] || data.operator.role);
      applyPermissions();
    });
  }

  function applyPermissions() {
    var sectionByTab = { reception: "reception", yard: "yard", wagons: "wagons", journal: "journal", reports: "reports", search: "search", trips: "trips", receipts: "receipts", operator: "reception" };
    document.querySelectorAll(".tn-tab").forEach(function (tab) {
      var name = tab.getAttribute("data-tab");
      var section = sectionByTab[name];
      if (section) tab.hidden = state.permissions[section] === false;
    });
    document.querySelectorAll("[data-operator1-only]").forEach(function (node) { node.hidden = !isOperator1(); });
    document.querySelectorAll("[data-admin-only]").forEach(function (node) { node.hidden = !isAdmin(); });
    document.querySelectorAll("[data-dispatcher-only]").forEach(function (node) { node.hidden = !state.permissions.trips; });
    document.querySelectorAll("[data-accountant-only]").forEach(function (node) { node.hidden = !state.permissions.receipts; });
    document.querySelectorAll("[data-operator-only]").forEach(function (node) { node.hidden = !state.permissions.reception; });
    var allowedTabs = { manage: isOperator1(), admin: isAdmin() };
    if (allowedTabs[state.activeTab] === false) showPanel("today");
  }

  function bind() {
    document.querySelectorAll(".tn-tab").forEach(function (tab) {
      tab.addEventListener("click", function () { showPanel(tab.getAttribute("data-tab")); });
    });
    document.querySelectorAll("[data-refresh]").forEach(function (button) {
      button.addEventListener("click", function () { loadPanel(state.activeTab); });
    });
    document.getElementById("manualIntakeForm").addEventListener("submit", submitManualIntake);
    document.getElementById("wagonLoadForm").addEventListener("submit", submitWagonLoad);
    document.getElementById("correctionForm").addEventListener("submit", submitCorrection);
    document.getElementById("operationCancellationForm").addEventListener("submit", submitOperationCancellation);
    document.getElementById("adminCarrierForm").addEventListener("submit", function (event) {
      submitAdminForm(event, "/admin/carriers", ["id", "legal_form", "name", "inn", "active"], "Перевозчик сохранён.");
    });
    document.getElementById("adminDriverForm").addEventListener("submit", function (event) {
      submitAdminForm(event, "/admin/drivers", ["id", "full_name", "phone", "license_number", "active"], "Водитель сохранён.");
    });
    document.getElementById("adminVehicleForm").addEventListener("submit", function (event) {
      submitAdminForm(event, "/admin/vehicles", ["id", "plate", "carrier_id", "default_driver_id", "note", "active"], "Машина сохранена.");
    });
    document.getElementById("adminWagonForm").addEventListener("submit", function (event) {
      submitAdminForm(event, "/admin/wagons", ["id", "wagon_number", "scheme_code", "note", "active"], "Вагон сохранён.");
    });
    document.getElementById("tripForm").addEventListener("submit", submitTripForm);
    document.getElementById("receiptForm").addEventListener("submit", submitReceiptForm);
    document.getElementById("addTripBlockButton").addEventListener("click", addTripBlockRow);
    ensureTripBlockRows();
    document.getElementById("reportDate").addEventListener("change", loadReport);
    document.getElementById("searchInput").addEventListener("input", function () {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(function () { runSearch().catch(function (error) { setError(error.message || "Не удалось выполнить поиск"); }); }, 280);
    });
    document.getElementById("searchKind").addEventListener("change", function () {
      runSearch().catch(function (error) { setError(error.message || "Не удалось выполнить поиск"); });
    });
    document.getElementById("logoutButton").addEventListener("click", function () {
      request("/auth/logout", { method: "POST" }).catch(function () {}).finally(function () { location.replace("/taksimo-new/login.html"); });
    });
    document.querySelectorAll("[data-close-modal]").forEach(function (button) { button.addEventListener("click", closeModal); });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !modal.hidden) closeModal(); });
  }

  bind();
  loadIdentity().then(function () { return loadDashboard(); }).catch(function (error) {
    if (error.message !== "Требуется вход") setError(error.message || "Не удалось открыть кабинет");
  });
})();
