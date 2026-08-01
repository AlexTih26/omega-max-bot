(function () {
  var webApp = window.WebApp || null;
  var initData = "";
  var categories = [];

  var form = document.getElementById("workForm");
  var categoryEl = document.getElementById("category");
  var requestList = document.getElementById("requestList");
  var emptyList = document.getElementById("emptyList");
  var outsideMax = document.getElementById("outsideMax");
  var submitBtn = document.getElementById("submitBtn");
  var toast = document.getElementById("toast");
  var panelNew = document.getElementById("panelNew");
  var panelList = document.getElementById("panelList");

  function apiHeaders() {
    var h = { "Content-Type": "application/json" };
    if (initData) h["X-Max-Init-Data"] = initData;
    return h;
  }

  function formatDate(ts) {
    try {
      return new Date(ts * 1000).toLocaleString("ru-RU", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (e) {
      return "";
    }
  }

  function statusClass(status) {
    if (status === "готова") return "work-status work-status--done";
    if (status === "в работе") return "work-status work-status--progress";
    return "work-status";
  }

  function showToast(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    toast.classList.add("work-toast--show");
    setTimeout(function () {
      toast.classList.remove("work-toast--show");
      setTimeout(function () {
        toast.hidden = true;
      }, 200);
    }, 2200);
  }

  function fillCategories(list) {
    categoryEl.innerHTML = "";
    list.forEach(function (cat) {
      var opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat.charAt(0).toUpperCase() + cat.slice(1);
      categoryEl.appendChild(opt);
    });
  }

  function renderRequests(items) {
    requestList.innerHTML = "";
    if (!items || !items.length) {
      emptyList.hidden = false;
      return;
    }
    emptyList.hidden = true;
    items.forEach(function (item) {
      var li = document.createElement("li");
      li.className = "work-card";
      li.innerHTML =
        '<div class="work-card-head">' +
        '<span class="work-card-title">' + escapeHtml(item.title) + "</span>" +
        '<span class="' + statusClass(item.status) + '">' + escapeHtml(item.status) + "</span>" +
        "</div>" +
        '<p class="work-card-meta">' +
        escapeHtml(item.category) +
        " · " +
        formatDate(item.created_at) +
        "</p>" +
        '<p class="work-card-text">' + escapeHtml(item.text) + "</p>";
      requestList.appendChild(li);
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setTab(name) {
    document.querySelectorAll(".work-tab").forEach(function (btn) {
      btn.classList.toggle("work-tab--active", btn.getAttribute("data-tab") === name);
    });
    panelNew.hidden = name !== "new";
    panelList.hidden = name !== "list";
    if (name === "list") loadRequests();
  }

  function loadMeta() {
    return fetch("/api/work/meta")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        categories = data.categories || [];
        fillCategories(categories.length ? categories : ["другое"]);
      });
  }

  function loadRequests() {
    if (!initData) return;
    return fetch("/api/work/requests", { headers: apiHeaders() })
      .then(function (r) {
        if (r.status === 401) throw new Error("auth");
        return r.json();
      })
      .then(function (data) {
        renderRequests(data.requests || []);
      })
      .catch(function () {
        renderRequests([]);
      });
  }

  function initBridge() {
    if (!webApp) return false;
    try {
      webApp.ready();
      if (typeof webApp.expand === "function") webApp.expand();
    } catch (e) {}
    initData = webApp.initData || "";
    return !!initData;
  }

  document.querySelectorAll(".work-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTab(btn.getAttribute("data-tab"));
    });
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!initData) {
      outsideMax.hidden = false;
      return;
    }
    submitBtn.disabled = true;
    fetch("/api/work/requests", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        category: categoryEl.value,
        title: document.getElementById("title").value,
        text: document.getElementById("text").value,
      }),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "error");
        form.reset();
        if (categories.length) categoryEl.value = categories[0];
        showToast("Заявка №" + res.data.request.id + " принята");
      })
      .catch(function (err) {
        alert(err.message || "Не удалось отправить");
      })
      .finally(function () {
        submitBtn.disabled = false;
      });
  });

  loadMeta().then(function () {
    var inMax = initBridge();
    if (!inMax) {
      outsideMax.hidden = false;
      submitBtn.disabled = true;
    }
  });
})();
