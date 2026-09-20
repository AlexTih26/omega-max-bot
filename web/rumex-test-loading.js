(function () {
  "use strict";

  var API = "/api/rumex-registry/test";
  var blockTypes = [];
  var registry = [];
  var openedShipmentId = null;
  var lookupTimer = null;
  var correctionShipment = null;
  var handoverShipmentId = null;
  var busy = false;
  var vehicleReadyForTtn = false;
  var registryLoadPromise = null;
  var registryReloadRequested = false;
  var refreshTimer = null;
  var autoRefreshStarted = false;
  var REFRESH_INTERVAL_MS = 5000;
  var RUMEX_TIME_ZONE = "Asia/Irkutsk";

  var accessNotice = document.getElementById("accessNotice");
  var accessNoticeText = document.getElementById("accessNoticeText");
  var passwordLoginLink = document.getElementById("passwordLoginLink");
  var passwordSession = document.getElementById("passwordSession");
  var passwordSessionUser = document.getElementById("passwordSessionUser");
  var passwordLogoutButton = document.getElementById("passwordLogoutButton");
  var loadingForm = document.getElementById("loadingForm");
  var plateTail = document.getElementById("plateTail");
  var loadedAt = document.getElementById("loadedAt");
  var blockCount = document.getElementById("blockCount");
  var blocksList = document.getElementById("blocksList");
  var totalWeight = document.getElementById("totalWeight");
  var vehicleCard = document.getElementById("vehicleCard");
  var vehicleDetails = document.getElementById("vehicleDetails");
  var vehicleHelp = document.getElementById("vehicleHelp");
  var formError = document.getElementById("formError");
  var submitButton = document.getElementById("submitButton");
  var registryCount = document.getElementById("registryCount");
  var registryList = document.getElementById("registryList");
  var emptyState = document.getElementById("emptyState");
  var registryDateFrom = document.getElementById("registryDateFrom");
  var registryDateTo = document.getElementById("registryDateTo");
  var registrySearch = document.getElementById("registrySearch");
  var registryFiltersReset = document.getElementById("registryFiltersReset");
  var registryExportButton = document.getElementById("registryExportButton");
  var archiveStatus = document.getElementById("archiveStatus");
  var correctionPanel = document.getElementById("correctionPanel");
  var correctionReason = document.getElementById("correctionReason");
  var correctionForm = document.getElementById("correctionForm");
  var correctionLoadedAt = document.getElementById("correctionLoadedAt");
  var correctionBlockCount = document.getElementById("correctionBlockCount");
  var correctionBlocksList = document.getElementById("correctionBlocksList");
  var correctionTotalWeight = document.getElementById("correctionTotalWeight");
  var correctionError = document.getElementById("correctionError");
  var resubmitButton = document.getElementById("resubmitButton");
  var correctionToggle = document.getElementById("correctionToggle");
  var correctionContent = document.getElementById("correctionContent");
  var correctionToggleLabel = correctionToggle.querySelector(".rtl-correction-toggle-label");
  var handoverDialog = document.getElementById("handoverDialog");
  var confirmHandoverButton = document.getElementById("confirmHandoverButton");
  var cancelHandoverButton = document.getElementById("cancelHandoverButton");
  var toast = document.getElementById("toast");

  function escapeText(value) {
    return String(value || "");
  }

  function apiHeaders() { return { "Content-Type": "application/json" }; }

  function request(path, options) {
    return fetch(API + path, Object.assign({ headers: apiHeaders(), credentials: "same-origin" }, options || {}))
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (!response.ok) throw new Error(body.error || "Не удалось выполнить запрос");
          return body;
        });
      });
  }

  function showAccessNotice(message, showLogin) {
    accessNoticeText.textContent = message;
    passwordLoginLink.hidden = !showLogin;
    accessNotice.hidden = false;
  }

  function passwordAuthRequest(path, options) {
    return fetch("/api/rumex-registry/test/auth" + path, Object.assign({ credentials: "same-origin" }, options || {}))
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (!response.ok) throw new Error(body.error || "Не удалось выполнить запрос");
          return body;
        });
      });
  }

  function showToast(message) {
    toast.textContent = message;
    toast.hidden = false;
    setTimeout(function () { toast.hidden = true; }, 3300);
  }

  function pad(value) { return String(value).padStart(2, "0"); }

  function timeParts(timestamp) {
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: RUMEX_TIME_ZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    }).formatToParts(new Date(Number(timestamp) * 1000));
    return parts.reduce(function (result, part) {
      if (part.type !== "literal") result[part.type] = part.value;
      return result;
    }, {});
  }

  function localDatetimeValue(timestamp) {
    var parts = timeParts(timestamp || Math.floor(Date.now() / 1000));
    return parts.year + "-" + parts.month + "-" + parts.day + "T" + parts.hour + ":" + parts.minute;
  }

  function datetimeTimestamp(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value || "");
    if (!match) return 0;
    var utcTimestamp = Date.UTC(
      Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5])
    );
    var utcParts = timeParts(Math.floor(utcTimestamp / 1000));
    var offset = Date.UTC(
      Number(utcParts.year), Number(utcParts.month) - 1, Number(utcParts.day),
      Number(utcParts.hour), Number(utcParts.minute)
    ) - utcTimestamp;
    return Math.floor((utcTimestamp - offset) / 1000);
  }

  function formatTime(timestamp) {
    if (!timestamp) return "—";
    return new Date(Number(timestamp) * 1000).toLocaleString("ru-RU", {
      timeZone: RUMEX_TIME_ZONE,
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"
    });
  }

  function renderArchiveStatus(status) {
    if (!status || !status.registry_saved_at) {
      archiveStatus.textContent = "Состояние архива реестра пока недоступно.";
      return;
    }
    archiveStatus.textContent = "Реестр сохранён: " + formatTime(status.registry_saved_at)
      + " · Резервная копия: " + (status.backup_saved_at ? formatTime(status.backup_saved_at) : "ещё не создана");
  }

  function formatDate(timestamp) {
    if (!timestamp) return "—";
    return new Date(Number(timestamp) * 1000).toLocaleDateString("ru-RU", {
      timeZone: RUMEX_TIME_ZONE,
      day: "2-digit", month: "2-digit", year: "numeric"
    });
  }

  function statusInfo(status) {
    var values = {
      awaiting_accountant_review: ["Ждёт проверки бухгалтера", ""],
      requires_correction: ["Нужно исправление", ""],
      awaiting_er_sent: ["Ждёт отметку ЭР в Контуре", ""],
      documents_ready: ["Документы готовы", "rtl-status--ready"],
      documents_handed_to_driver: ["Документы готовы и переданы водителю", "rtl-status--done"]
    };
    if (status === "documents_handed_to_driver" && !arguments[1]) {
      return ["Машина выпущена · ждёт проверки бухгалтера", "rtl-status--ready"];
    }
    if (status === "documents_ready" && arguments[1] && arguments[2]) {
      return ["Проверено бухгалтером · расписка отправлена в ЭДО", "rtl-status--done"];
    }
    return values[status] || [status || "—", ""];
  }

  function eventText(event) {
    var payload = event.payload || {};
    var texts = {
      test_shipment_loaded: "Диспетчер зафиксировал фактическую погрузку.",
      test_shipment_returned_for_correction: "Бухгалтер вернул на исправление: " + (payload.reason || "—"),
      test_shipment_resubmitted: "Диспетчер отправил исправленную ревизию №" + (payload.revision_number || ""),
      test_shipment_taken_in_work: "Бухгалтер взял погрузку в работу.",
      test_documents_opened_automatically: "Система открыла ТТН: за 10 минут статус не был изменён.",
      test_shipment_reviewed: payload.er_required ? "Документы проверены: нужна ЭР." : "Документы проверены: ЭР не требуется.",
      test_er_sent_to_kontur_documents_opened: "Бухгалтер отметил отправку расписки в ЭДО Контур. Документы открыты.",
      test_ttn_downloaded: "Диспетчер скачал ТТН для печати.",
      test_documents_handed_to_driver: "Диспетчер подтвердил печать и передачу документов водителю."
    };
    return texts[event.event_type] || event.event_type;
  }

  function getBlockType(code) {
    return blockTypes.filter(function (item) { return item.code === code; })[0] || null;
  }

  function createElement(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = escapeText(text);
    return element;
  }

  function blockRow(index, item, scope) {
    var row = createElement("div", "rtl-block");
    row.appendChild(createElement("span", "rtl-block-index", String(index + 1)));
    var select = document.createElement("select");
    select.name = "letter";
    select.setAttribute("aria-label", "Буква блока " + (index + 1));
    blockTypes.forEach(function (type) {
      var option = document.createElement("option");
      option.value = type.code;
      option.textContent = type.code;
      option.selected = type.code === (item.letter || item.block_type_code || defaultBlockLetter(index));
      select.appendChild(option);
    });
    row.appendChild(select);
    var number = document.createElement("input");
    number.name = "number";
    number.type = "text";
    number.maxLength = 100;
    number.required = true;
    number.placeholder = "Индивидуальный номер";
    number.value = item.number || item.block_number || "";
    number.setAttribute("aria-label", "Номер блока " + (index + 1));
    row.appendChild(number);
    var weight = createElement("span", "rtl-block-weight");
    function updateWeight() {
      var type = getBlockType(select.value);
      weight.textContent = type ? type.nominal_weight_kg + " кг" : "—";
      updateTotal(scope);
    }
    select.addEventListener("change", updateWeight);
    number.addEventListener("input", function () { updateTotal(scope); });
    updateWeight();
    row.appendChild(weight);
    return row;
  }

  function scopeElements(scope) {
    return scope === "correction"
      ? { list: correctionBlocksList, count: correctionBlockCount, total: correctionTotalWeight }
      : { list: blocksList, count: blockCount, total: totalWeight };
  }

  function defaultBlockLetter(index) {
    var letters = ["A", "B", "C", "D", "E", "F", "K"];
    return letters[index] || "A";
  }

  function renderBlocks(scope, items) {
    var target = scopeElements(scope);
    var count = Math.max(3, Math.min(6, Number(target.count.value) || 3));
    target.count.value = count;
    var previous = items || collectBlocks(scope);
    target.list.replaceChildren();
    for (var index = 0; index < count; index += 1) {
      target.list.appendChild(blockRow(index, previous[index] || { letter: defaultBlockLetter(index), number: "" }, scope));
    }
    updateTotal(scope);
  }

  function collectBlocks(scope) {
    var target = scopeElements(scope);
    return Array.prototype.map.call(target.list.querySelectorAll(".rtl-block"), function (row) {
      return {
        letter: row.querySelector("select").value,
        number: row.querySelector("input").value.trim()
      };
    });
  }

  function updateTotal(scope) {
    var target = scopeElements(scope);
    var total = collectBlocks(scope).reduce(function (sum, item) {
      var type = getBlockType(item.letter);
      return sum + (type ? Number(type.nominal_weight_kg) : 0);
    }, 0);
    target.total.textContent = "Итого: " + total.toLocaleString("ru-RU") + " кг";
  }

  function renderVehicleLookup(body) {
    vehicleReadyForTtn = Boolean(body.found);
    submitButton.disabled = !vehicleReadyForTtn;
    vehicleCard.hidden = !body.found;
    vehicleDetails.replaceChildren();
    if (!body.found) {
      vehicleHelp.textContent = body.reason || "Для этой машины нет подтверждённой документной карточки.";
      return;
    }
    var binding = body.document_binding || {};
    var carrier = binding.carrier_snapshot || {};
    var vehicle = binding.vehicle_snapshot || {};
    [
      ["Полный номер", vehicle.full_plate], ["Марка / модель", vehicle.model],
      ["Водитель", binding.driver_full_name], ["Удостоверение", binding.driver_license_number], ["Перевозчик", carrier.name],
      ["ИНН", carrier.inn], ["Юридический адрес", carrier.legal_address],
      ["Проверено", formatTime(binding.checked_at)]
    ].forEach(function (pair) {
      var line = document.createElement("p");
      var label = document.createElement("strong");
      label.textContent = pair[0] + ": ";
      line.appendChild(label);
      line.appendChild(document.createTextNode(escapeText(pair[1]) || "—"));
      vehicleDetails.appendChild(line);
    });
    vehicleHelp.textContent = "Данные зафиксирует бухгалтерская карточка; изменить их в этом кабинете нельзя.";
  }

  function lookupVehicle() {
    var tail = plateTail.value.trim();
    vehicleReadyForTtn = false;
    submitButton.disabled = true;
    vehicleCard.hidden = true;
    if (!tail) {
      vehicleHelp.textContent = "После ввода хвоста карточка подставится только из подтверждённого бухгалтером снимка.";
      return;
    }
    request("/vehicles/" + encodeURIComponent(tail))
      .then(renderVehicleLookup)
      .catch(function (error) {
        vehicleReadyForTtn = false;
        submitButton.disabled = true;
        vehicleHelp.textContent = error.message || "Не удалось проверить машину.";
      });
  }

  function documentUrl(shipmentId, copyNumber) {
    var url = API + "/shipments/" + encodeURIComponent(shipmentId) + "/documents/tn";
    return url + "?copy=" + encodeURIComponent(copyNumber);
  }

  function renderDocuments(shipment, parent) {
    var documents = shipment.documents || [];
    documents.forEach(function (item) {
      var box = createElement("div", "rtl-document");
      if (item.document_kind === "TN" && shipment.ttn_number && ["ready", "issued"].indexOf(item.status) >= 0) {
        [1, 2, 3, 4].forEach(function (copyNumber) {
          var row = createElement("div", "rtl-ttn-download");
          var checked = (shipment.ttn_downloaded_copies || []).indexOf(copyNumber) >= 0;
          row.appendChild(createElement("span", "rtl-ttn-copy-check" + (checked ? " rtl-ttn-copy-check--done" : ""), checked ? "✓" : ""));
          var link = document.createElement("a");
          link.href = documentUrl(shipment.id, copyNumber);
          link.textContent = "Скачать " + (shipment.ttn_number || "ТТН") + " · экземпляр № " + copyNumber;
          link.addEventListener("click", function () {
            setTimeout(loadRegistry, 350);
          });
          row.appendChild(link);
          box.appendChild(row);
        });
      } else if (item.document_kind === "TN") {
        box.textContent = "Старая ТТН " + (item.registry_number || "")
          + ": утверждённый номер не выдавался, новый XLSX недоступен.";
      } else if (item.document_kind === "ER") {
        box.textContent = item.status === "not_required"
          ? "ЭР не требуется"
          : "ЭР: внешняя расписка Контура (шаблон не утверждён)";
      } else {
        box.textContent = (item.display_suffix || item.document_kind) + ": " + item.status;
      }
      parent.appendChild(box);
    });
  }

  function showCorrection(shipment) {
    correctionShipment = shipment;
    correctionReason.textContent = shipment.correction_reason || "Бухгалтер запросил исправление.";
    correctionLoadedAt.value = localDatetimeValue(shipment.loaded_at);
    correctionBlockCount.value = (shipment.items || []).length || 3;
    renderBlocks("correction", shipment.items || []);
    correctionError.textContent = "";
    correctionPanel.hidden = false;
    setCorrectionOpen(true);
    correctionPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function setCorrectionOpen(isOpen) {
    correctionContent.hidden = !isOpen;
    correctionToggle.setAttribute("aria-expanded", String(isOpen));
    correctionToggleLabel.textContent = isOpen ? "Свернуть" : "Открыть";
  }

  function shipmentDate(shipment) {
    var parts = timeParts(shipment.loaded_at);
    return parts.year + "-" + parts.month + "-" + parts.day;
  }

  function filteredRegistry() {
    var from = registryDateFrom.value;
    var to = registryDateTo.value;
    var query = registrySearch.value.trim().toLowerCase();
    return registry.filter(function (shipment) {
      var date = shipmentDate(shipment);
      if (from && date < from) return false;
      if (to && date > to) return false;
      if (!query) return true;
      var vehicle = ((shipment.document_snapshot || {}).vehicle || {});
      var searchable = [shipment.ttn_number, shipment.registry_number, vehicle.plate_tail, vehicle.full_plate]
        .join(" ").toLowerCase();
      return searchable.indexOf(query) >= 0;
    });
  }

  function exportRegistry() {
    var query = new URLSearchParams();
    if (registryDateFrom.value) query.set("date_from", registryDateFrom.value);
    if (registryDateTo.value) query.set("date_to", registryDateTo.value);
    if (registrySearch.value.trim()) query.set("search", registrySearch.value.trim());
    window.location.assign(API + "/registry/export?" + query.toString());
  }

  function renderRegistry() {
    var shipments = filteredRegistry();
    registryCount.textContent = registry.length
      ? "Найдено: " + shipments.length + " из " + registry.length
      : "Записей пока нет";
    emptyState.hidden = shipments.length > 0;
    emptyState.textContent = registry.length
      ? "По заданным фильтрам погрузок не найдено."
      : "Тестовых погрузок пока нет.";
    registryList.replaceChildren();
    shipments.forEach(function (shipment, index) {
      var isOpen = openedShipmentId === shipment.id;
      var card = createElement(
        "article",
        "rtl-card rtl-card--tone-" + (index % 2 ? "b" : "a") + (isOpen ? " rtl-card--open" : "")
      );
      var head = createElement("button", "rtl-card-head");
      var detailsId = "shipment-details-" + shipment.id;
      head.type = "button";
      head.setAttribute("aria-expanded", String(isOpen));
      head.setAttribute("aria-controls", detailsId);
      var heading = document.createElement("div");
      heading.appendChild(createElement(
        "h3",
        "",
        formatDate(shipment.loaded_at) + " · " + (shipment.ttn_number || shipment.registry_number) +
          " · Блоков: " + (shipment.items || []).length
      ));
      var snapshot = shipment.document_snapshot || {};
      var vehicle = snapshot.vehicle || {};
      heading.appendChild(createElement("p", "rtl-card-summary", "…" + (vehicle.plate_tail || "—") + " · " + formatTime(shipment.loaded_at)));
      head.appendChild(heading);
      var status = statusInfo(
        shipment.status,
        shipment.accountant_reviewed_at,
        shipment.kontur_sent_at
      );
      head.appendChild(createElement("span", "rtl-status " + status[1], status[0]));
      head.addEventListener("click", function () {
        openedShipmentId = isOpen ? null : shipment.id;
        renderRegistry();
      });
      card.appendChild(head);
      if (!isOpen) {
        registryList.appendChild(card);
        return;
      }

      var details = createElement("div", "rtl-card-details");
      details.id = detailsId;
      details.setAttribute("role", "region");
      if (shipment.ttn_number) details.appendChild(createElement("p", "", "Внутренняя запись: " + shipment.registry_number));
      details.appendChild(createElement("p", "", "Погрузка: " + formatTime(shipment.loaded_at) + " · " + shipment.total_weight_kg.toLocaleString("ru-RU") + " кг"));
      if (shipment.ttn_printed_at) {
        details.appendChild(createElement(
          "span", "rtl-ttn-printed", "✓ ТТН скачана для печати · " + formatTime(shipment.ttn_printed_at)
        ));
      }
      var list = document.createElement("dl");
      [
        ["Автомобиль", [vehicle.full_plate, vehicle.model].filter(Boolean).join(" · ") || "—"],
        ["Перевозчик", ((snapshot.carrier || {}).name || "—")],
        ["Водитель", ((snapshot.driver || {}).full_name || "—")],
        ["Удостоверение", ((snapshot.driver || {}).license_number || "—")],
        ["Ревизия", "№" + (shipment.revision_number || 1)],
        ["Проверил документы", shipment.accountant_name || "Ещё не проверены"]
      ].forEach(function (pair) {
        list.appendChild(createElement("dt", "", pair[0]));
        list.appendChild(createElement("dd", "", pair[1]));
      });
      details.appendChild(list);
      if (shipment.status === "awaiting_accountant_review") {
        var waitText = shipment.task_taken_by
          ? "В работе у: " + shipment.task_taken_by
          : "Ожидается решение бухгалтера до " + formatTime(shipment.accountant_decision_due_at);
        details.appendChild(createElement("p", "", waitText));
      }
      var items = createElement("p", "", "Блоки: " + (shipment.items || []).map(function (item) {
        return item.block_type_code + " / " + item.block_number + " (" + item.weight_kg + " кг)";
      }).join(", "));
      details.appendChild(items);
      var docs = createElement("div", "rtl-card-documents");
      renderDocuments(shipment, docs);
      if (docs.children.length) details.appendChild(docs);
      var actions = createElement("div", "rtl-card-actions");
      if (shipment.status === "requires_correction") {
        var correct = createElement("button", "rtl-link-button", "Исправить и отправить снова");
        correct.type = "button";
        correct.addEventListener("click", function () { showCorrection(shipment); });
        actions.appendChild(correct);
      }
      if (shipment.status === "documents_ready") {
        var handed = createElement("button", "rtl-link-button rtl-handover-button", "Подтвердить печать и передачу водителю");
        handed.type = "button";
        handed.addEventListener("click", function () {
          handoverShipmentId = shipment.id;
          handoverDialog.hidden = false;
        });
        actions.appendChild(handed);
      }
      if (actions.children.length) details.appendChild(actions);
      if ((shipment.events || []).length) {
        var timeline = createElement("ul", "rtl-timeline");
        var timelineWrap = createElement("div", "rtl-timeline-wrap");
        shipment.events.slice().reverse().forEach(function (event) {
          var item = document.createElement("li");
          item.appendChild(createElement("time", "", formatTime(event.occurred_at)));
          item.appendChild(document.createTextNode(
            eventText(event) + (event.actor_name ? " · " + event.actor_name : "")
          ));
          timeline.appendChild(item);
        });
        timelineWrap.appendChild(timeline);
        details.appendChild(timelineWrap);
      }
      card.appendChild(details);
      registryList.appendChild(card);
    });
  }

  function loadRegistry() {
    if (registryLoadPromise) {
      registryReloadRequested = true;
      return registryLoadPromise.then(function () { return registryLoadPromise || loadRegistry(); });
    }
    registryLoadPromise = request("/registry")
      .then(function (body) {
        blockTypes = body.block_types || [];
        registry = body.shipments || [];
        renderArchiveStatus(body.archive_status);
        if (!blocksList.children.length) renderBlocks("new");
        else updateTotal("new");
        if (!correctionPanel.hidden) updateTotal("correction");
        renderRegistry();
      })
      .catch(function (error) {
        showAccessNotice(error.message || "Кабинет недоступен.", true);
      })
      .finally(function () {
        registryLoadPromise = null;
        if (registryReloadRequested) {
          registryReloadRequested = false;
          loadRegistry();
        }
      });
    return registryLoadPromise;
  }

  function scheduleAutoRefresh() {
    clearTimeout(refreshTimer);
    if (!autoRefreshStarted || document.hidden) return;
    refreshTimer = setTimeout(function () {
      if (busy || document.hidden) {
        scheduleAutoRefresh();
        return;
      }
      loadRegistry().finally(scheduleAutoRefresh);
    }, REFRESH_INTERVAL_MS);
  }

  function startAutoRefresh() {
    if (autoRefreshStarted) return;
    autoRefreshStarted = true;
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        clearTimeout(refreshTimer);
        return;
      }
      if (busy) {
        scheduleAutoRefresh();
        return;
      }
      loadRegistry().finally(scheduleAutoRefresh);
    });
    scheduleAutoRefresh();
  }

  function createShipment(event) {
    event.preventDefault();
    formError.textContent = "";
    if (!loadingForm.checkValidity()) {
      loadingForm.reportValidity();
      return;
    }
    if (!vehicleReadyForTtn) {
      formError.textContent = "Сначала укажите машину с подтверждённой карточкой и номером водительского удостоверения.";
      return;
    }
    var timestamp = datetimeTimestamp(loadedAt.value);
    if (!timestamp) {
      formError.textContent = "Укажите фактическую дату и время погрузки.";
      return;
    }
    busy = true;
    submitButton.disabled = true;
    request("/shipments", {
      method: "POST",
      body: JSON.stringify({
        plate_tail: plateTail.value.trim(),
        block_count: Number(blockCount.value),
        items: collectBlocks("new"),
        loaded_at: timestamp
      })
    })
      .then(function (body) {
        showToast(body.shipment.ttn_number
          ? "Погрузка сохранена. ТТН открыта автоматически до начала смены бухгалтера."
          : "Погрузка " + body.shipment.registry_number + " сохранена и ждёт бухгалтера.");
        loadingForm.reset();
        loadedAt.value = localDatetimeValue();
        blockCount.value = 3;
        vehicleReadyForTtn = false;
        submitButton.disabled = true;
        vehicleCard.hidden = true;
        vehicleHelp.textContent = "После ввода хвоста карточка подставится только из подтверждённого бухгалтером снимка.";
        renderBlocks("new", []);
        return loadRegistry();
      })
      .catch(function (error) { formError.textContent = error.message || "Не удалось сохранить погрузку."; })
      .finally(function () { busy = false; submitButton.disabled = false; });
  }

  function resubmitShipment(event) {
    event.preventDefault();
    correctionError.textContent = "";
    if (!correctionShipment || !correctionForm.checkValidity()) {
      correctionForm.reportValidity();
      return;
    }
    var timestamp = datetimeTimestamp(correctionLoadedAt.value);
    if (!timestamp) {
      correctionError.textContent = "Укажите фактическую дату и время погрузки.";
      return;
    }
    resubmitButton.disabled = true;
    request("/shipments/" + encodeURIComponent(correctionShipment.id) + "/resubmit", {
      method: "POST",
      body: JSON.stringify({
        block_count: Number(correctionBlockCount.value),
        items: collectBlocks("correction"),
        loaded_at: timestamp
      })
    })
      .then(function (body) {
        showToast("Исправленная ревизия №" + body.shipment.revision_number + " отправлена бухгалтеру.");
        correctionPanel.hidden = true;
        correctionShipment = null;
        return loadRegistry();
      })
      .catch(function (error) { correctionError.textContent = error.message || "Не удалось отправить исправление."; })
      .finally(function () { resubmitButton.disabled = false; });
  }

  function confirmHandover() {
    if (!handoverShipmentId) return;
    confirmHandoverButton.disabled = true;
    request("/shipments/" + encodeURIComponent(handoverShipmentId) + "/handed-to-driver", { method: "POST", body: "{}" })
      .then(function () {
        showToast("Передача документов водителю подтверждена.");
        handoverDialog.hidden = true;
        handoverShipmentId = null;
        return loadRegistry();
      })
      .catch(function (error) { showToast(error.message || "Не удалось подтвердить передачу."); })
      .finally(function () { confirmHandoverButton.disabled = false; });
  }

  plateTail.addEventListener("input", function () {
    clearTimeout(lookupTimer);
    lookupTimer = setTimeout(lookupVehicle, 350);
  });
  plateTail.addEventListener("blur", lookupVehicle);
  blockCount.addEventListener("change", function () { renderBlocks("new"); });
  correctionBlockCount.addEventListener("change", function () { renderBlocks("correction"); });
  loadingForm.addEventListener("submit", createShipment);
  correctionForm.addEventListener("submit", resubmitShipment);
  correctionToggle.addEventListener("click", function () {
    setCorrectionOpen(correctionContent.hidden);
  });
  [registryDateFrom, registryDateTo, registrySearch].forEach(function (filter) {
    filter.addEventListener("input", renderRegistry);
  });
  registryFiltersReset.addEventListener("click", function () {
    registryDateFrom.value = "";
    registryDateTo.value = "";
    registrySearch.value = "";
    renderRegistry();
  });
  registryExportButton.addEventListener("click", exportRegistry);
  cancelHandoverButton.addEventListener("click", function () { handoverDialog.hidden = true; handoverShipmentId = null; });
  confirmHandoverButton.addEventListener("click", confirmHandover);

  passwordLogoutButton.addEventListener("click", function () {
    passwordLogoutButton.disabled = true;
    passwordAuthRequest("/logout", { method: "POST" })
      .then(function () { location.replace("/rumex-test-loading-login.html"); })
      .catch(function (error) { showToast(error.message || "Не удалось выйти"); })
      .finally(function () { passwordLogoutButton.disabled = false; });
  });

  loadedAt.value = localDatetimeValue();
  submitButton.disabled = true;
  passwordAuthRequest("/check")
    .then(function (body) {
      if (body.authenticated) {
        return passwordAuthRequest("/me").then(function (identity) {
          passwordSessionUser.textContent = "Вход по паролю: " + identity.user;
          passwordSession.hidden = false;
          return loadRegistry().then(startAutoRefresh);
        });
      }
      if (body.configured) {
        showAccessNotice("Войдите по личному паролю диспетчера.", true);
      } else {
        showAccessNotice("Парольный вход диспетчера пока не настроен.", false);
      }
      return null;
    })
    .catch(function (error) {
      showAccessNotice(error.message || "Не удалось проверить доступ к кабинету.", true);
    });
})();
