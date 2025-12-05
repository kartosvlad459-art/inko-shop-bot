const TG = window.Telegram?.WebApp;
if (TG) {
  TG.ready();
  TG.expand();
}

const state = {
  products: [],
  filtered: [],
  categories: [],
  activeCat: "all",
  query: "",
  cart: [],
  fav: []
};

const $grid = document.getElementById("grid");
const $cats = document.getElementById("cats");
const $search = document.getElementById("search");
const $btnCart = document.getElementById("btnCart");
const $btnFav = document.getElementById("btnFav");
const $cartCount = document.getElementById("cartCount");
const $favCount = document.getElementById("favCount");
const $drawer = document.getElementById("drawer");
const $drawerTitle = document.getElementById("drawerTitle");
const $drawerBody = document.getElementById("drawerBody");
const $drawerClose = document.getElementById("drawerClose");
const $openInBot = document.getElementById("openInBot");

function sendToBot(payload){
  if (!TG) return alert("Открой витрину внутри Telegram");
  TG.sendData(JSON.stringify(payload));
}

function uniq(arr){ return [...new Set(arr)]; }

function computeCats(){
  const cats = uniq(state.products.map(p => p.category || "без категории"));
  state.categories = ["all", ...cats];
}

function applyFilter(){
  let list = [...state.products];
  if (state.activeCat !== "all") {
    list = list.filter(p => (p.category || "без категории") === state.activeCat);
  }
  if (state.query.trim()){
    const q = state.query.toLowerCase();
    list = list.filter(p =>
      (p.title||"").toLowerCase().includes(q) ||
      (p.description||"").toLowerCase().includes(q)
    );
  }
  state.filtered = list;
  render();
}

function renderCats(){
  $cats.innerHTML = "";
  state.categories.forEach(c => {
    const btn = document.createElement("button");
    btn.className = "cat" + (state.activeCat === c ? " active" : "");
    btn.textContent = c === "all" ? "Все" : "#" + c;
    btn.onclick = () => {
      state.activeCat = c;
      renderCats();
      applyFilter();
    };
    $cats.appendChild(btn);
  });
}

function cardHTML(p){
  const inCart = state.cart.includes(p.id);
  const inFav = state.fav.includes(p.id);
  const price = p.price ? `${p.price}₽` : "—";
  const imgTag = (p.photos && p.photos.length)
    ? `<img src="https://api.telegram.org/file/botTOKEN/${p.photos[0]}" alt="">`
    : `<div class="muted">no photo</div>`;

  // NOTE: фотки по file_id в GitHub Pages не откроются напрямую.
  // Это ок: ты обычно ставишь фото в постах, а для витрины на GH можно потом допилить CDN.
  // Сейчас будет просто плейсхолдер.
  const img = (p.photos && p.photos.length)
    ? `<div class="card-img">🖼</div>`
    : `<div class="card-img">🖼</div>`;

  return `
  <article class="card">
    ${img}
    <div class="card-body">
      <div class="card-title">${escapeHTML(p.title||"")}</div>
      <div class="card-price">${price}</div>
      <div class="card-desc">${escapeHTML(p.description||"")}</div>
    </div>
    <div class="card-actions">
      <button class="btn ${inCart ? "secondary" : ""}" data-act="cart" data-id="${p.id}">
        ${inCart ? "В корзине" : "В корзину"}
      </button>
      <button class="btn small ${inFav ? "" : "secondary"}" data-act="fav" data-id="${p.id}">
        ${inFav ? "❤️" : "🤍"}
      </button>
    </div>
  </article>`;
}

function render(){
  $grid.innerHTML = state.filtered.map(cardHTML).join("");
  $cartCount.textContent = state.cart.length;
  $favCount.textContent = state.fav.length;

  $grid.querySelectorAll("button[data-act]").forEach(btn => {
    btn.addEventListener("click", () => {
      const act = btn.dataset.act;
      const id = btn.dataset.id;
      if (act === "cart") {
        if (!state.cart.includes(id)) state.cart.push(id);
        sendToBot({action:"add_to_cart", product_id:id});
      }
      if (act === "fav") {
        if (state.fav.includes(id)) state.fav = state.fav.filter(x=>x!==id);
        else state.fav.push(id);
        sendToBot({action:"toggle_fav", product_id:id});
      }
      applyFilter();
    });
  });
}

function openDrawer(type){
  $drawer.classList.remove("hidden");
  if (type === "cart") {
    $drawerTitle.textContent = "Корзина";
    if (!state.cart.length) {
      $drawerBody.innerHTML = `<div class="muted">Пусто</div>`;
      return;
    }
    $drawerBody.innerHTML = state.cart.map((id) => {
      const p = state.products.find(x=>x.id===id);
      if (!p) return "";
      return `<div class="drawer-item">
        <div>${escapeHTML(p.title)}</div>
        <div>${p.price? p.price+"₽":"—"}</div>
      </div>`;
    }).join("");
  }
  if (type === "fav") {
    $drawerTitle.textContent = "Избранное";
    if (!state.fav.length) {
      $drawerBody.innerHTML = `<div class="muted">Пусто</div>`;
      return;
    }
    $drawerBody.innerHTML = state.fav.map((id) => {
      const p = state.products.find(x=>x.id===id);
      if (!p) return "";
      return `<div class="drawer-item">
        <div>${escapeHTML(p.title)}</div>
        <div>${p.price? p.price+"₽":"—"}</div>
      </div>`;
    }).join("");
  }
}

function closeDrawer(){
  $drawer.classList.add("hidden");
}

function escapeHTML(s){
  return (s||"")
   .replaceAll("&","&amp;")
   .replaceAll("<","&lt;")
   .replaceAll(">","&gt;");
}

$search.addEventListener("input", (e) => {
  state.query = e.target.value;
  applyFilter();
});

$btnCart.onclick = () => openDrawer("cart");
$btnFav.onclick = () => openDrawer("fav");
$drawerClose.onclick = closeDrawer;
$openInBot.onclick = () => {
  if (TG) TG.close();
};

async function init(){
  try{
    const res = await fetch("./products.json?ts=" + Date.now());
    state.products = await res.json();
  }catch(e){
    state.products = [];
  }
  computeCats();
  renderCats();
  applyFilter();
}
init();
