(function () {
  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  }

  function todayIso() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  function toast(msg) {
    var el = $("toast");
    if (!el) return alert(msg);
    el.textContent = msg;
    el.hidden = false;
    el.classList.add("tk-toast--show");
    setTimeout(function () {
      el.classList.remove("tk-toast--show");
      el.hidden = true;
    }, 2400);
  }

  var exportToday = $("exportToday");
  if (exportToday) {
    exportToday.href = "/api/taksimo/export/registry.xlsx?date=" + todayIso();
  }

  function loadFileList() {
    var ul = $("fileList");
    if (!ul) return;
    fetch("/api/taksimo/docs", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        ul.innerHTML = "";
        var files = data.files || [];
        if (!files.length) {
          ul.innerHTML = "<li>Пока пусто</li>";
          return;
        }
        files.slice(0, 15).forEach(function (f) {
          var li = document.createElement("li");
          li.innerHTML = "<strong>" + esc(f.name) + "</strong><br>" + esc(f.folder) + " · " + Math.round(f.size / 1024) + " КБ";
          ul.appendChild(li);
        });
      })
      .catch(function () {
        ul.innerHTML = "<li>Не удалось загрузить список</li>";
      });
  }

  loadFileList();

  var uploadBtn = $("uploadBtn");
  if (uploadBtn) {
    uploadBtn.addEventListener("click", function () {
      var input = $("uploadFile");
      if (!input.files || !input.files[0]) {
        alert("Выберите файл");
        return;
      }
      var fd = new FormData();
      fd.append("folder", $("uploadFolder").value);
      fd.append("file", input.files[0]);
      uploadBtn.disabled = true;
      fetch("/api/taksimo/docs/upload", { method: "POST", credentials: "same-origin", body: fd })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) throw new Error(res.data.error || "Ошибка");
          toast("Загружено: " + res.data.file.name);
          input.value = "";
          loadFileList();
        })
        .catch(function (err) { alert(err.message); })
        .finally(function () { uploadBtn.disabled = false; });
    });
  }
})();
