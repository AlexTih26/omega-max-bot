(function () {
  "use strict";

  var API = "/api/taksimo-new";
  var state = { operator: null, activeTab: "today", searchTimer: null };
  var statusLabels = {
    expected: "Ожидается",
    draft: "Черновик",
    in_progress: "В работе",
    confirmed: "Подтверждена",
    discrepancy: "Расхождение",
    loading: "Загрузка",
    dispatched: "Отправлен"
  };
  var eventLabels = {
    intake_expected_imported: "Получена ожидаемая приёмка из служебного моста",
    intake_draft_created: "Создана ручная ожидаемая приёмка",
    intake_locked: "Приёмка взята в работу",
    intake_confirmed: "Приёмка подтверждена",
    intake_discrepancy: "Приёмка подтверждена с расхождением",
    intake_cancelled: "Оператор 1 отменил подтверждённую приёмку",
    block_loaded_to_wagon: "Блок загружен в вагон",
    wagon_dispatched: "Вагон отправлен",
    operation_corrected: "Создана корректировка подтверждённой операции",
    operation_cancelled: "Оператор 1 отменил подтверждённую операцию"
  };

  var currentUser = document.getElementById("currentUser");
  var appError = document.getElementById("appError");
  var toast = document.getElementById("toast");
  var modal = document.getElementById("intakeModal");
  var modalTitle = document.getElementById("intakeModalTitle");
  var modalContent = document.getElementById("intakeModalContent");

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

  function isOperator1() {
    return state.operator && state.operator.role === "operator1";
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
      stats.appendChild(statCard(wagons.loading || 0, "вагонов загружается"));
      stats.appendChild(statCard(wagons.dispatched || 0, "вагонов отправлено"));
      stats.appendChild(statCard(data.pending_integrations || 0, "неподтверждённых сообщений моста"));
      renderIntakes(document.getElementById("expectedIntakes"), data.expected_intakes || [], document.getElementById("expectedEmpty"));
    });
  }

  function loadIntakes() {
    return request("/intakes?limit=100").then(function (data) {
      renderIntakes(document.getElementById("intakesList"), data.intakes || [], document.getElementById("intakesEmpty"));
    });
  }

  function locationText(block) {
    if (block.current_location_kind === "wagon") return "вагон " + (block.wagon_number || "—");
    return "площадка X=" + block.yard_x + ", Y=" + block.yard_y + (block.yard_slot ? ", место " + block.yard_slot : "");
  }

  function loadYard() {
    return request("/yard").then(function (data) {
      var blocks = data.blocks || [];
      var byCell = {};
      blocks.forEach(function (block) {
        var key = block.yard_x + ":" + block.yard_y;
        if (!byCell[key]) byCell[key] = [];
        byCell[key].push(block);
      });
      var grid = document.getElementById("yardGrid");
      clear(grid);
      for (var y = 1; y <= 25; y += 1) {
        for (var x = 1; x <= 13; x += 1) {
          var items = byCell[x + ":" + y] || [];
          var cell = element("button", "tn-yard-cell" + (items.length ? " tn-yard-cell--occupied" : ""));
          cell.type = "button";
          cell.textContent = x + "/" + y;
          cell.title = items.length ? items.map(function (item) { return item.block_type + " " + item.block_number; }).join(", ") : "Свободная ячейка";
          if (items.length) cell.appendChild(element("span", "tn-yard-cell-count", String(items.length)));
          grid.appendChild(cell);
        }
      }
      var list = document.getElementById("yardBlocks");
      clear(list);
      blocks.forEach(function (block) {
        var card = element("article", "tn-card");
        card.appendChild(element("p", "tn-card-title", "#" + block.id + " · " + block.block_type + " " + block.block_number));
        card.appendChild(element("p", "tn-card-meta", locationText(block) + (block.condition_code === "damage" ? " · повреждение" : "")));
        list.appendChild(card);
      });
    });
  }

  function loadWagons() {
    return request("/wagons").then(function (data) {
      var wagons = data.wagons || [];
      var list = document.getElementById("wagonsList");
      clear(list);
      document.getElementById("wagonsEmpty").hidden = wagons.length > 0;
      wagons.forEach(function (wagon) {
        var card = element("article", "tn-card");
        var row = element("div", "tn-card-title-row");
        row.appendChild(element("p", "tn-card-title", "Вагон " + wagon.wagon_number));
        row.appendChild(statusBadge(wagon.status));
        card.appendChild(row);
        card.appendChild(element("p", "tn-card-meta", "Блоков: " + wagon.blocks_count + " · создан: " + formatDate(wagon.created_at)));
        if (wagon.dispatched_at) card.appendChild(element("p", "tn-card-meta", "Отправлен: " + formatDate(wagon.dispatched_at)));
        if (isOperator1() && wagon.status === "loading") {
          var actions = element("div", "tn-card-actions");
          var dispatch = element("button", "tn-button tn-button--primary", "Отправить вагон");
          dispatch.type = "button";
          dispatch.addEventListener("click", function () { dispatchWagon(wagon.wagon_number, dispatch); });
          actions.appendChild(dispatch);
          card.appendChild(actions);
        }
        list.appendChild(card);
      });
    });
  }

  function runSearch() {
    var input = document.getElementById("searchInput");
    var query = input.value.trim();
    var results = document.getElementById("searchResults");
    var empty = document.getElementById("searchEmpty");
    clear(results);
    empty.hidden = true;
    if (!query) return Promise.resolve();
    return request("/search?q=" + encodeURIComponent(query) + "&limit=50").then(function (data) {
      var items = data.results || [];
      empty.hidden = items.length > 0;
      items.forEach(function (block) {
        var card = element("article", "tn-card");
        card.appendChild(element("p", "tn-card-title", "#" + block.id + " · " + block.block_type + " " + block.block_number));
        card.appendChild(element("p", "tn-card-meta", locationText(block) + (block.ttn_number ? " · ТТН " + block.ttn_number : "")));
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

  function loadPanel(name) {
    var loaders = { today: loadDashboard, reception: loadIntakes, yard: loadYard, wagons: loadWagons, journal: loadEvents, reports: loadReport };
    if (!loaders[name]) return;
    loaders[name]().catch(function (error) { setError(error.message || "Не удалось загрузить данные"); });
  }

  function closeModal() {
    modal.hidden = true;
    clear(modalContent);
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
    modalContent.appendChild(receiptForm(intake));
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

  function makeReceiptLine(data, isExpected) {
    var line = element("section", "tn-intake-line");
    line.appendChild(element("h3", "", isExpected ? "Ожидаемый блок" : "Блок"));
    if (isExpected) line.appendChild(element("p", "tn-line-identity", "Поставлен в ожидание служебным мостом; тип и номер не меняются."));
    line.appendChild(lineField("Тип *", "text", "block_type", data.block_type || "", { required: true, readOnly: isExpected, maxLength: 30 }));
    line.appendChild(lineField("Номер *", "text", "block_number", data.block_number || "", { required: true, readOnly: isExpected, maxLength: 80 }));
    line.appendChild(lineField("Наименование", "text", "product_name", data.product_name || "", { maxLength: 200 }));
    line.appendChild(lineField("Вес, кг", "number", "weight_kg", data.weight_kg || "", { min: 1 }));
    var receipt = lineField("Статус *", "select", "receipt_state", data.receipt_state || "received", {
      items: [{ value: "received", label: "Принят" }, { value: "missing", label: "Недостача" }, { value: "damaged", label: "Повреждён" }]
    });
    var condition = lineField("Состояние *", "select", "condition_code", data.condition_code || "ok", {
      items: [{ value: "ok", label: "Без повреждений" }, { value: "damage", label: "Есть повреждение" }]
    });
    var note = lineField("Примечание", "textarea", "discrepancy_note", data.discrepancy_note || "", { maxLength: 500 });
    var x = lineField("Координата X *", "number", "yard_x", data.yard_x || "", { min: 1, max: 13 });
    var y = lineField("Координата Y *", "number", "yard_y", data.yard_y || "", { min: 1, max: 25 });
    line.appendChild(receipt);
    line.appendChild(condition);
    line.appendChild(note);
    line.appendChild(x);
    line.appendChild(y);
    var receiptControl = receipt.querySelector("select");
    var conditionControl = condition.querySelector("select");
    var noteControl = note.querySelector("textarea");
    var xControl = x.querySelector("input");
    var yControl = y.querySelector("input");
    function updateLine() {
      var missing = receiptControl.value === "missing";
      xControl.disabled = missing;
      yControl.disabled = missing;
      xControl.required = !missing;
      yControl.required = !missing;
      noteControl.required = missing || receiptControl.value === "damaged" || conditionControl.value === "damage";
      if (receiptControl.value === "damaged") conditionControl.value = "damage";
    }
    receiptControl.addEventListener("change", updateLine);
    conditionControl.addEventListener("change", updateLine);
    updateLine();
    return line;
  }

  function receiptForm(intake) {
    var form = element("form", "tn-form-card");
    form.appendChild(element("h3", "", "Подтвердить фактическую приёмку"));
    form.appendChild(element("p", "tn-help", "После подтверждения строки, блоки и журнал не изменяются. Для недостачи, повреждения и незаявленного блока обязательно укажите примечание."));
    var linesWrap = element("div", "tn-intake-lines");
    var expected = intake.source_system === "rumex" ? (intake.expected_lines || []) : [];
    var count = expected.length || Number(intake.expected_blocks_count || 0);
    for (var index = 0; index < count; index += 1) linesWrap.appendChild(makeReceiptLine(expected[index] || {}, expected.length > 0));
    form.appendChild(linesWrap);
    if (expected.length) {
      var addExtra = element("button", "tn-button tn-button--secondary", "Добавить незаявленный блок");
      addExtra.type = "button";
      addExtra.addEventListener("click", function () { linesWrap.appendChild(makeReceiptLine({}, false)); });
      form.appendChild(addExtra);
    }
    var actions = element("div", "tn-form-actions");
    var confirm = element("button", "tn-button tn-button--primary", "Подтвердить приёмку");
    confirm.type = "submit";
    actions.appendChild(confirm);
    form.appendChild(actions);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!form.checkValidity()) { form.reportValidity(); return; }
      var lines = Array.from(linesWrap.querySelectorAll(".tn-intake-line")).map(function (line) {
        var get = function (name) { return line.querySelector("[name='" + name + "']").value.trim(); };
        return {
          block_type: get("block_type"), block_number: get("block_number"), product_name: get("product_name"),
          weight_kg: get("weight_kg") || null, receipt_state: get("receipt_state"), condition_code: get("condition_code"),
          discrepancy_note: get("discrepancy_note"), yard_x: get("yard_x") || null, yard_y: get("yard_y") || null
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

  function dispatchWagon(number, button) {
    if (!window.confirm("Отправить вагон " + number + "? Подтверждённая отправка останется в журнале.")) return;
    button.disabled = true;
    jsonRequest("/wagons/" + encodeURIComponent(number) + "/dispatch", "POST", {})
      .then(function () { showToast("Вагон отправлен и записан в журнал."); return loadWagons(); })
      .catch(function (error) { showToast(error.message || "Не удалось отправить вагон"); button.disabled = false; });
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
      block_id: Number(form.elements.block_id.value), wagon_number: form.elements.wagon_number.value.trim()
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
      currentUser.textContent = data.operator.name + " · " + (data.operator.role === "operator1" ? "Оператор 1" : data.operator.role === "operator2" ? "Оператор 2" : "Оператор 3");
      document.querySelectorAll("[data-operator1-only]").forEach(function (node) { node.hidden = !isOperator1(); });
      if (!isOperator1() && state.activeTab === "manage") showPanel("today");
    });
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
    document.getElementById("reportDate").addEventListener("change", loadReport);
    document.getElementById("searchInput").addEventListener("input", function () {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(function () { runSearch().catch(function (error) { setError(error.message || "Не удалось выполнить поиск"); }); }, 280);
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
