(function () {
  var webApp = window.WebApp || null;
  var initData = "";
  var busy = false;
  var registry = null;
  var mode = "load";
  var blockCount = 3;
  var blockLetters = ["A", "B", "C", "D", "E", "F", "K"];
  var lookupTimer = null;
  var suggestedMode = "load";

  var siteLabel = document.getElementById("siteLabel");
  var plateTail = document.getElementById("plateTail");
  var loadedBtn = document.getElementById("loadedBtn");
  var docsBtn = document.getElementById("docsBtn");
  var outsideMax = document.getElementById("outsideMax");
  var toast = document.getElementById("toast");
  var infoBtn = document.getElementById("infoBtn");
  var infoPanel = document.getElementById("infoPanel");
  var modeHint = document.getElementById("modeHint");
  var loadSection = document.getElementById("loadSection");
  var docsSection = document.getElementById("docsSection");
  var driverCard = document.getElementById("driverCard");
  var driverName = document.getElementById("driverName");
  var driverPlate = document.getElementById("driverPlate");
  var driverMiss = document.getElementById("driverMiss");
  var blockCountBtns = document.getElementById("blockCountBtns");
  var blocksForm = document.getElementById("blocksForm");
  var activeSection = document.getElementById("activeSection");
  var activeList = document.getElementById("activeList");
  var docsQueueSection = document.getElementById("docsQueueSection");
  var docsQueueList = document.getElementById("docsQueueList");

  function apiHeaders() {
    var h = { "Content-Type": "application/json" };
    if (initData) h["X-Max-Init-Data"] = initData;
    return h;
  }

  function setupBridge() {
    if (!webApp) return false;
    initData = webApp.initData || "";
    try {
      if (webApp.ready) webApp.ready();
      if (webApp.expand) webApp.expand();
    } catch (e) {}
    return Boolean(initData);
  }

  function showToast(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    toast.classList.add("rmx-toast--show");
    setTimeout(function () {
      toast.classList.remove("rmx-toast--show");
      setTimeout(function () {
        toast.hidden = true;
      }, 200);
    }, 2400);
  }

  function normalizeTail(value) {
    return String(value || "").replace(/\D/g, "").slice(-6);
  }

  function setMode(next) {
    mode = next === "docs" ? "docs" : "load";
    document.querySelectorAll(".rmx-mode-tab").forEach(function (btn) {
      var active = btn.getAttribute("data-mode") === mode;
      btn.classList.toggle("rmx-mode-tab--active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    loadSection.hidden = mode !== "load";
    docsSection.hidden = mode !== "docs";
    modeHint.textContent =
      mode === "docs"
        ? "Как раньше: хвост и «Выдала документы». Блоки не нужны."
        : "ТТН на бумаге. Блоки — здесь. Расписку оформляет бухгалтер.";
  }

  function renderBlockCountBtns(counts) {
    blockCountBtns.innerHTML = "";
    (counts || [3, 4, 5, 6]).forEach(function (n) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "rmx-count-btn" + (n === blockCount ? " rmx-count-btn--active" : "");
      btn.textContent = String(n);
      btn.addEventListener("click", function () {
        blockCount = n;
        renderBlockCountBtns(counts);
        renderBlocksForm();
      });
      blockCountBtns.appendChild(btn);
    });
  }

  function renderBlocksForm() {
    blocksForm.innerHTML = "";
    for (var i = 0; i < blockCount; i++) {
      var row = document.createElement("div");
      row.className = "rmx-block-row";
      var select = document.createElement("select");
      select.className = "rmx-block-letter";
      select.dataset.index = String(i);
      blockLetters.forEach(function (letter) {
        var opt = document.createElement("option");
        opt.value = letter;
        opt.textContent = letter;
        select.appendChild(opt);
      });
      select.value = blockLetters[Math.min(i, blockLetters.length - 1)] || "A";
      var input = document.createElement("input");
      input.type = "text";
      input.inputMode = "numeric";
      input.className = "rmx-block-number";
      input.placeholder = "номер";
      input.dataset.index = String(i);
      input.autocomplete = "off";
      row.appendChild(select);
      row.appendChild(input);
      blocksForm.appendChild(row);
    }
  }

  function collectBlocks() {
    var blocks = [];
    blocksForm.querySelectorAll(".rmx-block-row").forEach(function (row) {
      var letter = row.querySelector(".rmx-block-letter");
      var number = row.querySelector(".rmx-block-number");
      blocks.push({
        letter: letter ? letter.value : "",
        number: number ? number.value.trim() : "",
      });
    });
    return blocks;
  }

  function fillBlocksFromLoading(loading) {
    if (!loading || !loading.blocks || !loading.blocks.length) return;
    blockCount = loading.block_count || loading.blocks.length;
    renderBlockCountBtns(registry && registry.block_counts);
    renderBlocksForm();
    loading.blocks.forEach(function (block, idx) {
      var row = blocksForm.children[idx];
      if (!row) return;
      var letter = row.querySelector(".rmx-block-letter");
      var number = row.querySelector(".rmx-block-number");
      if (letter) letter.value = block.letter;
      if (number) number.value = block.number;
    });
  }

  function lookupTail(tail) {
    if (!tail) {
      driverCard.hidden = true;
      driverMiss.hidden = true;
      return;
    }
    fetch("/api/rumex/lookup?tail=" + encodeURIComponent(tail), { headers: apiHeaders() })
      .then(function (r) {
        return r.json();
      })
      .then(function (body) {
        if (!body.found) {
          driverCard.hidden = true;
          driverMiss.hidden = false;
          return;
        }
        driverMiss.hidden = true;
        driverCard.hidden = false;
        driverName.textContent = body.driver.name || "";
        driverPlate.textContent = body.driver.plate || body.driver.vehicle || "";
        suggestedMode = body.suggested_mode || "load";
        if (document.querySelector('.rmx-mode-tab[data-mode="' + suggestedMode + '"]')) {
          setMode(suggestedMode);
        }
      })
      .catch(function () {});
  }

  function renderActive(loadings) {
    var data = loadings || {};
    var active = data.active || [];
    var kontur = (data.in_kontur || []).slice();
    var awaiting = (data.awaiting_receipt || []).slice();
    var cards = kontur.concat(awaiting);

    if (!cards.length) {
      activeSection.hidden = true;
      activeList.innerHTML = "";
      return;
    }
    activeSection.hidden = false;
    activeList.innerHTML = "";
    cards.forEach(function (item) {
      var li = document.createElement("li");
      li.className = "rmx-card";
      var statusClass =
        item.status === "in_kontur" ? "rmx-card-status--kontur" : "rmx-card-status--wait";
      var actionBtn =
        item.status === "in_kontur"
          ? '<button type="button" class="rmx-btn rmx-btn--docs rmx-btn--card" data-release="' +
            item.plate_tail +
            '">✓ Разрешаю выезд</button>'
          : "";
      li.innerHTML =
        '<div class="rmx-card-head">' +
        '<p class="rmx-card-title">…' +
        item.plate_tail +
        " · " +
        (item.name || "") +
        "</p>" +
        '<p class="rmx-card-status ' +
        statusClass +
        '">' +
        (item.status_label || "") +
        "</p>" +
        "</div>" +
        '<p class="rmx-card-meta">' +
        (item.blocks_short || "") +
        (item.loaded_at ? " · " + item.loaded_at : "") +
        "</p>" +
        actionBtn;
      activeList.appendChild(li);
      li.addEventListener("click", function (e) {
        var release = e.target && e.target.getAttribute("data-release");
        if (release) {
          e.stopPropagation();
          postRelease(release);
          return;
        }
        if (plateTail) {
          plateTail.value = item.plate_tail;
          setMode("load");
          lookupTail(item.plate_tail);
          fillBlocksFromLoading(item);
        }
      });
    });
  }

  function renderDocsQueue(queue) {
    var rows = queue || [];
    if (!rows.length) {
      docsQueueSection.hidden = true;
      docsQueueList.innerHTML = "";
      return;
    }
    docsQueueSection.hidden = false;
    docsQueueList.innerHTML = "";
    rows.forEach(function (item) {
      var li = document.createElement("li");
      li.className = "rmx-card rmx-card--here";
      li.innerHTML =
        '<div class="rmx-card-head">' +
        '<p class="rmx-card-title">…' +
        item.plate_tail +
        " · " +
        (item.name || "") +
        "</p>" +
        '<p class="rmx-card-status">' +
        (item.status_label || "") +
        "</p>" +
        "</div>" +
        '<p class="rmx-card-meta">' +
        (item.detail || "") +
        "</p>";
      li.addEventListener("click", function () {
        if (plateTail) {
          plateTail.value = item.plate_tail;
          setMode("docs");
          lookupTail(item.plate_tail);
        }
      });
      docsQueueList.appendChild(li);
    });
  }

  function applyRegistry(data) {
    registry = data;
    if (data.site_label && siteLabel) siteLabel.textContent = data.site_label;
    if (data.block_letters && data.block_letters.length) blockLetters = data.block_letters;
    renderBlockCountBtns(data.block_counts);
    renderBlocksForm();
    renderActive(data.loadings);
    renderDocsQueue(data.queue);
  }

  function loadProfile() {
    return fetch("/api/rumex/registry", { headers: apiHeaders() })
      .then(function (r) {
        return r.json().then(function (body) {
          if (r.status === 403) {
            outsideMax.hidden = false;
            outsideMax.textContent = "Панель Румекс — только для диспетчера завода.";
            throw new Error("forbidden");
          }
          if (!r.ok) throw new Error(body.error || "profile failed");
          return body;
        });
      })
      .then(function (data) {
        applyRegistry(data);
      });
  }

  function postAction(payload) {
    if (busy) return;
    busy = true;
    if (loadedBtn) loadedBtn.disabled = true;
    if (docsBtn) docsBtn.disabled = true;
    return fetch("/api/rumex/action", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.notification || body.error || "action failed");
          return body;
        });
      })
      .then(function (body) {
        showToast(body.notification || "Готово");
        if (body.registry) applyRegistry(body.registry);
        if (plateTail && payload.action !== "release") plateTail.value = "";
        if (payload.action !== "release") {
          driverCard.hidden = true;
          driverMiss.hidden = true;
        }
      })
      .catch(function (err) {
        showToast(err.message || "Ошибка");
      })
      .finally(function () {
        busy = false;
        if (loadedBtn) loadedBtn.disabled = false;
        if (docsBtn) docsBtn.disabled = false;
      });
  }

  function postLoaded() {
    var tail = normalizeTail(plateTail && plateTail.value);
    if (!tail) {
      showToast("Введите хвост номера");
      if (plateTail) plateTail.focus();
      return;
    }
    postAction({
      action: "loaded",
      plate_tail: tail,
      block_count: blockCount,
      blocks: collectBlocks(),
    });
  }

  function postDocuments() {
    var tail = normalizeTail(plateTail && plateTail.value);
    if (!tail) {
      showToast("Введите хвост номера");
      if (plateTail) plateTail.focus();
      return;
    }
    postAction({ action: "documents", plate_tail: tail });
  }

  function postRelease(tail) {
    postAction({ action: "release", plate_tail: normalizeTail(tail) });
  }

  document.querySelectorAll(".rmx-mode-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setMode(btn.getAttribute("data-mode"));
    });
  });

  if (loadedBtn) loadedBtn.addEventListener("click", postLoaded);
  if (docsBtn) docsBtn.addEventListener("click", postDocuments);

  if (plateTail) {
    plateTail.addEventListener("input", function () {
      var tail = normalizeTail(plateTail.value);
      clearTimeout(lookupTimer);
      lookupTimer = setTimeout(function () {
        lookupTail(tail);
      }, 280);
    });
    plateTail.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        if (mode === "docs") postDocuments();
        else postLoaded();
      }
    });
  }

  if (infoBtn && infoPanel) {
    infoBtn.addEventListener("click", function () {
      var open = infoBtn.getAttribute("aria-expanded") === "true";
      infoBtn.setAttribute("aria-expanded", open ? "false" : "true");
      infoPanel.hidden = open;
    });
  }

  function boot() {
    if (typeof installPanelFeedback === "function") {
      installPanelFeedback({
        app: "rumex",
        getHeaders: apiHeaders,
        showToast: showToast,
      });
    }
    renderBlockCountBtns([3, 4, 5, 6]);
    renderBlocksForm();
    setMode("load");

    if (!setupBridge()) {
      setTimeout(function () {
        if (!setupBridge()) {
          outsideMax.hidden = false;
          return;
        }
        loadProfile().catch(function () {});
      }, 400);
      return;
    }
    loadProfile().catch(function () {});
  }

  boot();
})();
