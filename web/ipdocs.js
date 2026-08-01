(function () {
  var webApp = window.WebApp || null;
  var profile = null;
  var documents = [];
  var activeDocId = null;
  var selectedDocType = "invoice";
  var selectedTariff = "shoulder";
  var amountManual = false;

  var pageTitle = document.getElementById("pageTitle");
  var pageSub = document.getElementById("pageSub");
  var tabs = document.getElementById("tabs");
  var guestPanel = document.getElementById("guestPanel");
  var guestText = document.getElementById("guestText");
  var panelHome = document.getElementById("panelHome");
  var panelCreate = document.getElementById("panelCreate");
  var panelArchive = document.getElementById("panelArchive");
  var toolbar = document.getElementById("toolbar");
  var reqWarn = document.getElementById("reqWarn");
  var customerName = document.getElementById("customerName");
  var statCount = document.getElementById("statCount");
  var statSum = document.getElementById("statSum");
  var docTypeChips = document.getElementById("docTypeChips");
  var tariffChips = document.getElementById("tariffChips");
  var tariffHint = document.getElementById("tariffHint");
  var qtyField = document.getElementById("qtyField");
  var priceField = document.getElementById("priceField");
  var qtyLabel = document.getElementById("qtyLabel");
  var docDate = document.getElementById("docDate");
  var docQty = document.getElementById("docQty");
  var docPrice = document.getElementById("docPrice");
  var docAmount = document.getElementById("docAmount");
  var amountPreview = document.getElementById("amountPreview");
  var createForm = document.getElementById("createForm");
  var archiveList = document.getElementById("archiveList");
  var archiveEmpty = document.getElementById("archiveEmpty");
  var previewSheet = document.getElementById("previewSheet");
  var previewBackdrop = document.getElementById("previewBackdrop");
  var previewTitle = document.getElementById("previewTitle");
  var previewFrame = document.getElementById("previewFrame");
  var previewClose = document.getElementById("previewClose");
  var previewPrint = document.getElementById("previewPrint");
  var statusChips = document.getElementById("statusChips");
  var infoBtn = document.getElementById("infoBtn");
  var infoPanel = document.getElementById("infoPanel");
  var toast = document.getElementById("toast");

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

  function syncInitData() {
    if (webApp && webApp.initData) return webApp.initData;
    return readInitDataFromUrl();
  }

  function apiHeaders() {
    var initData = syncInitData();
    return initData ? { "X-Max-Init-Data": initData } : {};
  }

  function todayLabel() {
    var d = new Date();
    var dd = String(d.getDate()).padStart(2, "0");
    var mm = String(d.getMonth() + 1).padStart(2, "0");
    return dd + "." + mm + "." + d.getFullYear();
  }

  function parseNum(value) {
    if (value == null) return 0;
    var s = String(value).replace(/\s/g, "").replace(",", ".");
    var n = parseFloat(s);
    return isNaN(n) ? 0 : n;
  }

  function formatMoney(n) {
    return n.toFixed(2).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function showToast(text) {
    toast.textContent = text;
    toast.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(function () {
      toast.hidden = true;
    }, 2600);
  }

  function setTab(name) {
    document.querySelectorAll(".ip-tab").forEach(function (btn) {
      btn.classList.toggle("ip-tab--active", btn.getAttribute("data-tab") === name);
    });
    panelHome.classList.toggle("ip-panel--active", name === "home");
    panelCreate.classList.toggle("ip-panel--active", name === "create");
    panelArchive.classList.toggle("ip-panel--active", name === "archive");
    if (name === "archive") loadArchive();
  }

  function renderDocTypeChips(items) {
    renderChips(
      docTypeChips,
      items || [
        { id: "invoice", label: "Счёт" },
        { id: "act", label: "Акт" },
      ],
      selectedDocType,
      function (id) {
        selectedDocType = id;
        renderDocTypeChips(items);
      }
    );
  }

  function renderTariffChips(items) {
    renderChips(
      tariffChips,
      items || [],
      selectedTariff,
      function (id) {
        selectedTariff = id;
        renderTariffChips(items);
        updateTariffUi();
      }
    );
  }

  function renderChips(container, items, selected, onPick) {
    container.innerHTML = "";
    items.forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ip-chip" + (item.id === selected ? " ip-chip--active" : "");
      btn.textContent = item.label;
      btn.addEventListener("click", function () {
        onPick(item.id);
      });
      container.appendChild(btn);
    });
  }

  function currentTariff() {
    var list = (profile && profile.tariffs) || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === selectedTariff) return list[i];
    }
    return { id: "manual", label: "Вручную", unit: "", hint: "" };
  }

  function updateTariffUi() {
    var t = currentTariff();
    tariffHint.textContent = t.hint || "";
    var manual = t.id === "manual";
    qtyField.hidden = manual;
    priceField.hidden = manual;
    if (!manual) {
      qtyLabel.textContent = t.id === "shoulder" ? "Количество (рейсов)" : "Количество (" + (t.unit || "") + ")";
      if (t.id === "shoulder" && !docQty.value) docQty.value = "1";
    }
    recalcAmount();
  }

  function recalcAmount() {
    var t = currentTariff();
    var qty = parseNum(docQty.value);
    var price = parseNum(docPrice.value);
    var total = 0;
    if (t.id === "manual") {
      total = parseNum(docAmount.value);
      amountManual = true;
    } else {
      amountManual = false;
      total = qty * price;
      docAmount.value = total > 0 ? String(total).replace(".", ",") : "";
    }
    amountPreview.textContent = total > 0 ? "Итого: " + formatMoney(total) + " ₽" : "";
  }

  function applyProfile(data) {
    profile = data;
    if (!data.allowed || data.role === "admin") {
      guestPanel.hidden = false;
      tabs.hidden = true;
      toolbar.hidden = true;
      panelHome.hidden = true;
      panelCreate.hidden = true;
      panelArchive.hidden = true;
      guestText.textContent =
        data.message ||
        "Доступ только для ИП Кудрук и ИП Патели. Откройте под своей учётной записью в MAX.";
      pageSub.textContent = "Мини-приложение для перевозчиков Омега-М";
      return;
    }

    guestPanel.hidden = true;
    tabs.hidden = false;
    toolbar.hidden = false;
    panelHome.hidden = false;
    panelCreate.hidden = false;
    panelArchive.hidden = false;

    var c = data.contractor || {};
    pageTitle.textContent = c.short_name || "Счёт и акт";
    pageSub.textContent = "Документы для " + ((data.customer && data.customer.name) || "ООО «Омега-М»");
    customerName.textContent = (data.customer && data.customer.name) || "ООО «Омега-М»";

    var ready = (c.requisites_ready && data.customer && data.customer.requisites_ready);
    reqWarn.hidden = !!ready;

    var stats = data.stats || {};
    statCount.textContent = String(stats.documents_count || 0);
    statSum.textContent = (stats.month_amount_label || "0,00") + " ₽";

    renderDocTypeChips(data.doc_types);
    renderTariffChips(data.tariffs);
    updateTariffUi();
  }

  function loadProfile() {
    return fetch("/api/ipdocs/profile", { headers: apiHeaders() })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "profile error");
          return body;
        });
      })
      .then(function (data) {
        applyProfile(data);
        return data;
      });
  }

  function loadArchive() {
    return fetch("/api/ipdocs/documents", { headers: apiHeaders() })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "archive error");
          return body;
        });
      })
      .then(function (data) {
        documents = data.documents || [];
        renderArchive();
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка архива");
      });
  }

  function badgeClass(status) {
    if (status === "sent") return "ip-badge ip-badge--sent";
    if (status === "paid") return "ip-badge ip-badge--paid";
    return "ip-badge";
  }

  function renderArchive() {
    archiveList.innerHTML = "";
    archiveEmpty.hidden = documents.length > 0;
    documents.forEach(function (doc) {
      var li = document.createElement("li");
      li.className = "ip-doc";
      li.innerHTML =
        '<div class="ip-doc-top">' +
        '<span class="ip-doc-type">' +
        (doc.doc_type_label || "") +
        " " +
        (doc.number_label || "") +
        "</span>" +
        '<span class="ip-doc-sum">' +
        (doc.amount_label || "") +
        " ₽</span>" +
        "</div>" +
        '<p class="ip-doc-sub">' +
        (doc.date || "") +
        " · " +
        (doc.route || "") +
        "</p>" +
        '<span class="' +
        badgeClass(doc.status) +
        '">' +
        (doc.status_label || "") +
        "</span>";
      li.addEventListener("click", function () {
        openPreview(doc.id, doc);
      });
      archiveList.appendChild(li);
    });
  }

  function openPreview(docId, docMeta) {
    activeDocId = docId;
    previewTitle.textContent =
      (docMeta.doc_type_label || "Документ") + " " + (docMeta.number_label || "");
    previewSheet.hidden = false;
    previewFrame.srcdoc = "<p style='padding:16px;font-family:sans-serif'>Загрузка…</p>";
    fetch("/api/ipdocs/documents/" + encodeURIComponent(docId) + "/html", {
      headers: apiHeaders(),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("preview failed");
        return r.text();
      })
      .then(function (html) {
        previewFrame.srcdoc = html;
      })
      .catch(function () {
        previewFrame.srcdoc = "<p style='padding:16px;color:#c00'>Не удалось загрузить превью</p>";
      });

    statusChips.innerHTML = "";
    (profile.statuses || []).forEach(function (st) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "ip-chip" + (docMeta.status === st.id ? " ip-chip--active" : "");
      btn.textContent = st.label;
      btn.addEventListener("click", function () {
        setStatus(docId, st.id);
      });
      statusChips.appendChild(btn);
    });
  }

  function setStatus(docId, status) {
    fetch("/api/ipdocs/documents/" + encodeURIComponent(docId) + "/status", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, apiHeaders()),
      body: JSON.stringify({ status: status }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "status error");
          return body;
        });
      })
      .then(function (data) {
        showToast(data.notification || "Готово");
        loadArchive();
        if (data.document) openPreview(docId, data.document);
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка");
      });
  }

  function submitDocument(ev) {
    ev.preventDefault();
    var payload = {
      doc_type: selectedDocType,
      date: docDate.value.trim(),
      period: document.getElementById("docPeriod").value.trim(),
      route: document.getElementById("docRoute").value.trim(),
      tariff_id: selectedTariff,
      quantity: parseNum(docQty.value),
      unit_price: parseNum(docPrice.value),
      amount: parseNum(docAmount.value),
      note: document.getElementById("docNote").value.trim(),
      status: "draft",
    };
    fetch("/api/ipdocs/documents", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, apiHeaders()),
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "create error");
          return body;
        });
      })
      .then(function (data) {
        showToast(data.notification || "Сохранено");
        createForm.reset();
        docDate.value = todayLabel();
        selectedTariff = "shoulder";
        if (profile) applyProfile(profile);
        loadProfile().then(loadArchive);
        setTab("archive");
        if (data.document) openPreview(data.document.id, data.document);
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка");
      });
  }

  document.querySelectorAll(".ip-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTab(btn.getAttribute("data-tab"));
    });
  });

  document.querySelectorAll("[data-quick]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      selectedDocType = btn.getAttribute("data-quick");
      if (profile) applyProfile(profile);
      setTab("create");
    });
  });

  [docQty, docPrice, docAmount].forEach(function (el) {
    el.addEventListener("input", function () {
      if (el === docAmount && currentTariff().id === "manual") amountManual = true;
      recalcAmount();
    });
  });

  createForm.addEventListener("submit", submitDocument);

  previewClose.addEventListener("click", function () {
    previewSheet.hidden = true;
    activeDocId = null;
  });
  previewBackdrop.addEventListener("click", function () {
    previewSheet.hidden = true;
  });
  previewPrint.addEventListener("click", function () {
    try {
      previewFrame.contentWindow.focus();
      previewFrame.contentWindow.print();
    } catch (e) {
      showToast("Печать недоступна — откройте превью ещё раз");
    }
  });

  if (infoBtn && infoPanel) {
    infoBtn.addEventListener("click", function () {
      var open = infoBtn.getAttribute("aria-expanded") === "true";
      infoBtn.setAttribute("aria-expanded", open ? "false" : "true");
      infoPanel.hidden = open;
    });
  }

  if (typeof installPanelFeedback === "function") {
    installPanelFeedback({
      app: "ipdocs",
      getHeaders: apiHeaders,
      showToast: showToast,
    });
  }

  function boot() {
    if (webApp) {
      try {
        if (webApp.ready) webApp.ready();
        if (webApp.expand) webApp.expand();
      } catch (e) {}
    }
    docDate.value = todayLabel();
    if (!syncInitData()) {
      guestPanel.hidden = false;
      guestText.textContent = "Откройте мини-приложение из MAX.";
      pageSub.textContent = "";
      return;
    }
    loadProfile()
      .then(function () {
        return loadArchive();
      })
      .catch(function (err) {
        guestPanel.hidden = false;
        guestText.textContent = err.message || "Ошибка загрузки";
      });
  }

  setTimeout(boot, webApp && webApp.initData ? 0 : 280);
})();
