function toggleAIDropdown(dropdownId) {
    document.getElementById(dropdownId).classList.toggle("show");
}

document.addEventListener("click", function (event) {
    if (!event.target.matches('.ai-btn')) {
        var dropdowns = document.getElementsByClassName("ai-dropdown-content");
        for (var i = 0; i < dropdowns.length; i++) {
            if (dropdowns[i].classList.contains('show')) {
                dropdowns[i].classList.remove('show');
            }
        }
    }
});

async function fetchAndCopyAIPrompt(endpointUrl, mode, btnEl) {
    const originalText = btnEl.innerHTML;
    btnEl.innerText = "⏳ Generating...";
    btnEl.style.backgroundColor = "#ffaa00";
    btnEl.style.color = "#121212";
    btnEl.style.borderColor = "#ffaa00";
    try {
        const response = await fetch(endpointUrl + '?mode=' + encodeURIComponent(mode));
        const data = await response.json();
        if (response.ok) {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(data.prompt);
            } else {
                const textArea = document.createElement("textarea");
                textArea.value = data.prompt;
                textArea.style.position = "fixed";
                document.body.appendChild(textArea);
                textArea.focus();
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
            }
            btnEl.innerText = "✅ Copied!";
            btnEl.style.backgroundColor = "#00ff00";
        } else {
            alert("Failed to generate AI prompt: " + data.message);
            btnEl.innerText = "❌ Error";
            btnEl.style.backgroundColor = "#ff4d4d";
        }
    } catch (err) {
        alert("Network/Clipboard error occurred: " + err.message);
        btnEl.innerText = "❌ Error";
        btnEl.style.backgroundColor = "#ff4d4d";
    }
    setTimeout(function () {
        btnEl.innerHTML = originalText;
        btnEl.style.backgroundColor = "";
        btnEl.style.color = "";
        btnEl.style.borderColor = "";
    }, 3000);
}
