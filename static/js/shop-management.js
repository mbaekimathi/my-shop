(() => {
  const body = document.body;

  let placesReady = false;
  let autocompleteService = null;
  let placesService = null;
  let geocoder = null;
  const fieldStates = new WeakMap();

  const setModalOpen = (modal, open) => {
    if (!modal) return;
    modal.hidden = !open;
    modal.setAttribute("aria-hidden", open ? "false" : "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
    if (open) {
      const firstField = modal.querySelector(
        "input:not([type=hidden]):not([type=password]), textarea, select"
      );
      firstField?.focus();
      if (window.initUppercaseInputs) window.initUppercaseInputs(modal);
      if (window.lucide?.createIcons) window.lucide.createIcons();
      modal.querySelectorAll("[data-shop-location-field]").forEach(initLocationField);
    }
  };

  const bindPasswordMatch = (form) => {
    if (!form || form.dataset.passwordMatchBound === "true") return;
    const password = form.querySelector("[data-password]");
    const passwordConfirm = form.querySelector("[data-password-confirm]");
    const matchStatus = form.querySelector("[data-password-match]");
    if (!password || !passwordConfirm) return;

    form.dataset.passwordMatchBound = "true";

    const setMatchStatus = (state, message) => {
      if (!matchStatus) return;
      matchStatus.textContent = message || "";
      matchStatus.className = "field-hint";
      if (state === "ok") matchStatus.classList.add("field-hint--ok");
      if (state === "error") matchStatus.classList.add("field-hint--error");
    };

    const checkPasswordMatch = () => {
      const a = password.value || "";
      const b = passwordConfirm.value || "";

      if (!a && !b) {
        setMatchStatus("", "");
        password.classList.remove("is-invalid");
        passwordConfirm.classList.remove("is-invalid");
        return true;
      }

      if (a && a.length < 6) {
        setMatchStatus("error", "Password must be at least 6 characters.");
        return false;
      }

      if (b && a !== b) {
        setMatchStatus("error", "Password and confirm password do not match.");
        password.classList.add("is-invalid");
        passwordConfirm.classList.add("is-invalid");
        return false;
      }

      if (a && b && a === b) {
        setMatchStatus("ok", "Passwords match.");
        password.classList.remove("is-invalid");
        passwordConfirm.classList.remove("is-invalid");
        return true;
      }

      setMatchStatus("", "");
      return true;
    };

    password.addEventListener("input", checkPasswordMatch);
    passwordConfirm.addEventListener("input", checkPasswordMatch);
  };

  document.querySelectorAll(".workspace-modal-form").forEach(bindPasswordMatch);

  const ensurePlacesServices = () => {
    if (!window.google?.maps?.places) return false;
    placesReady = true;
    autocompleteService =
      autocompleteService || new window.google.maps.places.AutocompleteService();
    if (!placesService) {
      const holder = document.createElement("div");
      holder.hidden = true;
      document.body.append(holder);
      placesService = new window.google.maps.places.PlacesService(holder);
    }
    geocoder = geocoder || new window.google.maps.Geocoder();
    return true;
  };

  const getFieldParts = (root) => ({
    root,
    input: root.querySelector("[data-shop-location-input]"),
    suggestions: root.querySelector("[data-shop-location-suggestions]"),
    selected: root.querySelector("[data-shop-location-selected]"),
    hint: root.querySelector("[data-shop-location-hint]"),
    latInput: root.querySelector("[data-shop-location-lat]"),
    lngInput: root.querySelector("[data-shop-location-lng]"),
    mapsUrlInput: root.querySelector("[data-shop-location-maps-url]"),
    currentBtn: root.querySelector("[data-shop-location-current]"),
  });

  const hideSuggestions = (parts) => {
    if (!parts.suggestions) return;
    parts.suggestions.hidden = true;
    parts.suggestions.textContent = "";
    const state = fieldStates.get(parts.root);
    if (state) state.activeIndex = -1;
  };

  const setCoords = (parts, { address = "", mapsUrl = "", lat = null, lng = null } = {}) => {
    let nextLat = lat == null || lat === "" ? null : Number(lat);
    let nextLng = lng == null || lng === "" ? null : Number(lng);
    if (Number.isNaN(nextLat) || Number.isNaN(nextLng)) {
      nextLat = null;
      nextLng = null;
    }
    if (parts.input && address) {
      parts.input.value = String(address).trim();
      if (window.initUppercaseInputs) window.initUppercaseInputs(parts.root);
    }
    if (parts.latInput) parts.latInput.value = nextLat == null ? "" : String(nextLat);
    if (parts.lngInput) parts.lngInput.value = nextLng == null ? "" : String(nextLng);
    if (parts.mapsUrlInput) parts.mapsUrlInput.value = String(mapsUrl || "").trim();
    if (parts.selected) {
      if (nextLat != null && nextLng != null) {
        parts.selected.hidden = false;
        parts.selected.classList.remove("is-error");
        parts.selected.textContent = `Pin · ${nextLat.toFixed(5)}, ${nextLng.toFixed(5)}`;
      } else {
        parts.selected.hidden = true;
        parts.selected.textContent = "";
        parts.selected.classList.remove("is-error");
      }
    }
  };

  const clearCoordsKeepText = (parts) => {
    if (parts.latInput) parts.latInput.value = "";
    if (parts.lngInput) parts.lngInput.value = "";
    if (parts.mapsUrlInput) parts.mapsUrlInput.value = "";
    if (parts.selected) {
      parts.selected.hidden = true;
      parts.selected.textContent = "";
      parts.selected.classList.remove("is-error");
    }
  };

  const selectPrediction = (parts, prediction) => {
    const address = prediction?.description || "";
    if (!address) return;
    if (parts.input) parts.input.value = address;
    hideSuggestions(parts);

    const applyPlace = (place) => {
      let lat = null;
      let lng = null;
      let mapsUrl = place?.url || "";
      if (place?.geometry?.location) {
        lat = place.geometry.location.lat();
        lng = place.geometry.location.lng();
        if (!mapsUrl) {
          mapsUrl = `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
        }
      } else if (!mapsUrl && address) {
        mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`;
      }
      const formatted = place?.formatted_address || address;
      setCoords(parts, { address: formatted, mapsUrl, lat, lng });
    };

    if (placesService && prediction?.place_id) {
      placesService.getDetails(
        {
          placeId: prediction.place_id,
          fields: ["formatted_address", "geometry", "url", "name"],
        },
        (place, status) => {
          if (
            status === window.google.maps.places.PlacesServiceStatus.OK &&
            place
          ) {
            applyPlace(place);
            return;
          }
          applyPlace(null);
        }
      );
      return;
    }
    applyPlace(null);
  };

  const renderSuggestions = (parts, predictions) => {
    if (!parts.suggestions) return;
    parts.suggestions.textContent = "";
    if (!predictions.length) {
      hideSuggestions(parts);
      return;
    }
    predictions.forEach((prediction, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "shop-location-suggestion";
      button.setAttribute("role", "option");
      button.dataset.index = String(index);
      const main = document.createElement("strong");
      main.textContent =
        prediction.structured_formatting?.main_text || prediction.description;
      const secondary = document.createElement("small");
      secondary.textContent =
        prediction.structured_formatting?.secondary_text || "Google Maps result";
      button.append(main, secondary);
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
        selectPrediction(parts, prediction);
      });
      item.append(button);
      parts.suggestions.append(item);
    });
    parts.suggestions.hidden = false;
    const state = fieldStates.get(parts.root);
    if (state) {
      state.activeIndex = -1;
      state.predictions = predictions;
    }
  };

  const fetchSuggestions = (parts, query) => {
    if (!placesReady || !autocompleteService) {
      hideSuggestions(parts);
      return;
    }
    const input = String(query || "").trim();
    if (input.length < 2) {
      hideSuggestions(parts);
      return;
    }
    autocompleteService.getPlacePredictions(
      {
        input,
        componentRestrictions: { country: "ke" },
      },
      (predictions, status) => {
        if (status !== window.google.maps.places.PlacesServiceStatus.OK) {
          if (
            status ===
            window.google.maps.places.PlacesServiceStatus.ZERO_RESULTS
          ) {
            autocompleteService.getPlacePredictions(
              { input },
              (fallback, fallbackStatus) => {
                if (
                  fallbackStatus !==
                  window.google.maps.places.PlacesServiceStatus.OK
                ) {
                  hideSuggestions(parts);
                  return;
                }
                renderSuggestions(parts, (fallback || []).slice(0, 6));
              }
            );
            return;
          }
          hideSuggestions(parts);
          return;
        }
        renderSuggestions(parts, (predictions || []).slice(0, 6));
      }
    );
  };

  const useCurrentLocation = (parts) => {
    if (!navigator.geolocation) {
      if (parts.selected) {
        parts.selected.hidden = false;
        parts.selected.classList.add("is-error");
        parts.selected.textContent = "Geolocation is not supported on this device.";
      }
      return;
    }
    if (parts.currentBtn) {
      parts.currentBtn.disabled = true;
      parts.currentBtn.classList.add("is-busy");
    }
    if (parts.selected) {
      parts.selected.hidden = false;
      parts.selected.classList.remove("is-error");
      parts.selected.textContent = "Getting current location…";
    }

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const lat = position.coords.latitude;
        const lng = position.coords.longitude;
        const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
        const applyCoords = (address) => {
          setCoords(parts, {
            address: address || `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
            mapsUrl,
            lat,
            lng,
          });
          if (parts.currentBtn) {
            parts.currentBtn.disabled = false;
            parts.currentBtn.classList.remove("is-busy");
          }
        };

        if (geocoder || ensurePlacesServices()) {
          geocoder.geocode({ location: { lat, lng } }, (results, status) => {
            if (
              status === window.google.maps.GeocoderStatus.OK &&
              results?.[0]?.formatted_address
            ) {
              applyCoords(results[0].formatted_address);
              return;
            }
            applyCoords("");
          });
          return;
        }
        applyCoords("");
      },
      (error) => {
        if (parts.currentBtn) {
          parts.currentBtn.disabled = false;
          parts.currentBtn.classList.remove("is-busy");
        }
        if (parts.selected) {
          parts.selected.hidden = false;
          parts.selected.classList.add("is-error");
          parts.selected.textContent =
            error?.code === 1
              ? "Location permission denied. Allow access or search the address."
              : "Could not read current location. Try searching instead.";
        }
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
    );
  };

  const initLocationField = (root) => {
    if (!root || root.dataset.locationBound === "true") return;
    const parts = getFieldParts(root);
    if (!parts.input) return;
    root.dataset.locationBound = "true";
    fieldStates.set(root, { activeIndex: -1, predictions: [], debounce: null });

    const mapsEnabled = parts.input.dataset.mapsEnabled === "1";
    if (mapsEnabled) ensurePlacesServices();

    parts.input.addEventListener("input", () => {
      clearCoordsKeepText(parts);
      const state = fieldStates.get(root);
      if (!mapsEnabled) return;
      window.clearTimeout(state.debounce);
      state.debounce = window.setTimeout(() => {
        fetchSuggestions(parts, parts.input.value);
      }, 220);
    });

    parts.input.addEventListener("keydown", (event) => {
      const state = fieldStates.get(root);
      const buttons = parts.suggestions
        ? Array.from(
            parts.suggestions.querySelectorAll(".shop-location-suggestion")
          )
        : [];
      if (!buttons.length) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        state.activeIndex = Math.min(state.activeIndex + 1, buttons.length - 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        state.activeIndex = Math.max(state.activeIndex - 1, 0);
      } else if (event.key === "Enter" && state.activeIndex >= 0) {
        event.preventDefault();
        const prediction = state.predictions[state.activeIndex];
        if (prediction) selectPrediction(parts, prediction);
        return;
      } else if (event.key === "Escape") {
        hideSuggestions(parts);
        return;
      } else {
        return;
      }
      buttons.forEach((btn, index) => {
        btn.classList.toggle("is-active", index === state.activeIndex);
      });
    });

    parts.input.addEventListener("blur", () => {
      window.setTimeout(() => hideSuggestions(parts), 150);
    });

    parts.currentBtn?.addEventListener("click", () => {
      if (!mapsEnabled) return;
      ensurePlacesServices();
      useCurrentLocation(parts);
    });
  };

  const resetLocationField = (root) => {
    const parts = getFieldParts(root);
    if (parts.input) parts.input.value = "";
    setCoords(parts, {});
    hideSuggestions(parts);
  };

  const fillLocationField = (root, { location, latitude, longitude, mapsUrl }) => {
    const parts = getFieldParts(root);
    setCoords(parts, {
      address: location || "",
      mapsUrl: mapsUrl || "",
      lat: latitude,
      lng: longitude,
    });
    hideSuggestions(parts);
  };

  window.initShopManagementPlaces = () => {
    if (!ensurePlacesServices()) return;
    document
      .querySelectorAll("[data-shop-location-field]")
      .forEach(initLocationField);
  };

  document.querySelectorAll(".workspace-modal").forEach((modal) => {
    const name = modal.dataset.modal;
    if (!name) return;

    document.querySelectorAll(`[data-modal-open="${name}"]`).forEach((trigger) => {
      trigger.addEventListener("click", () => {
        if (name === "register-shop") {
          modal.querySelectorAll('input[type="password"]').forEach((input) => {
            input.value = "";
          });
          const matchStatus = modal.querySelector("[data-password-match]");
          if (matchStatus) {
            matchStatus.textContent = "";
            matchStatus.className = "field-hint";
          }
          modal
            .querySelectorAll("[data-shop-location-field]")
            .forEach(resetLocationField);
        }
        setModalOpen(modal, true);
      });
    });

    modal.querySelectorAll("[data-modal-close]").forEach((el) => {
      el.addEventListener("click", () => setModalOpen(modal, false));
    });

    if (!modal.hidden) body.classList.add("workspace-modal-open");
  });

  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".workspace-modal:not([hidden])").forEach((modal) => {
      setModalOpen(modal, false);
    });
  });

  const editModal = document.querySelector('[data-modal="edit-shop"]');
  const editForm = editModal?.querySelector("[data-edit-form]");
  const shopIdInput = editForm?.querySelector("[data-edit-shop-id]");
  const imageWrap = editForm?.querySelector("[data-current-image-wrap]");
  const currentImage = editForm?.querySelector("[data-current-image]");
  const removeImage = editForm?.querySelector("[data-remove-image]");

  const setField = (name, value) => {
    const field = editForm?.querySelector(`[name="${name}"]`);
    if (field) field.value = value ?? "";
  };

  document.querySelectorAll("[data-edit-shop]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!editForm) return;

      const dataset = button.dataset;
      if (shopIdInput) shopIdInput.value = dataset.shopId || "";
      setField("name", dataset.name);
      setField("email", dataset.email);
      setField("phone_number", dataset.phoneNumber);
      setField("login_code", dataset.loginCode);
      setField("password", "");
      setField("password_confirm", "");

      const matchStatus = editForm.querySelector("[data-password-match]");
      if (matchStatus) {
        matchStatus.textContent = "";
        matchStatus.className = "field-hint";
      }

      const locationRoot = editForm.querySelector("[data-shop-location-field]");
      if (locationRoot) {
        initLocationField(locationRoot);
        fillLocationField(locationRoot, {
          location: dataset.location || "",
          latitude: dataset.latitude || "",
          longitude: dataset.longitude || "",
          mapsUrl: dataset.mapsUrl || "",
        });
      } else {
        setField("location", dataset.location);
      }

      if (window.initUppercaseInputs) window.initUppercaseInputs(editForm);

      const fileInput = editForm.querySelector('input[type="file"][name="image"]');
      if (fileInput) fileInput.value = "";
      if (removeImage) removeImage.checked = false;

      if (dataset.imageUrl && imageWrap && currentImage) {
        currentImage.src = dataset.imageUrl;
        imageWrap.hidden = false;
      } else if (imageWrap) {
        imageWrap.hidden = true;
        if (currentImage) currentImage.removeAttribute("src");
      }

      setModalOpen(editModal, true);
    });
  });

  document.querySelectorAll("[data-confirm-delete]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const name = form.dataset.shopName || "this shop";
      if (!window.confirm(`Delete “${name}”? This cannot be undone.`)) {
        event.preventDefault();
      }
    });
  });

  const searchInput = document.querySelector("[data-shop-search]");
  const rows = Array.from(document.querySelectorAll("[data-shop-row]"));
  const noResults = document.querySelector("[data-shop-no-results]");
  const visibleCount = document.querySelector("[data-shop-visible-count]");
  const total = Number(
    document.querySelector("[data-shop-total]")?.dataset.shopTotal || rows.length
  );

  const updateSearch = () => {
    const query = (searchInput?.value || "").trim().toLowerCase();
    let shown = 0;
    rows.forEach((row) => {
      const haystack = row.dataset.searchText || "";
      const match = !query || haystack.includes(query);
      row.hidden = !match;
      if (match) shown += 1;
    });
    if (noResults) noResults.hidden = shown > 0 || rows.length === 0;
    if (visibleCount) {
      visibleCount.textContent = query
        ? `${shown} of ${total} shop${total === 1 ? "" : "s"}`
        : `${total} shop${total === 1 ? "" : "s"}`;
    }
  };

  searchInput?.addEventListener("input", updateSearch);

  document
    .querySelectorAll("[data-shop-location-field]")
    .forEach(initLocationField);
})();
