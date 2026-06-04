(function () {
  const postTitle = document.getElementById("postTitle");
  const postHint = document.getElementById("postHint");
  const userLine = document.getElementById("userLine");
  const commentList = document.getElementById("commentList");
  const emptyState = document.getElementById("emptyState");
  const outsideMax = document.getElementById("outsideMax");
  const form = document.getElementById("commentForm");
  const textInput = document.getElementById("textInput");

  var webApp = window.WebApp || null;
  var initData = "";
  var postId = null;
  var displayName = "";

  function decodePayload(payload) {
    if (!payload || payload.charAt(0) !== "p") return null;
    var raw = payload.slice(1);
    try {
      var pad = raw.length % 4 === 0 ? "" : "=".repeat(4 - (raw.length % 4));
      var bin = atob(raw.replace(/-/g, "+").replace(/_/g, "/") + pad);
      return decodeURIComponent(escape(bin));
    } catch (e) {
      return null;
    }
  }

  function payloadToPostId(raw) {
    if (!raw) return null;
    if (raw.indexOf("mid.") === 0) return raw;
    return decodePayload(raw);
  }

  function resolvePostId() {
    var params = new URLSearchParams(window.location.search);
    var candidates = [
      params.get("postId"),
      params.get("WebAppStartParam"),
      params.get("startapp"),
    ];
    for (var i = 0; i < candidates.length; i++) {
      var id = payloadToPostId(candidates[i]);
      if (id) return id;
    }

    if (window.location.hash && window.location.hash.length > 1) {
      var hashParams = new URLSearchParams(
        window.location.hash.replace(/^#/, "")
      );
      var fromHash = payloadToPostId(
        hashParams.get("WebAppStartParam") || hashParams.get("startapp")
      );
      if (fromHash) return fromHash;
    }

    if (webApp && webApp.initDataUnsafe) {
      var sp = webApp.initDataUnsafe.start_param;
      if (typeof sp === "string" && sp) {
        var fromStart = payloadToPostId(sp);
        if (fromStart) return fromStart;
      }
      if (sp && typeof sp === "object" && sp.value) {
        return payloadToPostId(String(sp.value));
      }
    }
    return null;
  }

  function resolveDisplayName() {
    if (!webApp || !webApp.initDataUnsafe || !webApp.initDataUnsafe.user) return "";
    var u = webApp.initDataUnsafe.user;
    var first = (u.first_name || "").trim();
    var last = (u.last_name || "").trim();
    var username = (u.username || "").trim();
    if (first && last) return first + " " + last;
    if (first) return first;
    if (username) return "@" + username;
    return "Пользователь MAX";
  }

  function closeMiniApp() {
    if (webApp && typeof webApp.close === "function") {
      webApp.close();
      return;
    }
    if (window.history.length > 1) {
      window.history.back();
    }
  }

  function setupBridge() {
    if (!webApp) return false;
    initData = webApp.initData || "";

    if (webApp.disableClosingConfirmation) {
      webApp.disableClosingConfirmation();
    }

    var onBack = function () {
      closeMiniApp();
    };

    if (webApp.BackButton) {
      if (webApp.BackButton.onClick) {
        webApp.BackButton.onClick(onBack);
      }
      if (webApp.BackButton.show) {
        webApp.BackButton.show();
      }
    }

    if (webApp.onEvent) {
      webApp.onEvent("WebAppBackButtonPressed", onBack);
    }

    return Boolean(initData);
  }

  function signalReady() {
    if (webApp && typeof webApp.ready === "function") {
      webApp.ready();
    }
  }

  function formatTime(ts) {
    return new Date(ts * 1000).toLocaleString("ru-RU", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function renderComments(comments) {
    commentList.innerHTML = "";
    if (!comments.length) {
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;
    comments.forEach(function (c) {
      var li = document.createElement("li");
      li.className = "comment-item";
      li.innerHTML =
        '<div class="comment-meta">' +
        '<span class="comment-author-name">' + escapeHtml(c.author) + "</span>" +
        '<span class="comment-time">' + formatTime(c.created_at) + "</span>" +
        "</div>" +
        '<p class="comment-body">' + escapeHtml(c.text) + "</p>";
      commentList.appendChild(li);
    });
  }

  async function load() {
    if (!postId) {
      postTitle.textContent = "Пост не найден";
      postHint.textContent = "Откройте обсуждение из канала MAX.";
      return Promise.resolve();
    }

    postHint.textContent = "Комментарии к посту канала";

    try {
      var res = await fetch("/api/posts/" + encodeURIComponent(postId));
      if (!res.ok) {
        throw new Error(res.status === 404 ? "Пост ещё не зарегистрирован" : "Ошибка загрузки");
      }
      var data = await res.json();
      postTitle.textContent = data.post.title || "Пост в канале";
      renderComments(data.comments || []);
    } catch (e) {
      postTitle.textContent = "Ошибка";
      postHint.innerHTML = '<span class="comments-error">' + escapeHtml(e.message) + "</span>";
      form.hidden = true;
    }
    return Promise.resolve();
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    var text = textInput.value.trim();
    if (!text || !postId || !initData) return;

    var btn = form.querySelector(".comment-submit");
    btn.disabled = true;

    try {
      var res = await fetch("/api/posts/" + encodeURIComponent(postId) + "/comments", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Max-Init-Data": initData,
        },
        body: JSON.stringify({ text: text }),
      });
      if (!res.ok) {
        var err = await res.json().catch(function () { return {}; });
        throw new Error(err.error || "Не удалось отправить");
      }
      textInput.value = "";
      await load();
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  function boot() {
    var inMax = setupBridge();
    postId = resolvePostId();
    displayName = resolveDisplayName();

    if (!inMax) {
      outsideMax.hidden = false;
      form.hidden = true;
      if (postId) load().then(signalReady).catch(signalReady);
      return;
    }

    outsideMax.hidden = true;
    form.hidden = false;
    if (displayName) {
      userLine.textContent = "Вы: " + displayName;
      userLine.hidden = false;
    }
    load().then(signalReady).catch(signalReady);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
