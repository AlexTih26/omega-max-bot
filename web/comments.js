(function () {
  const postTitle = document.getElementById("postTitle");
  const postHint = document.getElementById("postHint");
  const commentList = document.getElementById("commentList");
  const emptyState = document.getElementById("emptyState");
  const outsideMax = document.getElementById("outsideMax");
  const form = document.getElementById("commentForm");
  const textInput = document.getElementById("textInput");
  const composerAvatar = document.getElementById("composerAvatar");
  const commentsEndAnchor = document.getElementById("commentsEndAnchor");

  var webApp = window.WebApp || null;
  var initData = "";
  var postId = null;
  var viewer = null;
  var replyToId = null;
  var activeCommentId = null;
  var scrollToEndNext = true;

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

  function viewerFromBridge() {
    if (!webApp || !webApp.initDataUnsafe || !webApp.initDataUnsafe.user) return null;
    var u = webApp.initDataUnsafe.user;
    var first = (u.first_name || "").trim();
    var last = (u.last_name || "").trim();
    var username = (u.username || "").trim();
    var author = first && last ? first + " " + last : first || (username ? "@" + username : "Пользователь MAX");
    var photo = u.photo_url || u.avatar_url || u.photo || u.avatar || null;
    return { id: u.id, author: author, author_photo: photo };
  }

  function closeMiniApp() {
    if (webApp && typeof webApp.close === "function") {
      webApp.close();
      return;
    }
    if (window.history.length > 1) window.history.back();
  }

  function setupBridge() {
    if (!webApp) return false;
    initData = webApp.initData || "";
    if (webApp.disableClosingConfirmation) webApp.disableClosingConfirmation();
    var onBack = function () { closeMiniApp(); };
    if (webApp.BackButton) {
      if (webApp.BackButton.onClick) webApp.BackButton.onClick(onBack);
      if (webApp.BackButton.show) webApp.BackButton.show();
    }
    if (webApp.onEvent) webApp.onEvent("WebAppBackButtonPressed", onBack);
    return Boolean(initData);
  }

  function signalReady() {
    if (webApp && typeof webApp.ready === "function") webApp.ready();
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  function initials(name) {
    var n = (name || "?").replace(/^@/, "").trim();
    var parts = n.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return (n[0] || "?").toUpperCase();
  }

  function mountAvatar(container, photo, name, sizePx) {
    var size = sizePx || 40;
    container.innerHTML = "";
    container.className = "comment-avatar-slot";

    function applySize(el) {
      el.style.width = size + "px";
      el.style.height = size + "px";
      el.style.maxWidth = size + "px";
      el.style.maxHeight = size + "px";
      el.style.borderRadius = "50%";
      el.style.objectFit = "cover";
      el.style.display = "block";
      el.style.flexShrink = "0";
    }

    if (photo && /^https?:\/\//i.test(photo)) {
      var img = document.createElement("img");
      img.className = "comment-avatar";
      img.src = photo;
      img.alt = name || "";
      img.referrerPolicy = "no-referrer";
      img.loading = "lazy";
      img.decoding = "async";
      applySize(img);
      img.onerror = function () {
        img.remove();
        var el = document.createElement("div");
        el.className = "comment-avatar comment-avatar--initials";
        el.textContent = initials(name);
        applySize(el);
        container.appendChild(el);
      };
      container.appendChild(img);
    } else {
      var el = document.createElement("div");
      el.className = "comment-avatar comment-avatar--initials";
      el.textContent = initials(name);
      applySize(el);
      container.appendChild(el);
    }
  }

  function formatTime(ts) {
    return new Date(ts * 1000).toLocaleString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function clearReplyTarget() {
    replyToId = null;
    textInput.placeholder = "Сообщение";
    document.querySelectorAll(".comment-bubble--active").forEach(function (el) {
      el.classList.remove("comment-bubble--active");
    });
  }

  function setReplyTarget(c) {
    if (replyToId === c.id) {
      clearReplyTarget();
      return;
    }
    replyToId = c.id;
    textInput.placeholder = "Ответ " + c.author + "…";
    textInput.focus();
    highlightComment(c.id);
  }

  function highlightComment(id) {
    document.querySelectorAll(".comment-bubble--active").forEach(function (el) {
      el.classList.remove("comment-bubble--active");
    });
    var row = document.querySelector('[data-comment-id="' + id + '"]');
    if (row) {
      var bubble = row.querySelector(".comment-bubble");
      if (bubble) bubble.classList.add("comment-bubble--active");
      row.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function countComments(tree) {
    var n = 0;
    tree.forEach(function (c) {
      n += 1;
      if (c.replies && c.replies.length) n += countComments(c.replies);
    });
    return n;
  }

  function pluralComments(n) {
    if (n % 10 === 1 && n % 100 !== 11) return n + " комментарий";
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) return n + " комментария";
    return n + " комментариев";
  }

  function buildLikeRow(likes, commentId) {
    var wrap = document.createElement("div");
    wrap.className = "comment-like-wrap";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "comment-like-btn" + (likes.liked ? " comment-like-btn--active" : "");
    var parts = [];
    if (likes.liked) {
      parts.push('<span class="comment-like-icon">👍</span>');
    } else {
      parts.push('<span class="comment-like-label">Нравится</span>');
    }
    if (likes.count > 0) {
      parts.push('<span class="comment-like-count">' + likes.count + "</span>");
    }
    btn.innerHTML = parts.join("");

    if (initData) {
      btn.addEventListener("click", function () {
        toggleLike(commentId, btn);
      });
    } else {
      btn.disabled = true;
    }

    wrap.appendChild(btn);

    if (likes.count > 0 && likes.users && likes.users.length) {
      var av = document.createElement("span");
      av.className = "comment-like-avatars";
      likes.users.slice(0, 3).forEach(function (u) {
        var slot = document.createElement("span");
        mountAvatar(slot, u.author_photo, u.author, 20);
        av.appendChild(slot);
      });
      wrap.appendChild(av);
    }
    return wrap;
  }

  async function toggleLike(commentId, btn) {
    btn.disabled = true;
    try {
      var res = await fetch("/api/comments/" + commentId + "/like", {
        method: "POST",
        headers: { "X-Max-Init-Data": initData },
      });
      if (!res.ok) throw new Error("Не удалось");
      await load();
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
    }
  }

  function renderQuote(replyTo, usePurple) {
    if (!replyTo) return null;
    var q = document.createElement("button");
    q.type = "button";
    q.className = "comment-quote" + (usePurple ? " comment-quote--purple" : "");
    q.innerHTML =
      '<span class="comment-quote-author">' + escapeHtml(replyTo.author) + "</span>" +
      '<span class="comment-quote-text">' + escapeHtml(replyTo.text) + "</span>";
    q.addEventListener("click", function () {
      highlightComment(replyTo.id);
    });
    return q;
  }

  function renderCommentNode(c, nested) {
    var thread = document.createElement("li");
    thread.className = "comment-thread";
    thread.dataset.commentId = String(c.id);

    var row = document.createElement("div");
    row.className = "comment-row" + (nested ? " comment-row--nested" : "");

    var avSlot = document.createElement("div");
    mountAvatar(avSlot, c.author_photo, c.author, 40);
    row.appendChild(avSlot);

    var column = document.createElement("div");
    column.className = "comment-column";

    var bubble = document.createElement("div");
    bubble.className = "comment-bubble";
    if (activeCommentId === c.id) bubble.classList.add("comment-bubble--active");

    var quote = renderQuote(c.reply_to, nested);
    if (quote) bubble.appendChild(quote);

    var head = document.createElement("div");
    head.className = "comment-head";
    head.innerHTML =
      '<span class="comment-author-name">' + escapeHtml(c.author) + "</span>" +
      '<span class="comment-time">' + formatTime(c.created_at) + "</span>";
    bubble.appendChild(head);

    var body = document.createElement("p");
    body.className = "comment-body";
    body.textContent = c.text;
    bubble.appendChild(body);

    var footer = document.createElement("div");
    footer.className = "comment-footer";
    footer.appendChild(buildLikeRow(c.likes || { count: 0, liked: false, users: [] }, c.id));

    if (initData) {
      var replyBtn = document.createElement("button");
      replyBtn.type = "button";
      replyBtn.className = "comment-reply-btn";
      replyBtn.textContent = "Ответить";
      replyBtn.addEventListener("click", function () {
        setReplyTarget(c);
      });
      if (replyToId === c.id) {
        replyBtn.classList.add("comment-reply-btn--active");
      }
      footer.appendChild(replyBtn);
    }
    bubble.appendChild(footer);
    column.appendChild(bubble);

    if (c.replies && c.replies.length) {
      var sub = document.createElement("ul");
      sub.className = "comment-replies-wrap";
      c.replies.forEach(function (child) {
        sub.appendChild(renderCommentNode(child, true));
      });
      column.appendChild(sub);
    }

    row.appendChild(column);
    thread.appendChild(row);
    return thread;
  }

  function renderComments(tree) {
    commentList.innerHTML = "";
    if (!tree.length) {
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;
    tree.forEach(function (c) {
      commentList.appendChild(renderCommentNode(c, false));
    });
  }

  function scrollToLatest() {
    function run() {
      if (commentsEndAnchor) {
        commentsEndAnchor.scrollIntoView({ block: "end", behavior: "instant" });
      }
      var main = document.querySelector(".comments-main");
      if (main) {
        main.scrollTop = main.scrollHeight;
      }
      var last = commentList.lastElementChild;
      if (last) {
        last.scrollIntoView({ block: "end", behavior: "instant" });
      }
      window.scrollTo(0, document.body.scrollHeight);
    }
    run();
    requestAnimationFrame(run);
    setTimeout(run, 80);
    setTimeout(run, 250);
    setTimeout(run, 500);
  }

  function scrollToCommentId(id) {
    var el = document.querySelector('[data-comment-id="' + id + '"]');
    if (el) {
      el.scrollIntoView({ block: "end", behavior: "instant" });
      var bubble = el.querySelector(".comment-bubble");
      if (bubble) {
        bubble.classList.add("comment-bubble--active");
        setTimeout(function () {
          bubble.classList.remove("comment-bubble--active");
        }, 2500);
      }
    }
    scrollToLatest();
  }

  function updateComposerAvatar() {
    var v = viewer || viewerFromBridge();
    if (v) mountAvatar(composerAvatar, v.author_photo, v.author, 36);
  }

  async function load() {
    if (!postId) {
      postTitle.textContent = "Пост не найден";
      postHint.textContent = "Откройте обсуждение из канала MAX.";
      return;
    }

    var headers = {};
    if (initData) headers["X-Max-Init-Data"] = initData;

    try {
      var res = await fetch("/api/posts/" + encodeURIComponent(postId), { headers: headers });
      if (!res.ok) {
        throw new Error(res.status === 404 ? "Пост ещё не зарегистрирован" : "Ошибка загрузки");
      }
      var data = await res.json();
      viewer = data.viewer || viewerFromBridge();
      updateComposerAvatar();

      var tree = data.comments || [];
      postTitle.textContent = data.post.title || "Пост в канале";
      var total = countComments(tree);
      postHint.textContent = total === 0 ? "0 комментариев" : pluralComments(total);
      renderComments(tree);
      if (scrollToEndNext) {
        scrollToLatest();
        scrollToEndNext = false;
      }
      return data;
    } catch (e) {
      postTitle.textContent = "Ошибка";
      postHint.innerHTML = '<span class="comments-error">' + escapeHtml(e.message) + "</span>";
      form.hidden = true;
    }
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    var text = textInput.value.trim();
    if (!text || !postId || !initData) return;

    var btn = form.querySelector(".comment-send");
    btn.disabled = true;
    var body = { text: text };
    if (replyToId !== null) body.parent_id = replyToId;

    try {
      var res = await fetch("/api/posts/" + encodeURIComponent(postId) + "/comments", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Max-Init-Data": initData,
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        var err = await res.json().catch(function () { return {}; });
        throw new Error(err.error || "Не удалось отправить");
      }
      var posted = await res.json();
      var newId = posted.comment && posted.comment.id;
      textInput.value = "";
      clearReplyTarget();
      scrollToEndNext = true;
      await load();
      if (newId) {
        scrollToCommentId(newId);
      } else {
        scrollToLatest();
      }
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  function boot() {
    var inMax = setupBridge();
    postId = resolvePostId();
    viewer = viewerFromBridge();

    if (!inMax) {
      outsideMax.hidden = false;
      form.hidden = true;
      if (postId) load().then(signalReady).catch(signalReady);
      return;
    }

    outsideMax.hidden = true;
    form.hidden = false;
    updateComposerAvatar();
    scrollToEndNext = true;
    load()
      .then(function () {
        signalReady();
        scrollToLatest();
        setTimeout(scrollToLatest, 400);
      })
      .catch(signalReady);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
