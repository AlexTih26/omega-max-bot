(function () {
  "use strict";

  var API = "/api/rumex-registry";
  var registryData = null;
  var testRegistryData = null;
  var carriers = [];
  var samples = [];
  var fleetVehicles = [];
  var fleetBindingHistory = {};
  var currentAccountant = null;
  var openedShipmentId = null;
  var openedTestShipmentId = null;
  var editingCarrierId = null;
  var bindingVehicle = null;
  var busy = false;
  var registryLoadPromise = null;
  var testRegistryLoadPromise = null;
  var directoryLoadPromise = null;
  var refreshTimer = null;
  var autoRefreshStarted = false;
  var REFRESH_INTERVAL_MS = 5000;

  var siteLabel = document.getElementById("siteLabel");
  var currentUser = document.getElementById("currentUser");
  var logoutButton = document.getElementById("logoutButton");
  var workTab = document.getElementById("workTab");
  var registryTab = document.getElementById("registryTab");
  var fleetTab = document.getElementById("fleetTab");
  var directoryTab = document.getElementById("directoryTab");
  var workPanel = document.getElementById("workPanel");
  var registryPanel = document.getElementById("registryPanel");
  var fleetPanel = document.getElementById("fleetPanel");
  var directoryPanel = document.getElementById("directoryPanel");
  var registryCount = document.getElementById("registryCount");
  var registryList = document.getElementById("registryList");
  var emptyState = document.getElementById("emptyState");
  var workEmptyState = document.getElementById("workEmptyState");
  var workRegistryList = document.getElementById("workRegistryList");
  var testEmptyState = document.getElementById("testEmptyState");
  var testRegistryList = document.getElementById("testRegistryList");
  var registryDateFrom = document.getElementById("registryDateFrom");
  var registryDateTo = document.getElementById("registryDateTo");
  var registrySearch = document.getElementById("registrySearch");
  var registryFiltersReset = document.getElementById("registryFiltersReset");
  var registryExportButton = document.getElementById("registryExportButton");
  var archiveStatus = document.getElementById("archiveStatus");
  var configNotice = document.getElementById("configNotice");
  var directoryAccessNotice = document.getElementById("directoryAccessNotice");
  var newCarrierButton = document.getElementById("newCarrierButton");
  var carrierForm = document.getElementById("carrierForm");
  var carrierFormTitle = document.getElementById("carrierFormTitle");
  var carrierFormError = document.getElementById("carrierFormError");
  var cancelCarrierButton = document.getElementById("cancelCarrierButton");
  var cancelCarrierButtonBottom = document.getElementById("cancelCarrierButtonBottom");
  var saveCarrierButton = document.getElementById("saveCarrierButton");
  var samplesList = document.getElementById("samplesList");
  var carrierCount = document.getElementById("carrierCount");
  var carrierEmptyState = document.getElementById("carrierEmptyState");
  var carrierList = document.getElementById("carrierList");
  var importFleetButton = document.getElementById("importFleetButton");
  var fleetAccessNotice = document.getElementById("fleetAccessNotice");
  var fleetEmptyState = document.getElementById("fleetEmptyState");
  var fleetList = document.getElementById("fleetList");
  var fleetBindingForm = document.getElementById("fleetBindingForm");
  var fleetBindingVehicle = document.getElementById("fleetBindingVehicle");
  var fleetBindingFormError = document.getElementById("fleetBindingFormError");
  var bindingCarrierId = document.getElementById("bindingCarrierId");
  var bindingDriverName = document.getElementById("bindingDriverName");
  var bindingDriverLicense = document.getElementById("bindingDriverLicense");
  var bindingNote = document.getElementById("bindingNote");
  var saveFleetBindingButton = document.getElementById("saveFleetBindingButton");
  var cancelFleetBindingButton = document.getElementById("cancelFleetBindingButton");
  var cancelFleetBindingButtonBottom = document.getElementById("cancelFleetBindingButtonBottom");
  var fleetSection = document.getElementById("fleetSection");
  var toast = document.getElementById("toast");
  var accountantAvailability = document.getElementById("accountantAvailability");

  function request(path, options) {
    return fetch(API + path, Object.assign({ credentials: "same-origin" }, options || {}))
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (response.status === 401) {
            location.replace("/rumex-accountant-login.html?next=" + encodeURIComponent(location.pathname));
            throw new Error("Требуется вход");
          }
          if (!response.ok) throw new Error(body.error || "Не удалось выполнить запрос");
          return body;
        });
      });
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function formatTime(timestamp) {
    if (!timestamp) return "—";
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone: "Asia/Irkutsk",
      dateStyle: "short", timeStyle: "short"
    }).format(new Date(Number(timestamp) * 1000));
  }

  function renderArchiveStatus(status) {
    if (!status || !status.registry_saved_at) {
      archiveStatus.textContent = "Состояние архива реестра пока недоступно.";
      return;
    }
    archiveStatus.textContent = "Реестр сохранён: " + formatTime(status.registry_saved_at)
      + " · Резервная копия: " + (status.backup_saved_at ? formatTime(status.backup_saved_at) : "ещё не создана");
  }

  function statusInfo(status) {
    var values = {
      awaiting_er: ["Ожидает отправки ЭР", "wait"],
      er_sent: ["ЭР отправлена в Контур", "wait"],
      er_confirmed: ["ЭР подтверждена", "ready"],
      tn_ready: ["ТТН доступна диспетчеру", "ready"],
      issued: ["Документы выданы", "issued"],
      departed: ["Машина выехала", "departed"],
      cancelled: ["Отменена", "wait"]
    };
    return values[status] || [status || "Неизвестный статус", "wait"];
  }

  function documentStatus(status) {
    var values = {
      draft: "черновик",
      waiting_er_confirmation: "ждёт подтверждения ЭР",
      sent_to_kontur: "отправлен в Контур",
      confirmed: "подтверждён",
      ready: "готов к печати",
      issued: "выдан",
      cancelled: "отменён"
    };
    return values[status] || status;
  }

  function eventText(event) {
    var values = {
      shipment_created: "Отгрузка подтверждена, номер зарезервирован",
      er_sent_to_kontur: "ЭР отправлена в Контур",
      er_confirmed_tn_opened: "ЭР подтверждена, ТТН открыта диспетчеру"
    };
    return values[event.event_type] || event.event_type;
  }

  function shipmentItems(shipment) {
    return (shipment.items || []).map(function (item) {
      return item.block_type_code + item.block_number;
    }).join(", ") || "Без позиций";
  }

  function renderDocuments(shipment, parent) {
    var section = element("section", "rr-section");
    section.appendChild(element("h2", "rr-section-title", "Документы"));
    (shipment.documents || []).forEach(function (document) {
      var line = element("div", "rr-document");
      var left = element("div");
      left.appendChild(element("div", "rr-document-name", document.display_number));
      if (document.external_reference) {
        left.appendChild(element("small", "rr-muted", "Контур: " + document.external_reference));
      }
      line.appendChild(left);
      line.appendChild(element("span", "rr-document-status", documentStatus(document.status)));
      section.appendChild(line);
    });
    parent.appendChild(section);
  }

  function renderErAction(shipment, parent, draftValues) {
    if (shipment.status !== "awaiting_er" && shipment.status !== "er_sent") return;
    var section = element("section", "rr-section rr-action");
    var action = shipment.status === "awaiting_er" ? "sent" : "confirmed";
    section.appendChild(element(
      "h2", "rr-section-title",
      action === "sent" ? "Работа с ЭР" : "Подтверждение ЭР"
    ));
    var label = element("label", "rr-label", "Номер или ссылка Контура (необязательно)");
    var reference = element("input", "rr-reference");
    reference.type = "text";
    reference.maxLength = 160;
    reference.placeholder = "Например: Контур-12345";
    reference.value = ((shipment.documents || []).filter(function (document) {
      return document.document_kind === "ER";
    })[0] || {}).external_reference || "";
    label.htmlFor = "reference-" + shipment.id;
    reference.id = label.htmlFor;
    if (draftValues && Object.prototype.hasOwnProperty.call(draftValues, reference.id)) {
      reference.value = draftValues[reference.id];
    }
    var button = element(
      "button", "rr-primary-button",
      action === "sent" ? "Отметить: ЭР отправлена в Контур" : "Подтвердить ЭР и открыть ТТН"
    );
    button.type = "button";
    button.addEventListener("click", function () {
      applyErAction(shipment.id, action, reference.value, button);
    });
    section.appendChild(label);
    section.appendChild(reference);
    section.appendChild(button);
    parent.appendChild(section);
  }

  function renderTimeline(shipment, parent) {
    var events = shipment.audit_events || [];
    if (!events.length) return;
    var section = element("section", "rr-section");
    section.appendChild(element("h2", "rr-section-title", "История"));
    var list = element("ul", "rr-timeline");
    events.forEach(function (event) {
      var item = element("li");
      item.appendChild(element("time", "", formatTime(event.occurred_at)));
      var actor = event.actor_name ? " · " + event.actor_name : "";
      item.appendChild(element("span", "", eventText(event) + actor));
      list.appendChild(item);
    });
    section.appendChild(list);
    parent.appendChild(section);
  }

  function renderDetails(shipment, card, draftValues) {
    var details = element("div", "rr-details");
    var cargo = element("section", "rr-section");
    cargo.appendChild(element("h2", "rr-section-title", "Груз"));
    var items = element("ul", "rr-items");
    (shipment.items || []).forEach(function (item) {
      var row = element("li", "rr-item");
      row.appendChild(element("span", "", item.product_name + " · " + item.block_type_code + item.block_number));
      row.appendChild(element("small", "", (item.weight_kg / 1000).toLocaleString("ru-RU") + " т"));
      items.appendChild(row);
    });
    cargo.appendChild(items);
    details.appendChild(cargo);
    renderDocuments(shipment, details);
    renderErAction(shipment, details, draftValues);
    renderTimeline(shipment, details);
    card.appendChild(details);
  }

  function renderShipment(shipment, draftValues) {
    var card = element("article", "rr-card");
    var summary = element("button", "rr-summary");
    summary.type = "button";
    summary.setAttribute("aria-expanded", String(openedShipmentId === shipment.id));
    var summaryLeft = element("div");
    summaryLeft.appendChild(element("p", "rr-summary-meta rr-summary-date", "Погрузка: " + formatTime(shipment.loaded_at)));
    summaryLeft.appendChild(element("p", "rr-number", shipment.registry_number));
    summaryLeft.appendChild(element("p", "rr-summary-meta", shipmentItems(shipment)));
    var state = statusInfo(shipment.status);
    summary.appendChild(summaryLeft);
    summary.appendChild(element("span", "rr-status rr-status--" + state[1], state[0]));
    summary.addEventListener("click", function () {
      openedShipmentId = openedShipmentId === shipment.id ? null : shipment.id;
      renderRegistry();
    });
    card.appendChild(summary);
    if (openedShipmentId === shipment.id) renderDetails(shipment, card, draftValues);
    registryList.appendChild(card);
  }

  function renderRegistry(draft) {
    var allShipments = (registryData && registryData.shipments) || [];
    var shipments = allShipments.filter(shipmentMatchesRegistryFilters);
    if (registryData && registryData.site_label) siteLabel.textContent = registryData.site_label;
    registryCount.textContent = allShipments.length
      ? "Найдено: " + shipments.length + " из " + allShipments.length
      : "Отгрузок в реестре пока нет";
    emptyState.hidden = shipments.length > 0;
    emptyState.textContent = allShipments.length
      ? "По заданным фильтрам отгрузок не найдено."
      : "В документообороте пока нет отгрузок.";
    clear(registryList);
    shipments.forEach(function (shipment) { renderShipment(shipment, draft && draft.values); });
    restoreDraft(registryList, draft);
  }

  function testStatusInfo(status) {
    var values = {
      awaiting_accountant_review: ["Ожидает проверки", "wait"],
      requires_correction: ["Возвращена на исправление", "wait"],
      awaiting_er_sent: ["Ожидает отметки в Контуре", "wait"],
      documents_ready: ["Документы открыты диспетчеру", "ready"],
      documents_handed_to_driver: ["Документы переданы водителю", "done"]
    };
    if (status === "documents_handed_to_driver" && !arguments[1]) {
      return ["Выпущена до проверки", "wait"];
    }
    return values[status] || [status || "Неизвестный статус", "wait"];
  }

  function testEventText(event) {
    var payload = event.payload || {};
    var labels = {
      test_shipment_loaded: "Диспетчер зафиксировал фактическую погрузку.",
      test_shipment_returned_for_correction: "Бухгалтер вернул на исправление: " + (payload.reason || "—"),
      test_shipment_resubmitted: "Диспетчер отправил ревизию №" + (payload.revision_number || ""),
      test_documents_opened_automatically: "Система открыла ТТН: за 10 минут статус не был изменён.",
      test_shipment_reviewed: "Документы проверены; ожидается отметка расписки в Контуре.",
      test_er_sent_to_kontur_documents_opened: "Бухгалтер отметил отправку расписки в ЭДО Контур. Документы открыты.",
      test_ttn_downloaded: "Диспетчер скачал ТТН для печати.",
      test_documents_handed_to_driver: "Диспетчер подтвердил печать и передачу документов водителю."
    };
    return labels[event.event_type] || event.event_type;
  }

  function testRevisionText(kind) {
    var labels = {
      submitted: "Первичная подача диспетчером",
      returned_for_correction: "Возврат бухгалтером на исправление",
      resubmitted: "Повторная подача после исправления"
    };
    return labels[kind] || kind || "Изменение данных погрузки";
  }

  function testDocumentText(document) {
    if (document.document_kind === "ER") {
      if (document.status === "not_required") return "ЭР: не требуется";
      if (document.status === "sent_to_kontur" || document.status === "issued") {
        return "ЭР: расписка отправлена в ЭДО Контур" + (document.external_reference ? " · " + document.external_reference : "");
      }
      return "ЭР: внешняя расписка Контура, юридический файл не формируется";
    }
    return (document.display_number || document.registry_number || "ТТН") + " · " + documentStatus(document.status);
  }

  function testDocumentUrl(shipment, copyNumber) {
    return API + "/test/shipments/" + encodeURIComponent(shipment.id) + "/documents/tn?copy=" + encodeURIComponent(copyNumber);
  }

  function renderTestDocument(shipment, document) {
    var label = [
      document.display_number || document.registry_number || shipment.ttn_number || "ТТН",
      document.display_suffix || document.document_kind,
      documentStatus(document.status)
    ].filter(Boolean).join(" · ");
    var archives = shipment.ttn_archives || [];
    if (document.document_kind === "TN" && shipment.ttn_number && document.status === "issued") {
      var archiveLinks = element("div", "rr-test-document-archive");
      archiveLinks.appendChild(element("strong", "", shipment.ttn_number + " · выдана водителю"));
      [1, 2, 3, 4].forEach(function (copyNumber) {
        var archived = archives.some(function (archive) {
          return Number(archive.copy_number) === copyNumber;
        });
        var link = element("a", "rr-test-document rr-test-document-link", "Открыть экземпляр № " + copyNumber);
        link.href = testDocumentUrl(shipment, copyNumber);
        link.target = "_blank";
        link.rel = "noopener";
        link.title = archived
          ? "Открыть неизменяемый архив ТТН, экземпляр № " + copyNumber
          : "Открыть ТТН, экземпляр № " + copyNumber;
        archiveLinks.appendChild(link);
      });
      return archiveLinks;
    }
    return element("span", "rr-test-document", testDocumentText(document));
  }

  function testSnapshot(shipment, parent) {
    var snapshot = shipment.document_snapshot || {};
    var vehicle = snapshot.vehicle || {};
    var carrier = snapshot.carrier || {};
    var driver = snapshot.driver || {};
    var section = element("div", "rr-test-snapshot");
    section.appendChild(element("p", "rr-test-detail", "Автомобиль: " + ([vehicle.full_plate, vehicle.model].filter(Boolean).join(" · ") || "—")));
    section.appendChild(element("p", "rr-test-detail", "Водитель: " + (driver.full_name || "—") + (driver.license_number ? " · удостоверение " + driver.license_number : "")));
    section.appendChild(element("p", "rr-test-detail", "Перевозчик: " + (carrier.name || "—") + (carrier.inn ? " · ИНН " + carrier.inn : "")));
    section.appendChild(element("p", "rr-test-detail", "Юридический адрес: " + (carrier.legal_address || "—")));
    section.appendChild(element(
      "p", "rr-test-detail",
      "Проверил документы: " + (shipment.accountant_name || "Ещё не проверены")
    ));
    parent.appendChild(section);
  }

  function testItems(shipment, parent) {
    var items = shipment.items || [];
    if (!items.length) return;
    var list = element("ul", "rr-test-items");
    items.forEach(function (item) {
      var row = element("li");
      row.appendChild(element("span", "", item.block_type_code + item.block_number + " · " + (item.product_name || "Блок")));
      row.appendChild(element("small", "rr-muted", Number(item.weight_kg || 0).toLocaleString("ru-RU") + " кг"));
      list.appendChild(row);
    });
    parent.appendChild(list);
  }

  function testHistory(shipment, parent) {
    var revisions = shipment.revisions || [];
    var events = shipment.events || [];
    if (!revisions.length && !events.length) return;
    var section = element("section", "rr-test-revisions");
    section.appendChild(element("h3", "rr-section-title", "История изменений"));
    section.appendChild(element("p", "rr-test-detail", "Здесь хранится, кто и когда подал данные, вернул на исправление или отправил исправленную версию. Эти записи нельзя удалить или переписать."));
    revisions.forEach(function (revision) {
      section.appendChild(element(
        "p", "rr-test-detail",
        "Ревизия №" + revision.revision_number + " · " + testRevisionText(revision.revision_kind) + " · " + formatTime(revision.created_at) + (revision.actor_name ? " · " + revision.actor_name : "")
      ));
    });
    events.forEach(function (event) {
      section.appendChild(element(
        "p", "rr-test-detail",
        formatTime(event.occurred_at) + " · " + testEventText(event) + (event.actor_name ? " · " + event.actor_name : "")
      ));
    });
    parent.appendChild(section);
  }

  function replaceTestShipment(shipment) {
    if (!testRegistryData) testRegistryData = { shipments: [] };
    var found = false;
    testRegistryData.shipments = (testRegistryData.shipments || []).map(function (item) {
      if (item.id !== shipment.id) return item;
      found = true;
      return shipment;
    });
    if (!found) testRegistryData.shipments.unshift(shipment);
    renderTestViews();
  }

  function applyTestAction(shipmentId, action, payload, button) {
    if (busy) return;
    busy = true;
    if (button) button.disabled = true;
    request("/test/shipments/" + encodeURIComponent(shipmentId) + "/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    })
      .then(function (body) {
        openedTestShipmentId = body.shipment.id;
        replaceTestShipment(body.shipment);
        showToast(body.message || "Изменение сохранено");
      })
      .catch(function (error) { showToast(error.message || "Не удалось сохранить изменение"); })
      .finally(function () {
        busy = false;
        if (button) button.disabled = false;
      });
  }

  function taskInfo(shipment) {
    if (shipment.status !== "awaiting_accountant_review") return "";
    if (shipment.task_taken_by) return "В работе у: " + shipment.task_taken_by;
    if (shipment.accountant_decision_due_at) return "Ожидание решения до " + formatTime(shipment.accountant_decision_due_at);
    return "Ожидает решения бухгалтера";
  }

  function testActions(shipment, parent, draftValues, idSuffix) {
    if (shipment.status === "awaiting_accountant_review" || (
      shipment.status === "documents_handed_to_driver" && !shipment.accountant_reviewed_at
    )) {
      var reviewSection = element("section", "rr-test-action");
      reviewSection.appendChild(element("h3", "rr-section-title", "Проверка бухгалтера"));
      var task = taskInfo(shipment);
      if (task) reviewSection.appendChild(element("p", "rr-test-detail", task));
      if (shipment.status === "awaiting_accountant_review" && !shipment.task_taken_by) {
        var claimButton = element("button", "rr-primary-button", "Взять в работу");
        claimButton.type = "button";
        claimButton.addEventListener("click", function () {
          applyTestAction(shipment.id, "claim", {}, claimButton);
        });
        reviewSection.appendChild(claimButton);
      }
      if (shipment.status === "awaiting_accountant_review" && shipment.task_taken_by && shipment.task_taken_by !== currentAccountant.user) {
        reviewSection.appendChild(element("p", "rr-test-detail", "Другой бухгалтер уже работает с этой погрузкой."));
        parent.appendChild(reviewSection);
        return;
      }
      reviewSection.appendChild(element("p", "rr-test-detail", shipment.status === "documents_handed_to_driver"
        ? "Машина уже выпущена. Проверьте документы и затем отметьте отправку расписки в ЭДО Контур."
        : "После проверки потребуется ручная отметка об отправке расписки в ЭДО Контур."));
      var reviewButton = element("button", "rr-primary-button", "Проверить и ожидать отметку ЭДО");
      reviewButton.type = "button";
      reviewButton.addEventListener("click", function () {
        applyTestAction(shipment.id, "review", { er_required: true }, reviewButton);
      });
      reviewSection.appendChild(reviewButton);

      var returnLabel = element("label", "rr-label", "Причина возврата на исправление *");
      var returnReason = element("textarea", "rr-reference");
      returnReason.rows = 2;
      returnReason.maxLength = 1000;
      returnReason.placeholder = "Опишите, что нужно исправить";
      returnLabel.htmlFor = "test-return-reason-" + shipment.id + "-" + idSuffix;
      returnReason.id = returnLabel.htmlFor;
      if (draftValues && Object.prototype.hasOwnProperty.call(draftValues, returnReason.id)) {
        returnReason.value = draftValues[returnReason.id];
      }
      var returnButton = element("button", "rr-refresh", "Вернуть диспетчеру на исправление");
      returnButton.type = "button";
      returnButton.addEventListener("click", function () {
        var reason = returnReason.value.trim();
        if (!reason) {
          showToast("Укажите причину возврата на исправление.");
          returnReason.focus();
          return;
        }
        applyTestAction(shipment.id, "return", { reason: reason }, returnButton);
      });
      reviewSection.appendChild(returnLabel);
      reviewSection.appendChild(returnReason);
      reviewSection.appendChild(returnButton);
      parent.appendChild(reviewSection);
      return;
    }
    if (shipment.status === "awaiting_er_sent") {
      var konturSection = element("section", "rr-test-action");
      konturSection.appendChild(element("h3", "rr-section-title", "ЭДО Контур"));
      var konturLabel = element("label", "rr-label", "Номер или ссылка Контура (необязательно)");
      var reference = element("input", "rr-reference");
      reference.type = "text";
      reference.maxLength = 500;
      reference.placeholder = "Например: Контур-12345";
      konturLabel.htmlFor = "test-kontur-reference-" + shipment.id + "-" + idSuffix;
      reference.id = konturLabel.htmlFor;
      if (draftValues && Object.prototype.hasOwnProperty.call(draftValues, reference.id)) {
        reference.value = draftValues[reference.id];
      }
      var konturButton = element("button", "rr-primary-button", "Расписка отправлена в ЭДО Контур");
      konturButton.type = "button";
      konturButton.addEventListener("click", function () {
        applyTestAction(shipment.id, "er-sent", { external_reference: reference.value.trim() }, konturButton);
      });
      konturSection.appendChild(konturLabel);
      konturSection.appendChild(reference);
      konturSection.appendChild(konturButton);
      parent.appendChild(konturSection);
    }
  }

  function renderTestShipment(shipment, index, draftValues, view) {
    var isNew = shipment.is_new_for_accountant === true;
    var isOpen = openedTestShipmentId === shipment.id;
    var card = element(
      "article",
      "rr-test-card rr-test-card--tone-" + (index % 2 ? "b" : "a")
        + (isNew ? " rr-test-card--new" : "") + (isOpen ? " rr-test-card--open" : "")
    );
    var summary = element("button", "rr-test-summary");
    var detailsId = "test-" + view + "-shipment-details-" + shipment.id;
    summary.type = "button";
    summary.id = "test-" + view + "-shipment-summary-" + shipment.id;
    summary.setAttribute("aria-expanded", String(isOpen));
    summary.setAttribute("aria-controls", detailsId);
    var summaryMain = element("div", "rr-test-summary-main");
    summaryMain.appendChild(element("strong", "rr-test-date", formatTime(shipment.loaded_at)));
    summaryMain.appendChild(element("strong", "rr-test-number", shipment.ttn_number || shipment.registry_number));
    var snapshot = shipment.document_snapshot || {};
    var vehicle = snapshot.vehicle || {};
    var vehicleLabel = [vehicle.full_plate, vehicle.model].filter(Boolean).join(" · ") || "Машина не указана";
    summaryMain.appendChild(element(
      "span",
      "rr-test-summary-meta",
      vehicleLabel + " · " + (shipment.items || []).length + " поз."
    ));
    summary.appendChild(summaryMain);
    var summaryStates = element("span", "rr-test-summary-states");
    if (isNew) summaryStates.appendChild(element("span", "rr-test-new-badge", "Новая"));
    var status = testStatusInfo(shipment.status, shipment.accountant_reviewed_at);
    summaryStates.appendChild(element("span", "rr-test-status rr-test-status--" + status[1], status[0]));
    summary.appendChild(summaryStates);
    summary.addEventListener("click", function () {
      openedTestShipmentId = isOpen ? null : shipment.id;
      renderTestViews();
    });
    var heading = element("div", "rr-test-heading");
    var title = element("div");
    title.appendChild(element("p", "rr-test-meta", "Погрузка: " + formatTime(shipment.loaded_at)));
    title.appendChild(element("h3", "", shipment.ttn_number || shipment.registry_number));
    if (shipment.ttn_number) title.appendChild(element("p", "rr-test-meta", "Внутренняя запись: " + shipment.registry_number));
    title.appendChild(element("p", "rr-test-meta", "Ревизия №" + (shipment.revision_number || 1)));
    title.appendChild(element("p", "rr-test-meta", "Масса: " + Number(shipment.total_weight_kg || 0).toLocaleString("ru-RU") + " кг"));
    heading.appendChild(title);
    heading.appendChild(element("span", "rr-test-status rr-test-status--" + status[1], status[0]));
    card.appendChild(summary);
    if (isOpen) {
      var details = element("div", "rr-test-details");
      details.id = detailsId;
      details.setAttribute("role", "region");
      details.setAttribute("aria-labelledby", summary.id);
      details.appendChild(heading);
      testSnapshot(shipment, details);
      testItems(shipment, details);
      var documents = shipment.documents || [];
      if (documents.length) {
        var documentList = element("div", "rr-test-documents");
        documents.forEach(function (document) {
          documentList.appendChild(renderTestDocument(shipment, document));
        });
        details.appendChild(documentList);
      }
      testActions(shipment, details, draftValues, view);
      testHistory(shipment, details);
      card.appendChild(details);
    }
    return card;
  }

  function testShipmentOrder(left, right) {
    return Number(right.loaded_at || 0) - Number(left.loaded_at || 0) || Number(right.id || 0) - Number(left.id || 0);
  }

  function shipmentMatchesRegistryFilters(shipment) {
    var date = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Irkutsk", year: "numeric", month: "2-digit", day: "2-digit"
    }).format(new Date(Number(shipment.loaded_at || 0) * 1000));
    if (registryDateFrom.value && date < registryDateFrom.value) return false;
    if (registryDateTo.value && date > registryDateTo.value) return false;
    var query = registrySearch.value.trim().toLowerCase();
    if (!query) return true;
    var vehicle = ((shipment.document_snapshot || {}).vehicle || {});
    return [shipment.ttn_number, shipment.registry_number, vehicle.plate_tail, vehicle.full_plate]
      .join(" ").toLowerCase().indexOf(query) >= 0;
  }

  function exportRegistry() {
    var query = new URLSearchParams();
    if (registryDateFrom.value) query.set("date_from", registryDateFrom.value);
    if (registryDateTo.value) query.set("date_to", registryDateTo.value);
    if (registrySearch.value.trim()) query.set("search", registrySearch.value.trim());
    window.location.assign(API + "/test/registry/export?" + query.toString());
  }

  function renderTestRegistry(draft) {
    var allShipments = (testRegistryData && testRegistryData.shipments) || [];
    var shipments = allShipments.filter(shipmentMatchesRegistryFilters);
    testEmptyState.hidden = shipments.length > 0;
    testEmptyState.textContent = allShipments.length
      ? "По заданным фильтрам погрузок не найдено."
      : "Погрузок в реестре пока нет.";
    clear(testRegistryList);
    shipments.slice().sort(testShipmentOrder).forEach(function (shipment, index) {
      testRegistryList.appendChild(renderTestShipment(shipment, index, draft && draft.values, "registry"));
    });
    restoreDraft(testRegistryList, draft);
  }

  function renderWorkQueue(draft) {
    var shipments = ((testRegistryData && testRegistryData.shipments) || []).filter(function (shipment) {
      return shipment.status === "awaiting_accountant_review" || shipment.status === "awaiting_er_sent" || (
        shipment.status === "documents_handed_to_driver" && !shipment.accountant_reviewed_at
      );
    });
    workEmptyState.hidden = shipments.length > 0;
    clear(workRegistryList);
    shipments.slice().sort(testShipmentOrder).forEach(function (shipment, index) {
      workRegistryList.appendChild(renderTestShipment(shipment, index, draft && draft.values, "work"));
    });
    restoreDraft(workRegistryList, draft);
  }

  function renderTestViews(drafts) {
    renderTestRegistry(drafts && drafts.registry);
    renderWorkQueue(drafts && drafts.work);
  }

  function confirmationSourceLabel(source) {
    var labels = {
      counterparty_card: "Карточка контрагента",
      legacy_tn: "Старая ТТН",
      kontur: "Реквизиты из Контура"
    };
    return labels[source] || "Источник не указан";
  }

  function renderSamples() {
    clear(samplesList);
    samples.forEach(function (sample) {
      var card = element("article", "rr-sample");
      var description = element("div");
      description.appendChild(element("h4", "", sample.title));
      description.appendChild(element(
        "p", "rr-help",
        sample.available ? "Доступен через защищённый кабинет." : "Образец ЭР ещё не загружен. Ссылка появится после загрузки подтверждённого файла."
      ));
      card.appendChild(description);
      if (sample.available) {
        var actions = element("div", "rr-sample-actions");
        var view = element("a", "", "Открыть");
        view.href = sample.view_url;
        view.target = "_blank";
        view.rel = "noopener";
        var download = element("a", "", "Скачать");
        download.href = sample.download_url;
        actions.appendChild(view);
        actions.appendChild(download);
        card.appendChild(actions);
      } else {
        card.appendChild(element("span", "rr-sample-unavailable", "Пока недоступен"));
      }
      samplesList.appendChild(card);
    });
  }

  function showCarrierForm(carrier) {
    if (!currentAccountant || !currentAccountant.can_manage_carriers) return;
    editingCarrierId = carrier ? carrier.id : null;
    carrierFormTitle.textContent = carrier ? "Редактирование перевозчика" : "Новый перевозчик";
    carrierFormError.textContent = "";
    carrierForm.elements.name.value = carrier ? carrier.name || "" : "";
    carrierForm.elements.inn.value = carrier ? carrier.inn || "" : "";
    carrierForm.elements.kpp.value = carrier ? carrier.kpp || "" : "";
    carrierForm.elements.legal_address.value = carrier ? carrier.legal_address || "" : "";
    carrierForm.elements.confirmation_source.value = carrier ? carrier.confirmation_source || "" : "";
    carrierForm.elements.confirmation_reference.value = carrier ? carrier.confirmation_reference || "" : "";
    carrierForm.elements.ogrn.value = carrier ? carrier.ogrn || "" : "";
    carrierForm.elements.contact_name.value = carrier ? carrier.contact_name || "" : "";
    carrierForm.elements.contact_phone.value = carrier ? carrier.contact_phone || "" : "";
    carrierForm.elements.note.value = carrier ? carrier.note || "" : "";
    carrierForm.elements.active.checked = carrier ? Boolean(carrier.active) : true;
    carrierForm.hidden = false;
    carrierForm.elements.name.focus();
  }

  function hideCarrierForm() {
    editingCarrierId = null;
    carrierForm.hidden = true;
    carrierFormError.textContent = "";
  }

  function renderCarriers() {
    var canManage = Boolean(currentAccountant && currentAccountant.can_manage_carriers);
    newCarrierButton.hidden = !canManage;
    directoryAccessNotice.hidden = canManage;
    if (!canManage) hideCarrierForm();
    carrierCount.textContent = carriers.length
      ? "Карточек в справочнике: " + carriers.length
      : "Карточек в справочнике пока нет";
    carrierEmptyState.hidden = carriers.length > 0;
    clear(carrierList);
    carriers.forEach(function (carrier) {
      var card = element("article", "rr-carrier" + (carrier.active ? "" : " rr-carrier--inactive"));
      var heading = element("div", "rr-carrier-heading");
      var title = element("div");
      title.appendChild(element("h4", "", carrier.name));
      title.appendChild(element("p", "rr-carrier-meta", "ИНН " + carrier.inn + " · КПП " + carrier.kpp));
      heading.appendChild(title);
      heading.appendChild(element(
        "span",
        "rr-carrier-status" + (carrier.active ? "" : " rr-carrier-status--inactive"),
        carrier.active ? "Активна" : "Отключена"
      ));
      card.appendChild(heading);
      card.appendChild(element("p", "rr-carrier-detail", "Юридический адрес: " + carrier.legal_address));
      card.appendChild(element(
        "p",
        "rr-carrier-detail",
        "Подтверждено: " + confirmationSourceLabel(carrier.confirmation_source) + " · " + carrier.confirmation_reference
      ));
      if (carrier.ogrn) card.appendChild(element("p", "rr-carrier-detail", "ОГРН: " + carrier.ogrn));
      if (carrier.contact_name || carrier.contact_phone) {
        card.appendChild(element("p", "rr-carrier-detail", "Контакт: " + [carrier.contact_name, carrier.contact_phone].filter(Boolean).join(" · ")));
      }
      if (carrier.note) card.appendChild(element("p", "rr-carrier-detail", "Примечание: " + carrier.note));
      if (canManage) {
        var edit = element("button", "rr-carrier-edit", "Изменить");
        edit.type = "button";
        edit.addEventListener("click", function () { showCarrierForm(carrier); });
        card.appendChild(edit);
      }
      carrierList.appendChild(card);
    });
    renderFleet();
  }

  function setActiveTab(tab) {
    workPanel.hidden = tab !== "work";
    registryPanel.hidden = tab !== "registry";
    fleetPanel.hidden = tab !== "fleet";
    directoryPanel.hidden = tab !== "directory";
    workTab.setAttribute("aria-selected", String(tab === "work"));
    registryTab.setAttribute("aria-selected", String(tab === "registry"));
    fleetTab.setAttribute("aria-selected", String(tab === "fleet"));
    directoryTab.setAttribute("aria-selected", String(tab === "directory"));
  }

  function showToast(message) {
    toast.textContent = message;
    toast.hidden = false;
    setTimeout(function () { toast.hidden = true; }, 3200);
  }

  function showConfigError(message) {
    configNotice.textContent = message;
    configNotice.hidden = false;
  }

  function captureDraft(node) {
    var draft = { values: {}, activeId: "", selectionStart: null, selectionEnd: null };
    Array.prototype.forEach.call(node.querySelectorAll("input, textarea, select"), function (field) {
      if (field.id) draft.values[field.id] = field.value;
    });
    var active = document.activeElement;
    if (active && node.contains(active) && active.id) {
      draft.activeId = active.id;
      if (typeof active.selectionStart === "number") draft.selectionStart = active.selectionStart;
      if (typeof active.selectionEnd === "number") draft.selectionEnd = active.selectionEnd;
    }
    return draft;
  }

  function restoreDraft(node, draft) {
    if (!draft || !draft.activeId) return;
    var active = document.getElementById(draft.activeId);
    if (!active || !node.contains(active)) return;
    active.focus();
    if (typeof active.setSelectionRange === "function" && draft.selectionStart !== null) {
      active.setSelectionRange(draft.selectionStart, draft.selectionEnd);
    }
  }

  function hasDirectoryDraft() {
    return !carrierForm.hidden || !fleetBindingForm.hidden;
  }

  function loadRegistry() {
    if (registryLoadPromise) return registryLoadPromise;
    registryLoadPromise = request("/registry")
      .then(function (body) {
        var draft = captureDraft(registryList);
        registryData = body;
        renderArchiveStatus(body.archive_status);
        renderRegistry(draft);
      })
      .catch(function (error) {
        showConfigError(error.message || "Не удалось загрузить реестр");
        registryCount.textContent = "Реестр недоступен";
      })
      .finally(function () { registryLoadPromise = null; });
    return registryLoadPromise;
  }

  function loadDirectory() {
    if (hasDirectoryDraft()) return Promise.resolve();
    if (directoryLoadPromise) return directoryLoadPromise;
    directoryLoadPromise = Promise.all([request("/carriers"), request("/samples"), request("/document-fleet")])
      .then(function (results) {
        carriers = results[0].carriers || [];
        samples = results[1].samples || [];
        fleetVehicles = results[2].vehicles || [];
        renderSamples();
        renderCarriers();
      })
      .catch(function (error) {
        showConfigError(error.message || "Не удалось загрузить справочники");
        carrierCount.textContent = "Справочник недоступен";
      })
      .finally(function () { directoryLoadPromise = null; });
    return directoryLoadPromise;
  }

  function loadTestRegistry() {
    if (testRegistryLoadPromise) return testRegistryLoadPromise;
    testRegistryLoadPromise = request("/test/registry")
      .then(function (body) {
        var drafts = {
          registry: captureDraft(testRegistryList),
          work: captureDraft(workRegistryList)
        };
        testRegistryData = body;
        renderArchiveStatus(body.archive_status);
        renderTestViews(drafts);
      })
      .catch(function (error) {
        showConfigError(error.message || "Не удалось загрузить тестовые погрузки");
        testEmptyState.hidden = false;
        testEmptyState.textContent = "Тестовые погрузки недоступны.";
      })
      .finally(function () { testRegistryLoadPromise = null; });
    return testRegistryLoadPromise;
  }

  function loadAccountantAvailability() {
    return request("/accountant/availability")
      .then(function (body) {
        if (body.availability) accountantAvailability.value = body.availability.availability || "schedule";
      })
      .catch(function (error) { showConfigError(error.message || "Не удалось загрузить доступность"); });
  }

  function refreshData() {
    if (busy || document.hidden) return Promise.resolve();
    return Promise.all([loadRegistry(), loadTestRegistry(), loadDirectory(), loadAccountantAvailability()]);
  }

  function scheduleAutoRefresh() {
    clearTimeout(refreshTimer);
    if (!autoRefreshStarted || document.hidden) return;
    refreshTimer = setTimeout(function () {
      refreshData().finally(scheduleAutoRefresh);
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
      refreshData().finally(scheduleAutoRefresh);
    });
    scheduleAutoRefresh();
  }

  function currentBinding(vehicle) {
    return vehicle && vehicle.document_binding ? vehicle.document_binding : null;
  }

  function showFleetBindingForm(vehicle, history) {
    if (!currentAccountant || !currentAccountant.can_manage_carriers || !vehicle) return;
    bindingVehicle = vehicle;
    fleetBindingHistory[vehicle.plate_tail] = history || fleetBindingHistory[vehicle.plate_tail] || [];
    var binding = currentBinding(vehicle);
    fleetBindingVehicle.textContent = [vehicle.full_plate || "Хвост " + vehicle.plate_tail, vehicle.model].filter(Boolean).join(" · ") + ". Физические поля доступны только для чтения.";
    clear(bindingCarrierId);
    var placeholder = element("option", "", "Выберите активного перевозчика");
    placeholder.value = "";
    bindingCarrierId.appendChild(placeholder);
    carriers.filter(function (carrier) { return carrier.active; }).forEach(function (carrier) {
      var option = element("option", "", carrier.name + " · ИНН " + carrier.inn);
      option.value = String(carrier.id);
      option.selected = Boolean(binding && Number(binding.carrier_id) === Number(carrier.id));
      bindingCarrierId.appendChild(option);
    });
    bindingDriverName.value = binding ? binding.driver_full_name || "" : vehicle.source_driver_name || "";
    bindingDriverLicense.value = binding ? binding.driver_license_number || "" : "";
    bindingNote.value = "";
    fleetBindingFormError.textContent = "";
    fleetBindingForm.hidden = false;
    bindingCarrierId.focus();
  }

  function hideFleetBindingForm() {
    bindingVehicle = null;
    fleetBindingForm.hidden = true;
    fleetBindingFormError.textContent = "";
  }

  function loadFleetVehicle(vehicle) {
    return request("/document-fleet/" + encodeURIComponent(vehicle.plate_tail))
      .then(function (body) {
        showFleetBindingForm(body.vehicle, body.binding_history || []);
      })
      .catch(function (error) { showToast(error.message || "Не удалось открыть машину"); });
  }

  function fleetHistory(vehicle, parent) {
    var history = fleetBindingHistory[vehicle.plate_tail];
    if (!history || !history.length) return;
    var list = element("ul", "rr-fleet-history");
    history.slice(0, 5).forEach(function (binding, index) {
      var item = element("li");
      item.appendChild(element("time", "", formatTime(binding.checked_at)));
      item.appendChild(element(
        "span", "",
        (index === 0 ? "Текущая: " : "Предыдущая: ") + (binding.driver_full_name || "—") + " · " + ((binding.carrier_snapshot || {}).name || "—")
      ));
      list.appendChild(item);
    });
    parent.appendChild(list);
  }

  function renderFleet() {
    var canManage = Boolean(currentAccountant && currentAccountant.can_manage_carriers);
    importFleetButton.hidden = !canManage;
    fleetAccessNotice.hidden = canManage;
    if (!canManage) hideFleetBindingForm();
    fleetEmptyState.hidden = fleetVehicles.length > 0;
    clear(fleetList);
    fleetVehicles.forEach(function (vehicle) {
      var card = element("article", "rr-fleet" + (vehicle.source_active ? "" : " rr-carrier--inactive"));
      var heading = element("div", "rr-fleet-heading");
      var title = element("div");
      title.appendChild(element("h4", "", vehicle.full_plate || "Хвост " + vehicle.plate_tail));
      title.appendChild(element("p", "rr-fleet-meta", "Хвост: " + vehicle.plate_tail + (vehicle.model ? " · " + vehicle.model : "")));
      title.appendChild(element("p", "rr-fleet-meta", "Источник парка: " + (vehicle.source_driver_name || "Водитель не указан")));
      heading.appendChild(title);
      heading.appendChild(element(
        "span", "rr-fleet-status" + (vehicle.source_active ? "" : " rr-fleet-status--inactive"),
        vehicle.source_active ? "Активна" : "Отключена"
      ));
      card.appendChild(heading);
      var binding = currentBinding(vehicle);
      if (binding) {
        var details = element("div", "rr-fleet-binding");
        var carrier = binding.carrier_snapshot || {};
        details.appendChild(element("p", "rr-fleet-detail", "Подтверждённая связь: " + (binding.driver_full_name || "—")));
        details.appendChild(element("p", "rr-fleet-detail", "Удостоверение: " + (binding.driver_license_number || "—")));
        details.appendChild(element("p", "rr-fleet-detail", "Перевозчик: " + (carrier.name || "—") + (carrier.inn ? " · ИНН " + carrier.inn : "")));
        details.appendChild(element("p", "rr-fleet-detail", "Проверено: " + formatTime(binding.checked_at) + (binding.confirmed_by ? " · " + binding.confirmed_by : "")));
        card.appendChild(details);
      } else {
        card.appendChild(element("p", "rr-fleet-detail", "Документная связь ещё не подтверждена. Эта машина не будет доступна диспетчеру тестовой погрузки."));
      }
      fleetHistory(vehicle, card);
      if (canManage && vehicle.source_active) {
        var button = element("button", "rr-carrier-edit", binding ? "Подтвердить новую ревизию" : "Подтвердить связь");
        button.type = "button";
        button.addEventListener("click", function () { loadFleetVehicle(vehicle); });
        card.appendChild(button);
      }
      fleetList.appendChild(card);
    });
  }

  function importFleetSnapshot() {
    if (!currentAccountant || !currentAccountant.can_manage_carriers || busy) return;
    busy = true;
    importFleetButton.disabled = true;
    request("/document-fleet/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(function (body) {
        fleetVehicles = body.vehicles || [];
        fleetBindingHistory = {};
        renderFleet();
        showToast("Снимок физического парка обновлён. Данные источника не изменялись.");
      })
      .catch(function (error) { showToast(error.message || "Не удалось обновить снимок машин"); })
      .finally(function () { busy = false; importFleetButton.disabled = false; });
  }

  function saveFleetBinding(event) {
    event.preventDefault();
    if (!bindingVehicle || !currentAccountant || !currentAccountant.can_manage_carriers || busy) return;
    if (!fleetBindingForm.checkValidity()) {
      fleetBindingForm.reportValidity();
      return;
    }
    busy = true;
    saveFleetBindingButton.disabled = true;
    fleetBindingFormError.textContent = "";
    request("/document-fleet/" + encodeURIComponent(bindingVehicle.plate_tail) + "/binding", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        carrier_id: Number(bindingCarrierId.value),
        driver_full_name: bindingDriverName.value.trim(),
        driver_license_number: bindingDriverLicense.value.trim(),
        note: bindingNote.value.trim()
      })
    })
      .then(function () {
        hideFleetBindingForm();
        return loadDirectory();
      })
      .then(function () { showToast("Документная связь подтверждена новой неизменяемой ревизией."); })
      .catch(function (error) { fleetBindingFormError.textContent = error.message || "Не удалось подтвердить связь"; })
      .finally(function () { busy = false; saveFleetBindingButton.disabled = false; });
  }

  function applyErAction(shipmentId, action, reference, button) {
    if (busy) return;
    busy = true;
    button.disabled = true;
    request("/shipments/" + encodeURIComponent(shipmentId) + "/er-" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ external_reference: reference.trim() })
    })
      .then(function (body) {
        var shipments = registryData ? registryData.shipments : [];
        registryData.shipments = shipments.map(function (shipment) {
          return shipment.id === body.shipment.id ? body.shipment : shipment;
        });
        openedShipmentId = body.shipment.id;
        renderRegistry();
        showToast(body.message || "Готово");
      })
      .catch(function (error) { showToast(error.message || "Не удалось сохранить отметку"); })
      .finally(function () { busy = false; button.disabled = false; });
  }

  function saveCarrier(event) {
    event.preventDefault();
    if (!currentAccountant || !currentAccountant.can_manage_carriers || busy) return;
    var form = carrierForm.elements;
    var payload = {
      name: form.name.value.trim(),
      inn: form.inn.value.trim(),
      kpp: form.kpp.value.trim(),
      legal_address: form.legal_address.value.trim(),
      confirmation_source: form.confirmation_source.value,
      confirmation_reference: form.confirmation_reference.value.trim(),
      ogrn: form.ogrn.value.trim(),
      contact_name: form.contact_name.value.trim(),
      contact_phone: form.contact_phone.value.trim(),
      note: form.note.value.trim(),
      active: form.active.checked
    };
    carrierFormError.textContent = "";
    if (!carrierForm.checkValidity()) {
      carrierForm.reportValidity();
      return;
    }
    busy = true;
    saveCarrierButton.disabled = true;
    request(editingCarrierId ? "/carriers/" + encodeURIComponent(editingCarrierId) : "/carriers", {
      method: editingCarrierId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function () {
        hideCarrierForm();
        return loadDirectory();
      })
      .then(function () { showToast("Подтверждённые данные перевозчика сохранены"); })
      .catch(function (error) {
        carrierFormError.textContent = error.message || "Не удалось сохранить карточку";
      })
      .finally(function () {
        busy = false;
        saveCarrierButton.disabled = false;
      });
  }

  function loadUser() {
    return request("/auth/me")
      .then(function (body) {
        currentAccountant = body;
        currentUser.textContent = body.user || "";
        logoutButton.hidden = false;
        renderCarriers();
      })
      .catch(function () {});
  }

  logoutButton.addEventListener("click", function () {
    request("/auth/logout", { method: "POST" })
      .finally(function () { location.replace("/rumex-accountant-login.html"); });
  });
  workTab.addEventListener("click", function () { setActiveTab("work"); });
  registryTab.addEventListener("click", function () { setActiveTab("registry"); });
  fleetTab.addEventListener("click", function () { setActiveTab("fleet"); });
  directoryTab.addEventListener("click", function () { setActiveTab("directory"); });
  newCarrierButton.addEventListener("click", function () { showCarrierForm(null); });
  cancelCarrierButton.addEventListener("click", hideCarrierForm);
  cancelCarrierButtonBottom.addEventListener("click", hideCarrierForm);
  carrierForm.addEventListener("submit", saveCarrier);
  importFleetButton.addEventListener("click", importFleetSnapshot);
  cancelFleetBindingButton.addEventListener("click", hideFleetBindingForm);
  cancelFleetBindingButtonBottom.addEventListener("click", hideFleetBindingForm);
  fleetBindingForm.addEventListener("submit", saveFleetBinding);
  accountantAvailability.addEventListener("change", function () {
    if (!currentAccountant || busy) return;
    busy = true;
    accountantAvailability.disabled = true;
    request("/accountant/availability", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ availability: accountantAvailability.value })
    })
      .then(function (body) {
        accountantAvailability.value = body.availability.availability;
        showToast("Режим доступности сохранён.");
      })
      .catch(function (error) { showToast(error.message || "Не удалось сохранить доступность"); })
      .finally(function () {
        busy = false;
        accountantAvailability.disabled = false;
      });
  });
  [registryDateFrom, registryDateTo, registrySearch].forEach(function (filter) {
    filter.addEventListener("input", function () {
      renderRegistry();
      renderTestRegistry();
    });
  });
  registryFiltersReset.addEventListener("click", function () {
    registryDateFrom.value = "";
    registryDateTo.value = "";
    registrySearch.value = "";
    renderRegistry();
    renderTestRegistry();
  });
  registryExportButton.addEventListener("click", exportRegistry);
  fleetPanel.appendChild(fleetSection);

  loadUser().then(function () {
    return refreshData();
  }).then(function () {
    if (currentAccountant) startAutoRefresh();
  });
})();
