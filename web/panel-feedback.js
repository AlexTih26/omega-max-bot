(function (global) {
  function installPanelFeedback(options) {
    var app = options.app || "panel";
    var getHeaders = options.getHeaders;
    var showToast = options.showToast || function () {};
    var btn = document.getElementById(options.buttonId || "panelFeedbackBtn");
    var sheet = document.getElementById(options.sheetId || "panelFeedbackSheet");
    if (!btn || !sheet) return;

    var backdrop = sheet.querySelector(".pfb-backdrop");
    var closeBtn = sheet.querySelector("[data-pfb-close]");
    var sendBtn = sheet.querySelector("[data-pfb-send]");
    var textEl = sheet.querySelector(".pfb-text");
    var kindBtns = sheet.querySelectorAll("[data-pfb-kind]");
    var busy = false;
    var kind = "bug";

    function setKind(next) {
      kind = next === "feature" ? "feature" : "bug";
      kindBtns.forEach(function (el) {
        var active = el.getAttribute("data-pfb-kind") === kind;
        el.classList.toggle("pfb-kind-btn--active", active);
      });
    }

    function openSheet() {
      sheet.hidden = false;
      if (textEl) {
        textEl.value = "";
        textEl.focus();
      }
      setKind("bug");
    }

    function closeSheet() {
      sheet.hidden = true;
    }

    function submitFeedback() {
      if (busy || !textEl) return;
      var text = (textEl.value || "").trim();
      if (!text) {
        showToast("Напишите, что случилось");
        return;
      }
      busy = true;
      if (sendBtn) sendBtn.disabled = true;
      fetch("/api/panel/feedback", {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({
          app: app,
          kind: kind,
          text: text,
          meta: {
            path: global.location.pathname,
            ua: global.navigator.userAgent || "",
          },
        }),
      })
        .then(function (r) {
          return r.json().then(function (body) {
            if (!r.ok) throw new Error(body.error || "send failed");
            return body;
          });
        })
        .then(function () {
          closeSheet();
          showToast("Спасибо — передали разработчику");
        })
        .catch(function (err) {
          showToast(err.message || "Не удалось отправить");
        })
        .finally(function () {
          busy = false;
          if (sendBtn) sendBtn.disabled = false;
        });
    }

    btn.addEventListener("click", openSheet);
    if (backdrop) backdrop.addEventListener("click", closeSheet);
    if (closeBtn) closeBtn.addEventListener("click", closeSheet);
    if (sendBtn) sendBtn.addEventListener("click", submitFeedback);
    kindBtns.forEach(function (el) {
      el.addEventListener("click", function () {
        setKind(el.getAttribute("data-pfb-kind"));
      });
    });
    setKind("bug");
  }

  global.installPanelFeedback = installPanelFeedback;
})(window);
