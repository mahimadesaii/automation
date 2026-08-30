document.addEventListener("DOMContentLoaded", () => {
    // System State Variables
    let activeBatchCount = 0;
    let completedBatches = 0;
    let batchContents = {};
    let batchMetadataMap = {};
    let referencesMap = new Map();
    let eventSource = null;
    
    let currentRunTotalTokens = 0;
    let currentRunPromptTokens = 0;
    let currentRunCompletionTokens = 0;

    let cooldownTimerInterval = null;
    let attachedDocumentText = "";
    let attachedDocumentName = "";

    // DOM Elements
    const topicInput = document.getElementById("research-topic");
    const accessTokenInput = document.getElementById("access-token-input");
    const saveTokenBtn = document.getElementById("save-token-btn");
    const clearTokenBtn = document.getElementById("clear-token-btn");
    const enableGroqToggle = document.getElementById("enable-groq-toggle");
    const toggleGroqStatus = document.getElementById("toggle-groq-status");
    const tokenStatusText = document.getElementById("token-status-text");

    // File Upload DOM Elements
    const documentUploadInput = document.getElementById("document-upload-input");
    const triggerFileBtn = document.getElementById("trigger-file-btn");
    const fileNameLabel = document.getElementById("file-name-label");
    const removeFileBtn = document.getElementById("remove-file-btn");
    const documentStatusText = document.getElementById("document-status-text");

    const paramDepth = document.getElementById("param-depth");
    const optDeepDive = document.getElementById("opt-deep-dive");
    const deepDiveLockBadge = document.getElementById("deep-dive-lock-badge");
    const cooldownTimerSec = document.getElementById("cooldown-timer-sec");

    const paramDomain = document.getElementById("param-domain");
    const paramTone = document.getElementById("param-tone");

    const startBtn = document.getElementById("start-btn");
    const btnText = startBtn.querySelector(".btn-text");
    const btnSpinner = startBtn.querySelector(".btn-spinner");
    const researchForm = document.getElementById("research-form");

    // Mobile Sidebar Drawer Controls
    const sidebar = document.getElementById("sidebar");
    const mobileSidebarToggle = document.getElementById("mobile-sidebar-toggle");
    const closeSidebarBtn = document.getElementById("close-sidebar-btn");
    const sidebarOverlay = document.getElementById("sidebar-overlay");

    function toggleMobileSidebar(open) {
        if (!sidebar || !sidebarOverlay) return;
        if (open === undefined) {
            open = !sidebar.classList.contains("open");
        }
        if (open) {
            sidebar.classList.add("open");
            sidebarOverlay.classList.add("active");
            sidebarOverlay.classList.remove("hidden");
        } else {
            sidebar.classList.remove("open");
            sidebarOverlay.classList.remove("active");
            setTimeout(() => sidebarOverlay.classList.add("hidden"), 300);
        }
    }

    if (mobileSidebarToggle) {
        mobileSidebarToggle.addEventListener("click", () => toggleMobileSidebar(true));
    }
    if (closeSidebarBtn) {
        closeSidebarBtn.addEventListener("click", () => toggleMobileSidebar(false));
    }
    if (sidebarOverlay) {
        sidebarOverlay.addEventListener("click", () => toggleMobileSidebar(false));
    }

    const capacityBadge = document.getElementById("capacity-badge");
    const capacityBarFill = document.getElementById("capacity-bar-fill");
    const capacityPctLabel = document.getElementById("capacity-pct-label");

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

    const resultsNav = document.getElementById("results-nav");
    const dynamicTabsContainer = document.getElementById("dynamic-tabs-container");
    const dynamicSectionsContentContainer = document.getElementById("dynamic-sections-content-container");

    const reportRenderedContent = document.getElementById("report-rendered-content");
    const referencesList = document.getElementById("references-list");
    const refCountSpan = document.getElementById("ref-count");

    const metricTotalTime = document.getElementById("metric-total-time");
    const metricTotalTokens = document.getElementById("metric-total-tokens");
    const metricPromptTokens = document.getElementById("metric-prompt-tokens");
    const metricCompletionTokens = document.getElementById("metric-completion-tokens");
    const metricsTableBody = document.getElementById("metrics-table-body");

    // ── Token & Key Storage ──────────────────────────────────────────────────
    const LS_KEY = "aethelgard_access_token";
    const LS_ENABLED = "aethelgard_groq_enabled";

    function loadSavedConfig() {
        const isEnabled = localStorage.getItem(LS_ENABLED) !== "false";
        if (enableGroqToggle) {
            enableGroqToggle.checked = isEnabled;
        }

        const storedKey = localStorage.getItem(LS_KEY);
        if (storedKey) {
            accessTokenInput.value = storedKey;
            updateKeyStatusDisplay();
        } else {
            updateKeyStatusDisplay();
        }

        checkSystemCapacity();
    }

    function updateKeyStatusDisplay() {
        const isEnabled = enableGroqToggle ? enableGroqToggle.checked : true;
        const currentKey = accessTokenInput.value.trim();

        if (toggleGroqStatus) {
            toggleGroqStatus.textContent = isEnabled ? "Cloud Engine Active" : "Custom Key Disabled";
            toggleGroqStatus.style.color = isEnabled ? "var(--success)" : "var(--text-muted)";
        }

        if (!isEnabled) {
            tokenStatusText.textContent = "Blank = Defaulting to Free Online Cloud Engine";
            tokenStatusText.style.color = "var(--text-secondary)";
            accessTokenInput.disabled = true;
        } else {
            accessTokenInput.disabled = false;
            if (currentKey) {
                const masked = currentKey.substring(0, 6) + "..." + currentKey.slice(-4);
                tokenStatusText.textContent = `✅ Saved Custom Key (${masked})`;
                tokenStatusText.style.color = "#16a34a";
            } else {
                tokenStatusText.textContent = "Blank = Defaulting to Free Online Cloud Engine (Zero Setup)";
                tokenStatusText.style.color = "var(--text-secondary)";
            }
        }
    }

    function getActiveAccessToken() {
        if (enableGroqToggle && !enableGroqToggle.checked) {
            return ""; // Explicitly return empty string to force Local Ollama engine
        }
        return accessTokenInput.value.trim() || localStorage.getItem(LS_KEY) || "";
    }

    if (enableGroqToggle) {
        enableGroqToggle.addEventListener("change", () => {
            localStorage.setItem(LS_ENABLED, enableGroqToggle.checked ? "true" : "false");
            updateKeyStatusDisplay();
        });
    }

    if (saveTokenBtn) {
        saveTokenBtn.addEventListener("click", () => {
            const raw = accessTokenInput.value.trim();
            if (raw) {
                localStorage.setItem(LS_KEY, raw);
                if (enableGroqToggle) {
                    enableGroqToggle.checked = true;
                    localStorage.setItem(LS_ENABLED, "true");
                }
                updateKeyStatusDisplay();
                addConsoleLog("Updated Groq API Key.", "success");
            } else {
                localStorage.removeItem(LS_KEY);
                updateKeyStatusDisplay();
            }
        });
    }

    if (clearTokenBtn) {
        clearTokenBtn.addEventListener("click", () => {
            accessTokenInput.value = "";
            localStorage.removeItem(LS_KEY);
            if (enableGroqToggle) {
                enableGroqToggle.checked = false;
                localStorage.setItem(LS_ENABLED, "false");
            }
            updateKeyStatusDisplay();
            addConsoleLog("Disabled & cleared Groq API Key. Engine set to Local Ollama.", "warning");
        });
    }

    // ── File Upload Controls ─────────────────────────────────────────────────
    if (triggerFileBtn && documentUploadInput) {
        triggerFileBtn.addEventListener("click", () => {
            documentUploadInput.click();
        });

        documentUploadInput.addEventListener("change", async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            fileNameLabel.textContent = `Uploading ${file.name}...`;
            documentStatusText.textContent = "Parsing document text...";

            const formData = new FormData();
            formData.append("file", file);

            try {
                const res = await fetch("/api/upload", {
                    method: "POST",
                    body: formData
                });
                const data = await res.json();

                if (res.ok && data.success) {
                    attachedDocumentText = data.text;
                    attachedDocumentName = data.filename;
                    
                    fileNameLabel.textContent = `📎 ${data.filename}`;
                    fileNameLabel.classList.add("attached");
                    removeFileBtn.classList.remove("hidden");
                    documentStatusText.textContent = `✅ Document attached (${data.character_count} chars)`;
                    documentStatusText.style.color = "#16a34a";
                } else {
                    throw new Error(data.error || "Upload failed");
                }
            } catch (err) {
                fileNameLabel.textContent = "Upload failed";
                documentStatusText.textContent = `❌ ${err.message}`;
                documentStatusText.style.color = "var(--error)";
            }
        });

        removeFileBtn.addEventListener("click", () => {
            attachedDocumentText = "";
            attachedDocumentName = "";
            documentUploadInput.value = "";
            fileNameLabel.textContent = "No document attached";
            fileNameLabel.classList.remove("attached");
            removeFileBtn.classList.add("hidden");
            documentStatusText.textContent = "Supports PDF, TXT, MD, CSV, JSON for document analysis";
            documentStatusText.style.color = "var(--text-muted)";
        });
    }

    // System Capacity Safeguards
    async function checkSystemCapacity() {
        try {
            const res = await fetch("/api/capacity");
            const data = await res.json();
            updateCapacityGauge(data.capacity_utilized_pct, data.deep_dive_locked, data.cooldown_seconds);
        } catch (e) {}
    }

    function updateCapacityGauge(pct, locked = false, cooldownSec = 0) {
        capacityBadge.textContent = `${pct}% Utilized`;
        capacityBarFill.style.width = `${pct}%`;
        capacityPctLabel.innerHTML = `<strong>${pct}%</strong> Utilized of 100% Overall System Capacity`;

        if (pct > 85) {
            capacityBarFill.style.background = "var(--error)";
        } else if (pct > 60) {
            capacityBarFill.style.background = "var(--warning)";
        } else {
            capacityBarFill.style.background = "linear-gradient(90deg, var(--primary), var(--secondary))";
        }

        if (locked || pct >= 80) {
            optDeepDive.disabled = true;
            deepDiveLockBadge.classList.remove("hidden");
            
            if (paramDepth.value === "deep") {
                paramDepth.value = "standard";
                addConsoleLog("Capacity constrained (>80%). Deep Dive auto-switched to Standard mode.", "warning");
            }
            startCooldownTimer(cooldownSec || 60);
        } else {
            optDeepDive.disabled = false;
            deepDiveLockBadge.classList.add("hidden");
            clearInterval(cooldownTimerInterval);
        }
    }

    function startCooldownTimer(seconds) {
        clearInterval(cooldownTimerInterval);
        let rem = seconds;
        cooldownTimerSec.textContent = rem;
        cooldownTimerInterval = setInterval(() => {
            rem--;
            if (rem <= 0) {
                clearInterval(cooldownTimerInterval);
                checkSystemCapacity();
            } else {
                cooldownTimerSec.textContent = rem;
            }
        }, 1000);
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

    clearConsoleBtn.addEventListener("click", () => {
        consoleLogs.innerHTML = "";
    });

    resultsNav.addEventListener("click", (e) => {
        const targetBtn = e.target.closest(".nav-tab");
        if (!targetBtn) return;

        const targetTabId = targetBtn.getAttribute("data-tab");
        if (!targetTabId) return;

        document.querySelectorAll(".nav-tab").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

        targetBtn.classList.add("active");
        const targetContent = document.getElementById(targetTabId);
        if (targetContent) {
            targetContent.classList.add("active");
        }
    });

    // Header Search Box DOM Elements
    const headerSearchBox = document.getElementById("header-search-box");
    const headerSearchInput = document.getElementById("header-search-input");
    const headerSearchBtn = document.getElementById("header-search-btn");

    researchForm.addEventListener("submit", (e) => {
        e.preventDefault();
        toggleMobileSidebar(false);
        
        const topic = getActiveQueryTopic();
        if (!topic) return;

        const accessToken = getActiveAccessToken();
        const depth = paramDepth.value;
        const domain = paramDomain.value;
        const tone = paramTone.value;

        startResearch({ topic, accessToken, depth, domain, tone, documentText: attachedDocumentText });
    });

    // Primary Hero Search Bar & Sample Card Handlers
    const heroSearchInput = document.getElementById("hero-search-input");
    const heroSearchBtn = document.getElementById("hero-search-btn");
    const sampleCards = document.querySelectorAll(".sample-card");

    function getActiveQueryTopic() {
        if (heroSearchInput && heroSearchInput.value.trim()) {
            return heroSearchInput.value.trim();
        }
        if (headerSearchInput && headerSearchInput.value.trim()) {
            return headerSearchInput.value.trim();
        }
        return "";
    }

    function executeHeroSearch(queryTopic) {
        const topic = (queryTopic || getActiveQueryTopic()).trim();
        if (!topic) return;

        if (heroSearchInput) heroSearchInput.value = topic;
        if (headerSearchInput) headerSearchInput.value = topic;
        
        toggleMobileSidebar(false);

        const accessToken = getActiveAccessToken();
        const depth = paramDepth.value;
        const domain = paramDomain.value;
        const tone = paramTone.value;

        startResearch({ topic, accessToken, depth, domain, tone, documentText: attachedDocumentText });
    }

    if (heroSearchBtn) {
        heroSearchBtn.addEventListener("click", () => executeHeroSearch());
    }

    if (heroSearchInput) {
        heroSearchInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                executeHeroSearch();
            }
        });
    }

    if (headerSearchBtn) {
        headerSearchBtn.addEventListener("click", () => executeHeroSearch());
    }

    if (headerSearchInput) {
        headerSearchInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                executeHeroSearch();
            }
        });
    }

    sampleCards.forEach(card => {
        card.addEventListener("click", () => {
            const topic = card.getAttribute("data-topic");
            if (topic) {
                executeHeroSearch(topic);
            }
        });
    });

    function startResearch(params) {
        completedBatches = 0;
        batchContents = {};
        batchMetadataMap = {};
        referencesMap.clear();
        currentRunTotalTokens = 0;
        currentRunPromptTokens = 0;
        currentRunCompletionTokens = 0;

        metricsTableBody.innerHTML = "";
        batchStepsList.innerHTML = "";
        dynamicTabsContainer.innerHTML = "";
        dynamicSectionsContentContainer.innerHTML = "";
        reportRenderedContent.innerHTML = "";

        placeholderPanel.classList.add("hidden");
        monitorPanel.classList.remove("hidden");
        resultsPanel.classList.add("hidden");
        
        if (headerSearchBox) headerSearchBox.classList.remove("hidden");
        if (headerSearchInput) headerSearchInput.value = params.topic;
        
        setUIState("running", "Analyzing Query & Synthesizing Plan");
        
        const modeLabel = params.accessToken ? "Custom Cloud Key" : "Online Free Cloud Engine";
        const docLabel = params.documentText ? " (Document Grounded)" : "";
        addConsoleLog(`Initiating research engine for: "${params.topic}" [${modeLabel}]${docLabel}`, "info");

        progressBar.style.width = "0%";
        progressPercent.textContent = "0%";

        const basePayload = {
            topic: params.topic,
            access_token: params.accessToken,
            depth: params.depth,
            domain: params.domain,
            tone: params.tone,
            document_text: params.documentText || ""
        };

        if (eventSource) eventSource.close();

        // ── Free mode: per-section fetch (each call gets its own Vercel timeout) ──
        // ── Keyed mode: original fast SSE stream ──
        const isKeyedMode = params.accessToken && params.accessToken.startsWith("gsk_");

        if (!isKeyedMode) {
            runPerSectionMode(params, basePayload);
        } else {
            runSSEStreamMode(basePayload);
        }
    }

    // Per-section mode: fetch plan first, then fetch each section sequentially
    async function runPerSectionMode(params, basePayload) {
        try {
            // Step 1: Get section plan (instant, no AI)
            addConsoleLog("Analyzing research query & domain intent...", "info");
            const planRes = await fetch("/api/research/plan", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(basePayload)
            });
            const planData = await planRes.json();
            if (!planRes.ok || !planData.sections) {
                addConsoleLog(`Plan error: ${planData.error || "Unknown error"}`, "error");
                setUIState("error", "Failed / Incomplete");
                return;
            }

            const archetype = planData.archetype;
            const sections = planData.sections;
            activeBatchCount = sections.length;

            addConsoleLog(`Detected research archetype: [${archetype}]. Planning customized section structure.`, "info");

            // Emit an init-like event to build the UI
            handleSSEEvent({
                type: "init",
                total_batches: sections.length,
                batch_metadata: sections.map(s => ({ id: s.id, name: s.name }))
            });
            addConsoleLog(`Section planner initialized ${sections.length} dynamic sections with context preservation.`, "success");

            // Step 2: Execute sections one by one
            const prevSummaries = [];
            for (const sec of sections) {
                handleSSEEvent({ type: "status", batch_id: sec.id, status: "running" });

                const sectionPayload = {
                    ...basePayload,
                    section_id: sec.id,
                    section_name: sec.name,
                    section_desc: sec.desc || "",
                    archetype: archetype,
                    prev_summaries: prevSummaries
                };

                try {
                    // 55-second client-side abort — ensures we never hang past Vercel's 60s limit
                    const controller = new AbortController();
                    const abortTimer = setTimeout(() => controller.abort(), 55000);

                    let secRes, secData;
                    try {
                        secRes = await fetch("/api/research/section", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(sectionPayload),
                            signal: controller.signal
                        });
                        clearTimeout(abortTimer);
                    } catch (fetchErr) {
                        clearTimeout(abortTimer);
                        // AbortError = client timeout; rethrow for outer catch to show clean message
                        throw new Error(fetchErr.name === "AbortError"
                            ? `Section ${sec.id} timed out — AI Horde queue is busy. Please try again.`
                            : fetchErr.message);
                    }

                    // Guard against Vercel error pages (HTML) being served instead of JSON
                    const ct = secRes.headers.get("content-type") || "";
                    if (!ct.includes("application/json")) {
                        const rawText = await secRes.text();
                        const shortMsg = rawText.slice(0, 120).replace(/\s+/g, " ");
                        handleSSEEvent({ type: "error", batch_id: sec.id, message: `Server error (section ${sec.id}): ${shortMsg}` });
                        setUIState("error", "Failed / Incomplete");
                        return;
                    }

                    secData = await secRes.json();

                    if (!secRes.ok || !secData.success) {
                        const errMsg = secData.error || "Section execution failed";
                        handleSSEEvent({ type: "error", batch_id: sec.id, message: `Execution error: ${errMsg}` });
                        setUIState("error", "Failed / Incomplete");
                        return;
                    }

                    // Track summary for context continuity
                    prevSummaries.push({ name: sec.name, summary: secData.summary || "" });

                    // Reuse the same result handler as SSE
                    handleSSEEvent({
                        type: "result",
                        batch_id: secData.section_id,
                        batch_name: secData.section_name,
                        content: secData.content,
                        prompt_tokens: secData.prompt_tokens,
                        completion_tokens: secData.completion_tokens,
                        tokens: secData.tokens,
                        time_taken: secData.time_taken,
                        node_name: secData.node_name,
                        capacity_pct: secData.capacity_pct,
                        deep_dive_locked: secData.deep_dive_locked,
                        cooldown_seconds: secData.cooldown_seconds
                    });
                    addConsoleLog(`Section ${sec.id} (${sec.name}) verified & synthesized via ${secData.node_name} in ${secData.time_taken}s (${secData.tokens} tokens).`, "success");

                } catch (err) {
                    handleSSEEvent({ type: "error", batch_id: sec.id, message: err.message || `Section ${sec.id} failed` });
                    setUIState("error", "Failed / Incomplete");
                    return;
                }

            }

            // All sections done
            handleSSEEvent({ type: "done", total_tokens: currentRunTotalTokens });

        } catch (err) {
            addConsoleLog(`Research error: ${err.message}`, "error");
            setUIState("error", "Connection Error");
        }
    }

    // Keyed SSE stream mode (original fast path for users with a Groq key)
    function runSSEStreamMode(payload) {
        fetch("/api/research/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(response => {
            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";

            function readChunk() {
                reader.read().then(({ done, value }) => {
                    if (done) {
                        if (completedBatches < activeBatchCount) {
                            setUIState("error", "Failed / Incomplete");
                        }
                        return;
                    }

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split("\n\n");
                    buffer = lines.pop();

                    for (const block of lines) {
                        const trimmed = block.trim();
                        if (trimmed.startsWith("data: ")) {
                            try {
                                const jsonStr = trimmed.replace(/^data:\s*/, "");
                                const data = JSON.parse(jsonStr);
                                handleSSEEvent(data);
                            } catch (err) {}
                        }
                    }

                    readChunk();
                }).catch(err => {
                    addConsoleLog("Connection closed or completed.", "warning");
                    if (completedBatches < activeBatchCount) {
                        setUIState("error", "Failed / Incomplete");
                    }
                });
            }

            readChunk();
        }).catch(err => {
            addConsoleLog(`Stream error: ${err.message}`, "error");
            setUIState("error", "Connection Error");
        });
    }



    function handleSSEEvent(data) {
        switch (data.type) {
            case "init":
                activeBatchCount = data.total_batches;
                const batchMeta = data.batch_metadata || [];
                
                addConsoleLog(`Section planner initialized ${activeBatchCount} dynamic sections with context preservation.`, "success");

                batchMeta.forEach((meta) => {
                    const bId = meta.id;
                    const bName = meta.name;
                    batchMetadataMap[bId] = bName;

                    const stepItem = document.createElement("div");
                    stepItem.id = `batch-step-row-${bId}`;
                    stepItem.className = "batch-step-item";
                    stepItem.innerHTML = `
                        <span class="batch-step-name">Section ${bId}: ${escapeHtml(bName)}</span>
                        <span class="batch-step-status" id="batch-step-status-lbl-${bId}">Queued</span>
                    `;
                    batchStepsList.appendChild(stepItem);

                    const tabBtn = document.createElement("button");
                    tabBtn.className = "nav-tab";
                    tabBtn.setAttribute("data-tab", `sec-tab-${bId}`);
                    tabBtn.textContent = bName;
                    dynamicTabsContainer.appendChild(tabBtn);

                    const secContainer = document.createElement("section");
                    secContainer.id = `sec-tab-${bId}`;
                    secContainer.className = "tab-content";
                    secContainer.innerHTML = `
                        <div class="section-header-badge">Section ${bId} • ${escapeHtml(bName)}</div>
                        <article id="sec-rendered-content-${bId}" class="rendered-markdown">
                            <p class="placeholder-text">Synthesizing content with context continuity...</p>
                        </article>
                    `;
                    dynamicSectionsContentContainer.appendChild(secContainer);
                });
                break;

            case "log":
                let logType = "info";
                if (data.message.includes("Rate Limit") || data.message.includes("Pausing")) {
                    logType = "warning";
                } else if (data.message.includes("completed") || data.message.includes("Synthesized")) {
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
                    statusLbl.textContent = data.status === "running" ? "Analyzing" : "Queued";
                }
                break;

            case "result":
                const batchId = data.batch_id;
                const batchName = data.batch_name || batchMetadataMap[batchId] || `Section ${batchId}`;
                const content = data.content;
                const pTok = data.prompt_tokens || 0;
                const cTok = data.completion_tokens || 0;
                const tokens = data.tokens || (pTok + cTok);
                const timeTaken = data.time_taken;
                const nodeUsedName = data.node_name || "Compute Node";
                
                completedBatches++;
                currentRunTotalTokens += tokens;
                currentRunPromptTokens += pTok;
                currentRunCompletionTokens += cTok;

                batchContents[batchId] = content;
                updateCapacityGauge(data.capacity_pct || 0, data.deep_dive_locked, data.cooldown_seconds);

                const stepRow = document.getElementById(`batch-step-row-${batchId}`);
                const stepLbl = document.getElementById(`batch-step-status-lbl-${batchId}`);
                if (stepRow && stepLbl) {
                    stepRow.className = "batch-step-item success";
                    stepLbl.textContent = "Complete";
                }

                const pct = Math.round((completedBatches / activeBatchCount) * 100);
                progressBar.style.width = `${pct}%`;
                progressPercent.textContent = `${pct}%`;

                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>Section ${batchId}</strong></td>
                    <td>${escapeHtml(batchName)}</td>
                    <td><code class="code-badge">${escapeHtml(nodeUsedName)}</code></td>
                    <td>${timeTaken}s</td>
                    <td>${pTok}</td>
                    <td>${cTok}</td>
                    <td><strong>${tokens}</strong></td>
                `;
                metricsTableBody.appendChild(tr);

                extractAndMapReferences(content);
                resultsPanel.classList.remove("hidden");
                renderActiveReports();
                break;

            case "error":
                addConsoleLog(data.message, "error");
                const errRow = document.getElementById(`batch-step-row-${data.batch_id}`);
                const errLbl = document.getElementById(`batch-step-status-lbl-${data.batch_id}`);
                if (errRow && errLbl) {
                    errRow.className = "batch-step-item error";
                    errLbl.textContent = "Error";
                }
                setUIState("error", "Error Occurred");
                break;

            case "done":
                addConsoleLog(`Synthesis complete in ${data.total_time}s! Consumed ${currentRunTotalTokens} tokens.`, "success");
                
                metricTotalTime.textContent = data.total_time;
                metricTotalTokens.textContent = currentRunTotalTokens;
                metricPromptTokens.textContent = currentRunPromptTokens;
                metricCompletionTokens.textContent = currentRunCompletionTokens;

                updateCapacityGauge(data.final_capacity_pct || 0);
                setUIState("done", "Completed");
                
                resultsPanel.classList.remove("hidden");
                renderActiveReports();
                break;
        }
    }

    function extractAndMapReferences(text) {
        const mdLinkRegex = /\[([^\]\n]+)\]\((https?:\/\/[^\s\)]+)\)/g;
        let match;
        while ((match = mdLinkRegex.exec(text)) !== null) {
            const name = match[1].trim();
            const url = match[2].trim();
            if (!url.includes("cdn.jsdelivr.net") && !url.includes("marked") && name.length < 150) {
                if (!referencesMap.has(url)) {
                    referencesMap.set(url, name);
                }
            }
        }
    }

    function renderActiveReports() {
        const topic = getActiveQueryTopic() || "Research Report";
        const currentDateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
        
        let compiledFullMarkdown = `# ${topic} — Research Report\n\n`;
        compiledFullMarkdown += `**Information Current As Of:** ${currentDateStr}\n\n`;

        if (attachedDocumentName) {
            compiledFullMarkdown += `**Primary Document Reference:** \`${attachedDocumentName}\`\n\n`;
        }

        compiledFullMarkdown += `---\n\n`;

        for (let i = 1; i <= activeBatchCount; i++) {
            const secContent = batchContents[i];
            const secName = batchMetadataMap[i] || `Section ${i}`;
            
            if (secContent) {
                const secEl = document.getElementById(`sec-rendered-content-${i}`);
                if (secEl) {
                    secEl.innerHTML = marked.parse(secContent);
                }
                compiledFullMarkdown += `## Section ${i}: ${secName}\n\n${secContent}\n\n---\n\n`;
            }
        }

        if (compiledFullMarkdown) {
            reportRenderedContent.innerHTML = marked.parse(compiledFullMarkdown);
        }

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

        refCountSpan.textContent = referencesMap.size;
    }

    function setUIState(state, labelText) {
        if (state === "running") {
            startBtn.disabled = true;
            btnText.textContent = "Synthesizing Research...";
            btnSpinner.classList.remove("hidden");
            
            connectionStatus.className = "status-badge status-running";
            statusText.textContent = labelText;
        } else {
            startBtn.disabled = false;
            btnText.textContent = "Synthesize Research";
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

    loadSavedConfig();
});
