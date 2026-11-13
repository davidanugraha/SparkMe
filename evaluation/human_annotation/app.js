"use strict";

// IDs and definitions align with agentic_judge/judging/turns.py.
const criteria = [
  { id: "premise", title: "Unsupported premises", definition: "Does it introduce an unsupported premise or presume a particular answer?" },
  { id: "meaning", title: "Accurate representation", definition: "Does the turn misrepresent an answer or discard an important qualification?" },
  { id: "question_burden", title: "Question clarity and burden", definition: "Does unclear wording or bundling competing requests make the turn materially difficult to answer? Related clarifications and a short factual question are not automatically violations." },
];
const findings = [
  ["violation", "Violation"],
  ["no_observed_violation", "No observed violation"],
  ["unclear", "Unclear"],
  ["not_applicable", "Not applicable"],
];
const preferences = [["a", "Prefer A"], ["b", "Prefer B"], ["tie", "Tie"], ["unable_to_judge", "Unable to judge"]];
const examples = window.ANNOTATION_EXAMPLES;
const drafts = new Map();
let exampleIndex = 0;
let mode = "conduct";
const byId = (id) => document.getElementById(id);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function currentDraft() {
  const key = `${examples[exampleIndex].id}/${mode}`;
  if (!drafts.has(key)) {
    drafts.set(key, { answers: {}, note: "", expanded: false, openCandidates: new Set(), submitted: false });
  }
  return drafts.get(key);
}

function utterance(message, className = "", speaker = message.speaker) {
  const article = element("article", `utterance ${message.speaker === "Participant" ? "participant" : ""} ${className}`);
  article.append(element("p", "speaker", speaker), element("p", "", message.text));
  return article;
}

function renderConversation() {
  const example = examples[exampleIndex];
  const draft = currentDraft();
  const hiddenCount = Math.max(0, example.history.length - 6);
  byId("purpose").textContent = example.purpose;
  byId("source-note").textContent = example.source;
  byId("recent-history").replaceChildren(...example.history.slice(hiddenCount).map((message) => utterance(message)));
  byId("earlier-history").replaceChildren(...example.history.slice(0, hiddenCount).map((message) => utterance(message)));
  byId("history-toggle").hidden = hiddenCount === 0;
  updateHistoryDisplay();

  const target = byId("target-area");
  const heading = element("div", "target-heading");
  heading.append(element("h3", "", mode === "conduct" ? "Turn to assess" : "Compare the next interviewer turn"));
  heading.append(element("span", "", example.target.turn_id));
  target.replaceChildren(heading);
  if (mode === "conduct") {
    target.append(utterance(example.target, "target-turn"));
  } else {
    const candidates = element("div", "candidate-grid");
    candidates.append(utterance(example.target, "target-turn", "Candidate A"));
    candidates.append(utterance({ speaker: "Interviewer", text: example.alternative }, "target-turn", "Candidate B"));
    target.append(candidates, element("p", "candidate-caption", "Both candidates follow the same conversation above."));
  }
  byId("scenario").value = String(exampleIndex);
  byId("task-position").textContent = `${exampleIndex + 1} of ${examples.length}`;
  byId("previous").disabled = exampleIndex === 0;
  byId("next").disabled = exampleIndex === examples.length - 1;
  byId("explanation").value = draft.note;
}

function updateHistoryDisplay() {
  const example = examples[exampleIndex];
  const hiddenCount = Math.max(0, example.history.length - 6);
  const expanded = currentDraft().expanded;
  byId("earlier-history").hidden = !expanded;
  byId("history-toggle").setAttribute("aria-expanded", String(expanded));
  byId("history-toggle").textContent = expanded ? "Collapse earlier history ↑" : `View full history · ${hiddenCount / 2} earlier exchanges ↓`;
  byId("history-description").textContent = hiddenCount === 0
    ? "All available conversation before this turn is shown."
    : expanded ? "All conversation before this turn is shown." : "Showing the 3 most recent exchanges. Earlier context is available above.";
  byId("context-count").textContent = `${example.history.length / 2} preceding exchanges`;
}

function choices(name, options) {
  const grid = element("div", "choice-grid");
  for (const [value, text] of options) {
    const label = element("label", "choice");
    const input = element("input");
    input.type = "radio";
    input.name = name;
    input.value = value;
    input.checked = currentDraft().answers[name] === value;
    label.append(input, element("span", "", text));
    grid.append(label);
  }
  return grid;
}

function criterionField(criterion, prefix = "") {
  const field = element("fieldset", "criterion");
  field.append(element("legend", "", criterion.title), element("p", "definition", criterion.definition));
  field.append(choices(`${prefix}${criterion.id}`, findings));
  return field;
}

function renderControls() {
  const controls = byId("answer-controls");
  controls.replaceChildren();
  const isConduct = mode === "conduct";
  byId("annotation-heading").textContent = isConduct ? "Review this turn" : "Which turn would you prefer?";
  byId("annotation-instruction").textContent = isConduct
    ? "Assess the full interviewer turn using the wording and preceding conversation."
    : "Choose the turn you would prefer at this point in the interview, considering its purpose and the preceding conversation.";
  byId("task-instruction").textContent = isConduct
    ? "Read the context, then assess the highlighted interviewer turn."
    : "Read the shared context, then compare two possible interviewer turns.";
  if (isConduct) {
    controls.append(...criteria.map((criterion) => criterionField(criterion)));
  } else {
    const preference = element("fieldset", "criterion preference-field");
    preference.append(element("legend", "", "Your preference"), element("p", "definition", "Choose a tie when both turns are equally preferable. Choose unable to judge when you cannot make a reliable comparison."), choices("preference", preferences));
    controls.append(preference);
    for (const candidate of ["a", "b"]) {
      const details = element("details", "candidate-checks");
      details.open = currentDraft().openCandidates.has(candidate);
      details.append(element("summary", "", `Candidate ${candidate.toUpperCase()} · Optional conduct labels`));
      details.append(...criteria.map((criterion) => criterionField(criterion, `${candidate}_`)));
      const draft = currentDraft();
      details.addEventListener("toggle", () => {
        if (details.open) draft.openCandidates.add(candidate);
        else draft.openCandidates.delete(candidate);
      });
      controls.append(details);
    }
  }
  updateCompletion();
  setStatus(currentDraft().submitted ? "Preview submitted. You can edit your answers and submit again." : "", "success");
}

function requiredAnswers() {
  return mode === "conduct" ? criteria.map((criterion) => criterion.id) : ["preference"];
}

function updateCompletion() {
  const answered = requiredAnswers().filter((key) => currentDraft().answers[key]).length;
  byId("completion-hint").textContent = mode === "conduct"
    ? `${answered} of ${criteria.length} criteria answered`
    : answered ? "Preference selected. Conduct labels are optional." : "Select a preference to submit. Conduct labels are optional.";
}

function setStatus(message, kind) {
  byId("form-status").textContent = message;
  byId("form-status").dataset.kind = kind;
}

function markEdited() {
  currentDraft().submitted = false;
  setStatus("", "");
  updateCompletion();
}

function render() {
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  }
  renderConversation();
  renderControls();
}

byId("scenario").replaceChildren(...examples.map((example, index) => {
  const option = element("option", "", example.title);
  option.value = String(index);
  return option;
}));
byId("scenario").addEventListener("change", (event) => {
  exampleIndex = Number(event.target.value);
  render();
});
for (const [id, delta] of [["previous", -1], ["next", 1]]) {
  byId(id).addEventListener("click", () => {
    exampleIndex += delta;
    render();
  });
}
for (const button of document.querySelectorAll("[data-mode]")) {
  button.addEventListener("click", () => {
    mode = button.dataset.mode;
    render();
  });
}
byId("history-toggle").addEventListener("click", () => {
  const anchor = byId("history-toggle");
  const top = anchor.getBoundingClientRect().top;
  currentDraft().expanded = !currentDraft().expanded;
  updateHistoryDisplay();
  window.scrollBy(0, anchor.getBoundingClientRect().top - top);
});
byId("annotation-form").addEventListener("change", (event) => {
  if (event.target.type !== "radio") return;
  currentDraft().answers[event.target.name] = event.target.value;
  markEdited();
});
byId("explanation").addEventListener("input", (event) => {
  currentDraft().note = event.target.value;
  markEdited();
});
byId("annotation-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const missing = requiredAnswers().find((key) => !currentDraft().answers[key]);
  if (missing) {
    byId("annotation-form").querySelector(`input[name="${missing}"]`).focus();
    setStatus(mode === "conduct" ? "Choose a finding for each criterion before submitting." : "Choose a preference before submitting.", "error");
    return;
  }
  currentDraft().submitted = true;
  setStatus("Preview submitted. You can edit your answers and submit again.", "success");
});
render();
