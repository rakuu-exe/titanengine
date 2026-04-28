const preview = document.getElementById("hoverPreview");

if (preview) {
    document.querySelectorAll("tr[data-preview]").forEach((row) => {
        row.addEventListener("mouseenter", () => {
            const text = row.dataset.preview || "No extracted text preview is available for this file yet.";
            preview.textContent = text.trim() || "No extracted text preview is available for this file yet.";
        });
    });
}
