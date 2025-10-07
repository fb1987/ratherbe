const $ = (sel) => document.querySelector(sel);
const onboardingEl = $("#onboarding");
const gameEl = $("#game");
const qEl = $("#question");
const bgEl = $("#bg");
const toastEl = $("#toast");
const labelX = $("#label-x");
const labelY = $("#label-y");
const startBtn = $("#start-btn");
const startSpinner = $("#start-spinner");
const startText = $("#start-text");

// Always start fresh on reload (clear any stale session)
try { localStorage.removeItem("rb_session"); } catch {}

const API = {
  async createSession(payload) {
    const r = await fetch(`/session`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      cache: "no-store",
      body: JSON.stringify(payload)
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async next(sessionId) {
    const r = await fetch(`/next?sessionId=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async vote(questionId, chosen) {
    const r = await fetch(`/vote`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      cache: "no-store",
      body: JSON.stringify({ questionId, chosen })
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }
};

let state = { sessionId: null, questionId: null, busy: false };

function show(el){ el.classList.remove("hidden"); }
function hide(el){ el.classList.add("hidden"); }
function toast(msg){
  toastEl.textContent = msg;
  toastEl.className = "fixed bottom-4 left-1/2 -translate-x-1/2 bg-black/80 text-white px-4 py-2 rounded-xl shadow";
  show(toastEl); setTimeout(()=>hide(toastEl),1600);
}

async function getNext(){
  if(!state.sessionId) return;
  try{
    state.busy = true;
    const data = await API.next(state.sessionId);
    state.questionId = data.question._id || data.question.id;
    qEl.textContent = data.question.text;
    labelX.textContent = data.question.x;
    labelY.textContent = data.question.y;

    if (data.question.imageDataUrl) {
      bgEl.style.backgroundImage = `url("${data.question.imageDataUrl}")`;
      bgEl.style.filter = "saturate(0.9) brightness(0.95)";
    } else {
      bgEl.style.backgroundImage = "";
      bgEl.style.filter = "none";
    }
  } catch (e) {
    console.error(e);
    toast("Fetching next failed.");
  } finally {
    state.busy = false;
  }
}

async function choose(which){
  if (state.busy || !state.questionId) return;
  try{
    state.busy = true;
    await API.vote(state.questionId, which);
    await getNext();
  } catch(e){
    console.error(e);
    toast("Choice failed");
  } finally {
    state.busy = false;
  }
}

function buildPayloadFromForm(formEl){
  const f = new FormData(formEl);
  const A = { name: (f.get("nameA")||"A").toString().trim()||"A", sliders:{
    creativity: parseFloat(f.get("A_creativity")),
    absurdism: parseFloat(f.get("A_absurdism")),
    romance: parseFloat(f.get("A_romance")),
    pop_culture: parseFloat(f.get("A_pop")),
    spice: parseFloat(f.get("A_spice")),
    decade: f.get("A_decade"),
  }};
  const B = { name: (f.get("nameB")||"B").toString().trim()||"B", sliders:{
    creativity: parseFloat(f.get("B_creivity"))||0.5,
    absurdism: parseFloat(f.get("B_absurdism")),
    romance: parseFloat(f.get("B_romance")),
    pop_culture: parseFloat(f.get("B_pop")),
    spice: parseFloat(f.get("B_spice")),
    decade: f.get("B_decade"),
  }};
  return { players:[A,B], pg13: !!f.get("pg13") };
}

async function startSession(e){
  e.preventDefault();
  try{
    startBtn.disabled = true;
    startSpinner.style.display = "inline-block";
    startText.textContent = "Loading…";

    const res = await API.createSession(buildPayloadFromForm(e.currentTarget));
    state.sessionId = res.sessionId;

    // Keep onboarding visible while we fetch the first question
    startText.textContent = "Preparing first card…";

    // Only show the game after getNext() resolves (prevents the blank/placeholder flash)
    await getNext();
    hide(onboardingEl);
    show(gameEl);
  }catch(err){
    console.error(err);
    toast("Failed to start session");
  } finally {
    startBtn.disabled = false;
    startSpinner.style.display = "none";
    startText.textContent = "Start →";
  }
}

/* Bindings */
$("#setup-form").addEventListener("submit", startSession);
$("#btn-x").addEventListener("click", () => choose("x"));
$("#btn-y").addEventListener("click", () => choose("y"));
document.addEventListener("keydown",(e)=>{
  if (state.busy) return;
  if (e.key==="ArrowLeft" || e.key==="a") $("#btn-x").click();
  if (e.key==="ArrowRight"|| e.key==="d") $("#btn-y").click();
});

/* Init: show onboarding (we cleared any saved session on load) */
show(onboardingEl);
