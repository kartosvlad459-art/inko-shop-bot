// webapp/app.js
(() => {
  const TG = window.Telegram?.WebApp;
  if (TG) {
    TG.ready();
    TG.expand();
    // цвета темы TG (если есть)
    document.documentElement.style.setProperty("--tg-bg", TG.themeParams?.bg_color || "");
    document.documentElement.style.setProperty("--tg-text", TG.themeParams?.text_color || "");
  }

  const state = {
    products: [],
    filtered: [],
    categories: [],
    activeCat: "all",
    query: "",
    cart: loadLS("inko_cart", []),       // [{id, size, qty}]
    favs: new Set(loadLS("inko_favs", [])), // [id]
    view: "catalog", // catalog | cart | favs
  };

  // ---------- helpers ----------
  function loadLS(key, def) {
    try {
      const v = JSON.parse(localStorage.getItem(key));
      return v ?? def;
    } catch { return def; }
  }
  function saveLS(key, val) {
    localStorage.setItem(key, JSON.stringify(val));
  }
  function money(x) {
    const n = Number(x || 0);
    return n.toLocaleString("ru-RU") + " ₽";
  }
  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function qs(sel, root = document) { return root.querySelector(sel); }
  function qsa(sel, root = document) { return [...root.querySelectorAll(sel)]; }

  // ---------- data ----------
  async function loadProducts() {
    // cache-buster
    const url = "./products.json?ts=" + Date.now();
    const res = await fetch(url);
    const data = await res.json();

    // нормализуем
    state.products = (Array.isArray(data) ? data : []).map(p => ({
      id: Number(p.id),
      title: p.title || "Без названия",
      description: p.description || "",
      price: Number(p.price || 0),
      category: (p.category || "Разное").trim(),
      photos: Array.isArray(p.photos) ? p.photos : (p.photos_json ? safeJson(p.photos_json, []) : []),
      is_preorder: !!p.is_preorder,
      sizes: Array.isArray(p.sizes) ? p.sizes : extractSizes(p.description || "")
    }));

    state.categories = uniq(["all", ...state.products.map(p => p.category)]);
    applyFilters();
  }

  function safeJson(str, def) {
    try { return JSON.parse(str); } catch { return def; }
  }

  function uniq(arr) {
    return [...new Set(arr)];
  }

  function extractSizes(text) {
    // ищем строку "Размеры: XS / S / M"
    const m = /(?:Размеры|Размер)\s*:\s*([^\n\r]+)/i.exec(text);
    if (!m) return ["XS","S","M","L","XL"];
    const raw = m[1];
    const parts = raw.split(/[\/, ]+/).map(x => x.trim()).filter(Boolean);
    return parts.length ? parts : ["XS","S","M","L","XL"];
  }

  function applyFilters() {
    const q = state.query.toLowerCase();
    state.filtered = state.products.filter(p => {
      const okCat = state.activeCat === "all" || p.category === state.activeCat;
      const okQ = !q || p.title.toLowerCase().includes(q);
      return okCat && okQ;
    });
    render();
  }

  // ---------- cart/favs ----------
  function addToCart(prodId, size) {
    const idx = state.cart.findIndex(i => i.id === prodId && i.size === size);
    if (idx >= 0) state.cart[idx].qty += 1;
    else state.cart.push({ id: prodId, size, qty: 1 });
    saveLS("inko_cart", state.cart);
    toast("Добавлено в корзину");
    renderHeaderCounters();
  }

  function changeQty(prodId, size, delta) {
    const idx = state.cart.findIndex(i => i.id === prodId && i.size === size);
    if (idx < 0) return;
    state.cart[idx].qty += delta;
    if (state.cart[idx].qty <= 0) state.cart.splice(idx, 1);
    saveLS("inko_cart", state.cart);
    render();
    renderHeaderCounters();
  }

  function clearCart() {
    state.cart = [];
    saveLS("inko_cart", state.cart);
    render();
    renderHeaderCounters();
  }

  function toggleFav(prodId) {
    if (state.favs.has(prodId)) state.favs.delete(prodId);
    else state.favs.add(prodId);
    saveLS("inko_favs", [...state.favs]);
    render();
    renderHeaderCounters();
  }

  function cartTotal() {
    let sum = 0;
    for (const it of state.cart) {
      const p = state.products.find(x => x.id === it.id);
      if (p) sum += p.price * it.qty;
    }
    return sum;
  }

  // ---------- telegram send ----------
  function sendCheckout() {
    if (!TG) {
      alert("Открой витрину через Telegram бот");
      return;
    }
    if (!state.cart.length) {
      toast("Корзина пустая");
      return;
    }
    const payload = {
      action: "checkout",
      cart: state.cart,
      total: cartTotal()
    };
    TG.sendData(JSON.stringify(payload));
    TG.close();
  }

  // ---------- UI build ----------
  function ensureRoot() {
    let root = qs("#root");
    if (!root) {
      root = qs(".app");
    }
    if (!root) {
      root = el("div", "app");
      document.body.appendChild(root);
    }
    return root;
  }

  function render() {
    const root = ensureRoot();
    root.innerHTML = "";

    root.appendChild(renderTopbar());
    root.appendChild(renderTabs());

    if (state.view === "catalog") {
      root.appendChild(renderCategories());
      root.appendChild(renderGrid());
    } else if (state.view === "cart") {
      root.appendChild(renderCart());
    } else if (state.view === "favs") {
      root.appendChild(renderFavs());
    }
  }

  function renderTopbar() {
    const wrap = el("header", "topbar");
    const brand = el("div", "brand", `
      <div class="logo">🛍</div>
      <div class="title">Inko Shop</div>
    `);

    const search = el("div", "searchbox");
    search.innerHTML = `
      <input id="searchInput" type="search" placeholder="Я ищу..." value="${escapeHtml(state.query)}"/>
      <button id="clearSearch" title="Сброс">✕</button>
    `;

    wrap.appendChild(brand);
    wrap.appendChild(search);

    // events
    setTimeout(() => {
      const inp = qs("#searchInput", wrap);
      const clr = qs("#clearSearch", wrap);
      inp?.addEventListener("input", e => {
        state.query = e.target.value || "";
        applyFilters();
      });
      clr?.addEventListener("click", () => {
        state.query = "";
        applyFilters();
      });
    });

    return wrap;
  }

  function renderTabs() {
    const tabs = el("div", "tabs");
    tabs.innerHTML = `
      <button class="tab ${state.view==="catalog"?"active":""}" data-view="catalog">Каталог</button>
      <button class="tab ${state.view==="cart"?"active":""}" data-view="cart">
        Корзина <span class="badge" id="cartBadge">${state.cart.reduce((a,b)=>a+b.qty,0)}</span>
      </button>
      <button class="tab ${state.view==="favs"?"active":""}" data-view="favs">
        Избранное <span class="badge" id="favBadge">${state.favs.size}</span>
      </button>
    `;
    setTimeout(() => {
      qsa(".tab", tabs).forEach(b => b.addEventListener("click", () => {
        state.view = b.dataset.view;
        render();
      }));
    });
    return tabs;
  }

  function renderHeaderCounters() {
    const cb = qs("#cartBadge");
    const fb = qs("#favBadge");
    if (cb) cb.textContent = state.cart.reduce((a,b)=>a+b.qty,0);
    if (fb) fb.textContent = state.favs.size;
  }

  function renderCategories() {
    const row = el("div", "cats");
    for (const c of state.categories) {
      const btn = el("button", "catbtn" + (state.activeCat===c?" active":""), c==="all"?"Все":c);
      btn.addEventListener("click", () => {
        state.activeCat = c;
        applyFilters();
      });
      row.appendChild(btn);
    }
    return row;
  }

  function renderGrid() {
    const grid = el("div", "grid");

    if (!state.filtered.length) {
      grid.appendChild(el("div", "empty", "Ничего не найдено 😔"));
      return grid;
    }

    for (const p of state.filtered) {
      grid.appendChild(renderCard(p));
    }
    return grid;
  }

  function renderCard(p) {
    const card = el("div", "card");
    const imgUrl = (p.photos && p.photos[0]) ? p.photos[0] : "";
    card.innerHTML = `
      <div class="imgwrap">
        ${imgUrl ? `<img src="${imgUrl}" alt="">` : `<div class="noimg">Нет фото</div>`}
        ${p.is_preorder ? `<div class="tag">предзаказ</div>` : ""}
        <button class="favbtn ${state.favs.has(p.id)?"on":""}" title="В избранное">★</button>
      </div>
      <div class="cardbody">
        <div class="ctitle">${escapeHtml(p.title)}</div>
        <div class="cprice">${money(p.price)}</div>
        <div class="csizes">Размеры: ${p.sizes.join(" / ")}</div>
        <div class="actions">
          <button class="buybtn">В корзину</button>
          <button class="morebtn">Подробнее</button>
        </div>
      </div>
    `;

    // events
    const favBtn = qs(".favbtn", card);
    favBtn.addEventListener("click", () => toggleFav(p.id));

    const buyBtn = qs(".buybtn", card);
    buyBtn.addEventListener("click", () => openSizePicker(p));

    const moreBtn = qs(".morebtn", card);
    moreBtn.addEventListener("click", () => openDetails(p));

    return card;
  }

  function openSizePicker(p) {
    const modal = buildModal();
    modal.content.innerHTML = `
      <div class="mhead">${escapeHtml(p.title)}</div>
      <div class="mtext">Выбери размер:</div>
      <div class="msizes">
        ${p.sizes.map(s=>`<button class="msize" data-size="${s}">${s}</button>`).join("")}
      </div>
      <div class="mfooter">
        <button class="mclose">Отмена</button>
      </div>
    `;
    qsa(".msize", modal.content).forEach(b => {
      b.addEventListener("click", () => {
        addToCart(p.id, b.dataset.size);
        closeModal(modal.wrap);
      });
    });
    qs(".mclose", modal.content).addEventListener("click", () => closeModal(modal.wrap));
  }

  function openDetails(p) {
    const modal = buildModal();
    const imgUrl = (p.photos && p.photos[0]) ? p.photos[0] : "";
    modal.content.innerHTML = `
      <div class="mhead">${escapeHtml(p.title)}</div>
      ${imgUrl ? `<img class="mimg" src="${imgUrl}" alt="">` : ""}
      <div class="mprice">${money(p.price)}</div>
      <div class="mcat">Категория: <b>${escapeHtml(p.category)}</b></div>
      <div class="mdesc">${escapeHtml(p.description).replace(/\n/g,"<br>")}</div>
      <div class="mtext">Размеры: ${p.sizes.join(" / ")}</div>
      <div class="mfooter">
        <button class="mbuy">В корзину</button>
        <button class="mclose">Закрыть</button>
      </div>
    `;
    qs(".mbuy", modal.content).addEventListener("click", () => openSizePicker(p));
    qs(".mclose", modal.content).addEventListener("click", () => closeModal(modal.wrap));
  }

  function renderCart() {
    const wrap = el("div", "cart");

    if (!state.cart.length) {
      wrap.appendChild(el("div", "empty", "Корзина пустая 🧺"));
      return wrap;
    }

    for (const it of state.cart) {
      const p = state.products.find(x => x.id === it.id);
      if (!p) continue;

      const row = el("div", "cartrow");
      row.innerHTML = `
        <div class="carttitle">${escapeHtml(p.title)} <span class="cartsize">(${it.size})</span></div>
        <div class="cartprice">${money(p.price * it.qty)}</div>
        <div class="cartqty">
          <button class="qbtn" data-d="-1">−</button>
          <span class="qnum">${it.qty}</span>
          <button class="qbtn" data-d="1">+</button>
        </div>
      `;
      qsa(".qbtn", row).forEach(b => {
        b.addEventListener("click", () => changeQty(it.id, it.size, Number(b.dataset.d)));
      });
      wrap.appendChild(row);
    }

    const total = el("div", "carttotal", `
      <div>Итого:</div>
      <div class="sum">${money(cartTotal())}</div>
    `);

    const actions = el("div", "cartactions");
    actions.innerHTML = `
      <button class="checkout">Оформить в боте</button>
      <button class="clear">Очистить</button>
    `;
    qs(".checkout", actions).addEventListener("click", sendCheckout);
    qs(".clear", actions).addEventListener("click", clearCart);

    wrap.appendChild(total);
    wrap.appendChild(actions);

    return wrap;
  }

  function renderFavs() {
    const wrap = el("div", "grid");

    const favList = state.products.filter(p => state.favs.has(p.id));
    if (!favList.length) {
      wrap.appendChild(el("div", "empty", "Избранного пока нет ⭐️"));
      return wrap;
    }
    favList.forEach(p => wrap.appendChild(renderCard(p)));
    return wrap;
  }

  // ---------- modal ----------
  function buildModal() {
    const wrap = el("div", "modalwrap");
    const bg = el("div", "modalbg");
    const box = el("div", "modalbox");
    const content = el("div", "modalcontent");

    box.appendChild(content);
    wrap.appendChild(bg);
    wrap.appendChild(box);
    document.body.appendChild(wrap);

    bg.addEventListener("click", () => closeModal(wrap));
    return { wrap, content };
  }

  function closeModal(wrap) {
    wrap?.remove();
  }

  // ---------- toast ----------
  let toastTimer = null;
  function toast(text) {
    clearTimeout(toastTimer);
    let t = qs(".toast");
    if (!t) {
      t = el("div", "toast");
      document.body.appendChild(t);
    }
    t.textContent = text;
    t.classList.add("show");
    toastTimer = setTimeout(() => t.classList.remove("show"), 1200);
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g,"&amp;")
      .replace(/</g,"&lt;")
      .replace(/>/g,"&gt;")
      .replace(/"/g,"&quot;");
  }

  // ---------- start ----------
  document.addEventListener("DOMContentLoaded", async () => {
    try {
      await loadProducts();
    } catch (e) {
      console.error(e);
      const root = ensureRoot();
      root.innerHTML = `<div class="empty">Не получилось загрузить товары 😔</div>`;
    }
  });

})();
