/**

 * Bootstrap offline layer: service worker, connectivity, auto-sync.

 */



import { initConnectivity } from "./connectivity.js";

import { initAutoSync } from "./sync.js";



export function initOffline() {

  initConnectivity();

  initAutoSync();



  if (!("serviceWorker" in navigator)) return;



  let refreshing = false;

  navigator.serviceWorker.addEventListener("controllerchange", () => {

    if (refreshing) return;

    refreshing = true;

    window.location.reload();

  });



  navigator.serviceWorker

    .register("/sw.js", { scope: "/", updateViaCache: "none" })

    .then((registration) => {

      registration.update();



      registration.addEventListener("updatefound", () => {

        const worker = registration.installing;

        if (!worker) return;



        worker.addEventListener("statechange", () => {

          if (worker.state === "installed" && navigator.serviceWorker.controller) {

            worker.postMessage({ type: "SKIP_WAITING" });

          }

        });

      });



      document.addEventListener("visibilitychange", () => {

        if (document.visibilityState === "visible") {

          registration.update();

        }

      });

    })

    .catch(() => {

      /* SW optional in dev */

    });

}

