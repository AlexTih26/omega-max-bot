(function () {
  var API = "/api/rumex-management";
  var data = {};
  var editing = null;
  var pendingAction = null;

  function request(path, options) {
    var defaults = { credentials: "same-origin", headers: { "Content-Type": "application/json" } };
    return fetch(API + path, Object.assign(defaults, options || {})).then(function (response) {
      return response.text().then(function (text) {
        var body = {};
        try { body = text ? JSON.parse(text) : {}; } catch (error) {}
        if (!response.ok) throw new Error(body.error || "Ошибка сервера");
        return body;
      });
    });
  }

  function escapeHtml(value) {
    var element = document.createElement("div");
    element.textContent = value == null ? "" : String(value);
    return element.innerHTML;
  }

  function formatTime(value) {
    return value ? new Date(value * 1000).toLocaleString("ru-RU") : "—";
  }

  function showPassword(password) {
    document.getElementById("issuedPassword").textContent = password;
    document.getElementById("passwordDialog").showModal();
  }

  function renderDispatchers() {
    var list = data.dispatchers || [];
    document.getElementById("dispatcherList").innerHTML = list.map(function (item) {
      var login = encodeURIComponent(item.username);
      return '<article class="card"><h3>' + escapeHtml(item.full_name) + '</h3>' +
        '<div class="meta">Логин: ' + escapeHtml(item.username) + " · MAX ID: " +
        escapeHtml(item.max_user_id || "не указан") + "<br>Последняя активность: " +
        formatTime(item.last_activity_at) + "</div><p class=\"" + (item.active ? "ok" : "blocked") +
        '\">' + (item.active ? "Активен" : "Заблокирован") + "</p>" +
        '<button onclick="window.rumexManagement.edit(\'' + login + '\')">Изменить</button>' +
        '<button onclick="window.rumexManagement.security(\'' + login + '\')">Сеансы</button>' +
        '<button onclick="window.rumexManagement.reset(\'' + login + '\')">Сбросить пароль</button></article>';
    }).join("") || "<p>Управляемых диспетчеров пока нет.</p>";
  }

  function renderFleet() {
    document.getElementById("fleetList").innerHTML = (data.vehicles || []).map(function (item) {
      var block = item.shipment_block;
      var action = block ? "false" : "true";
      return '<article class="card"><h3>' + escapeHtml(item.full_plate || item.plate_tail) + " · " +
        escapeHtml(item.model) + '</h3><div class="meta">Хвост: ' + escapeHtml(item.plate_tail) +
        " · водитель: " + escapeHtml(item.document_binding ? item.document_binding.driver_full_name : "не подтверждён") +
        '</div><p class="' + (block ? "blocked" : "ok") + '">' +
        (block ? "Новые погрузки заблокированы: " + escapeHtml(block.reason) : "Доступна для новых погрузок РУМЕКС") +
        '</p><button onclick="window.rumexManagement.block(\'' + escapeHtml(item.plate_tail) + "'," + action + ")\">" +
        (block ? "Разблокировать" : "Блокировать") + "</button></article>";
    }).join("") || "<p>Документный парк ещё не импортирован.</p>";
  }

  function renderShipments() {
    document.getElementById("shipmentList").innerHTML = (data.shipments || []).map(function (item) {
      return '<article class="card"><h3>' + escapeHtml(item.registry_number) + " · " + escapeHtml(item.status) +
        '</h3><div class="meta">Диспетчер: ' + escapeHtml(item.dispatcher_name) + " · бухгалтер: " +
        escapeHtml(item.accountant_name || "—") + "<br>Погрузка: " + formatTime(item.loaded_at) +
        (item.correction_reason ? "<br>Причина исправления: " + escapeHtml(item.correction_reason) : "") + "</div></article>";
    }).join("") || "<p>Погрузок нет.</p>";
  }

  function renderDirectories() {
    var blocks = (data.block_types || []).map(function (item) {
      return '<p class="meta">' + escapeHtml(item.code) + " · " + escapeHtml(item.product_name) + " · " + escapeHtml(item.nominal_weight_kg) + " кг</p>";
    }).join("");
    var carriers = (data.carriers || []).map(function (item) {
      return '<p class="meta">' + escapeHtml(item.name) + " · ИНН " + escapeHtml(item.inn) + "</p>";
    }).join("");
    document.getElementById("directoryList").innerHTML = "<h3>Типы блоков</h3>" + blocks + "<h3>Перевозчики</h3>" + carriers;
  }

  function renderAudit() {
    document.getElementById("auditList").innerHTML = (data.audit_events || []).map(function (item) {
      return '<article class="card"><strong>' + escapeHtml(item.event_type) + '</strong><div class="meta">' +
        formatTime(item.occurred_at) + " · " + escapeHtml(item.actor_name) + " · " + escapeHtml(item.subject_id) + "</div></article>";
    }).join("") || "<p>Событий пока нет.</p>";
  }

  function render() { renderDispatchers(); renderFleet(); renderShipments(); renderDirectories(); renderAudit(); }
  function load() { return request("/overview").then(function (body) { data = body; render(); }); }

  function askPin(action) {
    pendingAction = action;
    document.getElementById("pinError").textContent = "";
    document.getElementById("pinForm").reset();
    document.getElementById("pinDialog").showModal();
  }

  document.getElementById("login").onclick = function () {
    request("/auth", { method: "POST", headers: { "X-Max-Init-Data": (window.WebApp && window.WebApp.initData) || "" } })
      .then(function () { location.reload(); })
      .catch(function (error) { document.getElementById("loginError").textContent = error.message; });
  };
  document.getElementById("logout").onclick = function () { request("/auth/logout", { method: "POST" }).then(function () { location.reload(); }); };
  document.querySelectorAll("nav button").forEach(function (button) {
    button.onclick = function () {
      document.querySelectorAll(".panel").forEach(function (panel) { panel.hidden = true; });
      document.querySelectorAll("nav button").forEach(function (item) { item.classList.remove("active"); });
      document.getElementById(button.dataset.tab).hidden = false;
      button.classList.add("active");
    };
  });
  document.getElementById("addDispatcher").onclick = function () {
    editing = null;
    var form = document.getElementById("dispatcherForm");
    form.reset(); form.username.disabled = false;
    document.getElementById("dialogTitle").textContent = "Новый диспетчер";
    document.getElementById("dispatcherDialog").showModal();
  };
  document.getElementById("dispatcherForm").onsubmit = function (event) {
    event.preventDefault();
    var form = new FormData(event.target);
    var body = { full_name: form.get("full_name"), username: form.get("username"), max_user_id: form.get("max_user_id"), active: form.get("active") === "on" };
    request(editing ? "/dispatchers/" + editing : "/dispatchers", { method: editing ? "PUT" : "POST", body: JSON.stringify(body) })
      .then(function (result) { document.getElementById("dispatcherDialog").close(); if (result.issued_password) showPassword(result.issued_password); return load(); })
      .catch(function (error) { document.getElementById("dispatcherError").textContent = error.message; });
  };
  document.getElementById("pinForm").onsubmit = function (event) {
    event.preventDefault();
    var pin = new FormData(event.target).get("step_up_pin");
    pendingAction(pin).catch(function (error) { document.getElementById("pinError").textContent = error.message; });
    document.getElementById("pinDialog").close();
  };

  window.rumexManagement = {
    edit: function (encodedUsername) {
      var username = decodeURIComponent(encodedUsername);
      var item = (data.dispatchers || []).find(function (value) { return value.username === username; });
      if (!item) return;
      editing = encodedUsername;
      var form = document.getElementById("dispatcherForm");
      form.full_name.value = item.full_name; form.username.value = item.username; form.username.disabled = true;
      form.max_user_id.value = item.max_user_id || ""; form.active.checked = item.active;
      document.getElementById("dialogTitle").textContent = "Карточка диспетчера";
      document.getElementById("dispatcherDialog").showModal();
    },
    reset: function (username) {
      askPin(function (pin) { return request("/dispatchers/" + username + "/password", { method: "POST", body: JSON.stringify({ step_up_pin: pin }) }).then(function (result) { showPassword(result.issued_password); return load(); }); });
    },
    security: function (username) {
      request("/dispatchers/" + username + "/security").then(function (result) { alert("Попытки входа: " + result.login_attempts.length + "; сеансы: " + result.sessions.length); }).catch(function (error) { alert(error.message); });
    },
    block: function (tail, blocked) {
      var reason = blocked ? prompt("Причина блокировки машины РУМЕКС:") : "";
      if (blocked && !reason) return;
      askPin(function (pin) { return request("/vehicles/" + tail + "/shipment-block", { method: "POST", body: JSON.stringify({ blocked: blocked, reason: reason, step_up_pin: pin }) }).then(load); });
    }
  };

  request("/auth/check").then(function (body) {
    if (!body.authenticated) return;
    document.getElementById("loginPanel").hidden = true;
    document.getElementById("cabinet").hidden = false;
    document.getElementById("logout").hidden = false;
    load();
  });
})();
