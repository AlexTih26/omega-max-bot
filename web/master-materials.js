(function () {
  function $(id) {
    return document.getElementById(id);
  }

  function apiFetch(url, options) {
    options = options || {};
    options.credentials = "same-origin";
    return fetch(url, options).then(function (r) {
      if (r.status === 401) {
        location.href = "/taksimo-login.html?next=" + encodeURIComponent(location.pathname);
        return Promise.reject(new Error("auth"));
      }
      return r;
    });
  }

  function parseApiResponse(r) {
    return r.text().then(function (text) {
      var data = {};
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (e) {
          data = { error: text || ("HTTP " + r.status) };
        }
      }
      return { ok: r.ok, status: r.status, data: data };
    });
  }

  function showError(err, fallback) {
    if (!err || err.message === "auth") return;
    toast((err && err.message) || fallback || "Ошибка");
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function toast(msg) {
    var el = $("toast");
    el.textContent = msg;
    el.hidden = false;
    el.classList.add("tk-toast--show");
    setTimeout(function () {
      el.classList.remove("tk-toast--show");
      setTimeout(function () { el.hidden = true; }, 200);
    }, 2400);
  }

  function formatQty(value) {
    var num = Number(value || 0);
    if (!isFinite(num)) return "0";
    return Math.abs(num - Math.round(num)) < 0.001
      ? String(Math.round(num))
      : num.toFixed(3).replace(/\.?0+$/, "");
  }

  var units = ["шт", "кг", "м", "пачка", "рулон"];
  var dashboard = null;
  var activeWagon = "";
  var canManageMaterials = false;

  function renderDbStatus(db) {
    var el = $("dbStatus");
    if (!el) return;
    var ts = db && db.last_change_ts ? new Date(db.last_change_ts * 1000).toLocaleString("ru-RU") : "—";
    el.textContent = "База обновлена: " + ts;
  }

  function setManagementVisibility() {
    [
      "templateCreateCard",
      "templateItemCard",
      "prepAssignCard",
      "materialCreateCard",
      "materialReserveCard"
    ].forEach(function (id) {
      var el = $(id);
      if (el) el.hidden = !canManageMaterials;
    });
    var note = $("materialsReadonlyNote");
    if (note) note.hidden = canManageMaterials;
  }

  function unitOptions(selected) {
    var all = units.slice();
    if (selected && all.indexOf(selected) < 0) all.push(selected);
    return all.map(function (unit) {
      return '<option value="' + esc(unit) + '"' + (unit === selected ? " selected" : "") + ">" + esc(unit) + "</option>";
    }).join("");
  }

  function renderSummary(data) {
    var box = $("materialSummary");
    var summary = (data && data.summary) || {};
    box.innerHTML =
      '<div class="tk-material-metric"><b>' + esc(summary.material_count || 0) + '</b><span>материалов</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.template_count || 0) + '</b><span>схем крепления</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.low_stock_count || 0) + '</b><span>ниже минимума</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.ready_wagons || 0) + '</b><span>готовых вагонов</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.overall_wagons_left != null ? summary.overall_wagons_left : "—") + '</b><span>хватит ещё на вагонов</span></div>';
  }

  function refreshSelectors() {
    $("materialUnit").innerHTML = unitOptions(($("materialUnit").value || "шт"));
    var materials = (dashboard && dashboard.materials) || [];
    var wagons = (dashboard && dashboard.wagons) || [];
    var templates = (dashboard && dashboard.templates) || [];
    $("materialReserveMaterial").innerHTML =
      '<option value="">— выберите материал —</option>' +
      materials.map(function (item) {
        return '<option value="' + item.id + '">' + esc(item.name) + " · свободно " + esc(formatQty(item.available)) + " " + esc(item.unit) + '</option>';
      }).join("");
    $("materialReserveWagon").innerHTML =
      '<option value="">— выберите вагон —</option>' +
      wagons.map(function (wagon) {
        return '<option value="' + esc(wagon.number) + '">' + esc(wagon.number + " · " + (wagon.stage_label || "")) + '</option>';
      }).join("");
    $("templateMaterial").innerHTML =
      '<option value="">— выберите материал —</option>' +
      materials.map(function (item) {
        return '<option value="' + item.id + '">' + esc(item.name) + '</option>';
      }).join("");
    var templateOptions =
      '<option value="">— выберите схему —</option>' +
      templates.map(function (item) {
        return '<option value="' + item.id + '">' + esc(item.name) + '</option>';
      }).join("");
    $("templatePick").innerHTML = templateOptions;
    $("prepTemplate").innerHTML = templateOptions;
    $("prepWagon").innerHTML =
      '<option value="">— выберите вагон —</option>' +
      wagons.map(function (wagon) {
        return '<option value="' + esc(wagon.number) + '">' + esc(wagon.number) + '</option>';
      }).join("");
  }

  function renderTemplates(items) {
    var box = $("templateList");
    box.innerHTML = "";
    if (!(items || []).length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>Схем пока нет.</div></div>";
      return;
    }
    items.forEach(function (item) {
      var card = document.createElement("div");
      card.className = "tk-card";
      card.innerHTML =
        "<div class='tk-card-title'>" + esc(item.name) + "</div>" +
        "<div class='tk-card-meta'>" +
        esc(item.description || "—") +
        " · строк: " + esc(item.item_count || 0) +
        " · норма: " + esc(formatQty(item.total_norm_minutes || 0)) + " мин" +
        "</div>";
      box.appendChild(card);
    });
  }

  function renderMaterials(items) {
    var box = $("materialList");
    box.innerHTML = "";
    if (!(items || []).length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>Материалы пока не заведены.</div></div>";
      return;
    }
    items.forEach(function (item) {
      var card = document.createElement("div");
      card.className = "tk-card tk-material-card";
      var warnClass = item.low_stock ? " tk-material-low" : "";
      var html =
        '<div class="tk-material-card-top">' +
        '<div class="tk-card-title">' + esc(item.name) + '</div>' +
        '<div class="tk-card-meta' + warnClass + '">' +
        (item.low_stock ? "ниже минимума" : "остаток в норме") +
        '</div></div>' +
        '<div class="tk-material-grid">' +
        '<div class="tk-material-metric"><b>' + esc(formatQty(item.on_hand)) + '</b><span>физически, ' + esc(item.unit) + '</span></div>' +
        '<div class="tk-material-metric"><b>' + esc(formatQty(item.reserved)) + '</b><span>в резерве</span></div>' +
        '<div class="tk-material-metric"><b>' + esc(formatQty(item.available)) + '</b><span>свободно</span></div>' +
        '<div class="tk-material-metric"><b>' + esc(item.available_wagons != null ? item.available_wagons : "—") + '</b><span>вагонов по норме</span></div>' +
        '</div>';
      if (canManageMaterials) {
        html +=
          '<div class="tk-card-meta">Редактирование: исправьте название или единицу и нажмите "Сохранить изменения". Если ошиблись в остатке, используйте "Корректировка" с плюсом или минусом.</div>' +
          '<div class="tk-row2">' +
          '<div><label class="tk-label">Название</label><input class="tk-input" data-field="name" value="' + esc(item.name) + '"></div>' +
          '<div><label class="tk-label">Ед. изм.</label><input class="tk-input" data-field="unit" value="' + esc(item.unit) + '" placeholder="шт, м, м3, кг"></div>' +
          '</div>' +
          '<div class="tk-row2">' +
          '<div><label class="tk-label">Мин. остаток</label><input class="tk-input" data-field="min_level" type="number" min="0" step="0.001" value="' + esc(item.min_level) + '"></div>' +
          '<div><label class="tk-label">Норма на вагон</label><input class="tk-input" data-field="norm_per_wagon" type="number" min="0" step="0.001" value="' + esc(item.norm_per_wagon) + '"></div>' +
          '</div>' +
          '<div class="tk-material-inline-actions">' +
          '<input class="tk-input" data-field="receipt_qty" type="number" min="0" step="0.001" placeholder="Приход: количество">' +
          '<button type="button" class="tk-btn" data-act="receipt">Приход</button>' +
          '<button type="button" class="tk-btn tk-btn--primary" data-act="save">Сохранить изменения</button>' +
          '</div>' +
          '<div class="tk-material-inline-actions">' +
          '<input class="tk-input" data-field="adjust_qty" type="number" step="0.001" placeholder="Корректировка остатка: + / -">' +
          '<input class="tk-input" data-field="adjust_note" placeholder="Причина корректировки">' +
          '<button type="button" class="tk-btn" data-act="adjust">Корректировка</button>' +
          '</div>';
      }
      card.innerHTML = html;
      if (canManageMaterials) {
        card.querySelector('[data-act="save"]').addEventListener("click", function () {
          saveMaterial(item.id, card);
        });
        card.querySelector('[data-act="receipt"]').addEventListener("click", function () {
          addReceipt(item.id, card);
        });
        card.querySelector('[data-act="adjust"]').addEventListener("click", function () {
          adjustMaterial(item.id, card);
        });
      }
      box.appendChild(card);
    });
  }

  function renderWagons(items) {
    var box = $("materialWagonList");
    box.innerHTML = "";
    if (!(items || []).length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>Вагоны для подготовки пока не найдены.</div></div>";
      return;
    }
    items.forEach(function (wagon) {
      var card = document.createElement("div");
      card.className = "tk-card tk-card--click";
      if (wagon.is_ready) card.classList.add("tk-material-wagon-card--ready");
      else if (wagon.shortage_count > 0) card.classList.add("tk-material-wagon-card--warn");
      card.innerHTML =
        '<div class="tk-material-card-top">' +
        '<div class="tk-card-title">Вагон ' + esc(wagon.number) + '</div>' +
        '<div class="tk-card-meta">' + esc(wagon.stage_label || "—") + '</div>' +
        '</div>' +
        '<div class="tk-card-meta">Схема: ' + esc(wagon.template_name || "не назначена") +
        (wagon.norm_minutes ? " · " + esc(formatQty(wagon.norm_minutes)) + " мин" : "") +
        '</div>' +
        '<div class="tk-card-meta">' + esc(wagon.location_label || "—") + '</div>' +
        '<div class="tk-card-meta">Резервов: ' + esc(wagon.reserved_item_count || 0) +
        " · дефицит: " + esc(wagon.shortage_count || 0) +
        (wagon.shortage_names && wagon.shortage_names.length ? " · " + esc(wagon.shortage_names.join(", ")) : "") +
        '</div>';
      card.addEventListener("click", function () {
        loadWagon(wagon.number);
      });
      box.appendChild(card);
    });
  }

  function loadOverview(options) {
    options = options || {};
    return apiFetch("/api/taksimo/materials/overview")
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Не удалось загрузить остатки");
        var data = res.data || {};
        dashboard = data;
        units = data.units || units;
        renderSummary(data);
        renderTemplates(data.templates || []);
        renderMaterials(data.materials || []);
        renderWagons(data.wagons || []);
        refreshSelectors();
        if (activeWagon && !options.skipDetail) return loadWagon(activeWagon, { scroll: false });
      })
      .catch(function (err) {
        showError(err, "Не удалось загрузить остатки");
      });
  }

  function saveMaterial(materialId, root) {
    apiFetch("/api/taksimo/materials/items/" + materialId, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: root.querySelector('[data-field="name"]').value.trim(),
        unit: root.querySelector('[data-field="unit"]').value,
        min_level: root.querySelector('[data-field="min_level"]').value || 0,
        norm_per_wagon: root.querySelector('[data-field="norm_per_wagon"]').value || 0,
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Материал сохранён");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка сохранения"); });
  }

  function addReceipt(materialId, root) {
    var qty = root.querySelector('[data-field="receipt_qty"]').value;
    apiFetch("/api/taksimo/materials/receipt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ material_id: materialId, quantity: qty }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        root.querySelector('[data-field="receipt_qty"]').value = "";
        toast("Приход сохранён");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка прихода"); });
  }

  function adjustMaterial(materialId, root) {
    var qty = root.querySelector('[data-field="adjust_qty"]').value;
    apiFetch("/api/taksimo/materials/adjust", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        material_id: materialId,
        quantity: qty,
        note: root.querySelector('[data-field="adjust_note"]').value.trim(),
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        root.querySelector('[data-field="adjust_qty"]').value = "";
        root.querySelector('[data-field="adjust_note"]').value = "";
        toast("Корректировка сохранена");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка корректировки"); });
  }

  function createMaterial() {
    apiFetch("/api/taksimo/materials/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: $("materialName").value.trim(),
        unit: $("materialUnit").value,
        min_level: $("materialMin").value || 0,
        norm_per_wagon: $("materialNorm").value || 0,
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        $("materialName").value = "";
        $("materialMin").value = "";
        $("materialNorm").value = "";
        $("materialUnit").value = "шт";
        toast("Материал добавлен");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка добавления"); });
  }

  function createTemplate() {
    apiFetch("/api/taksimo/materials/templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: $("templateName").value.trim(),
        description: $("templateDescription").value.trim(),
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        $("templateName").value = "";
        $("templateDescription").value = "";
        toast("Схема создана");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка создания схемы"); });
  }

  function addTemplateItem() {
    var templateId = $("templatePick").value;
    apiFetch("/api/taksimo/materials/templates/" + encodeURIComponent(templateId) + "/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        material_id: $("templateMaterial").value,
        qty_norm: $("templateQty").value,
        work_type: $("templateWorkType").value.trim(),
        feature_text: $("templateFeature").value.trim(),
        tool_text: $("templateTool").value.trim(),
        norm_minutes: $("templateMinutes").value || 0,
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        $("templateQty").value = "";
        $("templateWorkType").value = "";
        $("templateFeature").value = "";
        $("templateTool").value = "";
        $("templateMinutes").value = "";
        toast("Строка схемы добавлена");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка добавления строки"); });
  }

  function assignTemplateToWagon() {
    var wagonNumber = $("prepWagon").value;
    apiFetch("/api/taksimo/materials/preps/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        wagon_number: wagonNumber,
        template_id: $("prepTemplate").value,
        note: $("prepNote").value.trim(),
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        activeWagon = wagonNumber;
        $("prepNote").value = "";
        toast("Схема назначена вагону");
        loadOverview({ skipDetail: true }).then(function () {
          loadWagon(wagonNumber, { scroll: true });
        });
      })
      .catch(function (err) { showError(err, "Ошибка назначения схемы"); });
  }

  function reserveMaterial() {
    var wagonNumber = $("materialReserveWagon").value;
    apiFetch("/api/taksimo/materials/reserve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        wagon_number: wagonNumber,
        material_id: $("materialReserveMaterial").value,
        quantity: $("materialReserveQty").value,
        note: $("materialReserveNote").value.trim(),
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        activeWagon = wagonNumber;
        $("materialReserveQty").value = "";
        $("materialReserveNote").value = "";
        toast("Резерв создан");
        loadOverview({ skipDetail: true }).then(function () {
          loadWagon(wagonNumber, { scroll: true });
        });
      })
      .catch(function (err) { showError(err, "Ошибка резерва"); });
  }

  function loadWagon(wagonNumber, options) {
    options = options || {};
    activeWagon = wagonNumber;
    var box = $("materialWagonDetail");
    box.hidden = false;
    box.innerHTML = "<h4>Вагон " + esc(wagonNumber) + "</h4><p>Загрузка…</p>";
    return apiFetch("/api/taksimo/materials/wagons/" + encodeURIComponent(wagonNumber))
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        var wagon = res.data.wagon;
        var items = wagon.items || [];
        var html =
          "<h4>Вагон " + esc(wagon.wagon_number) + "</h4>" +
          "<p>" + esc((wagon.wagon && wagon.wagon.stage_label) || "—") +
          ((wagon.wagon && wagon.wagon.location_label) ? " · " + esc(wagon.wagon.location_label) : "") +
          (wagon.prep && wagon.prep.template_name ? "<br>Схема: <strong>" + esc(wagon.prep.template_name) + "</strong>" : "") +
          "<br>Резервов: " + esc(wagon.reserved_item_count || 0) +
          " · дефицит: " + esc(wagon.shortage_count || 0) +
          " · норма: " + esc(formatQty(wagon.total_norm_minutes || 0)) + " мин" +
          "<br>Важно: при отправке вагона из тупика зарезервированный комплект спишется автоматически." +
          "</p>";
        if (items.length) {
          html += "<ul class='tk-list'>";
          items.forEach(function (item) {
            html +=
              "<li class='tk-card'>" +
              "<div class='tk-material-card-top'><div class='tk-card-title'>" + esc(item.name) + "</div>" +
              "<div class='tk-card-meta'>норма " + esc(formatQty(item.norm_per_wagon)) + " " + esc(item.unit) + "</div></div>" +
              "<div class='tk-card-meta'>резерв: " + esc(formatQty(item.reserved_qty)) + " · уже ушло: " + esc(formatQty(item.consumed_qty)) + " · дефицит: " + esc(formatQty(item.shortage_qty)) + " " + esc(item.unit) + "</div>" +
              "</li>";
          });
          html += "</ul>";
        } else {
          html += "<p class='tk-card-meta'>По этому вагону ещё нет расходников.</p>";
        }
        if ((wagon.operations || []).length) {
          html += "<h5 class='tk-wagon-card-subtitle'>Операции по схеме</h5><ul class='tk-list'>";
          (wagon.operations || []).forEach(function (op) {
            html +=
              "<li class='tk-card'>" +
              "<div class='tk-card-title'>#" + esc(op.line_no) + " · " + esc(op.material_name) + " · " + esc(op.work_type || "операция") + "</div>" +
              "<div class='tk-card-meta'>" +
              "кол-во: " + esc(formatQty(op.qty_norm)) +
              (op.feature_text ? " · " + esc(op.feature_text) : "") +
              (op.tool_text ? " · " + esc(op.tool_text) : "") +
              " · норма: " + esc(formatQty(op.norm_minutes || 0)) + " мин" +
              "</div>" +
              "</li>";
          });
          html += "</ul>";
        }
        box.innerHTML = html;
        if (options.scroll !== false) {
          box.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      })
      .catch(function (err) {
        box.innerHTML = "<h4>Вагон</h4><p>" + esc(err.message || "Не удалось загрузить") + "</p>";
      });
  }

  function loadCurrentUser() {
    apiFetch("/api/taksimo/auth/me")
      .then(parseApiResponse)
      .then(function (data) {
        data = data.data || {};
        canManageMaterials = !!data.can_delete;
        setManagementVisibility();
        if (data.user) {
          $("currentUser").textContent = data.user;
          $("currentUser").hidden = false;
        }
      })
      .catch(function () {});
  }

  function init() {
    setManagementVisibility();
    $("materialUnit").innerHTML = unitOptions("шт");
    $("materialCreateBtn").addEventListener("click", createMaterial);
    $("materialReserveBtn").addEventListener("click", reserveMaterial);
    $("templateCreateBtn").addEventListener("click", createTemplate);
    $("templateItemAddBtn").addEventListener("click", addTemplateItem);
    $("prepAssignBtn").addEventListener("click", assignTemplateToWagon);
    loadCurrentUser();
    apiFetch("/api/taksimo/stats")
      .then(parseApiResponse)
      .then(function (res) { renderDbStatus((res.data || {}).db || {}); })
      .catch(function () {});
    loadOverview();
  }

  init();
})();
