const eventsList = document.querySelector("#events-list");
const searchInput = document.querySelector("#search");
const eveningOnly = document.querySelector("#evening-only");
let events = [];

function eventHour(time) {
  const match = time.match(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/i);
  if (!match) return null;
  let hour = Number(match[1]) % 12;
  if (match[3].toUpperCase() === "PM") hour += 12;
  return hour + Number(match[2] || 0) / 60;
}

function renderEvents() {
  const term = searchInput.value.trim().toLocaleLowerCase();
  const filtered = events.filter((event) => {
    const matchesText = !term || [event.title, event.place, event.description, event.categories]
      .some((value) => (value || "").toLocaleLowerCase().includes(term));
    const hour = eventHour(event.time || "");
    const matchesTime = !eveningOnly.checked || (hour !== null && hour >= 17);
    return matchesText && matchesTime;
  });

  eventsList.replaceChildren();
  document.querySelector("#event-count").textContent = filtered.length;
  document.querySelector("#filter-empty").hidden = filtered.length > 0 || events.length === 0;

  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No events were listed for today. Check the calendar for the latest schedule.";
    eventsList.append(empty);
    return;
  }
  if (!filtered.length) return;

  for (const event of filtered) {
    const row = document.createElement("article");
    row.className = "event-row";

    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = event.time || "Time TBA";

    const details = document.createElement("div");
    const title = document.createElement(event.url ? "a" : "span");
    title.className = "event-title";
    title.textContent = event.title;
    if (event.url) {
      title.href = event.url;
      title.target = "_blank";
      title.rel = "noreferrer";
    }
    details.append(title);
    if (event.place) {
      const place = document.createElement("div");
      place.className = "event-place";
      place.textContent = event.place;
      details.append(place);
    }

    const tag = document.createElement("span");
    tag.className = "event-tag";
    tag.textContent = "SCU campus";
    row.append(time, details, tag);
    eventsList.append(row);
  }
}

function renderDining(items, status) {
  const content = document.querySelector("#dining-content");
  content.replaceChildren();
  if (items.length) {
    const list = document.createElement("ul");
    list.className = "menu-list";
    for (const item of items) {
      const entry = document.createElement("li");
      entry.textContent = item;
      list.append(entry);
    }
    content.append(list);
    return;
  }
  const message = document.createElement("p");
  message.textContent = status || "No menu details were published on the source page.";
  content.append(message);
}

async function loadToday() {
  try {
    const response = await fetch("/api/today");
    if (!response.ok) throw new Error(`Campus sources returned ${response.status}.`);
    const data = await response.json();
    if (data.error) throw new Error(data.error);

    document.querySelector("#today-date").textContent = data.date;
    const updatedTime = new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: "America/Los_Angeles",
    }).format(new Date(data.updated_at));
    document.querySelector("#updated-at").textContent = `Updated ${updatedTime}`;
    events = data.events || [];
    document.querySelector("#library-hours").textContent = data.library.hours || "Hours unavailable";
    document.querySelector("#library-status").textContent = data.library.status === "ok"
      ? "Today at the University Library"
      : data.library.status;
    renderDining(data.dining.items || [], data.dining.status);
    renderEvents();
  } catch (error) {
    eventsList.replaceChildren();
    const message = document.createElement("div");
    message.className = "empty-state";
    message.textContent = `Campus information could not be loaded. ${error.message}`;
    eventsList.append(message);
    document.querySelector("#library-status").textContent = "Hours could not be loaded.";
    renderDining([], "Menu details could not be loaded.");
    document.querySelector("#event-count").textContent = "—";
  }
}

searchInput.addEventListener("input", renderEvents);
eveningOnly.addEventListener("change", renderEvents);
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    searchInput.focus();
  }
  if (event.key === "Escape" && document.activeElement === searchInput) {
    searchInput.value = "";
    renderEvents();
    searchInput.blur();
  }
});

loadToday();
