(function () {
  var webApp = window.WebApp || null;
  var initData = "";

  var driverTitle = document.getElementById("driverTitle");
  var driverSub = document.getElementById("driverSub");
  var tripStage = document.getElementById("tripStage");
  var registerStage = document.getElementById("registerStage");
  var tripHint = document.getElementById("tripHint");
  var outsideMax = document.getElementById("outsideMax");
  var footerBar = document.getElementById("footerBar");
  var footerBarGuest = document.getElementById("footerBarGuest");
  var toast = document.getElementById("toast");
  var panelTrip = document.getElementById("panelTrip");
  var panelJournal = document.getElementById("panelJournal");
  var journalTitle = document.getElementById("journalTitle");
  var journalFeed = document.getElementById("journalFeed");
  var journalEmpty = document.getElementById("journalEmpty");
  var journalBadge = document.getElementById("journalBadge");
  var registerPanel = document.getElementById("registerPanel");
  var registerHint = document.getElementById("registerHint");
  var registerId = document.getElementById("registerId");
  var registerSlots = document.getElementById("registerSlots");
  var registerBtn = document.getElementById("registerBtn");
  var infoBtn = document.getElementById("infoBtn");
  var infoPanel = document.getElementById("infoPanel");
  var infoBtnGuest = document.getElementById("infoBtnGuest");
  var infoPanelGuest = document.getElementById("infoPanelGuest");
  var drvApp = document.querySelector(".drv-app");

  var status = null;
  var journal = null;
  var busy = false;
  var activeTab = "trip";

  function apiHeaders() {
    var h = { "Content-Type": "application/json" };
    if (initData) h["X-Max-Init-Data"] = initData;
    return h;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setupBridge() {
    if (!webApp) return false;
    initData = webApp.initData || "";
    if (webApp.ready) webApp.ready();
    if (webApp.expand) webApp.expand();
    if (webApp.disableClosingConfirmation) webApp.disableClosingConfirmation();
    return Boolean(initData);
  }

  function haptic(type) {
    if (!webApp || !webApp.HapticFeedback) return;
    if (type === "success" && webApp.HapticFeedback.notificationOccurred) {
      webApp.HapticFeedback.notificationOccurred("success");
    } else if (webApp.HapticFeedback.impactOccurred) {
      webApp.HapticFeedback.impactOccurred("light");
    }
  }

  function showToast(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    toast.classList.add("drv-toast--show");
    setTimeout(function () {
      toast.classList.remove("drv-toast--show");
      setTimeout(function () {
        toast.hidden = true;
      }, 200);
    }, 2200);
  }

  function setTab(name) {
    activeTab = name;
    document.querySelectorAll(".drv-tab").forEach(function (btn) {
      btn.classList.toggle("drv-tab--active", btn.getAttribute("data-tab") === name);
    });
    panelTrip.classList.toggle("drv-panel--active", name === "trip");
    panelTrip.hidden = name !== "trip";
    if (panelJournal) panelJournal.hidden = name !== "journal";
    if (footerBar && status && status.registered) {
      footerBar.hidden = name !== "trip";
    }
    if (name === "journal") loadJournal();
  }

  function tripCopy(next) {
    if (next === "factory_arrival") {
      return {
        now: "Сейчас: заезд на завод",
        next: "Нажмите, когда машина на территории завода",
        note: "",
        action: "factory_arrival",
        label: "📍 Прибыл на завод",
        btnClass: "drv-btn drv-btn--success",
      };
    }
    if (next === "factory") {
      return {
        now: "Сейчас: на заводе",
        next: "Нажмите после загрузки или ждите документы от Румекс",
        note: "",
        action: "factory",
        label: "🏭 Выехал с завода",
        btnClass: "drv-btn",
      };
    }
    if (next === "taksimo_arrival") {
      return {
        now: "Сейчас: в пути в Таксimo",
        next: "Нажмите по приезду на площадку (не раньше 4 ч после выезда с завода)",
        note: "Или отметит оператор при начале приёмки",
        action: "taksimo_arrival",
        label: "📍 Прибыл в Таксimo",
        btnClass: "drv-btn drv-btn--success",
      };
    }
    if (next === "yard_wait") {
      return {
        now: "Сейчас: на площадке Таксimo",
        next: "Дальше: выезд появится в чате после приёмки",
        note: "Ничего нажимать не нужно",
        action: null,
      };
    }
    return {
      now: "Рейс завершён",
      next: "Следующий заезд — снова «Прибыл на завод»",
      note: "",
      action: "factory_arrival",
      label: "📍 Новый рейс — на завод",
      btnClass: "drv-btn drv-btn--success",
    };
  }

  function renderTripStage(trip) {
    if (!tripStage || !trip) return;
    var copy = tripCopy(trip.next_action);
    tripStage.innerHTML = "";
    var nowEl = document.createElement("p");
    nowEl.className = "drv-trip-now";
    nowEl.textContent = copy.now;
    tripStage.appendChild(nowEl);
    if (copy.next) {
      var nextEl = document.createElement("p");
      nextEl.className = "drv-trip-next";
      nextEl.textContent = copy.next;
      tripStage.appendChild(nextEl);
    }
    var noteText = trip.action_blocked_reason || copy.note;
    if (noteText) {
      var noteEl = document.createElement("p");
      noteEl.className = "drv-trip-note";
      noteEl.textContent = noteText;
      tripStage.appendChild(noteEl);
    }
    if (copy.action) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = copy.btnClass || "drv-btn";
      btn.textContent = copy.label;
      if (trip.action_blocked) {
        btn.disabled = true;
        if (trip.action_blocked_until) {
          btn.title = "Можно после " + trip.action_blocked_until;
        }
      } else {
        btn.addEventListener("click", function () {
          postAction(copy.action);
        });
      }
      tripStage.appendChild(btn);
    }
  }

  function renderJournal(data) {
    journal = data || journal;
    if (!journal) return;

    var count = journal.count || 0;
    if (journalBadge) {
      if (count > 0) {
        journalBadge.hidden = false;
        journalBadge.textContent = String(count);
      } else {
        journalBadge.hidden = true;
      }
    }

    if (journalTitle) {
      var title = "Журнал · 7 дней";
      if (journal.plate_tail) title += " · …" + journal.plate_tail;
      if (journal.tz_label) title += " · " + journal.tz_label;
      journalTitle.textContent = title;
    }

    if (!journalFeed) return;
    journalFeed.innerHTML = "";
    var events = journal.events || [];
    if (journalEmpty) {
      journalEmpty.hidden = events.length > 0;
      journalEmpty.textContent = !journal.registered
        ? "Вас нет в реестре — журнал появится после регистрации."
        : "За 7 дней записей нет — отметьте рейс на вкладке «Мой рейс».";
    }

    events.forEach(function (item) {
      var li = document.createElement("li");
      var cls = "drv-feed-item";
      if (item.source === "self") cls += " drv-feed-item--me";
      else if (item.source === "rumex" || item.source === "operator") cls += " drv-feed-item--rumex";
      else if (item.source === "taksimo") cls += " drv-feed-item--taksimo";
      li.className = cls;
      var source = item.source_label
        ? '<span class="drv-feed-source">' + escapeHtml(item.source_label) + "</span>"
        : "";
      li.innerHTML = source + '<p class="drv-feed-line">' + escapeHtml(item.line || "") + "</p>";
      journalFeed.appendChild(li);
    });
  }

  function postRegister() {
    if (busy) return;
    busy = true;
    haptic("light");
    fetch("/api/drivers/register", { method: "POST", headers: apiHeaders(), body: "{}" })
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, body: body };
        });
      })
      .then(function (res) {
        if (res.body.status) renderStatus(res.body.status);
        if (res.ok) {
          haptic("success");
          showToast(res.body.notification || "Отправлено");
        } else {
          showToast(res.body.notification || res.body.error || "Не удалось");
        }
      })
      .catch(function () {
        showToast("Нет связи — попробуйте снова");
      })
      .finally(function () {
        busy = false;
      });
  }

  function canRegister(data) {
    return (
      data &&
      !data.registered &&
      !data.registration_pending &&
      !(data.slots_left != null && data.slots_left <= 0)
    );
  }

  function renderGuestStage(data) {
    if (!registerStage) return;
    registerStage.innerHTML = "";
    if (!canRegister(data)) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "drv-btn drv-btn--success";
    btn.textContent = "✅ Зарегистрироваться";
    btn.addEventListener("click", postRegister);
    registerStage.appendChild(btn);
  }

  function statusLine(data) {
    if (!data || !data.registered) return "";
    if (data.left_taksimo_at) return "Уехал с площадки · ждём новый рейс";
    if (data.arrived_taksimo_at && !data.left_taksimo_at) return "На площадке Таксimo";
    if (data.departed_at) return "В пути в Таксimo";
    if (data.arrived_factory_at) return "На заводе";
    return "Рейс не начат";
  }

  function renderStatus(data) {
    status = data;
    if (!data.registered) {
      if (drvApp) drvApp.classList.add("drv-app--guest");
      registerPanel.hidden = false;
      if (footerBar) footerBar.hidden = true;
      if (footerBarGuest) footerBarGuest.hidden = false;
      driverTitle.textContent = "Регистрация водителя";
      driverSub.textContent = data.registration_pending
        ? "Ожидайте добавления диспетчером"
        : "Один раз — ваш id уйдёт диспетчеру";
      registerHint.textContent = data.notification || "";
      registerId.textContent = data.max_user_id ? "MAX id: " + data.max_user_id : "";
      registerSlots.textContent =
        data.slots_left != null && data.drivers_max
          ? "Свободно мест: " + data.slots_left + " из " + data.drivers_max
          : "";
      renderGuestStage(data);
      return;
    }

    if (drvApp) drvApp.classList.remove("drv-app--guest");
    registerPanel.hidden = true;
    if (footerBarGuest) footerBarGuest.hidden = true;
    if (footerBar) footerBar.hidden = activeTab !== "trip";

    var tail = data.plate_tail ? "…" + data.plate_tail : "";
    driverTitle.textContent = (data.name || "Водитель") + (tail ? " · " + tail : "");
    driverSub.textContent = statusLine(data) + (data.tz_label ? " · " + data.tz_label : "");

    if (data.trip) renderTripStage(data.trip);
    else if (tripStage && tripHint) tripStage.innerHTML = "";
  }

  function loadStatus() {
    return fetch("/api/drivers/status", { headers: apiHeaders() })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "status error");
          return body;
        });
      })
      .then(renderStatus)
      .catch(function (err) {
        if (tripStage) {
          tripStage.innerHTML = "";
          var el = document.createElement("p");
          el.className = "drv-hint";
          el.textContent = "Ошибка загрузки: " + (err.message || "сеть");
          tripStage.appendChild(el);
        }
      });
  }

  function loadJournal() {
    return fetch("/api/drivers/journal?days=7", { headers: apiHeaders() })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "journal error");
          return body;
        });
      })
      .then(renderJournal)
      .catch(function () {
        if (journalEmpty) {
          journalEmpty.hidden = false;
          journalEmpty.textContent = "Не удалось загрузить журнал.";
        }
      });
  }

  function postAction(action) {
    if (busy) return;
    busy = true;
    haptic("light");
    fetch("/api/drivers/action", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ action: action }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, body: body };
        });
      })
      .then(function (res) {
        var body = res.body || {};
        if (body.status) renderStatus(body.status);
        if (body.journal) renderJournal(body.journal);
        else if (activeTab === "journal") loadJournal();
        if (res.ok) {
          haptic("success");
          showToast(body.notification || "Готово");
        } else {
          showToast(body.notification || body.error || "Не удалось");
        }
      })
      .catch(function () {
        showToast("Нет связи — попробуйте снова");
      })
      .finally(function () {
        busy = false;
      });
  }

  function bindInfoToggle(btn, panel) {
    if (!btn || !panel) return;
    btn.addEventListener("click", function () {
      var open = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", open ? "false" : "true");
      panel.hidden = open;
    });
  }

  bindInfoToggle(infoBtn, infoPanel);
  bindInfoToggle(infoBtnGuest, infoPanelGuest);

  document.querySelectorAll(".drv-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTab(btn.getAttribute("data-tab") || "trip");
    });
  });

  if (!setupBridge()) {
    outsideMax.hidden = false;
    if (footerBar) footerBar.hidden = true;
    if (footerBarGuest) footerBarGuest.hidden = true;
    return;
  }

  if (typeof installPanelFeedback === "function") {
    installPanelFeedback({
      app: "drivers",
      getHeaders: apiHeaders,
      showToast: showToast,
    });
  }

  Promise.all([loadStatus(), loadJournal()]);
})();
