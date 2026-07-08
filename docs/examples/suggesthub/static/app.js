const visitorId = localStorage.getItem("suggesthubVisitorId") || crypto.randomUUID();
localStorage.setItem("suggesthubVisitorId", visitorId);
const storedManagerEmail = localStorage.getItem("suggesthubManagerEmail") || "";

function currentFilter(id) {
  const element = document.getElementById(id);
  return element ? element.value : "";
}

window.suggestHubSync = function suggestHubSync() {
  document.querySelectorAll("[data-visitor-id]").forEach((input) => {
    input.value = visitorId;
  });
  document.querySelectorAll("[data-thread-id]").forEach((input) => {
    input.value = `visitor-${visitorId}`;
  });
  document.querySelectorAll('[data-filter="location"]').forEach((input) => {
    input.value = currentFilter("locationFilter");
  });
  document.querySelectorAll('[data-filter="category"]').forEach((input) => {
    input.value = currentFilter("categoryFilter");
  });
  document.querySelectorAll('[data-filter="status"]').forEach((input) => {
    input.value = currentFilter("statusFilter");
  });
  document.querySelectorAll("[data-manager-email]").forEach((input) => {
    input.value = localStorage.getItem("suggesthubManagerEmail") || "";
  });
};

document.addEventListener("DOMContentLoaded", window.suggestHubSync);
document.addEventListener("DOMContentLoaded", () => {
  const emailInput = document.getElementById("managerEmail");
  if (emailInput && storedManagerEmail) {
    emailInput.value = storedManagerEmail;
    unlockManager(storedManagerEmail);
  }
  const gate = document.getElementById("managerGate");
  if (gate) {
    gate.addEventListener("submit", handleManagerGateSubmit);
  }
});
document.body.addEventListener("htmx:configRequest", (event) => {
  if (event.detail.parameters && "visitor_id" in event.detail.parameters) {
    event.detail.parameters.visitor_id = visitorId;
  }
});

function isIbmEmail(value) {
  return /^[^@\s]+@ibm\.com$/i.test(value.trim());
}

function setManagerControlsEnabled(enabled) {
  document.querySelectorAll("#statusForm input, #statusForm select, #statusForm textarea, #statusForm button").forEach((element) => {
    if (element.name !== "manager_email" && element.name !== "manager_name") {
      element.disabled = !enabled;
    }
  });
  const location = document.getElementById("managerLocation");
  if (location) location.disabled = !enabled;
  const dashboard = document.getElementById("managerDashboard");
  if (dashboard) dashboard.hidden = !enabled;
}

function unlockManager(email) {
  localStorage.setItem("suggesthubManagerEmail", email);
  window.suggestHubSync();
  setManagerControlsEnabled(true);
  const error = document.getElementById("managerGateError");
  if (error) error.textContent = "";
  const gate = document.getElementById("managerGate");
  if (gate) gate.classList.add("unlocked");
  const summary = document.getElementById("statusSummary");
  if (window.htmx && summary) {
    window.htmx.ajax("GET", `/partials/manager/summary?manager_email=${encodeURIComponent(email)}`, {
      target: "#statusSummary",
      swap: "innerHTML",
    });
  }
}

function handleManagerGateSubmit(event) {
  event.preventDefault();
  const emailInput = document.getElementById("managerEmail");
  const error = document.getElementById("managerGateError");
  const email = emailInput ? emailInput.value.trim().toLowerCase() : "";
  if (!isIbmEmail(email)) {
    if (error) error.textContent = "Use an @ibm.com email for this prototype manager view.";
    setManagerControlsEnabled(false);
    return;
  }
  unlockManager(email);
}
