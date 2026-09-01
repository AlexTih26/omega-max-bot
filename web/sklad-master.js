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
    showRequestCreate: false,
    roleDefaultsApplied: false,
    historyMaterialId: "",
    historyMaterialName: "",
    editingReceiptId: "",
    materialCardId: "",
    materialCardShowRequest: false,
    materialCardUrgency: "plan",
    materialCardMovements: [],
    materialCardOpenRequest: null,
    historyReturnTo: "",
    historyReturnMaterialId: "",
    historyReturnMaterialName: ""
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
    syncInitData();
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
    var message = (error && error.message) || fallback || "Не удалось выполнить действие";
    if (message === "open in MAX mini-app") {
      message = "Сессия MAX истекла — закройте и откройте mini-app заново";
    }
    toast(message);
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

  function stockItemById(id) {
    return ((data && data.stock) || []).filter(function (item) {
      return String(item.id) === String(id);
    })[0] || null;
  }

  function stockStatusLabel(status) {
    return { critical: "Критично", warning: "Внимание", ok: "Норма" }[status] || status || "—";
  }

  var requestStatusLabels = {
    draft: "Черновик",
    submitted: "Новая",
    accepted: "Принята",
    in_transit: "В пути",
    partially_received: "Принята частично",
    received: "Получена",
    closed: "Закрыта",
    rejected: "Отклонена",
    cancelled: "Отменена"
  };

  function defaultRequestQty(item) {
    var balance = Number(item.balance || 0);
    var minLevel = Number(item.min_level || 0);
    if (balance < minLevel) {
      return Math.max(0.001, minLevel - balance);
    }
    return 1;
  }

  function urgencyLabel(value) {
    return value === "urgent" ? "срочно" : "планово";
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
      if (!state.siteId && data.default_site_id) state.siteId = String(data.default_site_id);
      applyRoleDefaults();
      render();
    });
  }

  function roleLabel(role) {
    return {
      admin: "Администратор",
      master: "Мастер",
      manager: "Руководитель",
      supply: "Снабжение"
    }[role] || role || "";
  }

  function primaryRole() {
    var access = (data && data.access) || {};
    return access.role || ((data && data.roles) || [])[0] || "";
  }

  function renderUser() {
    if (!data.user) return;
    var access = data.access || {};
    var roles = data.roles || access.roles || [];
    var roleText = roles.map(roleLabel).filter(Boolean).join(" · ") ||
      roleLabel(access.role) || "Без роли";
    var hint = data.role_hint || "";
    $("currentUserName").textContent = data.user.name || "Пользователь";
    $("currentUserRole").textContent = roleText;
    if (hint) {
      $("currentUserHint").textContent = hint;
      $("currentUserHint").hidden = false;
    } else {
      $("currentUserHint").hidden = true;
    }
    $("currentUserBox").hidden = false;
  }

  function applyRoleDefaults() {
    if (state.roleDefaultsApplied) return;
    var role = primaryRole();
    if (role === "supply") state.tab = "payment";
    else if (role === "manager") state.tab = "payment";
    else if (role === "master" && allowed("receipt")) state.tab = "receipt";
    else if (role === "admin") state.tab = "stock";
    state.roleDefaultsApplied = true;
  }

  function renderRoleHeadings() {
    var role = primaryRole();
    var receiptPanel = $("receiptPanel");
    if (receiptPanel && !receiptPanel.hidden) {
      receiptPanel.querySelector("h2").textContent = "Приход на площадку";
    }
    var requestsTitle = $("requestsPanelTitle");
    var createTitle = $("requestCreateTitle");
    var createBtn = $("toggleRequestCreateBtn");
    if (role === "master") {
      requestsTitle.textContent = "Заявки на снабжение";
      createTitle.textContent = "Запросить материалы";
      createBtn.textContent = state.showRequestCreate ? "Скрыть" : "Запросить";
    } else if (role === "supply") {
      requestsTitle.textContent = "Закупки и поставки";
      createTitle.textContent = "Новая заявка";
      createBtn.textContent = state.showRequestCreate ? "Скрыть" : "Новая";
    } else {
      requestsTitle.textContent = "Заявки и контроль";
      createTitle.textContent = "Новая заявка";
      createBtn.textContent = state.showRequestCreate ? "Скрыть" : "Новая";
    }
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
    document.querySelector('[data-tab="requests"]').hidden = !allowed("request_create") &&
      !allowed("request_manage") && !allowed("view");
    document.querySelector('[data-tab="payment"]').hidden =
      !(allowed("receipt_price") || allowed("payment_view"));
    $("toggleRequestCreateBtn").hidden = !allowed("request_create");
    $("showSupplierCreateBtn").hidden = !allowed("settings_manage");
    $("showMaterialCreateBtn").hidden = !allowed("settings_manage");
    $("materialAdminSettings").hidden = !allowed("settings_manage");
    $("minimumSettings").hidden = !allowed("settings_manage");
    $("rolesAdminSettings").hidden = !allowed("roles_manage");
    $("auditAdminSettings").hidden = !(allowed("roles_manage") || allowed("settings_manage"));
    $("adjustmentForm").hidden = !allowed("inventory_adjustment");
    $("cancelReceiptEditBtn").hidden = !state.editingReceiptId;
    $("saveReceiptBtn").textContent = state.editingReceiptId
      ? "Сохранить изменения прихода"
      : "Сохранить весь приход";
    if ($("receiptPanelTitle")) {
      $("receiptPanelTitle").textContent = state.editingReceiptId
        ? ("Правка прихода №" + state.editingReceiptId)
        : "Приход на площадку";
    }
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
    ["receipt", "issue", "stock", "requests", "payment"].forEach(function (name) {
      $(name + "Panel").hidden = state.tab !== name;
    });
  }

  function moneyLabel(value) {
    var amount = Number(value || 0);
    if (!isFinite(amount)) return "0 ₽";
    return amount.toLocaleString("ru-RU", { maximumFractionDigits: 2 }) + " ₽";
  }

  function paymentStatusLabel(status) {
    return {
      pending: "Ждёт цены",
      priced: "Цены сохранены",
      sent: "Отправлено руководителю"
    }[status] || status || "—";
  }

  function renderPayment() {
    if (!(allowed("receipt_price") || allowed("payment_view"))) return;
    var canEdit = allowed("receipt_price");
    $("paymentPanelNote").textContent = canEdit
      ? "Проставьте цены по каждому приходу и нажмите «Отправить руководителю». Суммы уйдут только в личку."
      : "Ведомости к оплате. Суммы видны только руководителю в личных сообщениях MAX.";
    var receipts = data.payment_receipts || [];
    $("paymentList").innerHTML = receipts.map(function (receipt) {
      var itemsHtml = (receipt.items || []).map(function (item) {
        if (canEdit && receipt.payment_status !== "sent") {
          return '<div class="sm-pay-item"><div class="sm-pay-item__info"><b>' +
            esc(item.material_name) + '</b><small>' + esc(formatQty(item.quantity)) + " " +
            esc(item.quantity_unit || "") + '</small></div><div class="sm-pay-fields">' +
            '<label class="sm-pay-field"><span>К оплате</span>' +
            '<input class="sm-input" type="number" min="0" step="0.001" placeholder="0" value="' +
            esc(formatQty(item.billing_quantity || item.quantity)) + '" data-pay-qty="' + esc(item.id) + '"></label>' +
            '<label class="sm-pay-field"><span>Ед.</span>' +
            '<input class="sm-input" placeholder="шт" value="' +
            esc(item.billing_unit || item.quantity_unit || "") + '" data-pay-unit="' + esc(item.id) + '"></label>' +
            '<label class="sm-pay-field"><span>Цена, ₽</span>' +
            '<input class="sm-input" type="number" min="0" step="0.01" placeholder="0" value="' +
            esc(item.unit_price || "") + '" data-pay-price="' + esc(item.id) + '"></label></div></div>';
        }
        return '<div class="sm-cart-row"><span><b>' + esc(item.material_name) + "</b> · " +
          esc(formatQty(item.billing_quantity || item.quantity)) + " " +
          esc(item.billing_unit || item.quantity_unit || "") +
          (item.unit_price ? " × " + moneyLabel(item.unit_price) + " = " + moneyLabel(item.line_total) : "") +
          "</span></div>";
      }).join("");
      var actions = "";
      if (canEdit && receipt.payment_status !== "sent") {
        var buttons = [
          '<button type="button" class="sm-mini-action" data-save-pricing="' + esc(receipt.id) +
          '">Сохранить цены</button>'
        ];
        if (receipt.payment_status === "priced") {
          buttons.push(
            '<button type="button" class="sm-mini-action" data-send-manager="' + esc(receipt.id) +
            '">Отправить руководителю</button>'
          );
        }
        actions = '<div class="sm-action-list sm-action-list--stack">' + buttons.join("") + "</div>";
      }
      return '<article class="sm-history-card sm-payment-card"><div class="sm-heading-row sm-heading-row--card"><div class="sm-heading-main"><b>Приход №' +
        esc(receipt.id) + '</b><span>' + esc(receipt.supplier_name || "") + " · " +
        esc(receipt.site_name || "") + '</span></div><span class="sm-status">' +
        esc(paymentStatusLabel(receipt.payment_status)) + '</span></div><p class="sm-note sm-note--tight">Принял: ' +
        esc(receipt.actor_name || "—") + " · " + esc(dateLabel(receipt.created_at)) +
        '</p><div class="sm-section-gap"><p class="sm-note sm-note--tight"><b>Итого к оплате</b></p>' +
        '<div class="sm-pay-list">' + itemsHtml + "</div></div>" +
        (receipt.total_amount
          ? '<p class="sm-note"><b>Итого: ' + moneyLabel(receipt.total_amount) + "</b></p>"
          : "") +
        actions + "</article>";
    }).join("") || '<p class="sm-empty">Приходов для оплаты пока нет.</p>';
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
    var inMaterialCard = Boolean(state.materialCardId);
    if ($("stockListBox")) $("stockListBox").hidden = inMaterialCard;
    if ($("materialCardBox")) $("materialCardBox").hidden = !inMaterialCard;
    if (inMaterialCard) {
      renderMaterialCard();
    } else {
      Array.prototype.forEach.call(document.querySelectorAll("[data-stock-filter]"), function (button) {
        button.classList.toggle("is-active", button.dataset.stockFilter === state.stockFilter);
      });
      var items = data.stock || [];
      if (state.stockFilter !== "all") {
        items = items.filter(function (item) { return item.status === state.stockFilter; });
      }
      var canOpenCard = allowed("request_create");
      $("stockList").innerHTML = items.map(function (item) {
        var tag = canOpenCard ? "button" : "article";
        var attrs = canOpenCard
          ? ' type="button" class="sm-row sm-row--' + esc(item.status || "ok") +
            ' sm-row-btn sm-stock-row-btn" data-open-material="' + esc(item.id) +
            '" data-open-material-name="' + esc(item.name) + '"'
          : ' class="sm-row sm-row--' + esc(item.status || "ok") + '"';
        return "<" + tag + attrs + "><div><b>" + esc(item.name) + '</b><span>Остаток: ' +
          esc(formatQty(item.balance)) + " " + esc(item.unit || "") +
          '</span></div><div class="sm-align-right"><span>Минимум</span><b>' +
          esc(formatQty(item.min_level)) + "</b></div></" + tag + ">";
      }).join("") || '<p class="sm-empty">Нет позиций в этой категории.</p>';
    }
    renderMinimums();
    renderMaterialAdmin();
    renderMovements();
  }

  function renderMaterialCardBriefHistory() {
    var box = $("materialCardHistory");
    if (!box) return;
    var items = state.materialCardMovements || [];
    box.innerHTML = items.slice(0, 5).map(function (item) {
      var delta = Number(item.quantity_delta || 0);
      return '<article class="sm-history-card sm-history-card--compact"><div class="sm-heading-row"><div><b>' +
        esc(movementLabels[item.op_type] || "Операция") + '</b><span>' +
        esc(dateLabel(item.created_at)) + (item.supplier_name ? " · " + esc(item.supplier_name) : "") +
        '</span></div><b class="' + (delta >= 0 ? "sm-positive" : "sm-negative") + '">' +
        (delta > 0 ? "+" : "") + esc(formatQty(delta)) + " " + esc(item.material_unit || "") +
        "</b></div></article>";
    }).join("") || '<p class="sm-empty sm-empty--compact">Движений пока нет.</p>';
  }

  function renderMaterialCard() {
    var item = stockItemById(state.materialCardId);
    if (!item || !$("materialCardBox")) return;
    var site = siteById(state.siteId) || {};
    $("materialCardTitle").textContent = item.name || "Материал";
    $("materialCardSite").textContent = "Площадка: " + (site.name || "—");
    $("materialCardSite").hidden = false;
    var status = item.status || "ok";
    $("materialCardSummary").innerHTML =
      '<div class="sm-material-card__stats sm-row--' + esc(status) + '">' +
      '<span class="sm-material-card__status is-' + esc(status) + '">' +
      esc(stockStatusLabel(status)) + "</span>" +
      '<dl><div><dt>Остаток</dt><dd>' + esc(formatQty(item.balance)) + " " +
      esc(item.unit || "") + '</dd></div><div><dt>Минимум</dt><dd>' +
      esc(formatQty(item.min_level)) + " " + esc(item.unit || "") +
      "</dd></div></dl></div>" +
      (state.materialCardOpenRequest
        ? '<div class="sm-material-card__open-request sm-section-gap"><p class="sm-note sm-note--tight"><b>Заявка уже есть</b><br>№' +
          esc(state.materialCardOpenRequest.id) + " · " +
          esc(state.materialCardOpenRequest.status_label ||
            requestStatusLabels[state.materialCardOpenRequest.status] ||
            state.materialCardOpenRequest.status || "в работе") +
          "<br>Снабжение уже получило запрос. Повторную можно создать только при необходимости.</p></div>"
        : "");
    renderMaterialCardBriefHistory();
    var canRequest = allowed("request_create");
    var hasDuplicate = Boolean(state.materialCardOpenRequest);
    $("materialCardRequestBtn").hidden = !canRequest || state.materialCardShowRequest;
    $("materialCardRequestBox").hidden = !canRequest || !state.materialCardShowRequest;
    if ($("materialCardRequestBtn")) {
      $("materialCardRequestBtn").textContent = hasDuplicate
        ? "Создать ещё одну заявку"
        : "Сделать заявку";
      $("materialCardRequestBtn").classList.toggle("sm-btn--ghost", hasDuplicate);
      $("materialCardRequestBtn").classList.toggle("sm-btn--primary", !hasDuplicate);
    }
    if (canRequest && state.materialCardShowRequest) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-material-urgency]"), function (button) {
        button.classList.toggle("is-active", button.dataset.materialUrgency === state.materialCardUrgency);
      });
    }
  }

  function loadMaterialCardData() {
    if (!state.materialCardId || !state.siteId) return Promise.resolve();
    var materialId = state.materialCardId;
    return Promise.all([
      api("/movements?site_id=" + encodeURIComponent(state.siteId) +
        "&material_id=" + encodeURIComponent(materialId) + "&limit=5"),
      api("/materials/" + encodeURIComponent(materialId) +
        "/open-request?site_id=" + encodeURIComponent(state.siteId))
    ]).then(function (results) {
      state.materialCardMovements = (results[0].items || []);
      state.materialCardOpenRequest = results[1].open_request || null;
      renderMaterialCard();
    });
  }

  function openMaterialCard(materialId, materialName) {
    state.materialCardId = String(materialId);
    state.materialCardShowRequest = false;
    state.materialCardMovements = [];
    state.materialCardOpenRequest = null;
    if (materialName) {
      state.historyMaterialName = materialName;
    }
    renderStock();
    loadMaterialCardData().catch(fail);
  }

  function closeMaterialCard() {
    state.materialCardId = "";
    state.materialCardShowRequest = false;
    state.materialCardMovements = [];
    state.materialCardOpenRequest = null;
    renderStock();
  }

  function openMaterialCardRequestForm() {
    var item = stockItemById(state.materialCardId);
    if (!item) return;
    if (state.materialCardOpenRequest) {
      var open = state.materialCardOpenRequest;
      var label = open.status_label || requestStatusLabels[open.status] || "в работе";
      if (!window.confirm(
        "По этому материалу уже есть заявка №" + open.id + " (" + label + ").\n\n" +
        "Снабжение уже получило запрос.\nСоздать ещё одну заявку?"
      )) {
        return;
      }
    }
    state.materialCardShowRequest = true;
    state.materialCardUrgency = (item.status === "critical") ? "urgent" : "plan";
    var qty = defaultRequestQty(item);
    $("materialCardQty").value = formatQty(qty);
    $("materialCardQtyHint").textContent = Number(item.balance) < Number(item.min_level)
      ? "До минимума: " + formatQty(qty) + " " + (item.unit || "")
      : "Можно указать нужное количество";
    $("materialCardComment").value = "";
    renderMaterialCard();
  }

  function cancelMaterialCardRequestForm() {
    state.materialCardShowRequest = false;
    renderMaterialCard();
  }

  function openMaterialCardFullHistory() {
    var materialId = state.materialCardId;
    var materialName = (stockItemById(materialId) || {}).name || state.historyMaterialName || "";
    state.historyReturnTo = "materialCard";
    state.historyReturnMaterialId = String(materialId);
    state.historyReturnMaterialName = materialName;
    state.materialCardId = "";
    state.materialCardShowRequest = false;
    state.stockTab = "movements";
    renderStock();
    openMaterialHistory(materialId, materialName);
  }

  function buildMaterialRequestConfirmText(item, quantity, urgency, comment, openRequest) {
    var site = siteById(state.siteId) || {};
    var lines = [
      "Отправить заявку в снабжение?",
      "",
      "Площадка: " + (site.name || "—"),
      "Материал: " + (item.name || "Материал"),
      "Количество: " + formatQty(quantity) + " " + (item.unit || ""),
      "Срочность: " + urgencyLabel(urgency)
    ];
    if (comment) lines.push("Комментарий: " + comment);
    if (openRequest) {
      lines.push("");
      lines.push(
        "По этому материалу уже есть заявка №" + openRequest.id + " (" +
        (openRequest.status_label || requestStatusLabels[openRequest.status] || "в работе") +
        "). Всё равно создать новую?"
      );
    }
    return lines.join("\n");
  }

  function saveMaterialCardRequest(form) {
    var item = stockItemById(state.materialCardId);
    if (!item) return toast("Материал не найден");
    var quantity = positive($("materialCardQty").value);
    if (!quantity) return toast("Количество должно быть больше нуля");
    var urgency = state.materialCardUrgency || "plan";
    var comment = $("materialCardComment").value.trim();
    var button = $("materialCardSubmitBtn");
    setBusy(button, true);
    var submit = function (openRequest) {
      var confirmText = buildMaterialRequestConfirmText(item, quantity, urgency, comment, openRequest);
      if (!window.confirm(confirmText)) {
        setBusy(button, false);
        return;
      }
      post("/requests", {
        site_id: Number(state.siteId),
        items: [{ material_id: Number(state.materialCardId), quantity: quantity }],
        urgency: urgency,
        comment: comment,
        confirm_duplicate: Boolean(openRequest)
      }).then(function (body) {
        var requestId = (body.request || {}).id;
        closeMaterialCard();
        return reload(requestId ? ("Заявка №" + requestId + " отправлена в снабжение") : "Заявка отправлена в снабжение");
      }).catch(fail).then(function () { setBusy(button, false); });
    };
    if (state.materialCardOpenRequest) {
      submit(state.materialCardOpenRequest);
      return;
    }
    api("/materials/" + encodeURIComponent(state.materialCardId) +
      "/open-request?site_id=" + encodeURIComponent(state.siteId))
      .then(function (body) {
        state.materialCardOpenRequest = body.open_request || null;
        submit(state.materialCardOpenRequest);
      })
      .catch(function (error) {
        fail(error);
        setBusy(button, false);
      });
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

  function movementSourceLabel(item) {
    if (item.supplier_name) return "От: " + item.supplier_name;
    if (String(item.op_type || "").indexOf("transfer") === 0) return "Перемещение между площадками";
    if (item.op_type === "issue") return "Расход со склада";
    if (item.op_type === "adjustment" || item.op_type === "inventory_adjustment") return "Корректировка";
    return "";
  }

  function batchItemsText(items, materialId) {
    if (!items || !items.length) return "";
    return items.map(function (batch) {
      var qty = batch.quantity != null
        ? formatQty(batch.quantity) + (batch.material_unit ? " " + batch.material_unit : "")
        : "";
      var suffix = String(batch.material_id) === String(materialId) ? " (эта позиция)" : "";
      return (batch.material_name || "Материал") + (qty ? " — " + qty : "") + suffix;
    }).join("; ");
  }

  function renderMovementsHeader() {
    var inMaterialHistory = Boolean(state.historyMaterialId);
    $("movementsTitle").textContent = inMaterialHistory
      ? (state.historyMaterialName || "Материал")
      : "История";
    $("movementsSubtitle").textContent = inMaterialHistory
      ? "Полная история поступлений и движений"
      : "";
    $("movementsSubtitle").hidden = !inMaterialHistory;
    $("movementsBackBtn").hidden = !inMaterialHistory;
  }

  function renderMovementDetailCard(item) {
    var delta = Number(item.quantity_delta || 0);
    var batchText = batchItemsText(item.batch_items, item.material_id);
    var meta = [
      ["От кого", item.supplier_name || "—"],
      ["Когда", dateLabel(item.created_at) || "—"],
      ["Площадка", item.site_name || "—"],
      ["Принял", item.received_by_name || item.actor_name || "—"],
      ["Заказал", item.ordered_by_name || "—"]
    ];
    if (item.request_id) {
      meta.push(["Заявка", "#" + item.request_id]);
    }
    if (item.receipt_id) {
      meta.push(["Приход", "№" + item.receipt_id]);
    }
    return '<article class="sm-history-card"><div class="sm-heading-row"><div><b>' +
      esc(item.material_name || "Материал") + '</b><span>' +
      esc(movementLabels[item.op_type] || "Операция") + " · " +
      esc(dateLabel(item.created_at)) + '</span></div><b class="' +
      (delta >= 0 ? "sm-positive" : "sm-negative") + '">' + (delta > 0 ? "+" : "") +
      esc(formatQty(delta)) + " " + esc(item.material_unit || "") + '</b></div><dl class="sm-history-meta">' +
      meta.map(function (row) {
        return "<div><dt>" + esc(row[0]) + "</dt><dd>" + esc(row[1]) + "</dd></div>";
      }).join("") + "</dl>" +
      (batchText
        ? '<p class="sm-history-batch"><span>Вместе пришло</span> ' + esc(batchText) + "</p>"
        : "") +
      (item.note ? '<p class="sm-note">' + esc(item.note) + "</p>" : "") +
      '<div class="sm-action-list">' +
      (item.receipt_id && item.op_type === "receipt" && !item.delivery_item_id && allowed("receipt")
        ? '<button type="button" class="sm-mini-action" data-edit-receipt="' + esc(item.receipt_id) +
          '">Изменить приход</button>'
        : "") +
      "</div>" +
      "</article>";
  }

  function renderMovements() {
    renderMovementsHeader();
    if (state.historyMaterialId) {
      $("movementsList").innerHTML = movements.map(function (item) {
        return renderMovementDetailCard(item);
      }).join("") || '<p class="sm-empty">По этому материалу движений пока нет.</p>';
      return;
    }
    $("movementsList").innerHTML = movements.map(function (item) {
      var delta = Number(item.quantity_delta || 0);
      var source = movementSourceLabel(item);
      return '<button type="button" class="sm-row sm-row-btn" data-history-material="' +
        esc(item.material_id) + '" data-history-material-name="' + esc(item.material_name || "") + '">' +
        '<div><b>' + esc(item.material_name || "Материал") + '</b><span>' +
        esc(movementLabels[item.op_type] || "Операция со складом") +
        (source ? " · " + esc(source) : "") +
        (item.note ? " · " + esc(item.note) : "") + '</span></div><div class="sm-align-right"><b class="' +
        (delta >= 0 ? "sm-positive" : "sm-negative") + '">' + (delta > 0 ? "+" : "") +
        esc(formatQty(delta)) + '</b><span>' + esc(dateLabel(item.created_at)) + "</span></div></button>";
    }).join("") || '<p class="sm-empty">Движений пока нет.</p>';
  }

  function renderRequestCart() {
    $("requestCreateForm").hidden = !state.showRequestCreate;
    renderRoleHeadings();
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
    create_delivery: "Заказал у поставщика", receive_delivery: "Поставка", close: "Закрыть"
  };
  var transitionStatuses = {
    submit: "submitted", cancel: "cancelled", accept: "accepted",
    reject: "rejected", mark_in_transit: "in_transit", close: "closed"
  };

  function etaPickerHtml(selectedDays) {
    var preset = [1, 2, 3, 5, 7];
    var selected = Number(selectedDays || 0);
    var chips = preset.map(function (days) {
      return '<button type="button" class="sm-chip' + (selected === days ? " is-active" : "") +
        '" data-eta-days="' + days + '">' + days + " дн.</button>";
    }).join("");
    var customActive = selected && preset.indexOf(selected) < 0;
    return '<div class="sm-eta-picker"><p class="sm-note">Срок до приёма на склад</p><div class="sm-chip-grid">' +
      chips + '<button type="button" class="sm-chip' + (customActive ? " is-active" : "") +
      '" data-eta-custom>Другое</button></div>' +
      '<input class="sm-input sm-section-gap sm-eta-custom' + (customActive ? "" : " hidden") +
      '" type="number" min="1" max="90" placeholder="Количество дней" data-eta-days-input value="' +
      (customActive ? esc(selected) : "") + '"></div>';
  }

  function readEtaDays(form) {
    var picker = form.querySelector(".sm-eta-picker");
    if (!picker) return null;
    var active = picker.querySelector(".sm-chip.is-active[data-eta-days]");
    if (active) return Number(active.dataset.etaDays);
    var custom = picker.querySelector("[data-eta-days-input]");
    if (custom && !custom.hidden) return positive(custom.value);
    return null;
  }

  function activateEtaChip(chip) {
    var picker = chip.closest(".sm-eta-picker");
    if (!picker) return;
    Array.prototype.forEach.call(picker.querySelectorAll(".sm-chip"), function (button) {
      button.classList.remove("is-active");
    });
    chip.classList.add("is-active");
    var custom = picker.querySelector("[data-eta-days-input]");
    if (!custom) return;
    if (chip.dataset.etaCustom != null) {
      custom.hidden = false;
      custom.focus();
    } else {
      custom.hidden = true;
      custom.value = "";
    }
  }

  function requestStatusHtml(request) {
    var label = esc(request.status_label || "Статус обновляется");
    if (request.status === "in_transit" || request.status === "partially_received") {
      return '<button type="button" class="sm-status sm-status--tap" data-request-eta="' +
        esc(request.id) + '" title="Подробнее о поставке">' + label + "</button>";
    }
    return '<span class="sm-status">' + label + "</span>";
  }

  function requestEtaLineHtml(request) {
    if (!request.eta_date_label) return "";
    var text = "Ожидаем ~" + request.eta_date_label;
    if (request.eta_label) text += " · " + request.eta_label;
    if (request.eta_overdue) text += " · просрочено";
    return '<p class="sm-note sm-eta-line">' + esc(text) + "</p>";
  }
  function requestItemsHtml(request) {
    return (request.items || []).map(function (item) {
      var progress = Number(item.received || 0) ? " · получено " + formatQty(item.received) : "";
      return '<li>' + esc(item.material_name || "Материал") + " — " +
        esc(formatQty(item.quantity || item.ordered)) + " " + esc(item.material_unit || "") +
        esc(progress) + "</li>";
    }).join("");
  }

  function requestActionsHtml(request) {
    var actions = (request.allowed_actions || []).map(function (action) {
      if (!actionLabels[action] || action === "update_eta") return "";
      return '<button type="button" class="sm-mini-action" data-request-action="' + esc(action) +
        '" data-request-id="' + esc(request.id) + '">' + esc(actionLabels[action]) + "</button>";
    }).join("");
    return actions + '<button type="button" class="sm-mini-action" data-request-history="' +
      esc(request.id) + '">История</button>';
  }

  function requestExtraHtml(request) {
    var detail = requestDetails[String(request.id)];
    if (!detail) return "";
    if (detail.mode === "delivery") return deliveryFormHtml(detail.request);
    if (detail.mode === "receive") return receiveFormHtml(detail.request);
    if (detail.mode === "transit") return transitFormHtml(detail.request);
    if (detail.mode === "eta_info") return etaInfoHtml(detail.request);
    if (detail.mode === "eta_edit") return etaEditFormHtml(detail.request);
    if (detail.mode === "history") return requestTimelineHtml(detail.request);
    return "";
  }

  function requestTimelineHtml(request) {
    var items = request.timeline || [];
    if (!items.length) {
      return '<div class="sm-subpanel sm-timeline"><h3>История</h3><p class="sm-empty">Событий пока нет.</p></div>';
    }
    return '<div class="sm-subpanel sm-timeline"><h3>История</h3><ol class="sm-timeline-list">' +
      items.map(function (item) {
        var actor = item.actor_name
          ? '<span class="sm-note"> · ' + esc(item.actor_name) +
            (item.actor_max_id ? " (id " + esc(item.actor_max_id) + ")" : "") + "</span>"
          : "";
        var detail = item.detail ? '<span class="sm-note"> · ' + esc(item.detail) + "</span>" : "";
        return '<li><span class="sm-timeline-when">' + esc(dateLabel(item.created_at)) +
          '</span><b>' + esc(item.label || item.action || "Событие") + "</b>" + actor + detail + "</li>";
      }).join("") + "</ol></div>";
  }

  function transitFormHtml(request) {
    return '<form class="sm-subpanel sm-inline-panel" data-transit-form="' + esc(request.id) + '">' +
      '<h3>Отметить «В пути»</h3>' + etaPickerHtml() +
      '<button class="sm-btn sm-btn--primary sm-submit sm-section-gap" type="submit">Подтвердить</button></form>';
  }

  function etaInfoHtml(request) {
    var materials = (request.items || []).map(function (item) {
      return (item.material_name || "Материал") + " — " + formatQty(item.quantity || item.ordered) +
        " " + (item.material_unit || "");
    }).join("; ") || request.material_name || "—";
    var etaText = request.eta_date_label
      ? ("~" + request.eta_date_label + (request.eta_label ? " · " + request.eta_label : ""))
      : "не указан";
    var edit = (request.allowed_actions || []).indexOf("update_eta") >= 0
      ? '<button type="button" class="sm-mini-action" data-request-eta-edit="' + esc(request.id) +
        '">Изменить срок</button>' : "";
    return '<div class="sm-subpanel sm-eta-panel"><h3>В пути</h3><dl>' +
      "<dt>Материал</dt><dd>" + esc(materials) + "</dd>" +
      "<dt>Площадка</dt><dd>" + esc(request.site_name || "—") + "</dd>" +
      "<dt>Срок</dt><dd>" + esc(etaText) + "</dd>" +
      "<dt>Поставщик</dt><dd>" + esc(request.supplier_name || "—") + "</dd>" +
      "<dt>Заказал</dt><dd>" + esc(request.ordered_by_name || "—") + "</dd></dl>" + edit + "</div>";
  }

  function etaEditFormHtml(request) {
    return '<form class="sm-subpanel sm-inline-panel" data-eta-form="' + esc(request.id) + '">' +
      '<h3>Изменить срок</h3>' + etaPickerHtml(request.expected_delivery_days) +
      '<button class="sm-btn sm-btn--primary sm-submit sm-section-gap" type="submit">Сохранить</button></form>';
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
      '<h3>Заказ у поставщика</h3><select class="sm-input sm-select" name="supplier_id">' +
      supplierOptions + '</select><div class="sm-section-gap">' + rows + '</div>' +
      etaPickerHtml() +
      '<input class="sm-input sm-section-gap" name="note" placeholder="Примечание">' +
      '<button class="sm-btn sm-btn--primary sm-submit" type="submit">Заказал у поставщика</button></form>';
  }

  function receiveFormHtml(request) {
    var deliveries = request.deliveries || [];
    if (!deliveries.length) {
      return '<p class="sm-empty">Сначала оформите заказ у поставщика.</p>';
    }
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
        '<h3>Поставка · заказ №' + esc(delivery.id) + '</h3><p class="sm-note">На базе ' +
        esc(formatQty(delivery.received_quantity)) + " из " + esc(formatQty(delivery.quantity)) +
        "</p>" + fallback + rows +
        '<input class="sm-input sm-section-gap" name="note" placeholder="Комментарий к поставке">' +
        '<button class="sm-btn sm-btn--primary sm-submit" type="submit">Поставка</button></form>';
    }).join("");
  }

  function renderRequests() {
    renderRequestCart();
    var requests = data.requests || [];
    $("requestsList").innerHTML = requests.map(function (request) {
      return '<article class="sm-request-card"><div class="sm-heading-row"><div><b>Заявка №' +
        esc(request.id) + '</b><span>' + esc(request.site_name || (siteById(request.site_id) || {}).name || "") +
        '</span></div>' + requestStatusHtml(request) +
        '</div>' + requestEtaLineHtml(request) +
        '<ul class="sm-request-items">' + requestItemsHtml(request) +
        '</ul><div class="sm-request-meta">' +
        (request.urgency === "urgent" ? '<span class="sm-urgent">Срочно</span>' : "<span>Планово</span>") +
        (request.comment ? "<span>" + esc(request.comment) + "</span>" : "") +
        '</div><div class="sm-action-list">' + requestActionsHtml(request) +
        '</div>' + requestExtraHtml(request) + "</article>";
    }).join("") || '<p class="sm-empty">Заявок по этой площадке пока нет.</p>';
  }

  var auditActionLabels = {
    receipt: "Приход",
    receipt_batch: "Приход партией",
    receipt_edit: "Правка прихода",
    receipt_price: "Цены прихода",
    receipt_send_manager: "Отправка руководителю",
    issue: "Расход",
    transfer: "Перемещение",
    inventory_adjustment: "Корректировка",
    reversal: "Сторно",
    request_create: "Заявка",
    request_transition: "Статус заявки",
    request_eta_update: "Срок поставки",
    delivery_create: "Заказ у поставщика",
    delivery_receive: "Поставка на базу",
    role_update: "Роль",
    site_material_minimum: "Минимум",
    material_update: "Материал"
  };

  function renderAuditLog() {
    var box = $("auditAdminSettings");
    if (!box || box.hidden) return;
    var items = data.audit_log || [];
    $("auditList").innerHTML = items.map(function (item) {
      var action = auditActionLabels[item.action] || item.action || "Действие";
      var target = [item.entity_type, item.entity_id].filter(Boolean).join(" #");
      return '<article class="sm-audit-card"><div class="sm-heading-row"><div><b>' +
        esc(action) + '</b><span>' + esc(item.actor_name || "Пользователь") +
        (item.actor_max_id ? " · id " + esc(item.actor_max_id) : "") +
        '</span></div><span class="sm-status">' + esc(dateLabel(item.created_at)) +
        '</span></div><p class="sm-note">' + esc(target || "—") + "</p></article>";
    }).join("") || '<p class="sm-empty">Журнал пока пуст.</p>';
  }

  function renderRolesAdmin() {
    var box = $("rolesAdminSettings");
    if (!box || !allowed("roles_manage")) return;
    var sites = data.sites || [];
    $("roleSiteSelect").innerHTML = '<option value="">Все площадки</option>' +
      sites.map(function (site) {
        return '<option value="' + esc(site.id) + '">' + esc(site.name) + "</option>";
      }).join("");
    var roles = data.admin_roles || [];
    $("rolesList").innerHTML = roles.map(function (item) {
      var siteNames = (item.site_ids || []).map(function (siteId) {
        return (siteById(siteId) || {}).name || ("#" + siteId);
      }).join(", ");
      return '<article class="sm-role-card' + (item.active ? "" : " is-disabled") + '">' +
        '<div class="sm-heading-row"><div><b>' + esc(roleLabel(item.role)) + '</b><span>MAX id ' +
        esc(item.max_id) + '</span></div><span class="sm-status">' +
        (item.active ? "Активна" : "Отключена") + '</span></div>' +
        (siteNames ? '<p class="sm-note">Площадки: ' + esc(siteNames) + "</p>" : "") +
        '<button type="button" class="sm-mini-action" data-edit-role="' + esc(item.max_id) +
        '" data-edit-role-name="' + esc(item.role) + '">Изменить</button></article>';
    }).join("") || '<p class="sm-empty">Роли из базы пока не назначены. Роли из .env тоже действуют.</p>';
  }

  function render() {
    renderUser();
    renderRoleAware();
    renderRoleHeadings();
    renderSites();
    renderSummary();
    renderTabs();
    renderSelectors();
    renderCreateBoxes();
    renderReceiptCart();
    renderStock();
    renderRequests();
    renderPayment();
    renderRolesAdmin();
    renderAuditLog();
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

  function confirmReceiptSave() {
    var site = siteById(state.siteId) || {};
    var siteName = site.name || (data && data.default_site_name) || "Грузовой";
    return window.confirm(
      "Все позиции учли в приходе?\n\nЕсли да — сохранить на склад «" + siteName + "»."
    );
  }

  function resetReceiptForm() {
    state.editingReceiptId = "";
    state.receiptCart = [];
    state.receiptMaterialId = "";
    $("receiptNote").value = "";
  }

  function beginReceiptEdit(receiptId) {
    return api("/receipts/" + encodeURIComponent(receiptId)).then(function (body) {
      var receipt = body.receipt || {};
      state.editingReceiptId = String(receipt.id || receiptId);
      state.tab = "receipt";
      state.siteId = String(receipt.site_id || state.siteId);
      state.supplierId = String(receipt.supplier_id || "");
      state.receiptCart = (receipt.items || []).map(function (item) {
        return {
          material_id: Number(item.material_id),
          quantity: Number(item.quantity)
        };
      });
      $("receiptNote").value = receipt.note || "";
      render();
      toast("Редактирование прихода №" + state.editingReceiptId);
    });
  }

  function saveReceipt() {
    if (!state.siteId) return toast("Выберите площадку");
    if (!state.supplierId) return toast("Выберите поставщика");
    if (!state.receiptCart.length) return toast("Добавьте хотя бы одну позицию");
    if (!confirmReceiptSave()) return;
    var button = $("saveReceiptBtn");
    setBusy(button, true);

    function submitReceipt(targetReceiptId, existingReceipt) {
      var payload = {
        site_id: Number(state.siteId),
        supplier_id: Number(state.supplierId),
        items: state.receiptCart.map(function (item) {
          return {
            material_id: Number(item.material_id),
            quantity: Number(item.quantity)
          };
        }),
        note: $("receiptNote").value.trim()
      };
      if (targetReceiptId && existingReceipt) {
        var combined = {};
        (existingReceipt.items || []).forEach(function (item) {
          combined[item.material_id] = Number(item.quantity);
        });
        payload.items.forEach(function (item) {
          combined[item.material_id] = Math.round(
            ((combined[item.material_id] || 0) + item.quantity) * 1000
          ) / 1000;
        });
        payload.items = Object.keys(combined).map(function (materialId) {
          return { material_id: Number(materialId), quantity: combined[materialId] };
        });
        var existingNote = (existingReceipt.note || "").trim();
        if (existingNote && payload.note) payload.note = existingNote + " / " + payload.note;
        else if (existingNote) payload.note = existingNote;
      }
      var request = targetReceiptId
        ? post("/receipts/" + encodeURIComponent(targetReceiptId), payload, "PATCH")
        : (state.editingReceiptId
          ? post("/receipts/" + encodeURIComponent(state.editingReceiptId), payload, "PATCH")
          : post("/receipts", payload));
      var message = targetReceiptId
        ? ("Позиции добавлены к приходу №" + targetReceiptId)
        : (state.editingReceiptId ? "Приход изменён" : "Приход сохранён");
      return request.then(function () {
        resetReceiptForm();
        return reload(message);
      });
    }

    var chain = Promise.resolve();
    if (!state.editingReceiptId) {
      chain = api(
        "/receipts/similar?site_id=" + encodeURIComponent(state.siteId) +
        "&supplier_id=" + encodeURIComponent(state.supplierId)
      ).then(function (body) {
        var similar = body.similar_receipt;
        if (!similar) return null;
        if (!window.confirm(
          "Есть похожий приход сегодня №" + similar.id + " (" +
          (similar.supplier_name || "поставщик") + "). Добавить позиции к нему?"
        )) {
          return null;
        }
        return api("/receipts/" + encodeURIComponent(similar.id)).then(function (detail) {
          return { id: similar.id, receipt: detail.receipt || {} };
        });
      });
    }
    chain.then(function (appendTarget) {
      if (appendTarget) {
        return submitReceipt(appendTarget.id, appendTarget.receipt);
      }
      return submitReceipt(null, null);
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
    renderMovements();
    var path = "/movements?site_id=" + encodeURIComponent(state.siteId) + "&limit=100";
    if (state.historyMaterialId) {
      path += "&material_id=" + encodeURIComponent(state.historyMaterialId) + "&detailed=1";
    }
    return api(path).then(function (body) {
      movements = body.items || [];
      renderMovements();
    }).catch(fail);
  }

  function openMaterialHistory(materialId, materialName) {
    state.historyMaterialId = String(materialId);
    state.historyMaterialName = materialName || "";
    renderMovements();
    return loadMovements().catch(fail);
  }

  function closeMaterialHistory() {
    if (state.historyReturnTo === "materialCard" && state.historyReturnMaterialId) {
      var materialId = state.historyReturnMaterialId;
      var materialName = state.historyReturnMaterialName || "";
      state.historyReturnTo = "";
      state.historyReturnMaterialId = "";
      state.historyReturnMaterialName = "";
      state.historyMaterialId = "";
      state.historyMaterialName = "";
      state.stockTab = "materials";
      renderStock();
      openMaterialCard(materialId, materialName);
      return;
    }
    state.historyMaterialId = "";
    state.historyMaterialName = "";
    renderMovements();
    loadMovements().catch(fail);
  }

  function loadRequestDetail(id, mode) {
    return api("/requests/" + encodeURIComponent(id)).then(function (body) {
      requestDetails[String(id)] = { mode: mode, request: body.request || {} };
      renderRequests();
    }).catch(fail);
  }

  function transitionRequest(id, action, button, extra) {
    var status = transitionStatuses[action];
    if (!status) return;
    var comment = "";
    if (action === "reject") comment = window.prompt("Причина отклонения:", "") || "";
    var payload = Object.assign({ status: status, comment: comment }, extra || {});
    setBusy(button, true);
    post("/requests/" + encodeURIComponent(id), payload, "PATCH").then(function () {
      delete requestDetails[String(id)];
      return reload("Статус заявки обновлён");
    }).catch(fail).then(function () { setBusy(button, false); });
  }

  function submitTransit(form) {
    var days = readEtaDays(form);
    if (!days) return toast("Выберите срок поставки");
    var id = form.dataset.transitForm;
    var button = form.querySelector('[type="submit"]');
    transitionRequest(id, "mark_in_transit", button, { expected_delivery_days: days });
  }

  function updateRequestEta(form) {
    var days = readEtaDays(form);
    if (!days) return toast("Выберите срок поставки");
    var id = form.dataset.etaForm;
    var button = form.querySelector('[type="submit"]');
    setBusy(button, true);
    post("/requests/" + encodeURIComponent(id) + "/eta", {
      expected_delivery_days: days
    }, "PATCH").then(function () {
      delete requestDetails[String(id)];
      return reload("Срок поставки обновлён");
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
    var days = readEtaDays(form);
    if (!days) return toast("Выберите срок поставки");
    var id = form.dataset.deliveryForm;
    var button = form.querySelector('[type="submit"]');
    setBusy(button, true);
    post("/requests/" + encodeURIComponent(id) + "/deliveries", {
      supplier_id: Number(form.elements.supplier_id.value),
      items: items,
      note: form.elements.note.value.trim(),
      expected_delivery_days: days
    }).then(function () {
      delete requestDetails[String(id)];
      return reload("Заказ у поставщика оформлен");
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
      return reload("Поставка оформлена");
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

  function savePaymentPricing(receiptId, button) {
    var card = button.closest(".sm-history-card");
    if (!card) return;
    var items = Array.prototype.map.call(card.querySelectorAll("[data-pay-price]"), function (input) {
      var lineId = input.dataset.payPrice;
      var qtyInput = card.querySelector('[data-pay-qty="' + lineId + '"]');
      var unitInput = card.querySelector('[data-pay-unit="' + lineId + '"]');
      return {
        id: Number(lineId),
        billing_quantity: positive(qtyInput && qtyInput.value),
        billing_unit: unitInput ? unitInput.value.trim() : "",
        unit_price: positive(input.value)
      };
    }).filter(function (item) { return item.unit_price; });
    if (!items.length) return toast("Укажите цены хотя бы по одной позиции");
    setBusy(button, true);
    post("/receipts/" + encodeURIComponent(receiptId) + "/pricing", { items: items }, "PATCH")
      .then(function () { return reload("Цены сохранены"); })
      .catch(fail)
      .then(function () { setBusy(button, false); });
  }

  function sendPaymentToManager(receiptId, button) {
    if (!window.confirm("Отправить ведомость с суммами руководителю в личку MAX?")) return;
    setBusy(button, true);
    post("/receipts/" + encodeURIComponent(receiptId) + "/send-manager", {})
      .then(function () { return reload("Ведомость отправлена руководителю"); })
      .catch(fail)
      .then(function () { setBusy(button, false); });
  }

  function saveRoleAssign(form) {
    var maxId = positive($("roleMaxId").value);
    if (!maxId) return toast("Укажите MAX id");
    var siteValue = $("roleSiteSelect").value;
    var payload = {
      max_id: maxId,
      role: $("roleSelect").value,
      active: $("roleActive").checked
    };
    if (siteValue) payload.site_ids = [Number(siteValue)];
    setBusy(form.querySelector("button[type=submit]"), true);
    post("/admin/roles", payload).then(function () {
      $("roleMaxId").value = "";
      return loadBootstrap();
    }).then(function () {
      toast("Роль сохранена");
    }).catch(function (error) {
      fail(error, "Не удалось сохранить роль");
    }).finally(function () {
      setBusy(form.querySelector("button[type=submit]"), false);
    });
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
        state.historyMaterialId = "";
        state.historyMaterialName = "";
        state.materialCardId = "";
        state.materialCardShowRequest = false;
        state.historyReturnTo = "";
        state.historyReturnMaterialId = "";
        state.historyReturnMaterialName = "";
        movements = [];
        requestDetails = {};
        loadBootstrap().catch(fail);
      } else if (target.dataset.summaryFilter) {
        state.tab = "stock";
        state.stockTab = "materials";
        state.stockFilter = target.dataset.summaryFilter;
        render();
      } else if (target.dataset.stockTab) {
        if (state.materialCardId) closeMaterialCard();
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
      } else if (target.dataset.editRole) {
        $("roleMaxId").value = target.dataset.editRole;
        $("roleSelect").value = target.dataset.editRoleName || "master";
        if ($("rolesAdminSettings")) {
          $("rolesAdminSettings").scrollIntoView({ behavior: "smooth", block: "start" });
        }
      } else if (target.dataset.savePricing) {
        savePaymentPricing(target.dataset.savePricing, target);
      } else if (target.dataset.sendManager) {
        sendPaymentToManager(target.dataset.sendManager, target);
      } else if (target.dataset.materialUrgency) {
        state.materialCardUrgency = target.dataset.materialUrgency;
        Array.prototype.forEach.call(document.querySelectorAll("[data-material-urgency]"), function (button) {
          button.classList.toggle("is-active", button === target);
        });
      } else if (target.dataset.openMaterial) {
        openMaterialCard(target.dataset.openMaterial, target.dataset.openMaterialName || "");
      } else if (target.id === "materialCardBackBtn" || target.closest("#materialCardBackBtn")) {
        closeMaterialCard();
      } else if (target.id === "materialCardRequestBtn" || target.closest("#materialCardRequestBtn")) {
        openMaterialCardRequestForm();
      } else if (target.id === "materialCardCancelRequestBtn" || target.closest("#materialCardCancelRequestBtn")) {
        cancelMaterialCardRequestForm();
      } else if (target.id === "materialCardFullHistoryBtn" || target.closest("#materialCardFullHistoryBtn")) {
        openMaterialCardFullHistory();
      } else if (target.dataset.editReceipt) {
        beginReceiptEdit(target.dataset.editReceipt).catch(fail);
      } else if (target.dataset.historyMaterial) {
        state.historyReturnTo = "";
        state.historyReturnMaterialId = "";
        state.historyReturnMaterialName = "";
        openMaterialHistory(target.dataset.historyMaterial, target.dataset.historyMaterialName || "");
      } else if (target.id === "movementsBackBtn" || target.closest("#movementsBackBtn")) {
        closeMaterialHistory();
      } else if (target.dataset.requestHistory) {
        loadRequestDetail(target.dataset.requestHistory, "history");
      } else if (target.dataset.etaDays || target.dataset.etaCustom != null) {
        activateEtaChip(target);
      } else if (target.dataset.requestEta) {
        loadRequestDetail(target.dataset.requestEta, "eta_info");
      } else if (target.dataset.requestEtaEdit) {
        loadRequestDetail(target.dataset.requestEtaEdit, "eta_edit");
      } else if (target.dataset.requestAction) {
        var action = target.dataset.requestAction;
        if (action === "create_delivery" || action === "receive_delivery") {
          loadRequestDetail(target.dataset.requestId, action === "create_delivery" ? "delivery" : "receive");
        } else if (action === "mark_in_transit") {
          loadRequestDetail(target.dataset.requestId, "transit");
        } else {
          transitionRequest(target.dataset.requestId, action, target);
        }
      }
    });
    document.addEventListener("submit", function (event) {
      event.preventDefault();
      var form = event.target;
      if (form.id === "requestCreateForm") saveRequest(form);
      else if (form.id === "materialCardRequestForm") saveMaterialCardRequest(form);
      else if (form.id === "transferForm") saveTransfer(form);
      else if (form.id === "adjustmentForm") saveAdjustment(form);
      else if (form.id === "roleAssignForm") saveRoleAssign(form);
      else if (form.dataset.adminMaterial) saveAdminMaterial(form);
      else if (form.dataset.minimumMaterial) saveMinimum(form);
      else if (form.dataset.deliveryForm) createDelivery(form);
      else if (form.dataset.receiveForm) receiveDelivery(form);
      else if (form.dataset.transitForm) submitTransit(form);
      else if (form.dataset.etaForm) updateRequestEta(form);
    });
    $("supplierSelect").addEventListener("change", function () { state.supplierId = this.value; });
    $("receiptMaterialSelect").addEventListener("change", function () { state.receiptMaterialId = this.value; });
    $("issueMaterialSelect").addEventListener("change", function () { state.issueMaterialId = this.value; });
    $("requestMaterialSelect").addEventListener("change", function () { state.requestMaterialId = this.value; });
    $("saveReceiptBtn").addEventListener("click", saveReceipt);
    $("cancelReceiptEditBtn").addEventListener("click", function () {
      resetReceiptForm();
      render();
      toast("Правка отменена");
    });
    $("addReceiptItemBtn").addEventListener("click", addReceiptItem);
    $("saveIssueBtn").addEventListener("click", saveIssue);
    $("supplierCreateBtn").addEventListener("click", createSupplier);
    $("materialCreateBtn").addEventListener("click", createMaterial);
    $("addRequestItemBtn").addEventListener("click", addRequestItem);
    $("refreshMovementsBtn").addEventListener("click", loadMovements);
    if ($("materialCardFullHistoryBtn")) {
      $("materialCardFullHistoryBtn").addEventListener("click", openMaterialCardFullHistory);
    }
    if ($("materialCardCancelRequestBtn")) {
      $("materialCardCancelRequestBtn").addEventListener("click", cancelMaterialCardRequestForm);
    }
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
