document.addEventListener('DOMContentLoaded', () => {
  // DOM Elements
  const apiKeyInput = document.getElementById('apiKeyInput');
  const toggleApiKeyBtn = document.getElementById('toggleApiKey');
  const promptInput = document.getElementById('promptInput');
  const filterChips = document.getElementById('filterChips');
  const modelCountBadge = document.getElementById('modelCountBadge');
  const selectedCountText = document.getElementById('selectedCountText');
  
  const btnFetchModels = document.getElementById('btnFetchModels');
  const btnRunProbe = document.getElementById('btnRunProbe');
  const btnSelectAll = document.getElementById('btnSelectAll');
  const btnDeselectAll = document.getElementById('btnDeselectAll');
  const btnSelectRecommended = document.getElementById('btnSelectRecommended');
  const btnOpenCodeConfig = document.getElementById('btnOpenCodeConfig');

  const progressContainer = document.getElementById('progressContainer');
  const progressBar = document.getElementById('progressBar');
  const progressText = document.getElementById('progressText');
  const progressPercent = document.getElementById('progressPercent');
  const lastUpdatedTime = document.getElementById('lastUpdatedTime');

  const resultsTableBody = document.getElementById('resultsTableBody');
  const tableSearchInput = document.getElementById('tableSearchInput');
  const sortSelect = document.getElementById('sortSelect');

  const configModal = document.getElementById('configModal');
  const btnCloseModal = document.getElementById('btnCloseModal');
  const btnCloseModalBtn = document.getElementById('btnCloseModalBtn');
  const btnCopyConfig = document.getElementById('btnCopyConfig');
  const configJsonCode = document.getElementById('configJsonCode');

  // Application State
  let allModels = [];
  let selectedModelIds = new Set([
    "qwen/qwen2.5-coder-32b-instruct",
    "deepseek-ai/deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-pro",
    "meta/llama-3.3-70b-instruct",
    "nv-mistralai/mistral-nemo-12b-instruct",
    "google/gemma-4-31b-it"
  ]);
  let probeResults = [];
  let activeFilter = 'all';

  // Load saved API Key from localStorage
  const savedKey = localStorage.getItem('nvidia_api_key');
  if (savedKey) {
    apiKeyInput.value = savedKey;
  }

  apiKeyInput.addEventListener('input', () => {
    localStorage.setItem('nvidia_api_key', apiKeyInput.value.trim());
  });

  toggleApiKeyBtn.addEventListener('click', () => {
    apiKeyInput.type = apiKeyInput.type === 'password' ? 'text' : 'password';
  });

  // Fetch Live Models Catalog
  async function loadModelsCatalog() {
    modelCountBadge.textContent = "Fetching Live Models...";
    const apiKey = apiKeyInput.value.trim();

    try {
      const response = await fetch('/api/fetch-models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apiKey })
      });
      const data = await response.json();
      allModels = data.models || [];
      modelCountBadge.textContent = `${allModels.length} Models Available`;
      updateSelectionCounters();
    } catch (err) {
      console.error('Failed to fetch models:', err);
      modelCountBadge.textContent = 'Catalog Load Failed';
    }
  }

  // Filter Chips Logic
  filterChips.addEventListener('click', (e) => {
    if (!e.target.classList.contains('chip')) return;
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    e.target.classList.add('active');
    activeFilter = e.target.dataset.filter;
    renderResultsTable();
  });

  function getFilteredModels() {
    if (activeFilter === 'all') return allModels;
    return allModels.filter(m => {
      const id = m.id.toLowerCase();
      if (activeFilter === 'coding') return m.category === 'coding' || id.includes('code');
      if (activeFilter === 'reasoning') return m.category === 'reasoning' || id.includes('r1') || id.includes('v4') || id.includes('ultra') || id.includes('super');
      if (activeFilter === 'qwen') return id.includes('qwen') || id.includes('deepseek');
      if (activeFilter === 'meta') return id.includes('meta') || id.includes('llama');
      if (activeFilter === 'mistral') return id.includes('mistral');
      return true;
    });
  }

  // Selection Controls
  function updateSelectionCounters() {
    selectedCountText.textContent = `${selectedModelIds.size} model${selectedModelIds.size === 1 ? '' : 's'} selected`;
  }

  btnSelectAll.addEventListener('click', () => {
    const visible = getFilteredModels();
    visible.forEach(m => selectedModelIds.add(m.id));
    updateSelectionCounters();
    renderResultsTable();
  });

  btnDeselectAll.addEventListener('click', () => {
    selectedModelIds.clear();
    updateSelectionCounters();
    renderResultsTable();
  });

  btnSelectRecommended.addEventListener('click', () => {
    selectedModelIds.clear();
    const recommended = [
      "qwen/qwen2.5-coder-32b-instruct",
      "deepseek-ai/deepseek-v4-flash",
      "deepseek-ai/deepseek-v4-pro",
      "meta/llama-3.3-70b-instruct",
      "nv-mistralai/mistral-nemo-12b-instruct",
      "google/gemma-4-31b-it",
      "mistralai/codestral-22b-instruct-v0.1",
      "ibm/granite-34b-code-instruct"
    ];
    recommended.forEach(id => selectedModelIds.add(id));
    updateSelectionCounters();
    renderResultsTable();
  });

  // Run Real-Time Probe
  btnRunProbe.addEventListener('click', async () => {
    const apiKey = apiKeyInput.value.trim();
    if (!apiKey) {
      alert("Please enter your NVIDIA API Key (starts with nvapi-) to test live model queue responsiveness.");
      apiKeyInput.focus();
      return;
    }

    if (selectedModelIds.size === 0) {
      alert("Please select at least one model to benchmark.");
      return;
    }

    const prompt = promptInput.value.trim() || "Write a Python function to check prime numbers.";
    const targetArray = Array.from(selectedModelIds);

    // UI Loading State
    btnRunProbe.disabled = true;
    btnRunProbe.innerHTML = `<span class="icon">⏳</span> Benchmarking ${targetArray.length} Models...`;
    progressContainer.classList.remove('hidden');
    progressBar.style.width = '10%';
    progressPercent.textContent = '10%';
    progressText.textContent = `Pinging ${targetArray.length} NVIDIA endpoints concurrently...`;

    try {
      const response = await fetch('/api/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          apiKey: apiKey,
          models: targetArray,
          prompt: prompt
        })
      });

      progressBar.style.width = '80%';
      progressPercent.textContent = '80%';

      const data = await response.json();
      probeResults = data.results || [];
      lastUpdatedTime.textContent = `Last tested: ${data.timestamp || new Date().toLocaleTimeString()}`;

      progressBar.style.width = '100%';
      progressPercent.textContent = '100%';
      progressText.textContent = 'Benchmark completed!';

      setTimeout(() => {
        progressContainer.classList.add('hidden');
      }, 1500);

      renderResultsTable();
    } catch (err) {
      console.error('Probe failed:', err);
      alert('Error running latency probe. Check network connection or API Key.');
      progressContainer.classList.add('hidden');
    } finally {
      btnRunProbe.disabled = false;
      btnRunProbe.innerHTML = `<span class="icon">🚀</span> Run Real-Time Responsiveness Test`;
    }
  });

  // Search & Sort Event Listeners
  tableSearchInput.addEventListener('input', renderResultsTable);
  sortSelect.addEventListener('change', renderResultsTable);

  // Render Table
  function renderResultsTable() {
    const searchQuery = tableSearchInput.value.trim().toLowerCase();
    const sortBy = sortSelect.value;

    let itemsToRender = [];

    if (probeResults.length > 0) {
      itemsToRender = probeResults.map(res => {
        const catalogItem = allModels.find(m => m.id === res.model) || {};
        return {
          id: res.model,
          name: catalogItem.name || res.model.split('/').pop(),
          ttft: res.ttft,
          tps: res.tps,
          totalTime: res.totalTime,
          status: res.status,
          statusCode: res.statusCode,
          snippet: res.responseSnippet
        };
      });
    } else {
      itemsToRender = getFilteredModels().map(m => ({
        id: m.id,
        name: m.name,
        ttft: 0,
        tps: 0,
        totalTime: 0,
        status: 'Not Benchmark Yet',
        statusCode: 0,
        snippet: ''
      }));
    }

    // Search Filtering
    if (searchQuery) {
      itemsToRender = itemsToRender.filter(item => 
        item.name.toLowerCase().includes(searchQuery) || item.id.toLowerCase().includes(searchQuery)
      );
    }

    // Sorting Logic
    itemsToRender.sort((a, b) => {
      if (sortBy === 'ttft') return a.ttft - b.ttft;
      if (sortBy === 'tps') return b.tps - a.tps;
      if (sortBy === 'totalTime') return a.totalTime - b.totalTime;
      if (sortBy === 'name') return a.name.localeCompare(b.name);
      return 0;
    });

    if (itemsToRender.length === 0) {
      resultsTableBody.innerHTML = `
        <tr class="empty-state">
          <td colspan="6">
            <div class="placeholder-box">
              <h3>No matching models found</h3>
              <p>Try clearing your search query or filter chips.</p>
            </div>
          </td>
        </tr>`;
      return;
    }

    let html = '';
    itemsToRender.forEach((item, index) => {
      const isChecked = selectedModelIds.has(item.id);
      
      // Badges & Rank
      let statusBadge = `<span class="badge badge-info">${item.status}</span>`;
      if (item.statusCode === 200) {
        statusBadge = `<span class="badge badge-success">200 OK</span>`;
      } else if (item.statusCode === 429) {
        statusBadge = `<span class="badge badge-warning">429 Rate Limited</span>`;
      } else if (item.statusCode === 401) {
        statusBadge = `<span class="badge badge-danger">401 Auth Error</span>`;
      }

      let sweetBadge = `<span class="badge badge-info">Tier ${index + 1}</span>`;
      if (item.id.includes("qwen2.5-coder-32b")) {
        sweetBadge = `<span class="badge badge-sweet">★ Sweet Spot</span>`;
      } else if (item.ttft > 0 && item.ttft < 1500) {
        sweetBadge = `<span class="badge badge-success">Blazing Fast</span>`;
      } else if (item.ttft >= 1500 && item.ttft < 5000) {
        sweetBadge = `<span class="badge badge-warning">Moderate</span>`;
      } else if (item.ttft >= 5000) {
        sweetBadge = `<span class="badge badge-danger">High Queue Time</span>`;
      }

      const ttftDisplay = item.ttft > 0 ? (item.ttft < 99999 ? `${item.ttft} ms` : 'Timed Out') : '--';
      const tpsDisplay = item.tps > 0 ? `${item.tps} t/s` : '--';

      html += `
        <tr>
          <td>
            <input type="checkbox" data-id="${item.id}" ${isChecked ? 'checked' : ''} class="model-checkbox">
          </td>
          <td>
            <div class="model-cell">
              <span class="model-name">${item.name}</span>
              <span class="model-id">${item.id}</span>
            </div>
          </td>
          <td>
            <span class="latency-value">${ttftDisplay}</span>
            ${item.ttft > 0 && item.ttft < 99999 ? `<div class="latency-bar" style="width: ${Math.min(100, Math.max(10, 100000 / item.ttft))}%;"></div>` : ''}
          </td>
          <td>
            <span class="latency-value">${tpsDisplay}</span>
          </td>
          <td>${statusBadge}</td>
          <td>${sweetBadge}</td>
        </tr>
      `;
    });

    resultsTableBody.innerHTML = html;

    // Attach Checkbox Change Listeners
    document.querySelectorAll('.model-checkbox').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const id = e.target.dataset.id;
        if (e.target.checked) {
          selectedModelIds.add(id);
        } else {
          selectedModelIds.delete(id);
        }
        updateSelectionCounters();
      });
    });
  }

  // Generate OpenCode Config Modal
  btnOpenCodeConfig.addEventListener('click', async () => {
    const selected = Array.from(selectedModelIds);
    if (selected.length === 0) {
      alert("Please select at least one model to include in your OpenCode configuration.");
      return;
    }

    configModal.classList.remove('hidden');
    configJsonCode.textContent = "// Generating configuration...";

    try {
      const response = await fetch('/api/opencode-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models: selected })
      });
      const data = await response.json();
      configJsonCode.textContent = JSON.stringify(data.config, null, 2);
    } catch (err) {
      configJsonCode.textContent = "// Error generating configuration";
    }
  });

  // Modal Dismiss
  btnCloseModal.addEventListener('click', () => configModal.classList.add('hidden'));
  btnCloseModalBtn.addEventListener('click', () => configModal.classList.add('hidden'));
  configModal.addEventListener('click', (e) => {
    if (e.target === configModal) configModal.classList.add('hidden');
  });

  // Copy Config to Clipboard
  btnCopyConfig.addEventListener('click', () => {
    navigator.clipboard.writeText(configJsonCode.textContent);
    btnCopyConfig.textContent = "✅ Copied!";
    setTimeout(() => {
      btnCopyConfig.textContent = "📋 Copy JSON";
    }, 2000);
  });

  btnFetchModels.addEventListener('click', loadModelsCatalog);

  // Initial Load
  loadModelsCatalog();
  renderResultsTable();
});
