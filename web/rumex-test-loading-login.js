(function () {
  "use strict";

  var API = "/api/rumex-registry/test/auth";
  var form = document.getElementById("loginForm");
  var username = document.getElementById("username");
  var password = document.getElementById("password");
  var button = document.getElementById("loginButton");
  var error = document.getElementById("loginError");
  var params = new URLSearchParams(location.search);
  var next = params.get("next") || "/rumex-test-loading.html";
  if (!next.startsWith("/") || next.startsWith("//")) next = "/rumex-test-loading.html";

  function responseBody(response) {
    return response.text().then(function (text) {
      try {
        return text ? JSON.parse(text) : {};
      } catch (ignored) {
        return {};
      }
    });
  }

  function responseError(response, body) {
    if (body && body.error) return body.error;
    if (response.status === 404) return "Сервер тестового кабинета ещё не обновлён. Повторите через минуту.";
    return "Сервер вернул ошибку. Повторите позже.";
  }

  fetch(API + "/check", { credentials: "same-origin" })
    .then(function (response) {
      return responseBody(response).then(function (body) {
        if (!response.ok) throw new Error(responseError(response, body));
        return body;
      });
    })
    .then(function (body) {
      if (!body.configured) {
        error.textContent = "Парольный вход пока не настроен. Откройте кабинет через Mini App MAX.";
      } else if (body.authenticated) {
        location.replace(next);
      }
    })
    .catch(function (err) { error.textContent = err.message || "Не удалось проверить вход"; });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    error.textContent = "";
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }
    button.disabled = true;
    fetch(API, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.value.trim(), password: password.value })
    })
      .then(function (response) {
        return responseBody(response).then(function (body) {
          if (!response.ok) throw new Error(responseError(response, body));
          return body;
        });
      })
      .then(function () { location.replace(next); })
      .catch(function (err) {
        error.textContent = err.message || "Не удалось войти";
        password.select();
        button.disabled = false;
      });
  });
})();
