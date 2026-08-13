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

  function schemeCodeLabel(code) {
    if (code === "scheme2") return "Схема 2 · 16 K";
    if (code === "scheme3") return "Схема 3 · с ящиком";
    return "Схема 1 · кольцо + допы";
  }

  function returnStatusLabel(status) {
    if (status === "in_transit_back") return "ящик в пути";
    if (status === "returned_to_zone") return "возврат принят";
    if (status === "planned_return") return "возврат ожидается";
    return "без возврата";
  }

  var units = ["шт", "кг", "м", "пачка", "рулон"];
  var dashboard = null;
  var activeWagon = "";
  var canManageMaterials = false;
  var wagonSearchQuery = "";

  function renderDbStatus(db) {
    var el = $("dbStatus");
    if (!el) return;
    var ts = db && db.last_change_ts ? new Date(db.last_change_ts * 1000).toLocaleString("ru-RU") : "—";
    el.textContent = "База обновлена: " + ts;
  }

  function setManagementVisibility() {
    ["materialCreateCard", "materialReserveCard"].forEach(function (id) {
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

  function getScheme1() {
    return ((dashboard && dashboard.templates) || []).find(function (item) {
      return String(item.scheme_code || "") === "scheme1";
    }) || ((dashboard && dashboard.templates) || [])[0] || null;
  }

  function renderSummary(data) {
    var box = $("materialSummary");
    var summary = (data && data.summary) || {};
    box.innerHTML =
      '<div class="tk-material-metric"><b>' + esc(summary.material_count || 0) + '</b><span>материалов</span></div>' +
      '<div class="tk-material-metric"><b>1</b><span>схема сейчас</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.low_stock_count || 0) + '</b><span>ниже минимума</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.ready_wagons || 0) + '</b><span>готовых вагонов</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(summary.overall_wagons_left != null ? summary.overall_wagons_left : "—") + '</b><span>хватит ещё на вагонов</span></div>';
  }

  function renderScheme1Norms() {
    var box = $("scheme1NormList");
    if (!box) return;
    var scheme = getScheme1();
    var items = (scheme && scheme.items) || [];
    if (!items.length) {
      box.innerHTML = "<div class='tk-card-meta'>Нормы схемы 1 пока не заданы. Укажите «Норма на вагон» у материалов.</div>";
      return;
    }
    box.innerHTML = items.map(function (item) {
      return (
        '<div class="tk-material-metric">' +
        '<b>' + esc(formatQty(item.qty_norm)) + ' ' + esc(item.material_unit || "") + '</b>' +
        '<span>' + esc(item.material_name || "—") + '</span>' +
        '</div>'
      );
    }).join("");
  }

  function renderSchemeSummary(stats) {
    var box = $("schemeSummary");
    if (!box) return;
    stats = stats || {};
    var assigned = stats.assigned || {};
    var dispatched = stats.dispatched || {};
    box.innerHTML =
      '<div class="tk-material-metric"><b>' + esc(assigned.scheme1 || 0) + '</b><span>схема 1 назначено</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(assigned.scheme2 || 0) + '</b><span>схема 2 назначено</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(assigned.scheme3 || 0) + '</b><span>схема 3 назначено</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(dispatched.scheme2 || 0) + '</b><span>вагонов 16K ушло</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(stats.historical_k_total || 0) + '</b><span>K накоплено</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(stats.historical_extra_units || 0) + '</b><span>допов A–F по истории</span></div>';
  }

  function renderRingSummary(stats) {
    var box = $("ringSummary");
    if (!box) return;
    stats = stats || {};
    var statuses = stats.statuses || {};
    var sent = statuses.all_sent || {};
    var wagons = statuses.wagons || {};
    var platforms = Math.round(Number(sent.slab_total || 0) / 9);
    box.innerHTML =
      '<div class="tk-material-metric"><b>' + esc(platforms) + '</b><span>платформ отправлено БТС + Кодар</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(sent.slab_total || 0) + '</b><span>плит отправлено</span></div>' +
      '<div class="tk-material-metric"><b>' + esc(wagons.slab_total || 0) + '</b><span>★★ плит ещё в вагонах</span></div>';
  }

  function renderManagerRingReport(data) {
    data = data || {};
    var closed = Number(data.ring_total || 0);
    var closedBlocks = Number(data.closed_ring_blocks != null ? data.closed_ring_blocks : closed * 7);
    var openRings = Number(data.open_rings || 0);
    var openBlocks = Number(data.extra_blocks_total || 0);
    var kMissing = Number(data.open_rings_k_missing != null ? data.open_rings_k_missing : openRings);
    var missingRows = data.open_ring_missing || [];
    var html =
      '<div class="tk-card">' +
      '<div class="tk-card-title">Из отправленных БТС + Кодар</div>' +
      '<div class="tk-card-meta">' +
      esc(closed) + ' закрытых колец (' + esc(closedBlocks) + ' блоков)<br>' +
      esc(openRings) + ' незакрытых колец (' + esc(openBlocks) + ' блоков)';
    if (missingRows.length) {
      html += ' — не хватает:<br>';
      missingRows.forEach(function (row) {
        html += esc((row || []).join(", ")) + '<br>';
      });
    } else if (openRings > 0) {
      html += ' — по буквам A–F комплекты уже есть<br>';
    } else {
      html += '<br>';
    }
    html +=
      'и блоков K не хватает: <b>' + esc(kMissing) + '</b>' +
      '</div></div>';
    return html;
  }

  function renderWagonHintReport(data) {
    data = data || {};
    var slabs = Number(data.slab_total || 0);
    if (!slabs) {
      return '<div class="tk-card"><div class="tk-card-title">★★ В вагонах</div><div class="tk-card-meta">Сейчас в вагонах плит нет.</div></div>';
    }
    var extras = data.extra_blocks || {};
    var missingRows = data.open_ring_missing || [];
    var html =
      '<div class="tk-card">' +
      '<div class="tk-card-title">★★ Подсказка по текущим вагонам</div>' +
      '<div class="tk-card-meta">' +
      'плит: ' + esc(slabs) +
      ' · полных колец: ' + esc(data.ring_total || 0) +
      ' · незакрытых: ' + esc(data.open_rings || 0);
    if (Object.keys(extras).some(function (key) { return Number(extras[key] || 0) > 0; })) {
      html += '<br>допы: ' + esc(compactLetterMap(extras));
    }
    if (missingRows.length) {
      html += '<br>не хватает:<br>';
      missingRows.forEach(function (row) {
        html += esc((row || []).join(", ")) + '<br>';
      });
    }
    html += '</div></div>';
    return html;
  }

  function renderRingDeficit(stats) {
    var box = $("ringDeficit");
    if (!box) return;
    stats = stats || {};
    var statuses = stats.statuses || {};
    box.innerHTML =
      renderManagerRingReport(statuses.all_sent || {}) +
      renderWagonHintReport(statuses.wagons || {});
  }

  function renderRingRegistry(items) {
    var box = $("ringRegistry");
    if (!box) return;
    box.innerHTML = "";
    if (!(items || []).length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>По кольцам отправок пока нет.</div></div>";
      closeRingRegistryDetail();
      return;
    }
    items.forEach(function (item) {
      var card = document.createElement("div");
      card.className = "tk-card tk-card--click";
      var scheme = item.scheme_name || schemeCodeLabel(item.scheme_code);
      var lines = ["Статус: " + (item.status_label || "—")];
      if (item.origin_zone) lines.push("Тупик: " + item.origin_zone);
      if (item.dispatched_at_label) lines.push("Отправка: " + item.dispatched_at_label);
      if (item.received_at_label) lines.push("БТС: " + item.received_at_label);
      card.innerHTML =
        "<div class='tk-card-title'>Вагон " + esc(item.wagon_number || "—") + "</div>" +
        "<div class='tk-card-meta'>" + esc(scheme) + "</div>" +
        "<div class='tk-card-meta'>" + esc(lines.join(" · ")) + "</div>";
      card.addEventListener("click", function () {
        openRingRegistryDetail(item);
      });
      box.appendChild(card);
    });
  }

  function closeRingRegistryDetail() {
    var box = $("ringRegistryDetail");
    if (!box) return;
    box.hidden = true;
    box.innerHTML = "";
  }

  function openRingRegistryDetail(item) {
    var box = $("ringRegistryDetail");
    if (!box) return;
    item = item || {};
    var blocks = item.blocks || [];
    var letters = { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, K: 0 };
    blocks.forEach(function (block) {
      var letter = String(block.letter || "").trim().toUpperCase();
      if (letters[letter] != null) letters[letter] += 1;
    });
    var afCapacity = Math.min(letters.A, letters.B, letters.C, letters.D, letters.E, letters.F);
    var ringTotal = Math.min(afCapacity, letters.K);
    var extras = {};
    ["A", "B", "C", "D", "E", "F"].forEach(function (letter) {
      var qty = Math.max(0, letters[letter] - ringTotal);
      if (qty > 0) extras[letter] = qty;
    });
    var scheme = item.scheme_name || schemeCodeLabel(item.scheme_code);
    var meta = [];
    meta.push(item.status_label || "—");
    if (item.origin_zone) meta.push("тупик " + item.origin_zone);
    if (item.slot_zone) meta.push("слот " + item.slot_zone + (item.slot_index ? " №" + item.slot_index : ""));
    if (item.dispatched_at_label) meta.push("отправка " + item.dispatched_at_label);
    if (item.received_at_label) meta.push("БТС " + item.received_at_label);
    var html =
      "<div class='tk-card'>" +
      "<div class='tk-material-card-top'>" +
      "<div class='tk-card-title'>Вагон " + esc(item.wagon_number || "—") + "</div>" +
      "<button type='button' class='tk-btn' id='ringRegistryDetailClose'>Закрыть</button>" +
      "</div>" +
      "<div class='tk-card-meta'><strong>" + esc(scheme) + "</strong></div>" +
      "<div class='tk-card-meta'>" + esc(meta.join(" · ")) + "</div>" +
      "<div class='tk-material-grid' style='margin-top:10px'>" +
      "<div class='tk-material-metric'><b>" + esc(item.slab_count || blocks.length || 0) + "</b><span>плит в отправке</span></div>" +
      "<div class='tk-material-metric'><b>" + esc(ringTotal) + "</b><span>полных колец по факту</span></div>" +
      "</div>" +
      "<div class='tk-card-meta'>Блоки по буквам</div>" +
      '<div class="tk-letter-row">' + compactLetterBadges(letters, "have") + "</div>" +
      "<div class='tk-card-meta'>Допы A–F по факту</div>" +
      '<div class="tk-letter-row">' + compactLetterBadges(extras, "hint") + "</div>";
    if (item.returns_materials) {
      html +=
        "<div class='tk-card-meta'>Возврат: " + esc(returnStatusLabel(item.return_status)) +
        " · куда: " + esc(item.return_target_zone || item.origin_zone || "—") +
        (item.return_actual_zone ? " · принято: " + esc(item.return_actual_zone) : "") +
        "</div>";
    }
    if (blocks.length) {
      html += "<div class='tk-card-meta' style='margin-top:8px'>Плиты в вагоне</div><ul class='tk-list'>";
      blocks.forEach(function (block) {
        html +=
          "<li class='tk-card'>" +
          "<div class='tk-card-title'>" + esc(block.label || ((block.letter || "") + " " + (block.number || "")).trim() || "—") + "</div>" +
          (block.vehicle_plate ? "<div class='tk-card-meta'>машина " + esc(block.vehicle_plate) + "</div>" : "") +
          "</li>";
      });
      html += "</ul>";
    } else {
      html += "<div class='tk-card-meta'>Список плит по этой отправке пока пуст.</div>";
    }
    html += "</div>";
    box.hidden = false;
    box.innerHTML = html;
    $("ringRegistryDetailClose").addEventListener("click", closeRingRegistryDetail);
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function compactLetterMap(map) {
    var parts = [];
    ["A", "B", "C", "D", "E", "F"].forEach(function (letter) {
      var value = Number((map || {})[letter] || 0);
      if (value > 0) parts.push(letter + ":" + formatQty(value));
    });
    return parts.length ? parts.join(" · ") : "—";
  }

  function compactLetterBadges(map, tone) {
    var parts = [];
    ["A", "B", "C", "D", "E", "F"].forEach(function (letter) {
      var value = Number((map || {})[letter] || 0);
      if (value > 0) {
        parts.push(
          '<span class="tk-letter-chip tk-letter-chip--' + tone + '">' +
          esc(letter + " " + formatQty(value)) +
          "</span>"
        );
      }
    });
    return parts.length ? parts.join("") : '<span class="tk-letter-chip">нет</span>';
  }

  function renderReturnQueue(items) {
    var box = $("returnQueue");
    if (!box) return;
    box.innerHTML = "";
    if (!(items || []).length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>По ящикам возвраты пока не ожидаются.</div></div>";
      return;
    }
    items.forEach(function (item) {
      var card = document.createElement("div");
      card.className = "tk-card tk-master-scheme-card";
      var target = item.return_target_zone || "не выбран";
      card.innerHTML =
        "<div class='tk-card-title'>Вагон " + esc(item.wagon_number) + "</div>" +
        "<div class='tk-card-meta'>" + esc(item.template_name || schemeCodeLabel(item.scheme_code)) + "</div>" +
        "<div class='tk-card-meta'>Статус: " + esc(returnStatusLabel(item.return_status)) +
        " · откуда: " + esc(item.origin_zone || "—") +
        " · куда: " + esc(target) + "</div>" +
        "<div class='tk-card-meta'>Ёмкость ящика: " + esc(item.box_capacity_wagons || 0) +
        " ваг. · " + esc(item.stage_label || "—") +
        (item.location_label ? " · " + esc(item.location_label) : "") +
        "</div>";
      box.appendChild(card);
    });
  }

  function refreshSelectors() {
    var reserveWagonValue = $("materialReserveWagon").value;
    var reserveMaterialValue = $("materialReserveMaterial").value;
    if ($("materialUnit")) {
      $("materialUnit").innerHTML = unitOptions(($("materialUnit").value || "шт"));
    }
    var wagons = (dashboard && dashboard.wagons) || [];
    $("materialReserveWagon").innerHTML =
      '<option value="">— выберите вагон —</option>' +
      wagons.map(function (wagon) {
        return '<option value="' + esc(wagon.number) + '">' + esc(wagon.number + " · " + (wagon.stage_label || "")) + '</option>';
      }).join("");
    $("materialReserveWagon").value = reserveWagonValue;
    refreshReserveMaterialSelect();
    $("materialReserveMaterial").value = reserveMaterialValue;
  }

  function getTemplateById(templateId) {
    templateId = Number(templateId || 0);
    return ((dashboard && dashboard.templates) || []).find(function (item) {
      return Number(item.id) === templateId;
    }) || null;
  }

  function getWagonByNumber(wagonNumber) {
    wagonNumber = String(wagonNumber || "").trim();
    return ((dashboard && dashboard.wagons) || []).find(function (item) {
      return String(item.number || "").trim() === wagonNumber;
    }) || null;
  }

  function refreshReserveMaterialSelect() {
    var sel = $("materialReserveMaterial");
    if (!sel) return;
    var scheme = getScheme1();
    var templateItems = (scheme && scheme.items) || [];
    if (!templateItems.length) {
      sel.innerHTML = '<option value="">— нет норм схемы 1 —</option>';
      return;
    }
    sel.innerHTML =
      '<option value="">— выберите материал схемы 1 —</option>' +
      templateItems.map(function (item) {
        return '<option value="' + item.material_id + '">' +
          esc(item.material_name) + " · норма " + esc(formatQty(item.qty_norm)) + " " + esc(item.material_unit) +
          '</option>';
      }).join("");
  }

  function renderTemplates() {
    renderScheme1Norms();
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

  function wagonSearchHaystack(wagon) {
    return [
      wagon.number,
      wagon.stage_label,
      wagon.location_label,
      wagon.template_name,
      schemeCodeLabel(wagon.scheme_code),
      wagon.origin_zone,
      wagon.is_ready ? "готов" : "",
      (wagon.shortage_names || []).join(" "),
    ].join(" ").toLowerCase();
  }

  function filterWagons(items) {
    var q = String(wagonSearchQuery || "").trim().toLowerCase();
    if (!q) return items || [];
    return (items || []).filter(function (wagon) {
      return wagonSearchHaystack(wagon).indexOf(q) !== -1;
    });
  }

  function renderWagons(items) {
    var box = $("materialWagonList");
    var searchInput = $("wagonSearchQ");
    if (searchInput) wagonSearchQuery = searchInput.value;
    box.innerHTML = "";
    var allItems = items || [];
    var visible = filterWagons(allItems);
    if (!allItems.length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>Вагоны для подготовки пока не найдены.</div></div>";
      return;
    }
    if (!visible.length) {
      box.innerHTML = "<div class='tk-card'><div class='tk-card-meta'>По запросу «" + esc(wagonSearchQuery) + "» вагоны не найдены.</div></div>";
      return;
    }
    visible.forEach(function (wagon) {
      var card = document.createElement("div");
      card.className = "tk-card tk-card--click";
      if (wagon.is_ready) card.classList.add("tk-material-wagon-card--ready");
      else if (wagon.shortage_count > 0) card.classList.add("tk-material-wagon-card--warn");
      var stateLine = wagon.is_ready
        ? "Готов к отправке"
        : (wagon.shortage_count > 0
          ? "Не хватает: " + (wagon.shortage_names && wagon.shortage_names.length ? wagon.shortage_names.join(", ") : wagon.shortage_count || 0)
          : "Подготовка в работе");
      card.innerHTML =
        '<div class="tk-material-card-top">' +
        '<div class="tk-card-title">Вагон ' + esc(wagon.number) + '</div>' +
        '<div class="tk-card-meta">' + esc(wagon.stage_label || "—") + '</div>' +
        '</div>' +
        '<div class="tk-card-meta">Схема: ' + esc(wagon.template_name || "не назначена") +
        (wagon.template_name ? " · " + esc(schemeCodeLabel(wagon.scheme_code)) : "") +
        (wagon.norm_minutes ? " · " + esc(formatQty(wagon.norm_minutes)) + " мин" : "") +
        '</div>' +
        '<div class="tk-card-meta">' + esc(wagon.location_label || "—") + '</div>' +
        '<div class="tk-card-meta">' + esc(stateLine) + '</div>';
      card.addEventListener("click", function () {
        loadWagon(wagon.number);
      });
      box.appendChild(card);
    });
  }

  function applyWagonSearch() {
    var input = $("wagonSearchQ");
    wagonSearchQuery = input ? input.value : "";
    renderWagons((dashboard && dashboard.wagons) || []);
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
        renderScheme1Norms();
        renderRingSummary(data.ring_summary || {});
        renderRingDeficit(data.ring_summary || {});
        renderMaterials(data.materials || []);
        renderRingRegistry(data.ring_registry || []);
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
        scheme_code: $("templateSchemeCode").value,
        has_box: $("templateHasBox").checked,
        returns_materials: $("templateReturnsMaterials").checked,
        extra_ring_mode: "carry_over",
        extra_units: $("templateExtraUnits").value || 0,
        k_goal: $("templateKGoal").value || 0,
        box_capacity_wagons: $("templateBoxCapacity").value || 0,
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        $("templateName").value = "";
        $("templateDescription").value = "";
        $("templateSchemeCode").value = "scheme1";
        $("templateHasBox").checked = false;
        $("templateReturnsMaterials").checked = false;
        $("templateExtraUnits").value = "";
        $("templateKGoal").value = "";
        $("templateBoxCapacity").value = "";
        toast("Схема создана");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка создания схемы"); });
  }

  function saveTemplate(templateId, root) {
    apiFetch("/api/taksimo/materials/templates/" + templateId, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: root.querySelector('[data-template-field="name"]').value.trim(),
        description: root.querySelector('[data-template-field="description"]').value.trim(),
        scheme_code: root.querySelector('[data-template-field="scheme_code"]').value,
        has_box: root.querySelector('[data-template-field="has_box"]').checked,
        returns_materials: root.querySelector('[data-template-field="returns_materials"]').checked,
        extra_ring_mode: "carry_over",
        extra_units: root.querySelector('[data-template-field="extra_units"]').value || 0,
        k_goal: root.querySelector('[data-template-field="k_goal"]').value || 0,
        box_capacity_wagons: root.querySelector('[data-template-field="box_capacity_wagons"]').value || 0,
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Схема обновлена");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка обновления схемы"); });
  }

  function saveTemplateItem(templateItemId, root) {
    function value(field) {
      var el = root.querySelector('[data-item-id="' + templateItemId + '"][data-item-field="' + field + '"]');
      return el ? el.value : "";
    }
    apiFetch("/api/taksimo/materials/templates/items/" + templateItemId, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        material_id: value("material_id"),
        qty_norm: value("qty_norm"),
        work_type: value("work_type"),
        feature_text: value("feature_text"),
        tool_text: value("tool_text"),
        norm_minutes: value("norm_minutes"),
      }),
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Строка схемы обновлена");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка обновления строки"); });
  }

  function deleteTemplateItemUi(templateItemId) {
    if (!confirm("Удалить строку схемы?")) return;
    apiFetch("/api/taksimo/materials/templates/items/" + templateItemId, {
      method: "DELETE",
    })
      .then(parseApiResponse)
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Ошибка");
        toast("Строка схемы удалена");
        loadOverview();
      })
      .catch(function (err) { showError(err, "Ошибка удаления строки"); });
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
    if (!$("materialReserveMaterial").value) {
      toast("Сначала выберите материал из схемы вагона");
      return;
    }
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
    $("materialReserveWagon").value = wagonNumber;
    refreshReserveMaterialSelect();
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
          (wagon.prep && wagon.prep.template_name ? "<br>Схема: <strong>" + esc(wagon.prep.template_name) + "</strong>" : "<br>Схема: <strong>Схема 1 общая</strong>") +
          "<br>Резервов: " + esc(wagon.reserved_item_count || 0) +
          " · дефицит: " + esc(wagon.shortage_count || 0) +
          "</p>";
        if (items.length) {
          html += "<ul class='tk-list'>";
          items.forEach(function (item) {
            var detail = [];
            detail.push("норма " + formatQty(item.norm_per_wagon) + " " + item.unit);
            detail.push("резерв " + formatQty(item.reserved_qty));
            if (Number(item.shortage_qty || 0) > 0) detail.push("не хватает " + formatQty(item.shortage_qty) + " " + item.unit);
            html +=
              "<li class='tk-card'>" +
              "<div class='tk-material-card-top'><div class='tk-card-title'>" + esc(item.name) + "</div>" +
              "<div class='tk-card-meta'>" + esc(detail.join(" · ")) + "</div></div>" +
              "</li>";
          });
          html += "</ul>";
        } else {
          html += "<p class='tk-card-meta'>По этому вагону ещё нет расходников.</p>";
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
    if ($("materialUnit")) $("materialUnit").innerHTML = unitOptions("шт");
    if ($("materialCreateBtn")) $("materialCreateBtn").addEventListener("click", createMaterial);
    if ($("materialReserveBtn")) $("materialReserveBtn").addEventListener("click", reserveMaterial);
    loadCurrentUser();
    if ($("materialReserveWagon")) $("materialReserveWagon").addEventListener("change", refreshReserveMaterialSelect);
    if ($("wagonSearchQ")) $("wagonSearchQ").addEventListener("input", applyWagonSearch);
    apiFetch("/api/taksimo/stats")
      .then(parseApiResponse)
      .then(function (res) { renderDbStatus((res.data || {}).db || {}); })
      .catch(function () {});
    loadOverview();
  }

  init();
})();
