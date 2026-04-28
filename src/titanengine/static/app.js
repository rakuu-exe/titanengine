function bindFilePreview() {
    const preview = document.getElementById("hoverPreview");

    if (!preview) {
        return;
    }

    document.querySelectorAll("tr[data-preview]").forEach((row) => {
        row.addEventListener("mouseenter", () => {
            const text = row.dataset.preview || "No extracted text preview is available for this file yet.";
            preview.textContent = text.trim() || "No extracted text preview is available for this file yet.";
        });
    });
}

function syncActiveNav() {
    const currentPath = window.location.pathname.replace(/\/$/, "") || "/";

    document.querySelectorAll(".nav a").forEach((link) => {
        const linkPath = new URL(link.href, window.location.origin).pathname.replace(/\/$/, "") || "/";
        const isActive = linkPath === currentPath;

        link.classList.toggle("is-active", isActive);

        if (isActive) {
            link.setAttribute("aria-current", "page");
        } else {
            link.removeAttribute("aria-current");
        }
    });
}

function pulseClickedElement(element) {
    if (!element) {
        return;
    }

    element.classList.remove("is-click-glow");
    void element.offsetWidth;
    element.classList.add("is-click-glow");

    window.setTimeout(() => {
        element.classList.remove("is-click-glow");
    }, 650);
}

function keepBackgroundVideoAlive() {
    const video = document.querySelector(".video-background video");

    if (!video) {
        return;
    }

    video.muted = true;
    video.playsInline = true;
    video.loop = true;

    const playPromise = video.play();

    if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(() => {
            // Autoplay may wait for first user interaction in some browsers.
        });
    }
}

function replacePage(html, url, pushState = true) {
    const parser = new DOMParser();
    const nextDocument = parser.parseFromString(html, "text/html");
    const nextMain = nextDocument.querySelector("main");
    const currentMain = document.querySelector("main");
    const currentAlerts = document.querySelector(".alerts");
    const nextAlerts = nextDocument.querySelector(".alerts");
    const topbar = document.querySelector(".topbar");

    if (!nextMain || !currentMain) {
        window.location.href = url;
        return;
    }

    currentMain.innerHTML = nextMain.innerHTML;

    if (currentAlerts) {
        currentAlerts.remove();
    }

    if (nextAlerts && topbar) {
        topbar.insertAdjacentElement("afterend", nextAlerts);
    }

    document.title = nextDocument.title;

    if (pushState && url && url !== window.location.href) {
        history.pushState({}, "", url);
    }

    bindFilePreview();
    syncActiveNav();
    keepBackgroundVideoAlive();
    window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadPage(url, options = {}) {
    document.body.classList.add("is-loading");

    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                "X-Requested-With": "fetch",
                ...(options.headers || {}),
            },
        });

        const contentType = response.headers.get("content-type") || "";

        if (!contentType.includes("text/html")) {
            window.location.href = response.url || url;
            return;
        }

        replacePage(await response.text(), response.url || url, options.pushState !== false);
    } catch {
        window.location.href = url;
    } finally {
        document.body.classList.remove("is-loading");
    }
}

document.addEventListener("click", (event) => {
    const clickedButton = event.target.closest(".button, .link-button, .nav a, button, .checkbox-list label");

    if (clickedButton) {
        pulseClickedElement(clickedButton);
        keepBackgroundVideoAlive();
    }

    const link = event.target.closest("a");

    if (
        !link ||
        link.target ||
        link.origin !== window.location.origin ||
        link.hasAttribute("download") ||
        link.dataset.noLazy === "true"
    ) {
        return;
    }

    event.preventDefault();
    loadPage(link.href);
});

document.addEventListener("submit", (event) => {
    const form = event.target;

    if (!(form instanceof HTMLFormElement) || form.action.includes("/export/pdf")) {
        return;
    }

    event.preventDefault();

    const submitter = event.submitter || form.querySelector("button[type='submit'], .button");
    pulseClickedElement(submitter);
    keepBackgroundVideoAlive();

    const method = (form.method || "get").toUpperCase();
    const formData = new FormData(form);

    if (method === "GET") {
        const url = new URL(form.action);
        url.search = new URLSearchParams(formData).toString();
        loadPage(url.toString());
        return;
    }

    loadPage(form.action, {
        method,
        body: formData,
    });
});

window.addEventListener("popstate", () => {
    loadPage(window.location.href, { method: "GET", pushState: false });
});

document.addEventListener("DOMContentLoaded", () => {
    bindFilePreview();
    syncActiveNav();
    keepBackgroundVideoAlive();
});

bindFilePreview();
syncActiveNav();
keepBackgroundVideoAlive();
