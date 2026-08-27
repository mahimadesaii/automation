document.addEventListener("DOMContentLoaded", () => {
    // State management
    let activeBatchCount = 0;
    let completedBatches = 0;
    let batchContents = {};
    let referencesMap = new Map();
    let eventSource = null;
    let batchStartTime = 0;
    let totalTokensConsumed = 0;

    // DOM Elements
    const topicInput = document.getElementById("research-topic");
    const apiKeyInput = document.getElementById("api-key-input");
    const saveApiKeyBtn = document.getElementById("save-api-key-btn");
    const apiKeyStatusText = document.getElementById("api-key-status");
    const footerKeyIndicator = document.getElementById("footer-key-indicator");
    
    const startBtn = document.getElementById("start-btn");
    const btnText = startBtn.querySelector(".btn-text");
    const btnSpinner = startBtn.querySelector(".btn-spinner");
    const researchForm = document.getElementById("research-form");
    
    const connectionStatus = document.getElementById("connection-status");
    const statusText = document.getElementById("status-text");
    
    const placeholderPanel = document.getElementById("placeholder-panel");
    const monitorPanel = document.getElementById("monitor-panel");
    const resultsPanel = document.getElementById("results-panel");
    
    const progressBar = document.getElementById("progress-bar");
    const progressPercent = document.getElementById("progress-percent");
    const batchStepsList = document.getElementById("batch-steps-list");
    const consoleLogs = document.getElementById("console-logs");
    const clearConsoleBtn = document.getElementById("clear-console-btn");
    
    const tabButtons = document.querySelectorAll(".nav-tab");
    const tabContents = document.querySelectorAll(".tab-content");
    
    const reportRenderedContent = document.getElementById("report-rendered-content");
    const taxonomyRenderedContent = document.getElementById("taxonomy-rendered-content");
    const deepdiveRenderedContent = document.getElementById("deepdive-rendered-content");
    const vendorsRenderedContent = document.getElementById("vendors-rendered-content");
    const referencesList = document.getElementById("references-list");
    const refCountSpan = document.getElementById("ref-count");
    
    const metricTotalTime = document.getElementById("metric-total-time");
    const metricTotalTokens = document.getElementById("metric-total-tokens");
    const metricAvgTpm = document.getElementById("metric-avg-tpm");
    const metricBatchesRun = document.getElementById("metric-batches-run");
    const metricsTableBody = document.getElementById("metrics-table-body");

    // ── API Key Management ──────────────────────────────────────────────────
    const LS_KEY = "groq_api_key";

    function loadApiKey() {
        const stored = localStorage.getItem(LS_KEY);
        if (stored) {
            apiKeyInput.value = stored;
            markKeySaved(stored);
        } else {
            apiKeyStatusText.textContent = "No key saved yet.";
            apiKeyStatusText.style.color = "var(--text-secondary)";
        }
    }

    function markKeySaved(key) {
        const masked = key.substring(0, 6) + "..." + key.slice(-4);
        apiKeyStatusText.textContent = `✅ Key saved: ${masked}`;
        apiKeyStatusText.style.color = "#16a34a";
        footerKeyIndicator.textContent = `Key: ${masked} ✓`;
        footerKeyIndicator.style.color = "#16a34a";
    }

    function getApiKey() {
        return localStorage.getItem(LS_KEY) || "";
    }

    saveApiKeyBtn.addEventListener("click", () => {
        const raw = apiKeyInput.value.trim();
        if (!raw || !raw.startsWith("gsk_")) {
            apiKeyStatusText.textContent = "⚠️ Key must start with gsk_";
            apiKeyStatusText.style.color = "#b45309";
            return;
        }
        localStorage.setItem(LS_KEY, raw);
        markKeySaved(raw);
    });

    // Load saved key on startup
    loadApiKey();
    // ────────────────────────────────────────────────────────────────────────

    // Listeners
    clearConsoleBtn.addEventListener("click", () => {
        consoleLogs.innerHTML = "";
    });

    // Tab switching
    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetTab = btn.getAttribute("data-tab");
            
            tabButtons.forEach(b => b.classList.remove("active"));
            tabContents.forEach(c => c.classList.remove("active"));
            
            btn.classList.add("active");
            document.getElementById(targetTab).classList.add("active");
        });
    });

    // Handle form submit
    researchForm.addEventListener("submit", (e) => {
        e.preventDefault();
        
        const topic = topicInput.value.trim();
        if (!topic) return;

        const apiKey = getApiKey();
        if (!apiKey) {
            apiKeyStatusText.textContent = "⚠️ Please enter and save your Groq API key first.";
            apiKeyStatusText.style.color = "#b45309";
            apiKeyInput.focus();
            return;
        }

        startResearch(topic, apiKey);
    });

    function formatNumber(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
        if (num >= 1000) return (num / 1000).toFixed(1) + "K";
        return num;
    }

    function addConsoleLog(message, type = "info") {
        const timeStr = new Date().toLocaleTimeString();
        const div = document.createElement("div");
        div.className = `log-entry ${type}`;
        div.innerHTML = `<strong>[${timeStr}]</strong> ${escapeHtml(message)}`;
        consoleLogs.appendChild(div);
        consoleLogs.scrollTop = consoleLogs.scrollHeight;
    }

    function escapeHtml(unsafe) {
        return unsafe
             .replace(/&/g, "&amp;")
             .replace(/</g, "&lt;")
             .replace(/>/g, "&gt;")
             .replace(/"/g, "&quot;")
             .replace(/'/g, "&#039;");
    }

    function startResearch(topic, apiKey) {
        // Reset state
        completedBatches = 0;
        batchContents = {};
        referencesMap.clear();
        totalTokensConsumed = 0;
        metricsTableBody.innerHTML = "";
        batchStepsList.innerHTML = "";
        
        // UI transitions
        placeholderPanel.classList.add("hidden");
        monitorPanel.classList.remove("hidden");
        resultsPanel.classList.add("hidden");
        
        setUIState("running", "Running Batch Queue");
        addConsoleLog(`Initiating batch research queue for: "${topic}" using server-side dynamic load balancing.`, "info");

        progressBar.style.width = "0%";
        progressPercent.textContent = "0%";

        const url = `/api/research/stream?topic=${encodeURIComponent(topic)}&api_key=${encodeURIComponent(apiKey)}`;
        
        // Open SSE EventSource
        batchStartTime = Date.now();
        eventSource = new EventSource(url);

        eventSource.addEventListener("message", (event) => {
            const data = JSON.parse(event.data);
            handleSSEEvent(data);
        });

        eventSource.onerror = (err) => {
            addConsoleLog("EventSource connection interrupted or server closed connection unexpectedly.", "warning");
            cleanupEventSource();
            if (completedBatches < activeBatchCount) {
                setUIState("error", "Failed / Incomplete");
            }
        };
    }

    function handleSSEEvent(data) {
        switch (data.type) {
            case "init":
                activeBatchCount = data.total_batches;
                addConsoleLog(`Scheduler approved queue plan. Model Profile: ${data.model_name}. Total batches: ${activeBatchCount}`, "success");
                
                // Initialize batch step elements — names sent from backend via SSE
                const batchNames = [
                    "Foundations & Core Concepts",
                    "Context, Applications & Timeline",
                    "Evaluation, Trade-offs & Methodology",
                    "Advanced Analysis & Emerging Dimensions",
                    "Solutions, Tools & Recommendations"
                ];

                for (let i = 1; i <= activeBatchCount; i++) {
                    const stepItem = document.createElement("div");
                    stepItem.id = `batch-step-row-${i}`;
                    stepItem.className = "batch-step-item";
                    stepItem.innerHTML = `
                        <span class="batch-step-name">Phase ${i}: ${batchNames[i-1]}</span>
                        <span class="batch-step-status" id="batch-step-status-lbl-${i}">Queued</span>
                    `;
                    batchStepsList.appendChild(stepItem);
                }
                break;

            case "log":
                // Determine log category
                let logType = "info";
                if (data.message.includes("Rate Limit Check") || data.message.includes("Delaying")) {
                    logType = "warning";
                } else if (data.message.includes("Success")) {
                    logType = "success";
                } else if (data.message.includes("Error") || data.message.includes("Failed")) {
                    logType = "error";
                }
                addConsoleLog(data.message, logType);
                break;

            case "status":
                const bId = data.batch_id;
                const statusRow = document.getElementById(`batch-step-row-${bId}`);
                const statusLbl = document.getElementById(`batch-step-status-lbl-${bId}`);
                
                if (statusRow && statusLbl) {
                    statusRow.className = `batch-step-item ${data.status}`;
                    statusLbl.textContent = data.status === "running" ? "Running" : "Queued";
                }
                break;

            case "result":
                const batchId = data.batch_id;
                const content = data.content;
                const tokens = data.tokens;
                const timeTaken = data.time_taken;
                
                completedBatches++;
                totalTokensConsumed += tokens;
                batchContents[batchId] = content;

                // Update progress step row
                const stepRow = document.getElementById(`batch-step-row-${batchId}`);
                const stepLbl = document.getElementById(`batch-step-status-lbl-${batchId}`);
                if (stepRow && stepLbl) {
                    stepRow.className = "batch-step-item success";
                    stepLbl.textContent = "Done";
                }

                // Update progress bar
                const pct = Math.round((completedBatches / activeBatchCount) * 100);
                progressBar.style.width = `${pct}%`;
                progressPercent.textContent = `${pct}%`;

                // Add to performance table
                const tr = document.createElement("tr");
                const batchNamesTable = [
                    "Foundations & Core Concepts",
                    "Context, Applications & Timeline",
                    "Evaluation, Trade-offs & Methodology",
                    "Advanced Analysis & Emerging Dimensions",
                    "Solutions, Tools & Recommendations"
                ];
                const modelUsedName = data.model_name || "Unknown Model";
                tr.innerHTML = `
                    <td><strong>Batch ${batchId}</strong></td>
                    <td>${batchNamesTable[batchId - 1]}</td>
                    <td><code style="font-size: 11px; background-color: var(--bg-sidebar); padding: 2px 6px; border-radius: 4px; color: var(--secondary);">${escapeHtml(modelUsedName)}</code></td>
                    <td>${timeTaken}s</td>
                    <td>${tokens} tokens</td>
                    <td><span class="status-badge status-done" style="padding: 2px 8px; font-size: 11px;"><span class="status-dot"></span>Completed</span></td>
                `;
                metricsTableBody.appendChild(tr);

                // Extract references dynamically
                extractAndMapReferences(content);

                // Live compile and render reports
                renderActiveReports();
                break;

            case "error":
                addConsoleLog(`Error in batch ${data.batch_id}: ${data.message}`, "error");
                const errRow = document.getElementById(`batch-step-row-${data.batch_id}`);
                const errLbl = document.getElementById(`batch-step-status-lbl-${data.batch_id}`);
                if (errRow && errLbl) {
                    errRow.className = "batch-step-item error";
                    errLbl.textContent = "Error";
                }
                cleanupEventSource();
                setUIState("error", "Error Occurred");
                break;

            case "done":
                addConsoleLog(`Successfully completed all ${activeBatchCount} batches in ${data.total_time}s!`, "success");
                
                // Finalize metrics dashboard
                metricTotalTime.textContent = data.total_time;
                metricTotalTokens.textContent = totalTokensConsumed;
                
                const mins = parseFloat(data.total_time) / 60;
                const calculatedTpm = mins > 0 ? Math.round(totalTokensConsumed / mins) : totalTokensConsumed;
                metricAvgTpm.textContent = formatNumber(calculatedTpm);
                
                metricBatchesRun.textContent = `${completedBatches}/${activeBatchCount}`;

                cleanupEventSource();
                setUIState("done", "Completed");
                
                // Display results panel
                resultsPanel.classList.remove("hidden");
                break;
        }
    }

    function extractAndMapReferences(text) {
        // Match markdown style: [Ref Name](URL)
        const mdLinkRegex = /\[([^\]\n]+)\]\((https?:\/\/[^\s\)]+)\)/g;
        let match;
        while ((match = mdLinkRegex.exec(text)) !== null) {
            const name = match[1].trim();
            const url = match[2].trim();
            // Filter out internal and parse utility links
            if (!url.includes("cdn.jsdelivr.net") && !url.includes("marked") && name.length < 150) {
                // Keep only unique urls
                if (!referencesMap.has(url)) {
                    referencesMap.set(url, name);
                }
            }
        }

        // Also check for raw URLs in text format if references list is sparse
        const urlRegex = /(https?:\/\/[^\s\)\"\'>]+)/g;
        const allUrls = text.match(urlRegex) || [];
        if (referencesMap.size < 6) {
            allUrls.forEach(url => {
                if (!url.includes("cdn.jsdelivr.net") && !url.includes("marked") && !referencesMap.has(url)) {
                    // Try to guess a name
                    const urlObj = new URL(url);
                    const domain = urlObj.hostname.replace("www.", "");
                    referencesMap.set(url, `Source from ${domain}`);
                }
            });
        }
    }

    function renderActiveReports() {
        // Compile Overview Full Report
        let fullReportMarkdown = "";
        let taxonomyReportMarkdown = "";
        
        // Assemble sections
        if (batchContents[1]) {
            fullReportMarkdown += `# Foundations & Core Concepts\n\n${batchContents[1]}\n\n---\n\n`;
            taxonomyReportMarkdown += `# Phase 1 — Foundations & Core Concepts\n\n${batchContents[1]}\n\n---\n\n`;
        }
        if (batchContents[2]) {
            fullReportMarkdown += `# Context, Applications & Timeline\n\n${batchContents[2]}\n\n---\n\n`;
            taxonomyReportMarkdown += `# Phase 2 — Context, Applications & Timeline\n\n${batchContents[2]}\n\n---\n\n`;
        }
        if (batchContents[3]) {
            fullReportMarkdown += `# Evaluation, Trade-offs & Methodology\n\n${batchContents[3]}\n\n---\n\n`;
            taxonomyReportMarkdown += `# Phase 3 — Evaluation, Trade-offs & Methodology\n\n${batchContents[3]}\n\n---\n\n`;
        }
        if (batchContents[4]) {
            fullReportMarkdown += `# Advanced Analysis & Emerging Dimensions\n\n${batchContents[4]}\n\n---\n\n`;
            deepdiveRenderedContent.innerHTML = marked.parse(batchContents[4]);
        }
        if (batchContents[5]) {
            fullReportMarkdown += `# Solutions, Tools & Recommendations\n\n${batchContents[5]}\n\n`;
            vendorsRenderedContent.innerHTML = marked.parse(batchContents[5]);
        }

        // Render sections
        if (fullReportMarkdown) {
            reportRenderedContent.innerHTML = marked.parse(fullReportMarkdown);
        }
        if (taxonomyReportMarkdown) {
            taxonomyRenderedContent.innerHTML = marked.parse(taxonomyReportMarkdown);
        }

        // Render consolidated references list
        referencesList.innerHTML = "";
        let idx = 1;
        referencesMap.forEach((name, url) => {
            const card = document.createElement("li");
            card.className = "reference-card";
            card.innerHTML = `
                <span class="reference-name">${idx}. ${escapeHtml(name)}</span>
                <a href="${escapeHtml(url)}" target="_blank" class="reference-url">${escapeHtml(url)}</a>
            `;
            referencesList.appendChild(card);
            idx++;
        });

        const count = referencesMap.size;
        refCountSpan.textContent = count;
        
        // Show indicator if references are less than 6
        if (count < 6 && completedBatches === activeBatchCount) {
            const warningCard = document.createElement("li");
            warningCard.className = "reference-card";
            warningCard.style.borderColor = "var(--warning)";
            warningCard.style.backgroundColor = "var(--warning-light)";
            warningCard.innerHTML = `
                <span class="reference-name" style="color: #b45309;">⚠️ Reference Quality Notice</span>
                <p style="font-size: 13px; color: #b45309; margin: 0;">Only ${count} unique URLs were extracted from the reports. Groq prompts require at least 6 sources, please review the text files for inline textual references.</p>
            `;
            referencesList.appendChild(warningCard);
        }
    }

    function setUIState(state, labelText) {
        // Button state
        if (state === "running") {
            startBtn.disabled = true;
            btnText.textContent = "Processing Batches...";
            btnSpinner.classList.remove("hidden");
            
            connectionStatus.className = "status-badge status-running";
            statusText.textContent = labelText;
        } else {
            startBtn.disabled = false;
            btnText.textContent = "Generate Research";
            btnSpinner.classList.add("hidden");
            
            if (state === "done") {
                connectionStatus.className = "status-badge status-done";
                statusText.textContent = labelText;
            } else if (state === "error") {
                connectionStatus.className = "status-badge status-error";
                statusText.textContent = labelText;
            } else {
                connectionStatus.className = "status-badge status-idle";
                statusText.textContent = labelText;
            }
        }
    }

    function cleanupEventSource() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }
});
