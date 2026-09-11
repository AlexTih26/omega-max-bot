(function () {
  "use strict";

  var API = "/api/rumex-registry/test";
  var webApp = window.WebApp || null;
  var initData = "";
  var blockTypes = [];
  var registry = [];
  var lookupTimer = null;
  var correctionShipment = null;
  var handoverShipmentId = null;
  var busy = false;

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
  var refreshButton = document.getElementById("refreshButton");
  var registryCount = document.getElementById("registryCount");
  var registryList = document.getElementById("registryList");
  var emptyState = document.getElementById("emptyState");
  var correctionPanel = document.getElementById("correctionPanel");
  var correctionReason = document.getElementById("correctionReason");
  var correctionForm = document.getElementById("correctionForm");
  var correctionLoadedAt = document.getElementById("correctionLoadedAt");
  var correctionBlockCount = document.getElementById("correctionBlockCount");
  var correctionBlocksList = document.getElementById("correctionBlocksList");
  var correctionTotalWeight = document.getElementById("correctionTotalWeight");
  var correctionError = document.getElementById("correctionError");
  var resubmitButton = document.getElementById("resubmitButton");
  var closeCorrectionButton = document.getElementById("closeCorrectionButton");
  var handoverDialog = document.getElementById("handoverDialog");
  var confirmHandoverButton = document.getElementById("confirmHandoverButton");
  var cancelHandoverButton = document.getElementById("cancelHandoverButton");
  var toast = document.getElementById("toast");

  function escapeText(value) {
    return String(value || "");
  }

  function apiHeaders() {
    var headers = { "Content-Type": "application/json" };
    if (initData) headers["X-Max-Init-Data"] = initData;
    return headers;
  }

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

  function setupBridge() {
    if (!webApp) return false;
    initData = webApp.initData || "";
    try {
      if (webApp.ready) webApp.ready();
      if (webApp.expand) webApp.expand();
    } catch (error) {}
    return Boolean(initData);
  }

  function showToast(message) {
    toast.textContent = message;
    toast.hidden = false;
    setTimeout(function () { toast.hidden = true; }, 3300);
  }

  function pad(value) { return String(value).padStart(2, "0"); }

  function localDatetimeValue(timestamp) {
    var date = timestamp ? new Date(Number(timestamp) * 1000) : new Date();
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) + "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function datetimeTimestamp(value) {
    var date = new Date(value);
    return Number.isNaN(date.getTime()) ? 0 : Math.floor(date.getTime() / 1000);
  }

  function formatTime(timestamp) {
    if (!timestamp) return "—";
    return new Date(Number(timestamp) * 1000).toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"
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
    return values[status] || [status || "—", ""];
  }

  function eventText(event) {
    var payload = event.payload || {};
    var texts = {
      test_shipment_loaded: "Диспетчер зафиксировал фактическую погрузку.",
      test_shipment_returned_for_correction: "Бухгалтер вернул на исправление: " + (payload.reason || "—"),
      test_shipment_resubmitted: "Диспетчер отправил исправленную ревизию №" + (payload.revision_number || ""),
      test_shipment_reviewed: payload.er_required ? "Бухгалтер проверил погрузку: нужна ЭР." : "Бухгалтер проверил погрузку: ЭР не требуется.",
      test_er_sent_to_kontur_documents_opened: "Бухгалтер отметил отправку расписки в ЭДО Контур. Документы открыты.",
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
      option.selected = type.code === (item.letter || item.block_type_code || "A");
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

  function renderBlocks(scope, items) {
    var target = scopeElements(scope);
    var count = Math.max(1, Math.min(200, Number(target.count.value) || 1));
    target.count.value = count;
    var previous = items || collectBlocks(scope);
    target.list.replaceChildren();
    for (var index = 0; index < count; index += 1) {
      target.list.appendChild(blockRow(index, previous[index] || { letter: "A", number: "" }, scope));
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
      ["Водитель", binding.driver_full_name], ["Перевозчик", carrier.name],
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
    vehicleCard.hidden = true;
    if (!tail) {
      vehicleHelp.textContent = "После ввода хвоста карточка подставится только из подтверждённого бухгалтером снимка.";
      return;
    }
    request("/vehicles/" + encodeURIComponent(tail))
      .then(renderVehicleLookup)
      .catch(function (error) {
        vehicleHelp.textContent = error.message || "Не удалось проверить машину.";
      });
  }

  function documentUrl(shipmentId) {
    var url = API + "/shipments/" + encodeURIComponent(shipmentId) + "/documents/tn";
    return initData ? url + "?initData=" + encodeURIComponent(initData) : url;
  }

  function renderDocuments(shipment, parent) {
    var documents = shipment.documents || [];
    documents.forEach(function (item) {
      var box = createElement("div", "rtl-document");
      if (item.document_kind === "TN" && ["ready", "issued"].indexOf(item.status) >= 0) {
        var link = document.createElement("a");
        link.href = documentUrl(shipment.id);
        link.textContent = "Скачать ТТН №" + shipment.registry_number;
        box.appendChild(link);
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
    correctionBlockCount.value = (shipment.items || []).length || 1;
    renderBlocks("correction", shipment.items || []);
    correctionError.textContent = "";
    correctionPanel.hidden = false;
    correctionPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderRegistry() {
    registryCount.textContent = registry.length ? "Записей: " + registry.length : "Записей пока нет";
    emptyState.hidden = registry.length > 0;
    registryList.replaceChildren();
    registry.forEach(function (shipment) {
      var card = createElement("article", "rtl-card");
      var head = createElement("div", "rtl-card-head");
      var heading = document.createElement("div");
      heading.appendChild(createElement("h3", "", shipment.registry_number));
      var snapshot = shipment.document_snapshot || {};
      var vehicle = snapshot.vehicle || {};
      heading.appendChild(createElement("p", "", "…" + (vehicle.plate_tail || "—") + " · " + ((snapshot.driver || {}).full_name || "Водитель не указан")));
      heading.appendChild(createElement("p", "", "Погрузка: " + formatTime(shipment.loaded_at) + " · " + shipment.total_weight_kg.toLocaleString("ru-RU") + " кг"));
      head.appendChild(heading);
      var status = statusInfo(shipment.status);
      head.appendChild(createElement("span", "rtl-status " + status[1], status[0]));
      card.appendChild(head);

      var details = createElement("div", "rtl-card-details");
      var list = document.createElement("dl");
      [
        ["Автомобиль", [vehicle.full_plate, vehicle.model].filter(Boolean).join(" · ") || "—"],
        ["Перевозчик", ((snapshot.carrier || {}).name || "—")],
        ["Водитель", ((snapshot.driver || {}).full_name || "—")],
        ["Ревизия", "№" + (shipment.revision_number || 1)]
      ].forEach(function (pair) {
        list.appendChild(createElement("dt", "", pair[0]));
        list.appendChild(createElement("dd", "", pair[1]));
      });
      details.appendChild(list);
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
        var handed = createElement("button", "rtl-link-button", "Подтвердить печать и передачу водителю");
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
        shipment.events.forEach(function (event) {
          var item = document.createElement("li");
          item.appendChild(createElement("time", "", formatTime(event.occurred_at)));
          item.appendChild(document.createTextNode(eventText(event)));
          timeline.appendChild(item);
        });
        details.appendChild(timeline);
      }
      card.appendChild(details);
      registryList.appendChild(card);
    });
  }

  function loadRegistry() {
    refreshButton.disabled = true;
    return request("/registry")
      .then(function (body) {
        blockTypes = body.block_types || [];
        registry = body.shipments || [];
        renderBlocks("new");
        renderRegistry();
      })
      .catch(function (error) {
        showAccessNotice(error.message || "Кабинет недоступен.", !initData);
      })
      .finally(function () { refreshButton.disabled = false; });
  }

  function createShipment(event) {
    event.preventDefault();
    formError.textContent = "";
    if (!loadingForm.checkValidity()) {
      loadingForm.reportValidity();
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
        showToast("Погрузка " + body.shipment.registry_number + " сохранена и ждёт бухгалтера.");
        loadingForm.reset();
        loadedAt.value = localDatetimeValue();
        blockCount.value = 1;
        vehicleCard.hidden = true;
        vehicleHelp.textContent = "После ввода хвоста карточка подставится только из подтверждённого бухгалтером снимка.";
        renderBlocks("new", [{ letter: "A", number: "" }]);
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
  refreshButton.addEventListener("click", loadRegistry);
  closeCorrectionButton.addEventListener("click", function () { correctionPanel.hidden = true; correctionShipment = null; });
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
  if (setupBridge()) {
    loadRegistry();
    return;
  }
  passwordAuthRequest("/check")
    .then(function (body) {
      if (body.authenticated) {
        return passwordAuthRequest("/me").then(function (identity) {
          passwordSessionUser.textContent = "Вход по паролю: " + identity.user;
          passwordSession.hidden = false;
          return loadRegistry();
        });
      }
      if (body.configured) {
        showAccessNotice("Откройте кабинет через Mini App MAX или войдите по личному паролю.", true);
      } else {
        showAccessNotice("Откройте кабинет через Mini App MAX. Парольный вход пока не настроен.", false);
      }
      return null;
    })
    .catch(function (error) {
      showAccessNotice(error.message || "Не удалось проверить доступ к кабинету.", true);
    });
})();
