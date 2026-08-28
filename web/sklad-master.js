(function () {
  "use strict";

  var API = "/api/sklad-master";
  var webApp = window.WebApp || null;
  var initData = "";
  var data = null;
  var movements = [];
  var requestDetails = {};
  var state = {
    tab: "receipt",
    stockTab: "materials",
    stockFilter: "all",
    siteId: "",
    supplierId: "",
    receiptMaterialId: "",
    receiptCart: [],
    issueMaterialId: "",
    requestMaterialId: "",
    requestUrgency: "plan",
    requestCart: [],
    showSupplierCreate: false,
    showMaterialCreate: false,
    showRequestCreate: false
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderReceiptCart() {
    $("receiptCart").innerHTML = state.receiptCart.map(function (item, index) {
      var material = materialById(item.material_id) || {};
      return '<div class="sm-cart-row"><span><b>' + esc(material.name || "Материал") +
        "</b> · " + esc(formatQty(item.quantity)) + " " + esc(material.unit || "") +
        '</span><button type="button" data-remove-receipt="' + index +
        '" aria-label="Удалить позицию">×</button></div>';
    }).join("") ||
      '<p class="sm-empty sm-empty--compact">Добавьте материалы в приход.</p>';
  }

  function formatQty(value) {
    var number = Number(value || 0);
    if (!isFinite(number)) return "0";
    return Math.abs(number - Math.round(number)) < 0.001
      ? String(Math.round(number))
      : number.toFixed(3).replace(/\.?0+$/, "");
  }

  function positive(value) {
    var number = Number(String(value).replace(",", "."));
    return isFinite(number) && number > 0 ? number : null;
  }

  function key() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "sm-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) +
      "-" + Math.random().toString(36).slice(2);
  }

  function readInitDataFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var value = params.get("initData") || params.get("InitData") || params.get("tgWebAppData");
    if (value) return value;
    if (window.location.hash.length > 1) {
      params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      return params.get("initData") || params.get("InitData") || params.get("tgWebAppData") || "";
    }
    return "";
  }

  function syncInitData() {
    if (webApp && webApp.initData) initData = webApp.initData;
    if (!initData) initData = readInitDataFromUrl();
    return Boolean(initData);
  }

  function headers() {
    var result = { "Content-Type": "application/json" };
    if (initData) result["X-Max-Init-Data"] = initData;
    return result;
  }

  function api(path, options) {
    options = options || {};
    options.headers = headers();
    return fetch(API + path, options).then(function (response) {
      return response.text().then(function (text) {
        var body = {};
        try { body = text ? JSON.parse(text) : {}; } catch (ignore) { body = {}; }
        if (!response.ok) throw new Error(body.error || "Ошибка сервера");
        return body;
      });
    });
  }

  function post(path, payload, method) {
    payload.idempotency_key = payload.idempotency_key || key();
    return api(path, {
      method: method || "POST",
      body: JSON.stringify(payload)
    });
  }

  var toastTimer = null;
  function toast(message) {
    var element = $("toast");
    clearTimeout(toastTimer);
    element.textContent = message;
    element.hidden = false;
    element.classList.add("is-visible");
    toastTimer = setTimeout(function () {
      element.classList.remove("is-visible");
      setTimeout(function () { element.hidden = true; }, 180);
    }, 2500);
  }

  function fail(error, fallback) {
    toast((error && error.message) || fallback || "Не удалось выполнить действие");
  }

  function allowed(action) {
    var source = (data && (data.capabilities || (data.access && data.access.capabilities))) || {};
    if (source[action]) return true;
    var actions = (data && (data.allowed_actions || (data.access && data.access.allowed_actions))) || [];
    return actions.indexOf(action) !== -1;
  }

  function materialById(id) {
    return ((data && data.materials) || []).filter(function (item) {
      return String(item.id) === String(id);
    })[0] || null;
  }

  function siteById(id) {
    return ((data && data.sites) || []).filter(function (item) {
      return String(item.id) === String(id);
    })[0] || null;
  }

  function optionList(items, selected, placeholder, formatter) {
    var html = placeholder ? '<option value="">' + esc(placeholder) + "</option>" : "";
    return html + items.map(function (item) {
      return '<option value="' + esc(item.id) + '"' +
        (String(item.id) === String(selected) ? " selected" : "") + ">" +
        esc(formatter ? formatter(item) : item.name) + "</option>";
    }).join("");
  }

  function setBusy(button, busy) {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.textContent;
      button.textContent = "Сохраняем…";
    } else if (button.dataset.label) {
      button.textContent = button.dataset.label;
    }
    button.disabled = busy;
  }

  function loadBootstrap() {
    var path = "/bootstrap" + (state.siteId ? "?site_id=" + encodeURIComponent(state.siteId) : "");
    return api(path).then(function (body) {
      data = body || {};
      if (!state.siteId && data.selected_site_id) state.siteId = String(data.selected_site_id);
      render();
    });
  }

  function renderUser() {
    if (!data.user) return;
    $("currentUser").textContent = data.user.name || "Пользователь";
    $("currentUser").hidden = false;
  }

  function renderSites() {
    var sites = data.sites || [];
    $("siteList").innerHTML = sites.map(function (site) {
      return '<button type="button" class="sm-chip' +
        (String(site.id) === String(state.siteId) ? " is-active" : "") +
        '" data-site-id="' + esc(site.id) + '">' + esc(site.name) + "</button>";
    }).join("") || '<p class="sm-empty">Нет доступных площадок.</p>';
  }

  function renderSummary() {
    var summary = data.summary || {};
    $("summaryBox").innerHTML =
      '<button type="button" class="sm-stat sm-stat-btn" data-summary-filter="all"><b>' +
      esc(summary.material_count || 0) + '</b><span>материалов</span></button>' +
      '<button type="button" class="sm-stat sm-stat-btn" data-summary-filter="critical"><b>' +
      esc(summary.critical_count || 0) + '</b><span>критично</span></button>' +
      '<button type="button" class="sm-stat sm-stat-btn" data-summary-filter="warning"><b>' +
      esc(summary.warning_count || 0) + '</b><span>внимание</span></button>';
  }

  function renderRoleAware() {
    document.querySelector('[data-tab="receipt"]').hidden = !allowed("receipt");
    document.querySelector('[data-tab="issue"]').hidden = !allowed("issue");
    document.querySelector('[data-stock-tab="transfer"]').hidden = !allowed("transfer");
    document.querySelector('[data-stock-tab="settings"]').hidden =
      !(allowed("settings_manage") || allowed("inventory_adjustment"));
    $("toggleRequestCreateBtn").hidden = !allowed("request_create");
    $("showSupplierCreateBtn").hidden = !(allowed("receipt") || allowed("settings_manage"));
    $("showMaterialCreateBtn").hidden = !(allowed("receipt") || allowed("settings_manage"));
    $("materialAdminSettings").hidden = !allowed("settings_manage");
    $("minimumSettings").hidden = !allowed("settings_manage");
    $("adjustmentForm").hidden = !allowed("inventory_adjustment");
    if ((state.tab === "receipt" && !allowed("receipt")) ||
        (state.tab === "issue" && !allowed("issue"))) state.tab = "stock";
    if (state.stockTab === "transfer" && !allowed("transfer")) state.stockTab = "materials";
    if (state.stockTab === "settings" &&
        !(allowed("settings_manage") || allowed("inventory_adjustment"))) state.stockTab = "materials";
  }

  function renderTabs() {
    Array.prototype.forEach.call(document.querySelectorAll("#tabButtons [data-tab]"), function (tab) {
      tab.classList.toggle("is-active", tab.dataset.tab === state.tab);
    });
    ["receipt", "issue", "stock", "requests"].forEach(function (name) {
      $(name + "Panel").hidden = state.tab !== name;
    });
  }

  function renderSelectors() {
    var materials = data.materials || [];
    var materialOptions = function (selected, placeholder) {
      return optionList(materials, selected, placeholder, function (item) {
        return item.name + (item.unit ? " · " + item.unit : "");
      });
    };
    $("supplierSelect").innerHTML = optionList(
      data.suppliers || [], state.supplierId, "Выберите поставщика"
    );
    $("receiptMaterialSelect").innerHTML = materialOptions(state.receiptMaterialId, "Выберите материал");
    $("issueMaterialSelect").innerHTML = materialOptions(state.issueMaterialId, "Выберите материал");
    $("requestMaterialSelect").innerHTML = materialOptions(state.requestMaterialId, "Выберите материал");
    $("transferMaterial").innerHTML = materialOptions("", "Выберите материал");
    $("adjustmentMaterial").innerHTML = materialOptions("", "Выберите материал");
    var destinations = (data.sites || []).filter(function (site) {
      return String(site.id) !== String(state.siteId);
    });
    $("transferToSite").innerHTML = optionList(destinations, "", "Выберите площадку");
  }

  function renderCreateBoxes() {
    $("supplierCreateBox").hidden = !state.showSupplierCreate;
    $("materialCreateBox").hidden = !state.showMaterialCreate;
    $("showSupplierCreateBtn").textContent = state.showSupplierCreate ? "×" : "+";
    $("showMaterialCreateBtn").textContent = state.showMaterialCreate ? "×" : "+";
  }

  function renderStock() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-tab]"), function (tab) {
      tab.classList.toggle("is-active", tab.dataset.stockTab === state.stockTab);
    });
    $("stockMaterialsBox").hidden = state.stockTab !== "materials";
    $("movementsBox").hidden = state.stockTab !== "movements";
    $("transferForm").hidden = state.stockTab !== "transfer";
    $("stockSettingsBox").hidden = state.stockTab !== "settings";
    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-filter]"), function (button) {
      button.classList.toggle("is-active", button.dataset.stockFilter === state.stockFilter);
    });
    var items = data.stock || [];
    if (state.stockFilter !== "all") {
      items = items.filter(function (item) { return item.status === state.stockFilter; });
    }
    $("stockList").innerHTML = items.map(function (item) {
      return '<article class="sm-row sm-row--' + esc(item.status || "ok") + '">' +
        '<div><b>' + esc(item.name) + '</b><span>Остаток: ' + esc(formatQty(item.balance)) +
        " " + esc(item.unit || "") + '</span></div><div class="sm-align-right"><span>Минимум</span><b>' +
        esc(formatQty(item.min_level)) + "</b></div></article>";
    }).join("") || '<p class="sm-empty">Нет позиций в этой категории.</p>';
    renderMinimums();
    renderMaterialAdmin();
    renderMovements();
  }

  function renderMaterialAdmin() {
    var box = $("materialAdminList");
    if (!box || !allowed("settings_manage")) return;
    var items = data.admin_materials || data.materials || [];
    box.innerHTML = items.map(function (item) {
      return '<form class="sm-material-admin' + (item.active ? "" : " is-disabled") +
        '" data-admin-material="' + esc(item.id) + '">' +
        '<input class="sm-input" name="material_name" value="' + esc(item.name) +
        '" aria-label="Название материала">' +
        '<select class="sm-input sm-select" name="unit" aria-label="Единица измерения">' +
        ["шт", "кг", "м", "м3"].map(function (unit) {
          return '<option value="' + unit + '"' + (unit === item.unit ? " selected" : "") +
            ">" + unit + "</option>";
        }).join("") + '</select>' +
        '<label class="sm-switch"><input type="checkbox" name="active"' +
        (item.active ? " checked" : "") + '><span>Активен</span></label>' +
        '<button class="sm-mini-action" type="submit">Сохранить</button></form>';
    }).join("") || '<p class="sm-empty">Материалов пока нет.</p>';
  }

  function renderMinimums() {
    $("minimumList").innerHTML = (data.stock || []).map(function (item) {
      return '<form class="sm-min-row" data-minimum-material="' + esc(item.id) + '">' +
        '<label><b>' + esc(item.name) + '</b><span>' + esc(item.unit || "") + '</span></label>' +
        '<input class="sm-input" type="number" min="0" step="0.001" value="' +
        esc(formatQty(item.min_level)) + '" aria-label="Минимум для ' + esc(item.name) + '">' +
        '<button class="sm-mini-action" type="submit">OK</button></form>';
    }).join("") || '<p class="sm-empty">Нет материалов для настройки.</p>';
  }

  var movementLabels = {
    receipt: "Приход", issue: "Расход", transfer_in: "Перемещение: приход",
    transfer_out: "Перемещение: расход", adjustment: "Корректировка",
    inventory_adjustment: "Корректировка", reversal: "Сторно"
  };

  function dateLabel(value) {
    if (!value) return "";
    var date = new Date(Number(value) < 100000000000 ? Number(value) * 1000 : value);
    return isNaN(date.getTime()) ? "" : date.toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
    });
  }

  function renderMovements() {
    $("movementsList").innerHTML = movements.map(function (item) {
      var delta = Number(item.quantity_delta || 0);
      return '<article class="sm-row"><div><b>' + esc(item.material_name || "Материал") +
        '</b><span>' + esc(movementLabels[item.op_type] || "Операция со складом") +
        (item.note ? " · " + esc(item.note) : "") + '</span></div><div class="sm-align-right"><b class="' +
        (delta >= 0 ? "sm-positive" : "sm-negative") + '">' + (delta > 0 ? "+" : "") +
        esc(formatQty(delta)) + '</b><span>' + esc(dateLabel(item.created_at)) + "</span></div></article>";
    }).join("") || '<p class="sm-empty">Движений пока нет.</p>';
  }

  function renderRequestCart() {
    $("requestCreateForm").hidden = !state.showRequestCreate;
    $("toggleRequestCreateBtn").textContent = state.showRequestCreate ? "Скрыть" : "Новая";
    $("requestCart").innerHTML = state.requestCart.map(function (item, index) {
      var material = materialById(item.material_id) || {};
      return '<div class="sm-cart-row"><span><b>' + esc(material.name || "Материал") + "</b> · " +
        esc(formatQty(item.quantity)) + " " + esc(material.unit || "") +
        '</span><button type="button" data-remove-cart="' + index + '" aria-label="Удалить">×</button></div>';
    }).join("") || '<p class="sm-empty sm-empty--compact">Добавьте хотя бы одну позицию.</p>';
  }

  var actionLabels = {
    submit: "Отправить", cancel: "Отменить", accept: "Принять",
    reject: "Отклонить", mark_in_transit: "Отметить «В пути»",
    create_delivery: "Создать поставку", receive_delivery: "Принять поставку", close: "Закрыть"
  };
  var transitionStatuses = {
    submit: "submitted", cancel: "cancelled", accept: "accepted",
    reject: "rejected", mark_in_transit: "in_transit", close: "closed"
  };

  function requestItemsHtml(request) {
    return (request.items || []).map(function (item) {
      var progress = Number(item.received || 0) ? " · получено " + formatQty(item.received) : "";
      return '<li>' + esc(item.material_name || "Материал") + " — " +
        esc(formatQty(item.quantity || item.ordered)) + " " + esc(item.material_unit || "") +
        esc(progress) + "</li>";
    }).join("");
  }

  function requestActionsHtml(request) {
    return (request.allowed_actions || []).map(function (action) {
      if (!actionLabels[action]) return "";
      return '<button type="button" class="sm-mini-action" data-request-action="' + esc(action) +
        '" data-request-id="' + esc(request.id) + '">' + esc(actionLabels[action]) + "</button>";
    }).join("");
  }

  function requestExtraHtml(request) {
    var detail = requestDetails[String(request.id)];
    if (!detail) return "";
    if (detail.mode === "delivery") return deliveryFormHtml(detail.request);
    if (detail.mode === "receive") return receiveFormHtml(detail.request);
    return "";
  }

  function deliveryFormHtml(request) {
    var supplierOptions = optionList(data.suppliers || [], "", "Выберите поставщика");
    var rows = (request.items || []).map(function (item) {
      var available = Math.max(0, Number(item.remaining || item.quantity || 0) - Number(item.in_transit || 0));
      if (!available) return "";
      return '<label class="sm-quantity-row"><span>' + esc(item.material_name) + '<small>Доступно: ' +
        esc(formatQty(available)) + " " + esc(item.material_unit || "") +
        '</small></span><input class="sm-input" type="number" min="0" max="' +
        esc(available) + '" step="0.001" value="' + esc(formatQty(available)) +
        '" data-delivery-request-item="' + esc(item.id) + '"></label>';
    }).join("");
    return '<form class="sm-subpanel sm-inline-panel" data-delivery-form="' + esc(request.id) + '">' +
      '<h3>Новая поставка</h3><select class="sm-input sm-select" name="supplier_id">' +
      supplierOptions + '</select><div class="sm-section-gap">' + rows +
      '</div><input class="sm-input sm-section-gap" name="note" placeholder="Примечание">' +
      '<button class="sm-btn sm-btn--primary sm-submit" type="submit">Создать поставку</button></form>';
  }

  function receiveFormHtml(request) {
    var deliveries = request.deliveries || [];
    if (!deliveries.length) return '<p class="sm-empty">В заявке пока нет поставок.</p>';
    return deliveries.map(function (delivery) {
      var detailItems = delivery.items || [];
      var rows = detailItems.map(function (item) {
        var available = Math.max(0, Number(item.quantity || 0) - Number(item.received_quantity || 0));
        if (!available) return "";
        return '<label class="sm-quantity-row"><span>' + esc(item.material_name || "Материал") +
          '<small>Осталось: ' + esc(formatQty(available)) + " " + esc(item.material_unit || "") +
          '</small></span><input class="sm-input" type="number" min="0" max="' + esc(available) +
          '" step="0.001" value="' + esc(formatQty(available)) +
          '" data-receive-delivery-item="' + esc(item.id) + '"></label>';
      }).join("");
      var fallback = detailItems.length ? "" :
        '<p class="sm-note">Детализация недоступна — будет принят весь оставшийся объём.</p>';
      return '<form class="sm-subpanel sm-inline-panel" data-receive-form="' + esc(delivery.id) + '">' +
        '<h3>Поставка №' + esc(delivery.id) + '</h3><p class="sm-note">Получено ' +
        esc(formatQty(delivery.received_quantity)) + " из " + esc(formatQty(delivery.quantity)) +
        "</p>" + fallback + rows +
        '<input class="sm-input sm-section-gap" name="note" placeholder="Комментарий при приёмке">' +
        '<button class="sm-btn sm-btn--primary sm-submit" type="submit">Принять выбранное</button></form>';
    }).join("");
  }

  function renderRequests() {
    renderRequestCart();
    var requests = data.requests || [];
    $("requestsList").innerHTML = requests.map(function (request) {
      return '<article class="sm-request-card"><div class="sm-heading-row"><div><b>Заявка №' +
        esc(request.id) + '</b><span>' + esc(request.site_name || (siteById(request.site_id) || {}).name || "") +
        '</span></div><span class="sm-status">' + esc(request.status_label || "Статус обновляется") +
        '</span></div><ul class="sm-request-items">' + requestItemsHtml(request) +
        '</ul><div class="sm-request-meta">' +
        (request.urgency === "urgent" ? '<span class="sm-urgent">Срочно</span>' : "<span>Планово</span>") +
        (request.comment ? "<span>" + esc(request.comment) + "</span>" : "") +
        '</div><div class="sm-action-list">' + requestActionsHtml(request) +
        '</div>' + requestExtraHtml(request) + "</article>";
    }).join("") || '<p class="sm-empty">Заявок по этой площадке пока нет.</p>';
  }

  function render() {
    renderUser();
    renderRoleAware();
    renderSites();
    renderSummary();
    renderTabs();
    renderSelectors();
    renderCreateBoxes();
    renderReceiptCart();
    renderStock();
    renderRequests();
  }

  function reload(message) {
    if (message) toast(message);
    return loadBootstrap();
  }

  function addReceiptItem() {
    var quantity = positive($("receiptQty").value);
    if (!state.receiptMaterialId) return toast("Выберите материал");
    if (!quantity) return toast("Количество должно быть больше нуля");
    var existing = state.receiptCart.filter(function (item) {
      return String(item.material_id) === String(state.receiptMaterialId);
    })[0];
    if (existing) {
      existing.quantity = Math.round((existing.quantity + quantity) * 1000) / 1000;
    } else {
      state.receiptCart.push({
        material_id: Number(state.receiptMaterialId),
        quantity: quantity
      });
    }
    $("receiptQty").value = "";
    state.receiptMaterialId = "";
    renderSelectors();
    renderReceiptCart();
  }

  function saveReceipt() {
    if (!state.siteId) return toast("Выберите площадку");
    if (!state.supplierId) return toast("Выберите поставщика");
    if (!state.receiptCart.length) return toast("Добавьте хотя бы одну позицию");
    var button = $("saveReceiptBtn");
    setBusy(button, true);
    post("/receipts", {
      site_id: Number(state.siteId), supplier_id: Number(state.supplierId),
      items: state.receiptCart.map(function (item) {
        return {
          material_id: Number(item.material_id),
          quantity: Number(item.quantity)
        };
      }),
      note: $("receiptNote").value.trim()
    }).then(function () {
      $("receiptNote").value = "";
      state.receiptCart = [];
      return reload("Приход сохранён");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function saveIssue() {
    var quantity = positive($("issueQty").value);
    if (!state.siteId) return toast("Выберите площадку");
    if (!state.issueMaterialId) return toast("Выберите материал");
    if (!quantity) return toast("Количество должно быть больше нуля");
    var button = $("saveIssueBtn");
    setBusy(button, true);
    post("/issues", {
      site_id: Number(state.siteId), material_id: Number(state.issueMaterialId),
      quantity: quantity, note: $("issueNote").value.trim()
    }).then(function () {
      $("issueQty").value = "";
      $("issueNote").value = "";
      return reload("Расход сохранён");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function createSupplier() {
    var name = $("supplierName").value.trim();
    if (!name) return toast("Введите название поставщика");
    api("/suppliers", { method: "POST", body: JSON.stringify({ name: name }) })
      .then(function (body) {
        state.supplierId = String((body.supplier || {}).id || "");
        state.showSupplierCreate = false;
        $("supplierName").value = "";
        return reload("Поставщик добавлен");
      }).catch(fail);
  }

  function createMaterial() {
    var name = $("materialName").value.trim();
    var minimum = Number($("materialMin").value || 0);
    if (!name) return toast("Введите название материала");
    if (!isFinite(minimum) || minimum < 0) return toast("Минимум не может быть отрицательным");
    api("/materials", {
      method: "POST",
      body: JSON.stringify({ name: name, unit: $("materialUnit").value || "шт", min_level: minimum })
    }).then(function (body) {
      state.receiptMaterialId = String((body.material || {}).id || "");
      state.showMaterialCreate = false;
      $("materialName").value = "";
      $("materialMin").value = "";
      return reload("Материал добавлен");
    }).catch(fail);
  }

  function addRequestItem() {
    var quantity = positive($("requestQty").value);
    if (!state.requestMaterialId) return toast("Выберите материал");
    if (!quantity) return toast("Количество должно быть больше нуля");
    var existing = state.requestCart.filter(function (item) {
      return String(item.material_id) === String(state.requestMaterialId);
    })[0];
    if (existing) existing.quantity = Math.round((existing.quantity + quantity) * 1000) / 1000;
    else state.requestCart.push({ material_id: Number(state.requestMaterialId), quantity: quantity });
    $("requestQty").value = "";
    state.requestMaterialId = "";
    renderSelectors();
    renderRequestCart();
  }

  function saveRequest(form) {
    if (!state.siteId) return toast("Выберите площадку");
    if (!state.requestCart.length) return toast("Добавьте позиции в заявку");
    var button = form.querySelector('[type="submit"]');
    setBusy(button, true);
    post("/requests", {
      site_id: Number(state.siteId),
      items: state.requestCart.map(function (item) {
        return { material_id: Number(item.material_id), quantity: Number(item.quantity) };
      }),
      urgency: state.requestUrgency,
      comment: $("requestComment").value.trim()
    }).then(function () {
      state.requestCart = [];
      state.showRequestCreate = false;
      $("requestComment").value = "";
      return reload("Заявка отправлена");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function loadMovements() {
    if (!state.siteId) return Promise.resolve();
    return api("/movements?site_id=" + encodeURIComponent(state.siteId)).then(function (body) {
      movements = body.items || [];
      renderMovements();
    }).catch(fail);
  }

  function loadRequestDetail(id, mode) {
    return api("/requests/" + encodeURIComponent(id)).then(function (body) {
      requestDetails[String(id)] = { mode: mode, request: body.request || {} };
      renderRequests();
    }).catch(fail);
  }

  function transitionRequest(id, action, button) {
    var status = transitionStatuses[action];
    if (!status) return;
    var comment = "";
    if (action === "reject") comment = window.prompt("Причина отклонения:", "") || "";
    setBusy(button, true);
    post("/requests/" + encodeURIComponent(id), {
      status: status, comment: comment
    }, "PATCH").then(function () {
      delete requestDetails[String(id)];
      return reload("Статус заявки обновлён");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function createDelivery(form) {
    var items = Array.prototype.map.call(
      form.querySelectorAll("[data-delivery-request-item]"),
      function (input) {
        return { request_item_id: Number(input.dataset.deliveryRequestItem), quantity: positive(input.value) };
      }
    ).filter(function (item) { return item.quantity; });
    if (!form.elements.supplier_id.value) return toast("Выберите поставщика");
    if (!items.length) return toast("Укажите положительное количество");
    var id = form.dataset.deliveryForm;
    var button = form.querySelector('[type="submit"]');
    setBusy(button, true);
    post("/requests/" + encodeURIComponent(id) + "/deliveries", {
      supplier_id: Number(form.elements.supplier_id.value),
      items: items,
      note: form.elements.note.value.trim()
    }).then(function () {
      delete requestDetails[String(id)];
      return reload("Поставка создана");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function receiveDelivery(form) {
    var inputs = form.querySelectorAll("[data-receive-delivery-item]");
    var items = Array.prototype.map.call(inputs, function (input) {
      return { delivery_item_id: Number(input.dataset.receiveDeliveryItem), quantity: positive(input.value) };
    }).filter(function (item) { return item.quantity; });
    if (inputs.length && !items.length) return toast("Укажите положительное количество");
    var button = form.querySelector('[type="submit"]');
    setBusy(button, true);
    post("/deliveries/" + encodeURIComponent(form.dataset.receiveForm) + "/receive", {
      items: inputs.length ? items : undefined,
      note: form.elements.note.value.trim()
    }).then(function () {
      requestDetails = {};
      return reload("Поставка принята");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function saveTransfer(form) {
    var quantity = positive($("transferQty").value);
    if (!$("transferToSite").value) return toast("Выберите площадку назначения");
    if (!$("transferMaterial").value) return toast("Выберите материал");
    if (!quantity) return toast("Количество должно быть больше нуля");
    var button = form.querySelector('[type="submit"]');
    setBusy(button, true);
    post("/transfers", {
      from_site_id: Number(state.siteId), to_site_id: Number($("transferToSite").value),
      material_id: Number($("transferMaterial").value), quantity: quantity,
      note: $("transferNote").value.trim()
    }).then(function () {
      form.reset();
      return reload("Материал перемещён");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function saveAdjustment(form) {
    var quantity = Number(String($("adjustmentQty").value).replace(",", "."));
    if (!$("adjustmentMaterial").value) return toast("Выберите материал");
    if (!isFinite(quantity) || quantity === 0) return toast("Изменение должно быть не равно нулю");
    if (!$("adjustmentNote").value.trim()) return toast("Укажите причину корректировки");
    var button = form.querySelector('[type="submit"]');
    setBusy(button, true);
    post("/inventory-adjustments", {
      site_id: Number(state.siteId), material_id: Number($("adjustmentMaterial").value),
      quantity_delta: quantity, note: $("adjustmentNote").value.trim()
    }).then(function () {
      form.reset();
      return reload("Остаток скорректирован");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function saveMinimum(form) {
    var value = Number(form.querySelector("input").value);
    if (!isFinite(value) || value < 0) return toast("Минимум не может быть отрицательным");
    var button = form.querySelector("button");
    setBusy(button, true);
    api("/admin/sites/" + encodeURIComponent(state.siteId) + "/materials/" +
      encodeURIComponent(form.dataset.minimumMaterial), {
        method: "PUT", body: JSON.stringify({ min_level: value })
      }).then(function () {
        return reload("Минимум обновлён");
      }).catch(fail).then(function () { setBusy(button, false); });
  }

  function saveAdminMaterial(form) {
    var name = form.elements.material_name.value.trim();
    var active = form.elements.active.checked;
    if (!name) return toast("Название материала не может быть пустым");
    if (!active && !window.confirm("Отключить материал? Старые операции сохранятся.")) {
      form.elements.active.checked = true;
      return;
    }
    var button = form.querySelector('[type="submit"]');
    setBusy(button, true);
    api("/admin/materials/" + encodeURIComponent(form.dataset.adminMaterial), {
      method: "PATCH",
      body: JSON.stringify({
        name: name,
        unit: form.elements.unit.value,
        active: active
      })
    }).then(function () {
      return reload(active ? "Материал сохранён" : "Материал отключён");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function bindEvents() {
    document.addEventListener("click", function (event) {
      var target = event.target.closest("button");
      if (!target) return;
      if (target.dataset.tab) {
        state.tab = target.dataset.tab;
        renderTabs();
      } else if (target.dataset.siteId) {
        state.siteId = target.dataset.siteId;
        movements = [];
        requestDetails = {};
        loadBootstrap().catch(fail);
      } else if (target.dataset.summaryFilter) {
        state.tab = "stock";
        state.stockTab = "materials";
        state.stockFilter = target.dataset.summaryFilter;
        render();
      } else if (target.dataset.stockTab) {
        state.stockTab = target.dataset.stockTab;
        renderStock();
        if (state.stockTab === "movements") loadMovements();
      } else if (target.dataset.stockFilter) {
        state.stockFilter = target.dataset.stockFilter;
        renderStock();
      } else if (target.dataset.urgency) {
        state.requestUrgency = target.dataset.urgency;
        Array.prototype.forEach.call(document.querySelectorAll("[data-urgency]"), function (button) {
          button.classList.toggle("is-active", button === target);
        });
      } else if (target.dataset.removeCart != null) {
        state.requestCart.splice(Number(target.dataset.removeCart), 1);
        renderRequestCart();
      } else if (target.dataset.removeReceipt != null) {
        state.receiptCart.splice(Number(target.dataset.removeReceipt), 1);
        renderReceiptCart();
      } else if (target.dataset.requestAction) {
        var action = target.dataset.requestAction;
        if (action === "create_delivery" || action === "receive_delivery") {
          loadRequestDetail(target.dataset.requestId, action === "create_delivery" ? "delivery" : "receive");
        } else {
          transitionRequest(target.dataset.requestId, action, target);
        }
      }
    });
    document.addEventListener("submit", function (event) {
      event.preventDefault();
      var form = event.target;
      if (form.id === "requestCreateForm") saveRequest(form);
      else if (form.id === "transferForm") saveTransfer(form);
      else if (form.id === "adjustmentForm") saveAdjustment(form);
      else if (form.dataset.adminMaterial) saveAdminMaterial(form);
      else if (form.dataset.minimumMaterial) saveMinimum(form);
      else if (form.dataset.deliveryForm) createDelivery(form);
      else if (form.dataset.receiveForm) receiveDelivery(form);
    });
    $("supplierSelect").addEventListener("change", function () { state.supplierId = this.value; });
    $("receiptMaterialSelect").addEventListener("change", function () { state.receiptMaterialId = this.value; });
    $("issueMaterialSelect").addEventListener("change", function () { state.issueMaterialId = this.value; });
    $("requestMaterialSelect").addEventListener("change", function () { state.requestMaterialId = this.value; });
    $("saveReceiptBtn").addEventListener("click", saveReceipt);
    $("addReceiptItemBtn").addEventListener("click", addReceiptItem);
    $("saveIssueBtn").addEventListener("click", saveIssue);
    $("supplierCreateBtn").addEventListener("click", createSupplier);
    $("materialCreateBtn").addEventListener("click", createMaterial);
    $("addRequestItemBtn").addEventListener("click", addRequestItem);
    $("refreshMovementsBtn").addEventListener("click", loadMovements);
    $("refreshRequestsBtn").addEventListener("click", function () { loadBootstrap().catch(fail); });
    $("toggleRequestCreateBtn").addEventListener("click", function () {
      state.showRequestCreate = !state.showRequestCreate;
      renderRequestCart();
    });
    $("showSupplierCreateBtn").addEventListener("click", function () {
      state.showSupplierCreate = !state.showSupplierCreate;
      renderCreateBoxes();
    });
    $("showMaterialCreateBtn").addEventListener("click", function () {
      state.showMaterialCreate = !state.showMaterialCreate;
      renderCreateBoxes();
    });
  }

  function boot() {
    if (webApp) {
      try {
        if (webApp.ready) webApp.ready();
        if (webApp.expand) webApp.expand();
      } catch (ignore) {}
    }
    if (!syncInitData()) {
      $("outsideMax").hidden = false;
      $("siteCard").hidden = true;
      return;
    }
    bindEvents();
    loadBootstrap().catch(function (error) { fail(error, "Не удалось открыть программу"); });
  }

  setTimeout(boot, webApp && webApp.initData ? 0 : 280);
})();
