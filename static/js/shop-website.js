(() => {
  const root = document.querySelector("[data-storefront]");
  if (!root) return;

  const cart = document.querySelector("[data-storefront-cart]");
  const backdrop = document.querySelector("[data-storefront-cart-close]");
  const lines = document.querySelector("[data-storefront-cart-lines]");
  const empty = document.querySelector("[data-storefront-cart-empty]");
  const total = document.querySelector("[data-storefront-cart-total]");
  const countEls = document.querySelectorAll("[data-storefront-cart-count]");
  const whatsappBtn = document.querySelector("[data-storefront-whatsapp]");
  const whatsappHint = document.querySelector("[data-storefront-whatsapp-hint]");
  const whatsappSummary = document.querySelector("[data-storefront-whatsapp-summary]");
  const deliverySection = document.querySelector("[data-storefront-delivery]");
  const deliveryWanted = document.querySelector("[data-delivery-wanted]");
  const deliveryFields = document.querySelector("[data-delivery-fields]");
  const deliveryInput = document.querySelector("[data-delivery-location]");
  const deliverySelected = document.querySelector("[data-delivery-selected]");
  const deliveryHint = document.querySelector("[data-delivery-hint]");
  const deliverySuggestions = document.querySelector("[data-delivery-suggestions]");
  const productModal = document.querySelector("[data-storefront-product-modal]");
  const productMedia = productModal?.querySelector("[data-storefront-product-media]");
  const productCategory = productModal?.querySelector("[data-storefront-product-category]");
  const productName = productModal?.querySelector("[data-storefront-product-name]");
  const productDescription = productModal?.querySelector(
    "[data-storefront-product-description]"
  );
  const productPrice = productModal?.querySelector("[data-storefront-product-price]");
  const productQtyInput = productModal?.querySelector(
    "[data-storefront-product-qty-input]"
  );
  const productAdd = productModal?.querySelector("[data-storefront-product-add]");
  const pairModal = document.querySelector("[data-storefront-pair-modal]");
  const pairList = document.querySelector("[data-storefront-pair-list]");
  const pairCopy = document.querySelector("[data-storefront-pair-copy]");
  const suggestionTemplate = root.dataset.suggestionsUrlTemplate || "";
  const shopName = root.dataset.shopName || "the shop";
  const whatsappPhone = (root.dataset.whatsappPhone || "").replace(/\D+/g, "");
  const cartItems = new Map();
  let lastAddedId = "";
  let pendingPairPopup = false;
  let activeProduct = null;
  let deliveryPlace = {
    wanted: false,
    address: "",
    mapsUrl: "",
  };
  let placesService = null;
  let autocompleteService = null;
  let placesReady = false;
  let suggestionTimer = 0;
  let activeSuggestion = -1;

  const money = (amount) =>
    `KSh ${Number(amount || 0).toLocaleString("en-KE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;

  const absoluteUrl = (path) => {
    const value = String(path || "").trim();
    if (!value) return "";
    try {
      return new URL(value, window.location.origin).href;
    } catch {
      return value;
    }
  };

  const setCartOpen = (open, { fromClose = false } = {}) => {
    const wasOpen = cart?.classList.contains("is-open");
    cart?.classList.toggle("is-open", open);
    cart?.setAttribute("aria-hidden", String(!open));
    if (backdrop) backdrop.hidden = !open;
    document.body.classList.toggle("storefront-cart-open", open);
    if (!open && wasOpen && fromClose) {
      maybeShowPairPopup();
    }
  };

  const setPairOpen = (open) => {
    if (!pairModal) return;
    if (open) {
      pairModal.hidden = false;
      pairModal.removeAttribute("hidden");
    } else {
      pairModal.hidden = true;
      pairModal.setAttribute("hidden", "");
    }
    pairModal.setAttribute("aria-hidden", String(!open));
    document.body.classList.toggle("storefront-pair-open", open);
    if (open && window.lucide?.createIcons) window.lucide.createIcons();
  };

  const productFromElement = (el) => ({
    id: String(el.dataset.productId || ""),
    name: el.dataset.productName || "Item",
    category: el.dataset.productCategory || "",
    description: el.dataset.productDescription || "",
    price: Number(el.dataset.productPrice || 0),
    image: el.dataset.productImage || "",
  });

  const setMedia = (holder, image, name) => {
    if (!holder) return;
    holder.textContent = "";
    const imageUrl = absoluteUrl(image);
    if (imageUrl) {
      const img = document.createElement("img");
      img.src = imageUrl;
      img.alt = name || "";
      img.onerror = () => {
        holder.textContent = "";
        const icon = document.createElement("i");
        icon.setAttribute("data-lucide", "package");
        icon.setAttribute("aria-hidden", "true");
        holder.append(icon);
        window.lucide?.createIcons?.();
      };
      holder.append(img);
      return;
    }
    const icon = document.createElement("i");
    icon.setAttribute("data-lucide", "package");
    icon.setAttribute("aria-hidden", "true");
    holder.append(icon);
  };

  const readProductQty = () =>
    Math.max(1, Math.floor(Number(productQtyInput?.value) || 1));

  const setProductQty = (value) => {
    if (!productQtyInput) return;
    productQtyInput.value = String(Math.max(1, Math.floor(Number(value) || 1)));
  };

  const setProductOpen = (open) => {
    if (!productModal) return;
    if (open) {
      productModal.hidden = false;
      productModal.removeAttribute("hidden");
    } else {
      productModal.hidden = true;
      productModal.setAttribute("hidden", "");
      activeProduct = null;
    }
    productModal.setAttribute("aria-hidden", String(!open));
    document.body.classList.toggle("storefront-product-open", open);
    if (open && window.lucide?.createIcons) window.lucide.createIcons();
  };

  const openProduct = (card) => {
    if (!productModal || !card) return;
    const item = productFromElement(card);
    if (!item.id) return;
    activeProduct = item;
    const existing = cartItems.get(item.id);
    if (productCategory) productCategory.textContent = item.category || "";
    if (productName) productName.textContent = item.name;
    if (productDescription) productDescription.textContent = item.description || "";
    if (productPrice) productPrice.textContent = money(item.price);
    setProductQty(existing?.quantity || 1);
    if (productAdd) {
      const label = productAdd.querySelector("span");
      if (label) label.textContent = existing ? "Update cart" : "Add to cart";
    }
    setMedia(productMedia, item.image, item.name);
    setCartOpen(false);
    setPairOpen(false);
    setProductOpen(true);
  };

  const buildWhatsappMessage = (items, estimate) => {
    const blocks = [
      `Hello ${shopName} 👋`,
      "",
      "I'd like to enquire if these items are available and can be purchased:",
      "",
      "*Items*",
    ];
    items.forEach((item, index) => {
      const qty = Math.max(1, Number(item.quantity) || 1);
      const unit = Number(item.price) || 0;
      const lineTotal = unit * qty;
      blocks.push(`${index + 1}. ${item.name}`);
      blocks.push(`   Quantity: ${qty}`);
      blocks.push(`   Unit price: ${money(unit)}`);
      blocks.push(`   Estimated total: ${money(lineTotal)}`);
      blocks.push("");
    });
    if (deliveryPlace.wanted) {
      blocks.push("Delivery: Yes");
      blocks.push(
        `Location: ${deliveryPlace.address || "Not specified yet"}`
      );
      if (deliveryPlace.mapsUrl) {
        blocks.push(`Map: ${deliveryPlace.mapsUrl}`);
      }
    } else {
      blocks.push("Delivery: No (pickup / collect in store)");
    }
    blocks.push("");
    blocks.push("Please confirm availability. Thank you!");
    return blocks.join("\n");
  };

  const refreshDeliveryUi = () => {
    const wanted = Boolean(deliveryWanted?.checked);
    deliveryPlace.wanted = wanted;
    if (deliveryFields) deliveryFields.hidden = !wanted;
    if (!wanted) {
      deliveryPlace.address = "";
      deliveryPlace.mapsUrl = "";
      if (deliveryInput) deliveryInput.value = "";
      if (deliverySelected) {
        deliverySelected.hidden = true;
        deliverySelected.textContent = "";
        deliverySelected.classList.remove("is-error");
      }
      hideDeliverySuggestions();
    } else if (deliverySelected && deliveryPlace.address) {
      deliverySelected.hidden = false;
      deliverySelected.classList.remove("is-error");
      deliverySelected.textContent = `Selected: ${deliveryPlace.address}`;
    }
    const items = [...cartItems.values()];
    const estimate = items.reduce(
      (sum, item) => sum + item.price * item.quantity,
      0
    );
    updateWhatsappShare(items, estimate);
  };

  const setDeliveryAddress = (address, mapsUrl = "") => {
    deliveryPlace.address = String(address || "").trim();
    deliveryPlace.mapsUrl = String(mapsUrl || "").trim();
    if (deliverySelected) {
      if (deliveryPlace.address) {
        deliverySelected.hidden = false;
        deliverySelected.classList.remove("is-error");
        deliverySelected.textContent = `Selected: ${deliveryPlace.address}`;
      } else {
        deliverySelected.hidden = true;
        deliverySelected.textContent = "";
      }
    }
    const items = [...cartItems.values()];
    const estimate = items.reduce(
      (sum, item) => sum + item.price * item.quantity,
      0
    );
    updateWhatsappShare(items, estimate);
  };

  const hideDeliverySuggestions = () => {
    if (!deliverySuggestions) return;
    deliverySuggestions.hidden = true;
    deliverySuggestions.textContent = "";
    activeSuggestion = -1;
  };

  const selectDeliverySuggestion = (prediction) => {
    const address = prediction?.description || "";
    if (!address) return;
    if (deliveryInput) deliveryInput.value = address;
    hideDeliverySuggestions();

    const applyPlace = (place) => {
      let mapsUrl = place?.url || "";
      if (!mapsUrl && place?.geometry?.location) {
        const lat = place.geometry.location.lat();
        const lng = place.geometry.location.lng();
        mapsUrl = `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
      } else if (!mapsUrl) {
        mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`;
      }
      const formatted = place?.formatted_address || address;
      if (deliveryInput) deliveryInput.value = formatted;
      setDeliveryAddress(formatted, mapsUrl);
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

  const renderDeliverySuggestions = (predictions) => {
    if (!deliverySuggestions) return;
    deliverySuggestions.textContent = "";
    if (!predictions.length) {
      hideDeliverySuggestions();
      return;
    }
    predictions.forEach((prediction, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "storefront__delivery-suggestion";
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
        selectDeliverySuggestion(prediction);
      });
      item.append(button);
      deliverySuggestions.append(item);
    });
    deliverySuggestions.hidden = false;
    activeSuggestion = -1;
  };

  const fetchDeliverySuggestions = (query) => {
    if (!placesReady || !autocompleteService || !deliveryWanted?.checked) {
      hideDeliverySuggestions();
      return;
    }
    const input = String(query || "").trim();
    if (input.length < 2) {
      hideDeliverySuggestions();
      return;
    }
    autocompleteService.getPlacePredictions(
      {
        input,
        componentRestrictions: { country: "ke" },
      },
      (predictions, status) => {
        if (status !== window.google.maps.places.PlacesServiceStatus.OK) {
          // Retry without country filter if Kenya-restricted search is empty.
          if (
            status === window.google.maps.places.PlacesServiceStatus.ZERO_RESULTS
          ) {
            autocompleteService.getPlacePredictions(
              { input },
              (fallback, fallbackStatus) => {
                if (
                  fallbackStatus !==
                  window.google.maps.places.PlacesServiceStatus.OK
                ) {
                  hideDeliverySuggestions();
                  return;
                }
                renderDeliverySuggestions((fallback || []).slice(0, 6));
              }
            );
            return;
          }
          hideDeliverySuggestions();
          return;
        }
        renderDeliverySuggestions((predictions || []).slice(0, 6));
      }
    );
  };

  const initPlacesAutocomplete = () => {
    if (
      !deliveryInput ||
      deliveryInput.dataset.mapsEnabled !== "1" ||
      !window.google?.maps?.places
    ) {
      return;
    }
    placesReady = true;
    autocompleteService =
      autocompleteService ||
      new window.google.maps.places.AutocompleteService();
    // PlacesService needs a dummy attribution node.
    if (!placesService) {
      const holder = document.createElement("div");
      holder.hidden = true;
      document.body.append(holder);
      placesService = new window.google.maps.places.PlacesService(holder);
    }
    if (deliveryHint && !deliveryHint.dataset.readyText) {
      deliveryHint.dataset.readyText = "1";
      deliveryHint.textContent = "Live Google Maps suggestions as you type.";
    }
  };

  const updateWhatsappShare = (items, estimate) => {
    if (!whatsappBtn || !whatsappPhone) return;
    const hasItems = items.length > 0;
    const wasHidden = whatsappBtn.hidden;
    whatsappBtn.hidden = !hasItems;
    if (whatsappHint) whatsappHint.hidden = !hasItems;
    if (deliverySection) deliverySection.hidden = !hasItems;
    if (!hasItems) {
      whatsappBtn.removeAttribute("href");
      whatsappBtn.classList.remove("is-ready");
      return;
    }
    if (deliveryPlace.wanted && !deliveryPlace.address.trim()) {
      whatsappBtn.classList.add("is-disabled");
      whatsappBtn.setAttribute("aria-disabled", "true");
    } else {
      whatsappBtn.classList.remove("is-disabled");
      whatsappBtn.removeAttribute("aria-disabled");
    }
    const text = buildWhatsappMessage(items, estimate);
    whatsappBtn.href = `https://wa.me/${whatsappPhone}?text=${encodeURIComponent(text)}`;
    if (whatsappSummary) {
      const label = items.length === 1 ? "item" : "items";
      const deliveryLabel = deliveryPlace.wanted
        ? deliveryPlace.address
          ? "delivery set"
          : "add location"
        : "pickup";
      whatsappSummary.textContent = `${items.length} ${label} · ${money(estimate)} · ${deliveryLabel}`;
    }
    if (wasHidden) {
      whatsappBtn.classList.remove("is-ready");
      void whatsappBtn.offsetWidth;
      whatsappBtn.classList.add("is-ready");
      window.lucide?.createIcons?.();
    }
  };

  const addItem = (item, { showCart = true, quantity = 1 } = {}) => {
    if (!item.id) return;
    const current = cartItems.get(item.id);
    const extra = Math.max(1, Math.floor(Number(quantity) || 1));
    cartItems.set(item.id, {
      ...item,
      quantity: (current?.quantity || 0) + extra,
    });
    lastAddedId = item.id;
    pendingPairPopup = true;
    renderCart();
    if (showCart) setCartOpen(true);
  };

  const setItemQuantity = (item, quantity, { showCart = true } = {}) => {
    if (!item.id) return;
    const qty = Math.max(0, Math.floor(Number(quantity) || 0));
    if (qty <= 0) {
      cartItems.delete(item.id);
    } else {
      cartItems.set(item.id, { ...item, quantity: qty });
    }
    lastAddedId = item.id;
    pendingPairPopup = qty > 0;
    renderCart();
    if (showCart && qty > 0) setCartOpen(true);
  };

  const renderCart = () => {
    if (!lines || !empty || !total) return;
    lines.textContent = "";
    const items = [...cartItems.values()];
    const itemCount = items.length;
    const estimate = items.reduce(
      (sum, item) => sum + item.price * item.quantity,
      0
    );

    empty.hidden = itemCount > 0;
    total.textContent = money(estimate);
    updateWhatsappShare(items, estimate);
    if (countEls.length) {
      countEls.forEach((el) => {
        el.hidden = itemCount === 0;
        el.textContent = String(itemCount);
      });
    }

    items.forEach((item) => {
      const line = document.createElement("li");
      line.className = "storefront__cart-line";

      const media = document.createElement("div");
      media.className = "storefront__cart-media";
      const imageUrl = absoluteUrl(item.image);
      if (imageUrl) {
        const img = document.createElement("img");
        img.src = imageUrl;
        img.alt = item.name;
        img.loading = "lazy";
        img.onerror = () => {
          media.textContent = "";
          const icon = document.createElement("i");
          icon.setAttribute("data-lucide", "package");
          icon.setAttribute("aria-hidden", "true");
          media.append(icon);
          window.lucide?.createIcons?.();
        };
        media.append(img);
      } else {
        const icon = document.createElement("i");
        icon.setAttribute("data-lucide", "package");
        icon.setAttribute("aria-hidden", "true");
        media.append(icon);
      }

      const details = document.createElement("div");
      details.className = "storefront__cart-details";
      const name = document.createElement("h3");
      name.textContent = item.name;
      const meta = document.createElement("p");
      meta.className = "storefront__cart-meta";
      const qty = Math.max(1, Number(item.quantity) || 1);
      const unit = Number(item.price) || 0;
      const lineTotal = unit * qty;
      meta.textContent = `Qty ${qty} · ${money(unit)} each`;
      const price = document.createElement("strong");
      price.textContent = `Estimated: ${money(lineTotal)}`;
      details.append(name, meta, price);

      const controls = document.createElement("div");
      controls.className = "storefront__cart-controls";
      const decrement = document.createElement("button");
      decrement.type = "button";
      decrement.textContent = "−";
      decrement.setAttribute("aria-label", `Remove one ${item.name}`);
      decrement.addEventListener("click", () => changeQuantity(item.id, -1));
      const quantity = document.createElement("span");
      quantity.textContent = String(item.quantity);
      const increment = document.createElement("button");
      increment.type = "button";
      increment.textContent = "+";
      increment.setAttribute("aria-label", `Add one ${item.name}`);
      increment.addEventListener("click", () => changeQuantity(item.id, 1));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove ${item.name}`);
      remove.addEventListener("click", () => {
        cartItems.delete(item.id);
        renderCart();
      });
      controls.append(decrement, quantity, increment, remove);
      line.append(media, details, controls);
      lines.append(line);
    });
    window.lucide?.createIcons?.();
  };

  const changeQuantity = (id, delta) => {
    const item = cartItems.get(id);
    if (!item) return;
    const quantity = item.quantity + delta;
    if (quantity <= 0) {
      cartItems.delete(id);
    } else {
      cartItems.set(id, { ...item, quantity });
    }
    renderCart();
  };

  const suggestionsUrl = (itemId) => {
    if (!suggestionTemplate) return "";
    return suggestionTemplate.includes("__ID__")
      ? suggestionTemplate.replace("__ID__", String(itemId))
      : suggestionTemplate.replace("/0/", `/${itemId}/`);
  };

  const maybeShowPairPopup = async () => {
    if (!pendingPairPopup || !lastAddedId || !pairList || !pairModal) return;
    pendingPairPopup = false;
    const source = cartItems.get(lastAddedId);
    if (!source) return;

    const url = suggestionsUrl(lastAddedId);
    if (!url) return;

    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const data = await response.json();
      const items = (data.items || []).filter(
        (item) => !cartItems.has(String(item.id))
      );
      if (!items.length) return;
      renderPairPopup(source, items);
      setPairOpen(true);
    } catch {
      // Keep browsing quietly if suggestions fail.
    }
  };

  const renderPairPopup = (source, items) => {
    if (pairCopy) {
      pairCopy.textContent = `People who buy ${source.name} often add these too.`;
    }
    pairList.textContent = "";
    items.forEach((item) => {
      const row = document.createElement("article");
      row.className = "storefront__pair-item";

      const media = document.createElement("div");
      media.className = "storefront__pair-media";
      if (item.image_url) {
        const img = document.createElement("img");
        img.src = item.image_url;
        img.alt = item.name;
        img.loading = "lazy";
        img.onerror = () => {
          media.textContent = "";
          const icon = document.createElement("i");
          icon.setAttribute("data-lucide", "package");
          icon.setAttribute("aria-hidden", "true");
          media.append(icon);
          window.lucide?.createIcons?.();
        };
        media.append(img);
      } else {
        const icon = document.createElement("i");
        icon.setAttribute("data-lucide", "package");
        icon.setAttribute("aria-hidden", "true");
        media.append(icon);
      }

      const details = document.createElement("div");
      details.className = "storefront__pair-details";
      const name = document.createElement("h3");
      name.textContent = item.name;
      const price = document.createElement("strong");
      price.textContent = money(item.price);
      details.append(name, price);

      const add = document.createElement("button");
      add.type = "button";
      add.className = "storefront__pair-add";
      add.textContent = "Add";
      add.addEventListener("click", () => {
        addItem(
          {
            id: String(item.id),
            name: item.name,
            category: item.category || "",
            price: Number(item.price || 0),
            image: item.image_url || "",
          },
          { showCart: false }
        );
        add.textContent = "Added";
        add.disabled = true;
        pendingPairPopup = false;
      });

      row.append(media, details, add);
      pairList.append(row);
    });
    window.lucide?.createIcons?.();
  };

  root.querySelectorAll("[data-storefront-preview]").forEach((button) => {
    button.addEventListener("click", () => {
      const card = button.closest("[data-storefront-product]");
      if (!card) return;
      openProduct(card);
    });
  });

  document.querySelectorAll("[data-storefront-product-close]").forEach((button) => {
    button.addEventListener("click", () => setProductOpen(false));
  });

  productModal?.querySelectorAll("[data-storefront-product-qty]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.getAttribute("data-storefront-product-qty");
      const next = readProductQty() + (action === "inc" ? 1 : -1);
      setProductQty(next);
    });
  });

  productQtyInput?.addEventListener("change", () => {
    setProductQty(productQtyInput.value);
  });

  productAdd?.addEventListener("click", () => {
    if (!activeProduct) return;
    const item = activeProduct;
    const qty = readProductQty();
    setProductOpen(false);
    setItemQuantity(item, qty, { showCart: true });
  });

  document.querySelectorAll("[data-storefront-cart-open]").forEach((button) => {
    button.addEventListener("click", () => {
      if (productModal && !productModal.hidden) {
        setProductOpen(false);
      }
      if (pairModal && !pairModal.hidden) {
        setPairOpen(false);
      }
      setCartOpen(true);
    });
  });

  document.querySelectorAll("[data-storefront-cart-close]").forEach((button) => {
    button.addEventListener("click", () => {
      setCartOpen(false, { fromClose: true });
    });
  });

  document.querySelectorAll("[data-storefront-pair-close]").forEach((button) => {
    button.addEventListener("click", () => setPairOpen(false));
  });

  deliveryWanted?.addEventListener("change", () => {
    refreshDeliveryUi();
    if (deliveryWanted.checked) {
      initPlacesAutocomplete();
      window.setTimeout(() => deliveryInput?.focus(), 40);
    } else {
      hideDeliverySuggestions();
    }
  });

  deliveryInput?.addEventListener("input", () => {
    if (!deliveryWanted?.checked) return;
    const typed = deliveryInput.value.trim();
    if (!typed) {
      setDeliveryAddress("", "");
      hideDeliverySuggestions();
      return;
    }
    deliveryPlace.address = typed;
    deliveryPlace.mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(typed)}`;
    if (deliverySelected) {
      deliverySelected.hidden = false;
      deliverySelected.classList.remove("is-error");
      deliverySelected.textContent = `Selected: ${typed}`;
    }
    const items = [...cartItems.values()];
    const estimate = items.reduce(
      (sum, item) => sum + item.price * item.quantity,
      0
    );
    updateWhatsappShare(items, estimate);

    window.clearTimeout(suggestionTimer);
    suggestionTimer = window.setTimeout(() => {
      fetchDeliverySuggestions(typed);
    }, 180);
  });

  deliveryInput?.addEventListener("keydown", (event) => {
    if (!deliverySuggestions || deliverySuggestions.hidden) return;
    const options = Array.from(
      deliverySuggestions.querySelectorAll(".storefront__delivery-suggestion")
    );
    if (!options.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeSuggestion = (activeSuggestion + 1) % options.length;
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activeSuggestion =
        (activeSuggestion - 1 + options.length) % options.length;
    } else if (event.key === "Enter" && activeSuggestion >= 0) {
      event.preventDefault();
      options[activeSuggestion].dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true })
      );
      return;
    } else if (event.key === "Escape") {
      hideDeliverySuggestions();
      return;
    } else {
      return;
    }
    options.forEach((option, index) => {
      option.classList.toggle("is-active", index === activeSuggestion);
    });
  });

  deliveryInput?.addEventListener("blur", () => {
    window.setTimeout(hideDeliverySuggestions, 150);
  });

  const openWhatsappText = (text) => {
    const href = `https://wa.me/${whatsappPhone}?text=${encodeURIComponent(text)}`;
    window.open(href, "_blank", "noopener,noreferrer");
  };

  whatsappBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    if (deliveryPlace.wanted && !deliveryPlace.address.trim()) {
      if (deliverySelected) {
        deliverySelected.hidden = false;
        deliverySelected.classList.add("is-error");
        deliverySelected.textContent = "Please enter a delivery location first.";
      }
      deliveryFields && (deliveryFields.hidden = false);
      deliveryInput?.focus();
      return;
    }
    const items = [...cartItems.values()];
    if (!items.length) return;
    const estimate = items.reduce(
      (sum, item) => sum + item.price * item.quantity,
      0
    );
    openWhatsappText(buildWhatsappMessage(items, estimate));
  });

  const onMapsReady = () => {
    initPlacesAutocomplete();
  };
  document.addEventListener("storefront:maps-ready", onMapsReady);
  if (window.__storefrontMapsLoaded || window.google?.maps?.places) {
    onMapsReady();
  }

  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (productModal && !productModal.hidden) {
      setProductOpen(false);
      return;
    }
    if (pairModal && !pairModal.hidden) {
      setPairOpen(false);
      return;
    }
    setCartOpen(false, { fromClose: true });
  });
})();
