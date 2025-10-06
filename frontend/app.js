const $ = (sel) => document.querySelector(sel);
const statusEl = $("#status");
const onboardingEl = $("#onboarding");
const gameEl = $("#game");
const qEl = $("#question");
const remEl = $("#remaining");
const tagsEl = $("#tags");
const toastEl = $("#toast");
const bgEl = $("#bg");

const API = {
  async createSession(payload) {
    const r = await fetch(`/session`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async next(sessionId) {
    const r = await fetch(`/next?sessionId=${encodeURIComponent(sessionId)}`);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async vote(questionId, like) {
    const r = await fetch(`/vote`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ questionId, like }),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async more(sessionId) {
    await fetch(`/batch/more?sessionId=${encodeURIComponent(sessionId)}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ reason: "manual" }),
    });
  }
};

let state = {
  sessionId: null,
  questionId: null,
  busy: false,
};

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }
function setStatus(msg) { statusEl.textContent = msg; }
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.className = "fixed bottom-4 left-1/2 -translate-x-1/2 bg-black/80 text-white px-4 py-2 rounded-xl shadow";
  show(toastEl);
  setTimeout(() => hide(toastEl), 1600);
}

async function getNext() {
  if (!state.sessionId) return;
  try {
    state.busy = true;
    setStatus("Loading…");
    const data = await API.next(state.sessionId);
    state.questionId = data.question._id || data.question.id;
    qEl.textContent = data.question.text;
    remEl.textContent = `${data.remaining} in deck`;
    renderTags(data.question.tags || []);

    // background (if available)
    if (data.question.imageDataUrl) {
      bgEl.style.backgroundImage = `url("${data.question.imageDataUrl}")`;
      bgEl.style.filter = "saturate(0.9) brightness(0.95)";
    } else {
      bgEl.style.backgroundImage = "";
      bgEl.style.filter = "none";
    }

    setStatus(`Session ${state.sessionId.slice(-6)}`);
  } catch (e) {
    console.error(e);
    toast("Fetching next failed.");
  } finally {
    state.busy = false;
  }
}

function renderTags(tags) {
  tagsEl.innerHTML = "";
  (tags || []).slice(0, 6).forEach(t => {
    const span = document.createElement("span");
    span.className = "chip";
    span.textContent = t;
    tagsEl.appendChild(span);
  });
}

async function handleVote(like) {
  if (state.busy || !state.questionId) return;
  try {
    state.busy = true;
    await API.vote(state.questionId, like);
    await getNext();
  } catch (e) {
    console.error(e);
    toast("Vote failed");
  } finally {
    state.busy = false;
  }
}

function buildPayloadFromForm(formEl) {
  const f = new FormData(formEl);
  const A = {
    name: (f.get("nameA") || "A").toString().trim() || "A",
    sliders: {
      creativity: parseFloat(f.get("A_creativity")),
      absurdism: parseFloat(f.get("A_absurdism")),
      romance: parseFloat(f.get("A_romance")),
      pop_culture: parseFloat(f.get("A_pop")),
      spice: parseFloat(f.get("A_spice")),
      decade: f.get("A_decade"),
    },
  };
  const B = {
    name: (f.get("nameB") || "B").toString().trim() || "B",
    sliders: {
      creativity: parseFloat(f.get("B_creativity")),
      absurdism: parseFloat(f.get("B_absurdism")),
      romance: parseFloat(f.get("B_romance")),
      pop_culture: parseFloat(f.get("B_pop")),
      spice: parseFloat(f.get("B_spice")),
      decade: f.get("B_decade"),
    },
  };
  return {
    players: [A, B],
    pg13: !!f.get("pg13"),
  };
}

async function startSession(e) {
  e.preventDefault();
  const payload = buildPayloadFromForm(e.currentTarget);
  try {
    setStatus("Seeding…");
    const res = await API.createSession(payload);
    state.sessionId = res.sessionId;
    localStorage.setItem("rb_session", state.sessionId);
    hide(onboardingEl);
    show(gameEl);
    await getNext();
  } catch (err) {
    console.error(err);
    toast("Failed to start session");
  }
}

function maybeResume() {
  const saved = localStorage.getItem("rb_session");
  if (saved) {
    state.sessionId = saved;
    hide(onboardingEl);
    show(gameEl);
    getNext();
  } else {
    show(onboardingEl);
    setStatus("Ready");
  }
}

/* Bindings */
$("#setup-form").addEventListener("submit", startSession);
$("#btn-more").addEventListener("click", () => handleVote(true));
$("#btn-less").addEventListener("click", () => handleVote(false));
document.addEventListener("keydown", (e) => {
  if (state.busy) return;
  if (e.key === "ArrowRight" || e.key === "k") $("#btn-more").click();
  if (e.key === "ArrowLeft"  || e.key === "j") $("#btn-less").click();
});

/* Init */
maybeResume();
