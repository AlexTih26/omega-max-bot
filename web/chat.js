(function () {
  var webApp = window.WebApp || null;
  var initData = "";

  var appRoot = document.getElementById("appRoot");
  var outsideMax = document.getElementById("outsideMax");
  var chatMain = document.getElementById("chatMain");
  var limitLine = document.getElementById("limitLine");
  var limitBanner = document.getElementById("limitBanner");
  var limitBannerText = document.getElementById("limitBannerText");
  var messageFeed = document.getElementById("messageFeed");
  var welcomeBlock = document.getElementById("welcomeBlock");
  var messageInput = document.getElementById("messageInput");
  var sendBtn = document.getElementById("sendBtn");
  var settingsBtn = document.getElementById("settingsBtn");
  var settingsSheet = document.getElementById("settingsSheet");
  var settingsBackdrop = document.getElementById("settingsBackdrop");
  var settingsClose = document.getElementById("settingsClose");
  var settingsId = document.getElementById("settingsId");
  var settingsLimit = document.getElementById("settingsLimit");
  var clearBtn = document.getElementById("clearBtn");
  var scrollEndAnchor = document.getElementById("scrollEndAnchor");
  var toast = document.getElementById("toast");

  var status = null;
  var conversationId = null;
  var busy = false;

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

  function setupBridge() {
    if (!webApp) return false;
    initData = webApp.initData || readInitDataFromUrl();
    if (webApp.ready) webApp.ready();
    if (webApp.expand) webApp.expand();
    if (webApp.disableClosingConfirmation) webApp.disableClosingConfirmation();
    return Boolean(initData);
  }

  function apiHeaders() {
    var h = { "Content-Type": "application/json" };
    if (initData) h["X-Max-Init-Data"] = initData;
    return h;
  }

  function showToast(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    setTimeout(function () {
      toast.hidden = true;
    }, 2400);
  }

  function haptic(type) {
    if (!webApp || !webApp.HapticFeedback) return;
    if (type === "success" && webApp.HapticFeedback.notificationOccurred) {
      webApp.HapticFeedback.notificationOccurred("success");
    }
  }

  function scrollToBottom() {
    if (!chatMain) return;
    function run() {
      if (scrollEndAnchor && scrollEndAnchor.scrollIntoView) {
        scrollEndAnchor.scrollIntoView({ block: "end", behavior: "instant" });
      }
      chatMain.scrollTop = chatMain.scrollHeight;
      var last = messageFeed.lastElementChild;
      if (last && last.scrollIntoView) {
        last.scrollIntoView({ block: "end", behavior: "instant" });
      }
    }
    run();
    requestAnimationFrame(run);
    setTimeout(run, 50);
    setTimeout(run, 200);
    setTimeout(run, 500);
  }

  function updateLimitUi() {
    if (!status) return;
    var used = status.messages_today || 0;
    var limit = status.daily_limit || 50;
    var tz = status.tz_label ? " · " + status.tz_label : "";
    limitLine.textContent = used + " / " + limit + " сообщений сегодня" + tz;

    var blocked = !status.can_send;
    var bannerWasHidden = limitBanner.hidden;
    limitBanner.hidden = !blocked;
    if (blocked) {
      limitBannerText.textContent =
        "Лимит на сегодня исчерпан (" + used + " / " + limit + "). " +
        "Завтра будет новый лимит.";
    }
    messageInput.disabled = blocked || busy;
    sendBtn.disabled = blocked || busy || !messageInput.value.trim();
    if (blocked && bannerWasHidden) scrollToBottom();
  }

  function renderMessages(items) {
    messageFeed.innerHTML = "";
    if (!items || !items.length) {
      welcomeBlock.hidden = false;
      return;
    }
    welcomeBlock.hidden = true;
    items.forEach(function (m) {
      var li = document.createElement("li");
      var role = m.role === "user" ? "user" : "assistant";
      li.className = "och-bubble och-bubble--" + role;
      li.textContent = m.content || "";
      messageFeed.appendChild(li);
    });
    scrollToBottom();
  }

  function appendBubble(role, text, extraClass) {
    welcomeBlock.hidden = true;
    var li = document.createElement("li");
    li.className =
      "och-bubble och-bubble--" + role + (extraClass ? " " + extraClass : "");
    li.textContent = text;
    messageFeed.appendChild(li);
    scrollToBottom();
    return li;
  }

  function apiFetch(path, options) {
    return fetch(path, options).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok) {
          var err = new Error(body.error || "request failed");
          err.code = body.code;
          err.status = r.status;
          throw err;
        }
        return body;
      });
    });
  }

  function loadStatus() {
    return apiFetch("/api/ai/status", { headers: apiHeaders() }).then(function (data) {
      status = data;
      conversationId = data.conversation_id;
      settingsId.textContent = "MAX id · " + data.max_user_id;
      settingsLimit.textContent =
        "Лимит · " + (data.messages_today || 0) + " / " + (data.daily_limit || 50) + " сегодня";
      updateLimitUi();
      return data;
    });
  }

  function loadMessages() {
    if (!conversationId) return Promise.resolve();
    return apiFetch("/api/ai/messages?conversation_id=" + conversationId, {
      headers: apiHeaders(),
    }).then(function (data) {
      conversationId = data.conversation_id || conversationId;
      renderMessages(data.messages || []);
    });
  }

  function sendMessage() {
    var text = messageInput.value.trim();
    if (!text || busy || !status || !status.can_send) return;
    busy = true;
    sendBtn.disabled = true;
    messageInput.disabled = true;
    appendBubble("user", text);
    messageInput.value = "";
    messageInput.style.height = "auto";
    var typing = appendBubble("assistant", "Печатает…", "och-bubble--typing");

    apiFetch("/api/ai/chat", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
    })
      .then(function (data) {
        typing.remove();
        if (data.assistant_message) {
          appendBubble("assistant", data.assistant_message.content || "");
        }
        status.messages_today = data.messages_today;
        status.daily_limit = data.daily_limit;
        status.can_send = data.can_send;
        conversationId = data.conversation_id || conversationId;
        settingsLimit.textContent =
          "Лимит · " + data.messages_today + " / " + data.daily_limit + " сегодня";
        updateLimitUi();
        scrollToBottom();
        haptic("success");
      })
      .catch(function (err) {
        typing.remove();
        if (err.code === "daily_limit") {
          status.can_send = false;
          updateLimitUi();
          scrollToBottom();
        }
        showToast(err.message || "Ошибка отправки");
      })
      .finally(function () {
        busy = false;
        updateLimitUi();
      });
  }

  function clearDialog() {
    if (!conversationId) return;
    apiFetch("/api/ai/conversations/" + conversationId + "/clear", {
      method: "POST",
      headers: apiHeaders(),
    })
      .then(function () {
        renderMessages([]);
        showToast("Диалог очищен");
        settingsSheet.hidden = true;
      })
      .catch(function () {
        showToast("Не удалось очистить");
      });
  }

  messageInput.addEventListener("input", function () {
    messageInput.style.height = "auto";
    messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + "px";
    if (status) sendBtn.disabled = busy || !status.can_send || !messageInput.value.trim();
  });

  messageInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  sendBtn.addEventListener("click", sendMessage);
  settingsBtn.addEventListener("click", function () {
    settingsSheet.hidden = false;
  });
  settingsBackdrop.addEventListener("click", function () {
    settingsSheet.hidden = true;
  });
  settingsClose.addEventListener("click", function () {
    settingsSheet.hidden = true;
  });
  clearBtn.addEventListener("click", clearDialog);

  function bootChat() {
    if (!setupBridge()) {
      appRoot.hidden = true;
      outsideMax.hidden = false;
      return;
    }
    loadStatus()
      .then(loadMessages)
      .then(scrollToBottom)
      .catch(function () {
        showToast("Не удалось загрузить чат");
      });
  }

  setTimeout(bootChat, webApp && webApp.initData ? 0 : 280);
})();
