(function () {
  "use strict";

  function resolveSessionToken() {
    try {
      if (window.parent && window.parent.__HERMES_SESSION_TOKEN__) {
        return window.parent.__HERMES_SESSION_TOKEN__;
      }
    } catch (_) {}
    return window.__HERMES_SESSION_TOKEN__ || "";
  }

  function resolveBasePath() {
    try {
      if (window.parent && typeof window.parent.__HERMES_BASE_PATH__ === "string") {
        return window.parent.__HERMES_BASE_PATH__;
      }
    } catch (_) {}
    return "";
  }

  function buildHeaders(extra) {
    var h = Object.assign({}, extra || {});
    var tok = resolveSessionToken();
    if (tok) h["X-Hermes-Session-Token"] = tok;
    return h;
  }

  function buildUrl(endpoint, params) {
    var clean = String(endpoint || "").replace(/^\/+/, "");
    var url = resolveBasePath() + "/api/plugins/nai-image/" + clean;
    if (params && typeof params === "object") {
      var qs = new URLSearchParams();
      Object.keys(params).forEach(function (k) {
        var v = params[k];
        if (v !== undefined && v !== null) qs.set(k, String(v));
      });
      var s = qs.toString();
      if (s) url += (url.indexOf("?") === -1 ? "?" : "&") + s;
    }
    return url;
  }

  var ctxObj = { locale: "zh-CN", theme: "dark" };

  window.AstrBotPluginPage = {
    ready: function () {
      return Promise.resolve(ctxObj);
    },
    getContext: function () {
      return ctxObj;
    },
    onContext: function (cb) {
      if (typeof cb === "function") {
        try { cb(ctxObj); } catch (_) {}
      }
      return function () {};
    },
    apiGet: async function (endpoint, params) {
      var resp = await fetch(buildUrl(endpoint, params), {
        method: "GET",
        credentials: "include",
        headers: buildHeaders(),
      });
      var data = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        var msg = (data && (data.message || data.detail)) || ("HTTP " + resp.status);
        throw new Error(msg);
      }
      return data;
    },
    apiPost: async function (endpoint, body) {
      var resp = await fetch(buildUrl(endpoint), {
        method: "POST",
        credentials: "include",
        headers: buildHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body || {}),
      });
      var data = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        var msg = (data && (data.message || data.detail)) || ("HTTP " + resp.status);
        throw new Error(msg);
      }
      return data;
    },
  };
})();
