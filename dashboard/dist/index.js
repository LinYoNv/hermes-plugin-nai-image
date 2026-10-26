(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  var React = SDK.React;

  function NaiImageDashboardTab() {
    var basePath = window.__HERMES_BASE_PATH__ || "";
    var src = basePath + "/dashboard-plugins/nai-image/webui/index.html";

    return React.createElement(
      "div",
      {
        style: {
          width: "100%",
          height: "calc(100dvh - 4.5rem)",
          minHeight: "680px",
          border: "1px solid var(--border, rgba(128,128,128,0.25))",
          borderRadius: "var(--radius, 8px)",
          overflow: "hidden",
          background: "#0b0d12",
        },
      },
      React.createElement("iframe", {
        src: src,
        title: "NAI 图像合成与测试终端",
        style: {
          width: "100%",
          height: "100%",
          border: "0",
          display: "block",
        },
      })
    );
  }

  window.__HERMES_PLUGINS__.register("nai-image", NaiImageDashboardTab);
})();
