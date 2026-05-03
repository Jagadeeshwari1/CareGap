    // ─── State ───
    let currentPatientId = null;
    let appState = {
      dashboardBasic: null,
      dashboardFull: null,
      triageQueue: null,
      resourceForecast: null,
      lastSync: null
    };
    // ─── Infinite-scroll directory state ───
    const PAGE_SIZE = 50;
    let dirState = { query: '', cohort: '', offset: 0, total: 0, loading: false, exhausted: false };
    let searchDebounceTimer = null;
    let scrollObserver = null;
    let explorerTierData = {};
    let explorerSelectedTier = 'EMERGENCY';
    const tierNarratives = {
      EMERGENCY: {
        summary: 'Critical lab or blood-pressure signals.',
        description: 'HbA1c ≥ 9% or systolic BP ≥ 160 mmHg, which often requires same-day provider outreach or urgent care routing.'
      },
      HIGH: {
        summary: 'Trending toward escalation.',
        description: 'HbA1c 8–8.9% or systolic BP between 140 and 159 mmHg; patients may bleed into emergency risk without a targeted intervention.'
      },
      MODERATE: {
        summary: 'Chronic overlap—monitor closely.',
        description: 'Active diabetes and hypertension diagnoses with controlled labs; reinforce care coordination while watching for drift.'
      },
      PREVENTIVE: {
        summary: 'Age-based watch list.',
        description: 'Patients ≥ 65 years old who are otherwise stable but benefit from proactive outreach and lifestyle reinforcement.'
      },
      NORMAL: {
        summary: 'Routine maintenance cohort.',
        description: 'No high-risk labs or conditions detected; keep these patients on the standard care schedule.'
      }
    };
    // ─── DOM Ready ───
    document.addEventListener('DOMContentLoaded', () => {
      const searchNav = document.querySelector('.nav-item[onclick*="search"]');
      showSection('search', searchNav);
      loadAllPatients();
      checkAIStatus();

      // AI Explorer Input Listener
      const aiInput = document.getElementById('ai-explorer-input');
      if (aiInput) {
        aiInput.addEventListener('keypress', (e) => {
          if (e.key === 'Enter') askExplorerAI();
        });
      }
    });

    // ─── AI Explorer Assistant Logic ───
    async function askExplorerAI() {
      const input = document.getElementById('ai-explorer-input');
      const chat = document.getElementById('ai-explorer-chat');
      if (!input || !chat) return;
      const question = input.value.trim();
      if (!question) return;

      // Add user message
      chat.innerHTML += `<div style="margin-bottom:10px;text-align:right">
        <span style="background:var(--accent);color:#ffffff;padding:6px 12px;border-radius:12px 12px 0 12px;display:inline-block;box-shadow:0 2px 4px rgba(0,0,0,0.05)">${question}</span>
      </div>`;
      input.value = '';
      chat.scrollTop = chat.scrollHeight;

      // Thinking state
      const thinkingId = 'think-' + Date.now();
      chat.innerHTML += `<div id="${thinkingId}" style="margin-bottom:10px">
        <span style="background:var(--border);padding:8px 12px;border-radius:12px 12px 12px 0;display:inline-block"><div class="spin" style="width:12px;height:12px;border-width:2px"></div></span>
      </div>`;
      chat.scrollTop = chat.scrollHeight;

      try {
        const r = await fetch('/api/rag/ask-analytics/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question })
        });
        const d = await r.json();
        const thinker = document.getElementById(thinkingId);
        if (thinker) thinker.remove();

        const answer = d.answer || 'Sorry, I couldn\'t process that.';
        chat.innerHTML += `<div style="margin-bottom:12px">
            <span style="background:var(--border);padding:10px 14px;border-radius:12px 12px 12px 0;display:inline-block">${answer}</span>
          </div>`;
        chat.scrollTop = chat.scrollHeight;
      } catch (err) {
        const thinker = document.getElementById(thinkingId);
        if (thinker) thinker.remove();
        chat.innerHTML += `<div style="margin-bottom:10px;color:var(--red);font-size:.7rem">Connectivity error. Re-enabling local simulation...</div>`;
      }
    }

    function quickInsight(text) {
      const input = document.getElementById('ai-explorer-input');
      if (input) {
        input.value = text;
        askExplorerAI();
      }
    }

    // ─── Navigation ───
    function showSection(id, el) {
      document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      document.getElementById('sec-' + id).classList.add('active');
      if (el) el.classList.add('active');

      if (id === 'dashboard') loadDashboardStats();
      if (id === 'triage') loadTriage();
    }

    function showTab(id, el) {
      document.querySelectorAll('.tab-content').forEach(t => t.style.display = 'none');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.getElementById('tab-' + id).style.display = 'block';
      el.classList.add('active');
      if (id === 'urgentcare' && currentPatientId) loadUrgentCare();
      if (id === 'history' && currentPatientId) loadHistory();
      if (id === 'trends' && currentPatientId) loadTrends();
    }

    // ─── Patient Directory (infinite scroll) ───
    function attachScrollObserver() {
      if (scrollObserver) scrollObserver.disconnect();
      const sentinel = document.getElementById('dir-sentinel');
      if (!sentinel) return;
      scrollObserver = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting && !dirState.loading && !dirState.exhausted)
          fetchNextPage();
      }, { root: document.getElementById('search-results'), threshold: 0.1 });
      scrollObserver.observe(sentinel);
    }

    function setCohortFilter(btn, cohort) {
      document.querySelectorAll('.cohort-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      dirState = { query: dirState.query, cohort, offset: 0, total: 0, loading: false, exhausted: false };
      document.getElementById('search-results').innerHTML = '';
      document.getElementById('search-count').innerHTML = '';
      fetchNextPage();
    }

    function loadAllPatients() {
      dirState = { query: '', cohort: '', offset: 0, total: 0, loading: false, exhausted: false };
      document.getElementById('search-results').innerHTML =
        `<div style="padding:28px;text-align:center;color:var(--text3);font-size:.85rem;">
           <div class="spin" style="margin:0 auto 12px;"></div>Loading patients…
         </div>`;
      document.getElementById('search-count').innerHTML = '';
      fetchNextPage();
    }

    function filterPatients(val) {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => {
        dirState = { query: val.trim(), cohort: dirState.cohort, offset: 0, total: 0, loading: false, exhausted: false };
        document.getElementById('search-results').innerHTML = '';
        document.getElementById('search-count').innerHTML = '';
        fetchNextPage();
      }, 280);
    }

    async function fetchNextPage() {
      if (dirState.loading || dirState.exhausted) return;
      dirState.loading = true;
      const box = document.getElementById('search-results');
      const countEl = document.getElementById('search-count');
      try {
        const cohortParam = dirState.cohort ? `&cohort=${encodeURIComponent(dirState.cohort)}` : '';
        const url = `/api/patients/search/?q=${encodeURIComponent(dirState.query)}${cohortParam}&limit=${PAGE_SIZE}&offset=${dirState.offset}`;
        console.log('[CareGap] fetch:', url);
        const res = await fetch(url);
        const data = await res.json();
        console.log('[CareGap] response — total:', data.total, 'returned:', (data.results || []).length);
        const rows = data.results || [];
        const total = data.total || 0;
        dirState.total = total;
        // First page: clear spinner
        if (dirState.offset === 0) {
          box.innerHTML = '';
          if (rows.length === 0) {
            box.innerHTML = `<div style="padding:36px;text-align:center;color:var(--text3);font-size:.85rem;">No patients found.</div>`;
            countEl.innerHTML = `<strong>0</strong> patients match your search`;
            dirState.exhausted = true;
            dirState.loading = false;
            return;
          }
        }
        // Remove sentinel before appending
        const sentinel = document.getElementById('dir-sentinel');
        if (sentinel) sentinel.remove();
        // Append rows
        rows.forEach(p => {
          const initials = `${(p.first || '?')[0]}${(p.last || '?')[0]}`.toUpperCase();
          const div = document.createElement('div');
          div.className = 'search-result-item';
          div.onclick = () => loadPatient(p.patient_id);
          div.innerHTML =
            `<div class="sri-avatar" style="color:${genderColor(p.gender)}">${initials}</div>` +
            `<div style="flex:1;min-width:0;">` +
            `<div class="sri-name">${p.first || ''} ${p.last || ''}</div>` +
            `<div class="sri-meta">Age ${p.age || '—'} · ${p.city || '—'}</div>` +
            `</div>` +
            `<span class="sri-insurance">${p.insurance || 'Uninsured'}</span>`;
          box.appendChild(div);
        });
        dirState.offset += rows.length;
        const shown = dirState.offset;
        countEl.innerHTML = shown >= total
          ? `Showing <strong>${total.toLocaleString()}</strong> patients`
          : `Showing <strong>${shown.toLocaleString()}</strong> of <strong>${total.toLocaleString()}</strong> patients — scroll for more`;
        if (rows.length < PAGE_SIZE || dirState.offset >= total) {
          dirState.exhausted = true;
        } else {
          const s = document.createElement('div');
          s.id = 'dir-sentinel';
          s.style.height = '4px';
          box.appendChild(s);
          attachScrollObserver();
        }
      } catch (e) {
        console.error('[CareGap] fetchNextPage error:', e);
        if (dirState.offset === 0)
          box.innerHTML = `<div style="padding:24px;text-align:center;color:var(--red);font-size:.85rem;">Failed to load patients — check console.</div>`;
        else
          box.insertAdjacentHTML('beforeend', `<div style="padding:16px;text-align:center;color:var(--red);font-size:.82rem;">Failed to load more.</div>`);
      }
      dirState.loading = false;
    }

    function genderColor(g) {
      return g === 'F' ? '#60a5fa' : '#2dd4bf';
    }

    // ─── Load Patient ───
    async function loadPatient(pid) {
      currentPatientId = pid;

      // Show profile section
      document.getElementById('patient-search-input').value = '';

      // Show nav item
      document.getElementById('nav-profile').style.display = 'flex';
      showSection('profile', document.getElementById('nav-profile'));

      // Reset tabs
      showTab('risk', document.querySelector('.tab'));

      // Load patient data + risk in parallel
      document.getElementById('patient-card-wrap').innerHTML = `<div class="empty"><div class="spin"></div></div>`;
      document.getElementById('risk-content').innerHTML = `<div class="empty" style="padding:30px"><div class="spin"></div></div>`;

      const [profileRes, riskRes] = await Promise.all([
        fetch(`/api/patients/${pid}/`),
        fetch(`/api/patients/${pid}/risk/`)
      ]);

      const profile = await profileRes.json();
      const risk = await riskRes.json();

      document.getElementById('nav-profile-name').textContent = `${profile.first} ${profile.last}`;

      renderPatientCard(profile);
      renderRiskCard(risk, profile);
    }

    function renderPatientCard(p) {
      document.getElementById('patient-card-wrap').innerHTML = `
    <div class="patient-card" style="margin-bottom:20px">
      <div class="pc-header">
        <div style="display:flex;align-items:center;gap:16px">
          <div class="pc-avatar" style="color:${genderColor(p.gender)}">${p.first[0]}${p.last[0]}</div>
          <div>
            <div class="pc-name">${p.first} ${p.last}</div>
            <div class="pc-meta">${p.age || '—'} years · ${p.gender === 'F' ? 'Female' : 'Male'} · ${capitalize(p.race)}</div>
            <div class="pc-id">${p.patient_id}</div>
          </div>
        </div>
        <button class="btn btn-outline" onclick="showSection('search',null);document.querySelectorAll('.nav-item')[0].classList.add('active')">← Back to Search</button>
      </div>
      <div class="pc-details">
        <div class="pc-detail"><div class="pd-label">City</div><div class="pd-value">${p.city || '—'}</div></div>
        <div class="pc-detail"><div class="pd-label">State</div><div class="pd-value">${p.state || 'CA'}</div></div>
        <div class="pc-detail"><div class="pd-label">Insurance</div><div class="pd-value">${p.insurance || 'Unknown'}</div></div>
        <div class="pc-detail"><div class="pd-label">Ethnicity</div><div class="pd-value">${capitalize(p.ethnicity) || '—'}</div></div>
      </div>
    </div>`;
    }

    function renderRiskCard(risk, profile) {
      const tierColors = { HIGH: 'var(--red)', MODERATE: 'var(--amber)', PREVENTIVE: 'var(--blue)', NORMAL: 'var(--green)' };
      const color = tierColors[risk.tier] || 'var(--text)';

      // Follow-up box
      let followupHTML = '';
      if (risk.tier === 'MODERATE') {
        followupHTML = `
      <div class="followup-box">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;margin-top:2px"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        <div class="followup-body">
          <strong>Schedule a Follow-Up Visit</strong><br>
          Recommend scheduling within <strong>${risk.followup_urgency_days} days</strong> based on current risk indicators.
          Review HbA1c trends and medication adherence at the visit.
        </div>
      </div>`;
      }

      // Vitals
      const vitals = `
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0">
      <div class="vital">
        <div class="label">HbA1c</div>
        <div class="value">${risk.hba1c_value != null ? risk.hba1c_value + '<span class="unit"> %</span>' : '<span style="font-size:1.1rem;color:var(--text3)">Not Tested</span>'}</div>
      </div>
      <div class="vital">
        <div class="label">Days Since HbA1c</div>
        <div class="value">${risk.hba1c_days_gap != null ? risk.hba1c_days_gap.toLocaleString() + '<span class="unit"> days</span>' : '<span style="font-size:1.1rem;color:var(--text3)">Not Tested</span>'}</div>
      </div>
      <div class="vital">
        <div class="label">Systolic BP</div>
        <div class="value">${risk.latest_sbp != null ? risk.latest_sbp + '<span class="unit"> mmHg</span>' : '<span style="font-size:1.1rem;color:var(--text3)">Not Tested</span>'}</div>
      </div>
    </div>`;

      const reasons = risk.reasons.length
        ? `<ul class="risk-reasons">${risk.reasons.map(r => `<li>${r}</li>`).join('')}</ul>`
        : '<p style="color:var(--text3);font-size:.82rem">No significant risk factors detected.</p>';

      const urgentCareBtn = risk.tier === 'HIGH'
        ? `<button class="btn btn-danger" onclick="showTab('urgentcare',document.querySelectorAll('.tab')[1])">🏥 Find Urgent Care</button>`
        : '';

      document.getElementById('risk-content').innerHTML = `
    <div class="risk-card ${risk.tier}" style="margin-top:4px">
      <div class="risk-badge">
        <div class="rb-tier">${risk.tier}</div>
        <div class="rb-score" style="color:${color}">${risk.score}</div>
        <div class="rb-label">Risk Score</div>
      </div>
      <div class="risk-body">
        <div class="risk-action">${risk.recommended_action}</div>
        ${reasons}
        ${vitals}
        ${followupHTML}
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px">
          ${urgentCareBtn}
          <button class="btn btn-outline" onclick="runPredictionForPatient('${risk.patient_id}')">
            &#x1F4CA; Run 6-Month Prediction
          </button>
        </div>
      </div>
    </div>
    <div style="display:flex;gap:10px;margin-top:4px">
      <div style="flex:1;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;font-size:.82rem;color:var(--text2)">
        <strong style="color:var(--text)">Has Diabetes:</strong> ${risk.has_diabetes ? '<span style="color:var(--red)">Yes</span>' : '<span style="color:var(--green)">No</span>'}
      </div>
      <div style="flex:1;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;font-size:.82rem;color:var(--text2)">
        <strong style="color:var(--text)">Has Hypertension:</strong> ${risk.has_hypertension ? '<span style="color:var(--red)">Yes</span>' : '<span style="color:var(--green)">No</span>'}
      </div>
      <div style="flex:1;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;font-size:.82rem;color:var(--text2)">
        <strong style="color:var(--text)">Follow-Up In:</strong> <span style="color:var(--accent)">${risk.followup_urgency_days} days</span>
      </div>
    </div>
    
    <div style="margin-top:16px;">
        <div style="font-size:0.75rem; color:var(--text3); margin-bottom:8px; text-transform:uppercase; letter-spacing:0.05em; font-weight:600;">🤖 Quick Questions For AI Coordinator</div>
        <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <button class="btn" style="background:var(--bg3); border:1px solid var(--border2); color:var(--text2); font-size:0.8rem; padding:6px 12px; border-radius:16px; cursor:pointer;" onclick="sendGlobalChat('What are the common side effects of the active medications for this profile?')">Side Effects?</button>
            <button class="btn" style="background:var(--bg3); border:1px solid var(--border2); color:var(--text2); font-size:0.8rem; padding:6px 12px; border-radius:16px; cursor:pointer;" onclick="sendGlobalChat('What are the standard clinical guidelines for a patient in this exact risk tier?')">Clinical Guidelines?</button>
            <button class="btn" style="background:var(--bg3); border:1px solid var(--border2); color:var(--text2); font-size:0.8rem; padding:6px 12px; border-radius:16px; cursor:pointer;" onclick="sendGlobalChat('Provide 3 immediate lifestyle recommendations for this patient.')">Lifestyle Recommendations?</button>
        </div>
    </div>`;
    }

    // ─── Urgent Care ───
    async function loadUrgentCare() {
      if (!currentPatientId) return;
      document.getElementById('uc-content').innerHTML = `<div class="empty" style="padding:30px"><div class="spin"></div></div>`;
      try {
        const res = await fetch(`/api/patients/${currentPatientId}/urgent-care/`);
        const data = await res.json();
        renderUrgentCares(data);
      } catch (e) {
        document.getElementById('uc-content').innerHTML = `<div class="empty" style="padding:24px"><p>Error loading urgent care data. Is the server running?</p></div>`;
      }
    }

    function renderUrgentCares(data) {
      const box = document.getElementById('uc-content');
      if (!data.facilities || !data.facilities.length) {
        box.innerHTML = `<div class="empty" style="padding:24px"><p>No urgent care facilities found for ${data.patient_city}.</p></div>`;
        return;
      }
      box.innerHTML = `
    <div style="padding:12px 22px 0;font-size:.8rem;color:var(--text3)">
      Patient: <strong style="color:var(--text)">${data.patient_name}</strong> ·
      City: <strong style="color:var(--text)">${data.patient_city}</strong> ·
      Insurance: <strong style="color:var(--accent)">${data.patient_insurance || 'Unknown'}</strong>
    </div>
    <div class="uc-list">
      ${data.facilities.map((f, i) => `
        <div class="uc-item">
          <div class="uc-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
          </div>
          <div style="flex:1">
            <div class="uc-name">#${i + 1} ${f.name}</div>
            <div class="uc-addr">${f.address}</div>
            <div class="uc-tags">
              ${f.accepts.medicaid ? '<span class="tag tag-green">Medicaid \u2713</span>' : ''}
              ${f.accepts.medicare ? '<span class="tag tag-blue">Medicare \u2713</span>' : ''}
              ${f.accepts.private ? '<span class="tag tag-blue">Private \u2713</span>' : ''}
              ${f.accepts.uninsured ? '<span class="tag tag-amber">Uninsured \u2713</span>' : ''}
              ${f.open_24h ? '<span class="tag tag-green">Open 24h</span>' : ''}
            </div>
          </div>
          <div class="uc-right">
            <div class="uc-rating">\u2605 ${f.rating}</div>
            <div class="uc-dist">${f.distance_km != null ? f.distance_km + ' km away' : 'Distance N/A'}</div>
            <div class="uc-phone">${f.phone || ''}</div>
          </div>
        </div>`).join('')}
    </div>`;
    }

    // History Tab
    async function loadHistory() {
      if (!currentPatientId) return;
      const res = await fetch(`/api/patients/${currentPatientId}/`);
      const profile = await res.json();
      const obs = (profile.observations || []).slice(0, 15);
      const obsEl = document.getElementById('obs-content');
      if (obsEl) obsEl.innerHTML = obs.length ? '<table style="width:100%;border-collapse:collapse;font-size:.8rem"><thead><tr><th style="padding:10px 14px;text-align:left;color:var(--text3);font-size:.68rem;text-transform:uppercase;border-bottom:1px solid var(--border)">Date</th><th style="padding:10px 14px;text-align:left;color:var(--text3);font-size:.68rem;text-transform:uppercase;border-bottom:1px solid var(--border)">Description</th><th style="padding:10px 14px;text-align:right;color:var(--text3);font-size:.68rem;text-transform:uppercase;border-bottom:1px solid var(--border)">Value</th></tr></thead><tbody>' + obs.map(o => `<tr style="border-bottom:1px solid var(--border)"><td style="padding:10px 14px;color:var(--text3);font-family:'JetBrains Mono',monospace;font-size:.75rem">${(o.date || '').slice(0, 10)}</td><td style="padding:10px 14px;color:var(--text2)">${o.description}</td><td style="padding:10px 14px;text-align:right;color:var(--text);font-family:'JetBrains Mono',monospace">${o.value} ${o.units}</td></tr>`).join('') + '</tbody></table>' : '<div class="empty" style="padding:24px"><p>No observations recorded.</p></div>';
      const conds = profile.conditions || [];
      const condEl = document.getElementById('cond-content');
      if (condEl) condEl.innerHTML = conds.length ? '<div style="padding:14px 18px;display:flex;flex-direction:column;gap:9px">' + conds.map(c => `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 14px"><div style="font-size:.85rem;font-weight:500;color:var(--text)">${c.description}</div><div style="font-size:.74rem;color:var(--text3);margin-top:3px">Code: <span style="font-family:'JetBrains Mono',monospace">${c.code}</span> · Since: ${c.start || 'Unknown'} · ${c.stop ? 'Resolved' : '<span style=\"color:var(--red)\">Active</span>'}</div></div>`).join('') + '</div>' : '<div class="empty" style="padding:24px"><p>No conditions recorded.</p></div>';
      const encs = (profile.encounters || []).slice(0, 10);
      const encEl = document.getElementById('enc-content');
      if (encEl) encEl.innerHTML = encs.length ? '<table style="width:100%;border-collapse:collapse;font-size:.8rem"><thead><tr><th style="padding:10px 16px;text-align:left;color:var(--text3);font-size:.68rem;text-transform:uppercase;border-bottom:1px solid var(--border)">Date</th><th style="padding:10px 16px;text-align:left;color:var(--text3);font-size:.68rem;text-transform:uppercase;border-bottom:1px solid var(--border)">Type</th><th style="padding:10px 16px;text-align:left;color:var(--text3);font-size:.68rem;text-transform:uppercase;border-bottom:1px solid var(--border)">Description</th></tr></thead><tbody>' + encs.map(e => `<tr style="border-bottom:1px solid var(--border)"><td style="padding:10px 16px;color:var(--text3);font-family:'JetBrains Mono',monospace;font-size:.75rem">${(e.start || '').slice(0, 10)}</td><td style="padding:10px 16px;"><span class="tag tag-gray">${e.encounter_class}</span></td><td style="padding:10px 16px;color:var(--text2)">${e.description}</td></tr>`).join('') + '</tbody></table>' : '<div class="empty" style="padding:24px"><p>No encounters recorded.</p></div>';
    }

    // Dashboard Stats
    let _charts = [];
    function _statCardsHtml(d) {
      const cc = d.cohort_counts || {};
      return `<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:24px">
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;border-top:2px solid var(--accent)"><div style="font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);font-weight:600;margin-bottom:8px">Chronic Patients</div><div style="font-family:'DM Serif Display',serif;font-size:2rem">${(cc.chronic || 0).toLocaleString()}</div><div style="font-size:.72rem;color:var(--text3);margin-top:4px">HTN or T2D</div></div>
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;border-top:2px solid var(--amber)"><div style="font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);font-weight:600;margin-bottom:8px">At Risk</div><div style="font-family:'DM Serif Display',serif;font-size:2rem">${(cc.at_risk || 0).toLocaleString()}</div><div style="font-size:.72rem;color:var(--text3);margin-top:4px">Adult, no chronic dx</div></div>
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;border-top:2px solid var(--blue)"><div style="font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);font-weight:600;margin-bottom:8px">Pediatric</div><div style="font-family:'DM Serif Display',serif;font-size:2rem">${(cc.pediatric || 0).toLocaleString()}</div><div style="font-size:.72rem;color:var(--text3);margin-top:4px">Age &lt; 18</div></div>
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;border-top:2px solid var(--text3)"><div style="font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);font-weight:600;margin-bottom:8px">Deceased</div><div style="font-family:'DM Serif Display',serif;font-size:2rem">${(cc.deceased || 0).toLocaleString()}</div><div style="font-size:.72rem;color:var(--text3);margin-top:4px">With DEATHDATE</div></div>
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;border-top:2px solid var(--purple)"><div style="font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);font-weight:600;margin-bottom:8px">HTN Rate</div><div style="font-family:'DM Serif Display',serif;font-size:2rem">${d.hypertension_rate || 0}%</div><div style="font-size:.72rem;color:var(--text3);margin-top:4px">Diabetes ${d.diabetes_rate || 0}%</div></div>
      </div>`;
    }
    function _renderDashboardLayout(data, chartsHtml) {
      const el = document.getElementById('dashboard-stats-content');
      if (!el) return;
      el.innerHTML = (data ? _statCardsHtml(data) : '') + '<div class="panel" style="border-top:2px solid var(--blue);margin-bottom:18px"><div class="panel-h"><div><h3>Resource Forecast</h3><p>30-day demand projections.</p></div></div><div id="resource-forecast-content" style="padding:16px"><div class="empty"><div class="spin"></div><p>Loading...</p></div></div></div>' + (chartsHtml || '');
      loadResourceForecast();
    }
    function _renderResourceForecastContent(data, error) {
      const container = document.getElementById('resource-forecast-content');
      if (!container) return;
      if (error || !data || !data.resources) { container.innerHTML = `<div class="empty"><p style="color:var(--red)">${error ? 'Forecast error: ' + (error.message || 'Unknown') : 'No forecast data.'}</p></div>`; return; }
      const { resources, high_risk_volume, period_days, generated_at } = data;
      container.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:18px"><div style="flex:1;min-width:140px"><div style="font-size:.65rem;text-transform:uppercase;color:var(--text3)">High-risk volume</div><div style="font-size:1.8rem;font-weight:600;margin-top:4px">${(high_risk_volume || 0).toLocaleString()}</div><div style="font-size:.7rem;color:var(--text3);margin-top:4px">Window: ${period_days || 30} days</div></div><div style="flex:2;display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px">${Object.entries(resources).map(([k, v]) => `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px"><div style="font-size:.6rem;text-transform:uppercase;color:var(--text3)">${v.label || k}</div><div style="font-size:1.4rem;font-weight:600;margin-top:4px">${(v.count || 0).toLocaleString()}</div><div style="font-size:.68rem;color:var(--text3);margin-top:6px">${v.description || ''}</div></div>`).join('')}</div></div>`;
    }
    async function loadResourceForecast(force = false) {
      if (appState.resourceForecast && !force) { _renderResourceForecastContent(appState.resourceForecast); return; }
      try {
        const ctrl = new AbortController();
        const tid = setTimeout(() => ctrl.abort(), 20000);
        const res = await fetch('/api/patients/resources/forecast/', { signal: ctrl.signal });
        clearTimeout(tid);
        const data = await res.json();
        appState.resourceForecast = data;
        _renderResourceForecastContent(data);
      } catch (e) {
        _renderResourceForecastContent(null, { message: e?.name === 'AbortError' ? 'Timed out.' : e?.message });
      }
    }
    async function loadDashboardStats() {
      if (_charts.length) { _charts.forEach(c => c.destroy()); _charts = []; }
      if (!appState.dashboardBasic) {
        _renderDashboardLayout(null, '<div class="empty"><div class="spin"></div></div>');
        try {
          appState.dashboardBasic = await (await fetch('/api/patients/stats/basic/')).json();
        } catch (e) {
          _renderDashboardLayout(null, `<div style="padding:32px;text-align:center;color:var(--red)">Error: ${e.message}</div>`);
          return;
        }
      }
      _renderDashboardLayout(appState.dashboardBasic, '<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px"><div class="panel"><div class="panel-h"><div><h3>HbA1c Distribution</h3></div></div><div style="height:250px;display:flex;align-items:center;justify-content:center"><div class="spin"></div></div></div><div class="panel"><div class="panel-h"><div><h3>Systolic BP</h3></div></div><div style="height:250px;display:flex;align-items:center;justify-content:center"><div class="spin"></div></div></div></div>');
      if (appState.dashboardFull) { _renderCharts(appState.dashboardFull); return; }
      try {
        const ctrl = new AbortController();
        const tid = setTimeout(() => ctrl.abort(), 25000);
        const data = await (await fetch('/api/patients/stats/', { signal: ctrl.signal })).json();
        clearTimeout(tid);
        appState.dashboardFull = data; appState.lastSync = new Date();
        _renderDashboardLayout(data, '<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px"><div class="panel"><div class="panel-h"><div><h3>HbA1c Distribution</h3></div></div><div style="padding:18px;height:250px"><canvas id="chart-hba1c"></canvas></div></div><div class="panel"><div class="panel-h"><div><h3>Systolic BP</h3></div></div><div style="padding:18px;height:250px"><canvas id="chart-bp"></canvas></div></div></div>');
        _renderCharts(data);
      } catch (e) {
        _renderDashboardLayout(appState.dashboardBasic, `<div style="padding:32px;text-align:center">${e.name === 'AbortError' ? 'Timeout loading analytics.' : 'Error: ' + e.message}</div>`);
      }
    }
    function _renderCharts(data) {
      const a1c = document.getElementById('chart-hba1c');
      if (a1c && data.hba1c_dist) _charts.push(new Chart(a1c.getContext('2d'), { type: 'bar', data: { labels: ['Normal (<5.7)', 'Prediabetes', 'Diabetes (>=6.5)'], datasets: [{ data: [data.hba1c_dist.normal, data.hba1c_dist.prediabetes, data.hba1c_dist.diabetes], backgroundColor: ['#22c55e', '#f59e0b', '#ef4444'], borderRadius: 6 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } } }));
      const bp = document.getElementById('chart-bp');
      if (bp && data.bp_dist) _charts.push(new Chart(bp.getContext('2d'), { type: 'bar', data: { labels: ['Normal', 'Elevated', 'Stage 1', 'Stage 2'], datasets: [{ data: [data.bp_dist.normal, data.bp_dist.elevated, data.bp_dist.stage1, data.bp_dist.stage2], backgroundColor: ['#22c55e', '#f59e0b', '#ef4444', '#dc2626'], borderRadius: 6 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } } }));
    }
    function syncAllData() {
      appState.dashboardBasic = null; appState.dashboardFull = null; appState.triageQueue = null; appState.resourceForecast = null; appState.lastSync = null;
      const sec = document.querySelector('.section.active');
      if (sec) { const id = sec.id.replace('sec-', ''); showSection(id, document.querySelector('.nav-item.active')); }
    }

    // Patient Trends
    let _trendCharts = [];
    async function loadTrends() {
      if (!currentPatientId) return;
      const emptyEl = document.getElementById('trends-empty');
      const contentEl = document.getElementById('trends-content');
      try {
        const profile = await (await fetch(`/api/patients/${currentPatientId}/`)).json();
        const obs = profile.observations || [];
        let hba1cData = [], bpSysData = [], bpDiaData = [];
        obs.forEach(o => {
          if (!o.value || !o.date) return;
          const val = parseFloat(String(o.value).replace(/[^0-9.]/g, ''));
          if (isNaN(val)) return;
          const dt = String(o.date).slice(0, 10);
          const desc = (o.description || '').toLowerCase();
          const code = o.code || '';
          if (code === '4548-4' || desc.includes('a1c') || desc.includes('hemoglobin')) hba1cData.push({ x: dt, y: val });
          else if (code === '8480-6' || desc.includes('systolic')) bpSysData.push({ x: dt, y: val });
          else if (code === '8462-4' || desc.includes('diastolic')) bpDiaData.push({ x: dt, y: val });
        });
        hba1cData.sort((a, b) => new Date(a.x) - new Date(b.x));
        bpSysData.sort((a, b) => new Date(a.x) - new Date(b.x));
        bpDiaData.sort((a, b) => new Date(a.x) - new Date(b.x));
        if (!hba1cData.length && !bpSysData.length) {
          if (emptyEl) emptyEl.style.display = 'block'; if (contentEl) contentEl.style.display = 'none'; return;
        }
        if (emptyEl) emptyEl.style.display = 'none'; if (contentEl) contentEl.style.display = 'block';
        if (_trendCharts.length) { _trendCharts.forEach(c => c.destroy()); _trendCharts = []; }
        const wrapHba1c = document.getElementById('wrap-hba1c');
        if (wrapHba1c) {
          if (hba1cData.length) {
            wrapHba1c.innerHTML = '<canvas id="chart-hba1c-trend"></canvas>';
            _trendCharts.push(new Chart(document.getElementById('chart-hba1c-trend').getContext('2d'), { type: 'line', data: { datasets: [{ label: 'HbA1c (%)', data: hba1cData, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', borderWidth: 2, tension: 0.2, fill: true }] }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { type: 'category', labels: hba1cData.map(d => d.x) }, y: {} }, plugins: { legend: { display: false } } } }));
          } else { wrapHba1c.innerHTML = '<div class="empty" style="padding:60px 24px;color:var(--text3)">No HbA1c history available.</div>'; }
        }
        const wrapBp = document.getElementById('wrap-bp');
        if (wrapBp) {
          if (bpSysData.length || bpDiaData.length) {
            wrapBp.innerHTML = '<canvas id="chart-bp-trend"></canvas>';
            const allDates = [...new Set([...bpSysData.map(d => d.x), ...bpDiaData.map(d => d.x)])].sort((a, b) => new Date(a) - new Date(b));
            const getVal = (arr, dt) => { const f = arr.find(d => d.x === dt); return f ? f.y : null; };
            _trendCharts.push(new Chart(document.getElementById('chart-bp-trend').getContext('2d'), {
              type: 'line', data: {
                labels: allDates, datasets: [
                  { label: 'Systolic (mmHg)', data: allDates.map(dt => getVal(bpSysData, dt)), borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.1)', borderWidth: 2, tension: 0.2, spanGaps: true },
                  { label: 'Diastolic (mmHg)', data: allDates.map(dt => getVal(bpDiaData, dt)), borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', borderWidth: 2, tension: 0.2, spanGaps: true }
                ]
              }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'top' } } }
            }));
          } else { wrapBp.innerHTML = '<div class="empty" style="padding:60px 24px;color:var(--text3)">No Blood Pressure history available.</div>'; }
        }
      } catch (e) {
        console.error('Error loading trends:', e);
        if (emptyEl) { emptyEl.innerHTML = '<p>Error loading trend data.</p>'; emptyEl.style.display = 'block'; }
        if (contentEl) contentEl.style.display = 'none';
      }
    }

    function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—'; }
    function escapeHTML(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
    function getCookie(name) {
      const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
      return v ? v.pop() : '';
    }

    // ─── Action Required / Triage Queue ───
    async function loadTriage() {
      const eBox = document.getElementById('triage-emergency-content');
      const uBox = document.getElementById('triage-urgent-content');

      if (appState.triageQueue) {
        _renderTriageData(appState.triageQueue);
        return;
      }

      eBox.innerHTML = `<div class="empty"><div class="spin"></div></div>`;
      uBox.innerHTML = `<div class="empty"><div class="spin"></div></div>`;

      try {
        const res = await fetch('/api/patients/triage/');
        const data = await res.json();
        appState.triageQueue = data;
        _renderTriageData(data);
      } catch (e) {
        eBox.innerHTML = `<div class="empty"><p style="padding:10px 0;color:var(--red);">Failed to load triage queue: ${e.message}</p></div>`;
      }
    }

    function _formatRiskBadge(prob, available) {
      if (prob == null) return `<span class="tag tag-gray">ML pending</span>`;
      const percent = Math.round(prob * 100);
      const color = prob >= 0.75 ? 'var(--red)' : prob >= 0.60 ? 'var(--amber)' : 'var(--accent)';
      const fallback = available ? '' : ' <span style="color:var(--text3);font-size:.65rem">(fallback)</span>';
      return `<span style="font-weight:600;color:${color}">${percent}%</span>${fallback}`;
    }

    function _renderTriageData(data) {
      const eBox = document.getElementById('triage-emergency-content');
      const uBox = document.getElementById('triage-urgent-content');

      // Emergency Queue
      if (!data.emergency_patients || data.emergency_patients.length === 0) {
        eBox.innerHTML = `<div class="empty"><p style="padding:10px 0;">No emergency patients identified.</p></div>`;
      } else {
        eBox.innerHTML = `
          <table style="width:100%;border-collapse:collapse;font-size:.85rem">
            <thead>
              <tr style="border-bottom:1px solid var(--border);text-align:left;color:var(--text3);font-size:.7rem;text-transform:uppercase;letter-spacing:.05em">
                <th style="padding:10px 14px">Patient</th>
                <th style="padding:10px 14px">City</th>
                <th style="padding:10px 14px">HbA1c</th>
                <th style="padding:10px 14px">Systolic BP</th>
                <th style="padding:10px 14px">Risk Drivers</th>
                <th style="padding:10px 14px;text-align:center">Risk</th>
              </tr>
            </thead>
            <tbody>
              ${data.emergency_patients.map(p => `
                <tr style="border-bottom:1px solid var(--border)">
                  <td style="padding:12px 14px;font-weight:500">${p.name} (${p.age}y)</td>
                  <td style="padding:12px 14px;color:var(--text2)">${p.city}</td>
                  <td style="padding:12px 14px;color:${p.hba1c >= 9 ? 'var(--red)' : 'inherit'}">${p.hba1c || '—'}%</td>
                  <td style="padding:12px 14px;color:${p.sbp >= 160 ? 'var(--red)' : 'inherit'}">${p.sbp || '—'} mmHg</td>
                  <td style="padding:12px 14px;color:var(--text3);font-size:.75rem">${p.risk_drivers || 'Elevated Risk'}</td>
                  <td style="padding:12px 14px;text-align:center">
                    ${_formatRiskBadge(p.probability, p.model_available)}
                  </td>
                  <td style="padding:12px 14px;text-align:right">
                    <button class="btn btn-outline" style="padding:6px 12px;font-size:.75rem" onclick="loadPatientView('${p.patient_id}')">View Profile</button>
                  </td>
                </tr>`).join('')}
            </tbody>
          </table>`;
      }

      // Urgent Queue
      if (!data.urgent_patients || data.urgent_patients.length === 0) {
        uBox.innerHTML = `<div class="empty"><p style="padding:10px 0;">No urgent care patients identified.</p></div>`;
      } else {
        uBox.innerHTML = `
          <table style="width:100%;border-collapse:collapse;font-size:.85rem">
            <thead>
              <tr style="border-bottom:1px solid var(--border);text-align:left;color:var(--text3);font-size:.7rem;text-transform:uppercase;letter-spacing:.05em">
                <th style="padding:10px 14px">Patient</th>
                <th style="padding:10px 14px">City</th>
                <th style="padding:10px 14px">HbA1c</th>
                <th style="padding:10px 14px">Systolic BP</th>
                <th style="padding:10px 14px">Risk Drivers</th>
                <th style="padding:10px 14px;text-align:center">Risk</th>
              </tr>
            </thead>
            <tbody>
              ${data.urgent_patients.map(p => `
                <tr style="border-bottom:1px solid var(--border)">
                  <td style="padding:12px 14px;font-weight:500">${p.name} (${p.age}y)</td>
                  <td style="padding:12px 14px">${p.city}</td>
                  <td style="padding:12px 14px">${p.hba1c || '—'}%</td>
                  <td style="padding:12px 14px">${p.sbp || '—'} mmHg</td>
                  <td style="padding:12px 14px;color:var(--text3);font-size:.72rem">${p.risk_drivers || 'Rising Risk'}</td>
                  <td style="padding:12px 14px;text-align:center">
                    ${_formatRiskBadge(p.probability, p.model_available)}
                  </td>
                  <td style="padding:12px 14px;text-align:right">
                    <button class="btn btn-outline" style="padding:6px 12px;font-size:.75rem" onclick="loadPatientView('${p.patient_id}')">View Profile</button>
                  </td>
                </tr>`).join('')}
            </tbody>
          </table>`;
      }
    }

    // Helper to jump to a patient profile from a sub-dashboard
    function loadPatientView(pid) {
      showSection('profile', document.getElementById('nav-profile'));
      loadPatient(pid);
    }

    function runPredictionForPatient(pid) {
      // Navigate to Analytics Explorer → Predictive Modeling tab
      showSection('explorer', document.getElementById('nav-explorer'));
      showExplorerTab('predictive');
      // Pre-fill the patient ID input and run
      const input = document.getElementById('pred-patient-id');
      if (input) {
        input.value = pid;
        runPrediction();
      }
    }

    // ─── Analytics Explorer ───
    let _expCharts = [];

    function applyExplorerFilters() {
      const cohort = document.getElementById('flt-cohort')?.value || '';
      const gender = document.getElementById('flt-gender')?.value || '';
      const ageMin = document.getElementById('flt-age-min')?.value || '';
      const ageMax = document.getElementById('flt-age-max')?.value || '';
      const condition = document.getElementById('flt-condition')?.value || '';

      const filters = { cohort, gender, age_min: ageMin, age_max: ageMax, condition };
      _updateFilterTags(filters);

      const params = new URLSearchParams();
      if (cohort) params.set('cohort', cohort);
      if (gender) params.set('gender', gender);
      if (ageMin) params.set('age_min', ageMin);
      if (ageMax) params.set('age_max', ageMax);
      if (condition) params.set('condition', condition);

      const resultsEl = document.getElementById('exp-results');
      resultsEl.innerHTML = `
        <div style="padding:80px;text-align:center">
          <div class="spinner" style="width:36px;height:36px;border-width:3px;margin:0 auto 16px"></div>
          <div style="color:var(--text3);font-size:.9rem">Querying population…</div>
        </div>`;

      // Destroy old charts
      _expCharts.forEach(c => c.destroy());
      _expCharts = [];

      fetch('/api/patients/analytics/?' + params.toString())
        .then(r => r.json())
        .then(data => _renderExplorerResults(data))
        .catch(err => {
          resultsEl.innerHTML = `
            <div style="padding:60px;text-align:center">
              <div style="color:var(--red);font-size:.9rem">Failed to load results: ${err.message}</div>
              <button class="btn btn-outline" style="margin-top:16px" onclick="applyExplorerFilters()">Retry</button>
            </div>`;
        });
    }

    function resetExplorer() {
      ['flt-cohort', 'flt-gender', 'flt-age-min', 'flt-age-max', 'flt-condition'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
      });
      document.getElementById('exp-active-filters').style.display = 'none';
      document.getElementById('exp-filter-tags').innerHTML = '';
      _expCharts.forEach(c => c.destroy());
      _expCharts = [];
      document.getElementById('exp-results').innerHTML = `
        <div style="padding:80px;text-align:center">
          <div style="font-size:2rem;margin-bottom:12px;color:var(--border2)">⟳</div>
          <div style="color:var(--text3);font-size:.9rem">
            Choose filters and click <strong style="color:var(--text)">Apply Filters</strong> to explore the population.
          </div>
        </div>`;
    }

    function _updateFilterTags(filters) {
      const labels = {
        cohort: { chronic: 'Chronic', at_risk: 'At Risk', pediatric: 'Pediatric', deceased: 'Deceased' },
        gender: { M: 'Male', F: 'Female' },
        condition: { hypertension: 'Hypertension', diabetes: 'Diabetes (T2D)' },
      };
      const tags = [];
      if (filters.cohort) tags.push({ label: labels.cohort[filters.cohort] || filters.cohort, field: 'cohort' });
      if (filters.gender) tags.push({ label: labels.gender[filters.gender] || filters.gender, field: 'gender' });
      if (filters.age_min) tags.push({ label: `Age ≥ ${filters.age_min}`, field: 'age-min' });
      if (filters.age_max) tags.push({ label: `Age ≤ ${filters.age_max}`, field: 'age-max' });
      if (filters.condition) tags.push({ label: labels.condition[filters.condition] || filters.condition, field: 'condition' });

      const tagsEl = document.getElementById('exp-filter-tags');
      const wrapEl = document.getElementById('exp-active-filters');
      if (!tags.length) {
        wrapEl.style.display = 'none';
        tagsEl.innerHTML = '';
        return;
      }
      wrapEl.style.display = 'block';
      tagsEl.innerHTML = tags.map(t => `
        <span style="display:inline-flex;align-items:center;gap:5px;background:var(--bg3);
                     border:1px solid var(--border2);border-radius:20px;padding:3px 10px;font-size:.75rem;color:var(--text2)">
          ${t.label}
          <button onclick="_clearTag('${t.field}')" style="background:none;border:none;cursor:pointer;
                  color:var(--text3);font-size:.85rem;line-height:1;padding:0">&times;</button>
        </span>`).join('');
    }

    function _clearTag(field) {
      const el = document.getElementById(`flt-${field}`);
      if (el) el.value = '';
      applyExplorerFilters();
    }

    function _renderExplorerResults(data) {
      const count = (data.count || 0).toLocaleString();

      const hba1c = data.hba1c_dist || {};
      const bp = data.bp_dist || {};
      const age = data.age_dist || {};
      const conds = data.top_conditions || [];

      document.getElementById('exp-results').innerHTML = `
        <!-- Count badge -->
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
          <div style="font-size:2rem;font-weight:700;color:var(--accent)">${count}</div>
          <div style="color:var(--text2);font-size:.9rem">patients match your filters</div>
        </div>

        <!-- Charts row -->
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:20px">

          <!-- HbA1c -->
          <div class="panel" style="padding:18px">
            <div style="font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);
                        font-weight:600;margin-bottom:14px">HbA1c Distribution</div>
            <canvas id="exp-hba1c-chart" height="180"></canvas>
          </div>

          <!-- Blood Pressure -->
          <div class="panel" style="padding:18px">
            <div style="font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);
                        font-weight:600;margin-bottom:14px">Blood Pressure</div>
            <canvas id="exp-bp-chart" height="180"></canvas>
          </div>

          <!-- Age Distribution -->
          <div class="panel" style="padding:18px">
            <div style="font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);
                        font-weight:600;margin-bottom:14px">Age Distribution</div>
            <canvas id="exp-age-chart" height="180"></canvas>
          </div>

        </div>

        <!-- Top Conditions -->
        <div class="panel" style="padding:18px">
          <div style="font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);
                      font-weight:600;margin-bottom:14px">Top Conditions</div>
          ${conds.length ? `
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px">
              ${conds.map((c, i) => `
                <div style="display:flex;align-items:center;gap:10px;padding:8px 10px;
                            background:var(--bg3);border-radius:8px">
                  <span style="font-size:.72rem;color:var(--text3);width:18px;text-align:right">${i + 1}</span>
                  <span style="flex:1;font-size:.82rem;color:var(--text2);overflow:hidden;text-overflow:ellipsis;
                               white-space:nowrap" title="${c.name}">${c.name}</span>
                  <span style="font-size:.82rem;font-weight:600;color:var(--text);white-space:nowrap">
                    ${(c.count || 0).toLocaleString()}
                  </span>
                </div>`).join('')}
            </div>` : `<div style="color:var(--text3);font-size:.85rem">No condition data available.</div>`}
        </div>`;

      // ── Chart.js instances ──────────────────────────────────────
      const chartDefaults = {
        type: 'bar',
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#8fa8bf', font: { size: 11 } }, grid: { color: '#2a3f55' } },
            y: { ticks: { color: '#8fa8bf', font: { size: 11 } }, grid: { color: '#2a3f55' } },
          },
        },
      };

      // HbA1c
      _expCharts.push(new Chart(document.getElementById('exp-hba1c-chart'), {
        ...chartDefaults,
        data: {
          labels: ['Normal\n(<5.7%)', 'Prediabetes\n(5.7–6.4%)', 'Diabetes\n(≥6.5%)'],
          datasets: [{
            data: [hba1c.normal || 0, hba1c.prediabetes || 0, hba1c.diabetes || 0],
            backgroundColor: ['#4ade80', '#fbbf24', '#f87171'], borderRadius: 6
          }],
        },
      }));

      // BP
      _expCharts.push(new Chart(document.getElementById('exp-bp-chart'), {
        ...chartDefaults,
        data: {
          labels: ['Normal', 'Elevated', 'Stage 1', 'Stage 2'],
          datasets: [{
            data: [bp.normal || 0, bp.elevated || 0, bp.stage1 || 0, bp.stage2 || 0],
            backgroundColor: ['#4ade80', '#fbbf24', '#fb923c', '#f87171'], borderRadius: 6
          }],
        },
      }));

      // Age
      const ageLabels = Object.keys(age).sort();
      _expCharts.push(new Chart(document.getElementById('exp-age-chart'), {
        ...chartDefaults,
        data: {
          labels: ageLabels,
          datasets: [{
            data: ageLabels.map(k => age[k]),
            backgroundColor: '#60a5fa', borderRadius: 4
          }],
        },
      }));
    }

    // ─── Explorer Tab Switcher ───────────────────────────────────────
    function showExplorerTab(tabId) {
      const tabs = ['population', 'predictive'];
      tabs.forEach(t => {
        const pane = document.getElementById('exp-pane-' + t);
        const btn = document.getElementById('exp-tab-btn-' + t);
        if (!pane || !btn) return;
        const active = (t === tabId);
        pane.style.display = active ? '' : 'none';
        btn.style.borderBottomColor = active ? 'var(--accent)' : 'transparent';
        btn.style.color = active ? 'var(--text)' : 'var(--text3)';
      });
    }

    // ─── Predictive Modeling ─────────────────────────────────────────
    function runPrediction() {
      const id = document.getElementById('pred-patient-id').value.trim();
      if (!id) return alert('Enter a patient ID');
      const res = document.getElementById('pred-results');
      res.style.display = 'block';
      res.innerHTML = '<div style="padding:20px;text-align:center"><div class="spin" style="margin:0 auto 10px"></div>Scoring patient with 3-model ensemble...</div>';

      fetch(`/api/patients/${id}/predict/`)
        .then(r => r.json())
        .then(d => {
          if (d.error) {
            res.innerHTML = `<div style="padding:20px;color:var(--red);text-align:center">${d.error}</div>`;
            return;
          }

          if (d.cohort === 'at_risk') {
            res.innerHTML = _renderAtRiskOnset(d);
            return;
          }
          if (d.cohort === 'pediatric') {
            res.innerHTML = _renderPediatricBMI(d);
            return;
          }

          const prob = Math.round(d.progression_probability * 100);
          const color = prob >= 75 ? 'var(--red)' : (prob >= 40 ? 'var(--amber)' : 'var(--green)');

          const sRisk = d.sugar_risk?.model_scores || {};
          const bRisk = d.bp_risk?.model_scores || {};
          const sFor = d.sugar_forecast || {};
          const bFor = d.bp_forecast || {};
          const cScores = d.model_scores || {};

          const shap = d.shap_values || {};
          let shapHtml = '';
          if (Object.keys(shap).length > 0) {
            const sortedFeatures = Object.entries(shap)
              .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
              .slice(0, 4);
              
            shapHtml = `
            <div style="background:var(--bg3);border:1px solid var(--border2);border-radius:10px;padding:16px;margin-bottom:16px;">
                <div style="font-size:.7rem;font-weight:700;color:var(--purple);text-transform:uppercase;margin-bottom:12px;letter-spacing:0.1em">
                   🧠 AI Explainability (SHAP Feature Importance)
                </div>
                <div style="font-size:.65rem;color:var(--text3);margin-bottom:10px">Top drivers for this patient's ensemble score:</div>
                <div style="display:flex; flex-direction:column; gap:8px;">
                  ${sortedFeatures.map(([feat, impact]) => {
                     const isPositive = impact > 0;
                     const iColor = isPositive ? 'var(--red)' : 'var(--green)';
                     const icon = isPositive ? '↑ Increases Risk' : '↓ Lowers Risk';
                     const formattedName = feat.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                     
                     // Try to extract actual data value from features payload
                     let valStr = '';
                     if (d.features && typeof d.features[feat] !== 'undefined') {
                         let val = d.features[feat];
                         if (typeof val === 'number') val = Math.round(val * 10) / 10;
                         if (feat.includes('sbp')) valStr = ` (${val} mmHg)`;
                         else if (feat.includes('hba1c')) valStr = ` (${val}%)`;
                         else if (feat === 'age') valStr = ` (${val}y)`;
                         else if (feat === 'days_since_last_visit') valStr = ` (${val}d)`;
                         else if (feat.includes('has_')) valStr = val ? ' (Yes)' : ' (No)';
                         else valStr = ` (${val})`;
                     }

                     const width = Math.min(100, Math.max(5, Math.abs(impact) * 800)); // Scaled heuristic for visual relative impact
                     return `
                       <div style="display:flex; align-items:center; justify-content:space-between; font-size:.75rem;">
                         <div style="width:35%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${formattedName} <span style="opacity:0.6;font-size:0.9em">${valStr}</span></div>
                         <div style="width:40%; background:var(--bg2); height:8px; border-radius:4px; position:relative; overflow:hidden;">
                            <div style="position:absolute; ${isPositive ? 'left:0%;' : 'right:0%;'} width:${width}%; height:100%; background:${iColor}; border-radius:4px;"></div>
                         </div>
                         <div style="width:25%; text-align:right; font-weight:600; color:${iColor}; font-size:.65rem;">${icon}</div>
                       </div>
                     `;
                  }).join('')}
                </div>
            </div>
            `;
          }

          res.innerHTML = `
            <div style="background:var(--bg3);padding:14px 16px;border-radius:8px;border:1px solid var(--border2);margin-bottom:16px">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                  <div style="font-size:.95rem;font-weight:600;color:var(--text);margin-bottom:3px">${d.patient_name}</div>
                  <div style="font-size:.72rem;color:var(--text3)">${d.age}y · ${d.gender} · ID: ${d.patient_id}</div>
                </div>
                <div style="text-align:right">
                  <div style="font-size:1.8rem;font-weight:700;color:${color}">${prob}%</div>
                  <div style="font-size:.65rem;color:var(--text3);text-transform:uppercase;letter-spacing:.05em">Ensemble Risk</div>
                </div>
              </div>
            </div>

            ${shapHtml}
            <div id="inline-layman-explanation" style="background:var(--bg2); border-left:4px solid var(--purple); border-radius:4px; padding:12px 16px; margin-bottom:16px; font-size:0.8rem; color:var(--text2);">
               <div style="display:flex; align-items:center; gap:8px;">
                  <div class="spin" style="width:12px;height:12px;border-width:2px;border-color:var(--purple) transparent var(--purple) transparent;"></div>
                  <span style="font-style:italic;">AI generating specific layman summary...</span>
               </div>
            </div>

            ${
              d.current_vitals && Object.keys(cScores).length > 0 ? `
              <div style="background:var(--bg3);border:1px solid var(--border2);border-radius:10px;padding:16px 20px;margin-top:16px;">
                <div style="font-size:.9rem;font-weight:700;color:var(--text);text-transform:uppercase;margin-bottom:12px;letter-spacing:0.1em;display:flex;align-items:center;gap:8px;">
                   <span style="font-size:1.2rem;">📊</span> Current Baseline Vitals & Raw Model Outputs
                </div>
                <div style="display:grid;grid-template-columns:1.2fr 1.8fr;gap:20px;">
                   <!-- Current Vitals -->
                   <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;display:flex;justify-content:space-around;text-align:center;">
                      <div>
                         <div style="font-size:.7rem;color:var(--text3);text-transform:uppercase;font-weight:600;letter-spacing:1px;margin-bottom:4px;">Current Blood Pressure</div>
                         <div style="font-size:1.4rem;font-weight:700;color:var(--text);">${d.current_vitals.sbp !== 'N/A' && d.current_vitals.sbp ? Math.round(d.current_vitals.sbp) : '—'} <span style="font-size:.8rem;font-weight:500;color:var(--text3)">mmHg</span></div>
                      </div>
                      <div style="width:1px;background:var(--border);"></div>
                      <div>
                         <div style="font-size:.7rem;color:var(--text3);text-transform:uppercase;font-weight:600;letter-spacing:1px;margin-bottom:4px;">Current Blood Sugar</div>
                         <div style="font-size:1.4rem;font-weight:700;color:var(--text);">${d.current_vitals.hba1c !== 'N/A' && d.current_vitals.hba1c ? d.current_vitals.hba1c : '—'} <span style="font-size:.8rem;font-weight:500;color:var(--text3)">% HbA1c</span></div>
                      </div>
                   </div>
                   <!-- Current Model Scores -->
                   <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;display:flex;justify-content:space-around;text-align:center;">
                      <div>
                         <div style="font-size:.7rem;color:var(--text3);text-transform:uppercase;font-weight:600;letter-spacing:1px;margin-bottom:4px;">Lasso Baseline</div>
                         <div style="font-size:1.4rem;font-weight:700;color:${(cScores.Lasso||0) > 0.5 ? 'var(--red)' : 'var(--text)'};">${Math.round((cScores.Lasso||0)*100)}%</div>
                      </div>
                      <div>
                         <div style="font-size:.7rem;color:var(--text3);text-transform:uppercase;font-weight:600;letter-spacing:1px;margin-bottom:4px;">RF Baseline</div>
                         <div style="font-size:1.4rem;font-weight:700;color:${(cScores['Random Forest']||0) > 0.5 ? 'var(--red)' : 'var(--text)'};">${Math.round((cScores['Random Forest']||0)*100)}%</div>
                      </div>
                      <div>
                         <div style="font-size:.7rem;color:var(--text3);text-transform:uppercase;font-weight:600;letter-spacing:1px;margin-bottom:4px;">XGB Baseline</div>
                         <div style="font-size:1.4rem;font-weight:700;color:${(cScores.XGBoost||0) > 0.5 ? 'var(--red)' : 'var(--text)'};">${Math.round((cScores.XGBoost||0)*100)}%</div>
                      </div>
                   </div>
                </div>
              </div>
              ` : ''
            }

            <div style="background:var(--bg3);border:1px solid var(--border2);border-radius:10px;padding:20px;margin-top:16px;overflow-x:auto;">
              <div style="font-size:.9rem;font-weight:700;color:var(--text);text-transform:uppercase;margin-bottom:16px;letter-spacing:0.1em;display:flex;align-items:center;gap:8px;">
                <span style="font-size:1.2rem;">🧠</span> Model Impact Matrix & Simulations
              </div>

              <table style="width:100%; border-collapse: collapse; font-size: 0.8rem; text-align:left; white-space: nowrap;">
                <thead>
                  <tr style="border-bottom: 2px solid var(--border2); color:var(--text3); text-transform:uppercase; font-size:0.7rem; letter-spacing:0.05em;">
                    <th style="padding:10px 12px; width:15%; vertical-align:top;">Architecture</th>
                    <th style="padding:10px 12px; width:35%; white-space: normal; vertical-align:top;">Methodology & Behavior</th>
                    <th style="padding:10px 12px; width:12.5%; color:var(--red); vertical-align:top;">What if Blood Pressure is Fixed?<br><span style="font-size:.65rem;color:var(--text3);text-transform:none;font-weight:400;display:block;margin-top:4px;white-space:normal;">(Set to 120 mmHg)<br>Shows remaining risk driven purely by bad Blood Sugar.</span></th>
                    <th style="padding:10px 12px; width:12.5%; color:var(--blue); vertical-align:top;">What if Blood Sugar is Fixed?<br><span style="font-size:.65rem;color:var(--text3);text-transform:none;font-weight:400;display:block;margin-top:4px;white-space:normal;">(Set to 5.5% HbA1c)<br>Shows remaining risk driven purely by bad Blood Pressure.</span></th>
                    <th style="padding:10px 12px; width:12.5%; vertical-align:top;">Predicted Blood Pressure<br><span style="font-size:.65rem;color:var(--text3);text-transform:none;font-weight:400;display:block;margin-top:4px;white-space:normal;">Estimated timeline in 6 months based on clinical history.</span></th>
                    <th style="padding:10px 12px; width:12.5%; vertical-align:top;">Predicted Blood Sugar<br><span style="font-size:.65rem;color:var(--text3);text-transform:none;font-weight:400;display:block;margin-top:4px;white-space:normal;">Estimated timeline in 6 months based on clinical history.</span></th>
                  </tr>
                </thead>
                <tbody>
                  <tr style="border-bottom: 1px solid var(--border2); background:var(--surface);">
                    <td style="padding:14px 12px; font-weight:700; color:var(--blue);">Lasso Regression</td>
                    <td style="padding:14px 12px; color:var(--text2); font-size:0.75rem; white-space: normal;">Linear risks. Capable of saturating at 100% on high-risk baselines.</td>
                    <td style="padding:14px 12px; font-size:1.1rem; font-weight:700;">${Math.round((sRisk.Lasso || 0) * 100)}%</td>
                    <td style="padding:14px 12px; font-size:1.1rem; font-weight:700;">${Math.round((bRisk.Lasso || 0) * 100)}%</td>
                    <td style="padding:14px 12px; font-weight:600; font-size:.9rem;">${Math.round(bFor.lasso || 0)} mmHg</td>
                    <td style="padding:14px 12px; font-weight:600; font-size:.9rem;">${sFor.lasso || '—'}%</td>
                  </tr>
                  <tr style="border-bottom: 1px solid var(--border2); background:var(--bg3);">
                    <td style="padding:14px 12px; font-weight:700; color:var(--green);">Random Forest</td>
                    <td style="padding:14px 12px; color:var(--text2); font-size:0.75rem; white-space: normal;">Tree ensemble. Evaluates conservative, non-linear pathway averages.</td>
                    <td style="padding:14px 12px; font-size:1.1rem; font-weight:700;">${Math.round((sRisk['Random Forest'] || 0) * 100)}%</td>
                    <td style="padding:14px 12px; font-size:1.1rem; font-weight:700;">${Math.round((bRisk['Random Forest'] || 0) * 100)}%</td>
                    <td style="padding:14px 12px; font-weight:600; font-size:.9rem;">${Math.round(bFor.rf || 0)} mmHg</td>
                    <td style="padding:14px 12px; font-weight:600; font-size:.9rem;">${sFor.rf || '—'}%</td>
                  </tr>
                  <tr style="background:var(--surface);">
                    <td style="padding:14px 12px; font-weight:700; color:var(--red);">XGBoost</td>
                    <td style="padding:14px 12px; color:var(--text2); font-size:0.75rem; white-space: normal;">Aggressive logic interactions. Identifies critical gatekeeper vitals instantly.</td>
                    <td style="padding:14px 12px; font-size:1.1rem; font-weight:700;">${Math.round((sRisk.XGBoost || 0) * 100)}%</td>
                    <td style="padding:14px 12px; font-size:1.1rem; font-weight:700;">${Math.round((bRisk.XGBoost || 0) * 100)}%</td>
                    <td style="padding:14px 12px; font-weight:600; font-size:.9rem;">${Math.round(bFor.xgb || 0)} mmHg</td>
                    <td style="padding:14px 12px; font-weight:600; font-size:.9rem;">${sFor.xgb || '—'}%</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div style="margin-top:16px;padding:12px;background:${color}15;border-radius:8px;color:${color};font-size:.85rem;font-weight:500;border:1px solid ${color}33">
                <span style="margin-right:6px">ⓘ</span> ${d.recommendation}
            </div>
            <div style="margin-top:12px; display:flex; flex-direction:column; gap:12px;">
               <div>
                 <button class="btn btn-primary" onclick='explainPrediction("${d.patient_id}", ${JSON.stringify(d).replace(/'/g, "&apos;")})'>Explain This AI Result</button>
               </div>
               
               <div style="background:var(--bg2); padding:14px; border-radius:8px; border:1px solid var(--border);">
                 <div style="font-size:0.75rem; font-weight:600; color:var(--text); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:10px;">💬 Quick AI Prompts (Predictive Modeling)</div>
                 <div style="display:flex; flex-wrap:wrap; gap:8px;">
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('Explain why the XGBoost model assigned its specific risk score compared to the Random Forest score.')">Model Explainability</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('Which clinical vitals are the strongest predictors driving this patient\\'s current risk assessment?')">Feature Importance</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('Based on the 6-month prediction timeline, what is the expected clinical trajectory if no interventions are made?')">Clinical Trajectory</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('If we lower the patient\\'s blood pressure to 120 mmHg, how much will their remaining ensemble risk score decrease?')">What-If Simulation</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('How does the ensemble model weight the Lasso Regression, Random Forest, and XGBoost outputs for this specific patient profile?')">Ensemble Logic</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('Are there any specific recent encounters or changes in medication that spiked the patient\\'s risk profile in the predictive model?')">Risk Drivers</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('How heavily does the overdue care gap impact the current predictive risk score, and what happens to the score if it is resolved tomorrow?')">Care Gap Impact</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('Does the model\\'s prediction align with the standard clinical guidelines for a patient with these baselines?')">Clinical Guidelines</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('Why does the Lasso Regression model saturate differently than Random Forest on high-risk baselines?')">Algorithm Methodology</button>
                   <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('According to the predictive drivers, which single clinical intervention would yield the highest reduction in the patient\\'s immediate health risk?')">Intervention Prioritization</button>
                 </div>
               </div>
            </div>
          `;

          // Automatically trigger the AI layman explanation
          fetch('/api/rag/explain/', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
              body: JSON.stringify({ patient_id: id, prediction_data: d })
          })
          .then(r => r.json())
          .then(explainData => {
              const el = document.getElementById('inline-layman-explanation');
              if (el && explainData.explanation) {
                  el.innerHTML = `<div style="font-weight:600; color:var(--purple); margin-bottom:4px">Layman Risk Summary</div><div>${explainData.explanation.replace(/\n/g, '<br>')}</div>`;
              }
          })
          .catch(err => {
              const el = document.getElementById('inline-layman-explanation');
              if (el) el.style.display = 'none';
          });
        })
        .catch(err => {
          console.error(err);
          res.innerHTML = '<div style="padding:20px;color:var(--red);text-align:center">Error — check patient ID</div>';
        });
    }

    function renderExplorerTierPanel(riskTiers) {
      explorerTierData = riskTiers || {};
      const tierOrder = ['EMERGENCY', 'HIGH', 'MODERATE', 'PREVENTIVE', 'NORMAL'];
      if (!explorerTierData[explorerSelectedTier]) {
        explorerSelectedTier = tierOrder.find(t => explorerTierData[t] > 0) || 'EMERGENCY';
      }
      const grid = document.getElementById('exp-tier-grid');
      if (!grid) return;
      grid.innerHTML = tierOrder.map(tier => {
        const count = explorerTierData[tier] || 0;
        const tierMeta = tierNarratives[tier] || {};
        return `
          <button type="button" class="tier-card${tier === explorerSelectedTier ? ' active' : ''}" data-tier="${tier}" onclick="selectExplorerTier('${tier}')">
            <div class="tier-label">${tier}</div>
            <div class="tier-count">${count.toLocaleString()}</div>
            <div class="tier-sub">${tierMeta.summary || 'View tier details'}</div>
          </button>
        `;
      }).join('');
      updateTierDetail();
    }

    function selectExplorerTier(tier) {
      explorerSelectedTier = tier;
      updateTierDetail();
    }

    function updateTierDetail() {
      const detail = document.getElementById('exp-tier-detail');
      if (!detail) return;
      const tier = explorerSelectedTier && explorerTierData[explorerSelectedTier] !== undefined ? explorerSelectedTier : 'EMERGENCY';
      explorerSelectedTier = tier;
      const narrative = tierNarratives[tier] || {};
      const tierCount = explorerTierData[tier] || 0;
      detail.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
          <strong style="font-size:1rem;">${tier} · ${tierCount.toLocaleString()} patients</strong>
          <span style="color:var(--text3);font-size:.78rem;">${narrative.summary || ''}</span>
        </div>
        <p style="margin-top:10px;">${narrative.description || 'Tier logic is derived from recent labs, blood pressure, and active conditions.'}</p>
      `;
      const grid = document.getElementById('exp-tier-grid');
      if (grid) {
        grid.querySelectorAll('.tier-card').forEach(card => {
          card.classList.toggle('active', card.dataset.tier === tier);
        });
      }
    }

    // ─── AI & RAG ───
    async function checkAIStatus() {
      const dot = document.querySelector('#ragStatus .rag-dot');
      const text = document.getElementById('ragText');
      try {
        const res = await fetch('/api/rag/status/');
        const data = await res.json();
        if (data.status === 'ready') {
          document.getElementById('ragStatus').classList.add('ok');
          text.textContent = `AI: ${data.configured_model} Ready`;
        } else {
          document.getElementById('ragStatus').classList.remove('ok');
          text.textContent = `AI: Not Ready (FAISS/Ollama)`;
        }
      } catch (e) {
        text.textContent = `AI: Offline`;
      }
    }

    let aiCoordinatorHistory = [];

    async function sendAICoordinatorQuery(overrideQuestion = null) {
      if (!currentPatientId) return alert('Select a patient first.');
      const input = document.getElementById('ai-query-input');
      const question = overrideQuestion || input.value.trim();
      if (!question) return;

      const history = document.getElementById('ai-chat-history');
      history.innerHTML += `<div style="margin-bottom:12px; text-align:right;"><span style="background:var(--accent)22; padding:8px 12px; border-radius:12px; font-size:.85rem;">${question}</span></div>`;
      input.value = '';
      history.scrollTop = history.scrollHeight;

      const loadingId = 'ai-loading-' + Date.now();
      history.innerHTML += `<div id="${loadingId}" style="margin-bottom:12px;"><span style="color:var(--text3); font-size:.85rem;">AI is thinking...</span></div>`;

      try {
        const res = await fetch('/api/rag/ask/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
          body: JSON.stringify({ patient_id: currentPatientId, question: question, history: aiCoordinatorHistory })
        });
        const data = await res.json();
        const aiResponse = data.answer || data.error;
        document.getElementById(loadingId).remove();
        history.innerHTML += `<div style="margin-bottom:12px;"><div style="background:var(--bg2); border:1px solid var(--border); padding:12px; border-radius:12px; font-size:.85rem; line-height:1.6;">${aiResponse}</div></div>`;
        
        // Push user question and AI answer to memory
        aiCoordinatorHistory.push({ isUser: true, text: question });
        aiCoordinatorHistory.push({ isUser: false, text: aiResponse });
      } catch (e) {
        document.getElementById(loadingId).innerHTML = `<span style="color:var(--red); font-size:.85rem;">Error: ${e.message}</span>`;
      }
      history.scrollTop = history.scrollHeight;
    }

    async function explainPrediction(pid, predictionData) {
      const btn = event.currentTarget;
      const originalText = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<div class="spin" style="width:12px; height:12px;"></div> Explaining...';

      try {
        const res = await fetch('/api/rag/explain/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
          body: JSON.stringify({ patient_id: pid, prediction_data: predictionData })
        });
        const data = await res.json();

        // Open the global chat and inject the explanation
        openGlobalChat();
        const explainText = `AI Explanation for ${data.patient_name || 'Patient'}:\n\n${data.explanation}`;
        addGlobalChatMessage('ai', `<strong>${explainText.replace(/\n/g, '<br>')}</strong>`);
        
        // Push both the implied user request and AI response to conversational memory!
        globalChatHistory.push({ isUser: true, text: "Can you explain this patient's prediction result?" });
        globalChatHistory.push({ isUser: false, text: explainText });
      } catch (e) {
        addGlobalChatMessage('ai', `Sorry, I encountered an error while generating the explanation: ${e.message}`);
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    }

    // ─── Global Chat Logic ───
    function toggleGlobalChat() {
      const win = document.getElementById('ai-chat-window');
      const isVisible = win.style.display === 'flex';
      win.style.display = isVisible ? 'none' : 'flex';
      if (!isVisible) document.getElementById('global-chat-input').focus();
    }

    function openGlobalChat() {
      const win = document.getElementById('ai-chat-window');
      win.style.display = 'flex';
      document.getElementById('global-chat-input').focus();
    }

    function addGlobalChatMessage(role, text) {
      const body = document.getElementById('global-chat-body');
      body.innerHTML += `<div class="msg msg-${role}">${text}</div>`;
      body.scrollTop = body.scrollHeight;
    }

    let globalChatHistory = [];

    async function sendGlobalChat(overrideQuestion = null) {
      openGlobalChat();
      const input = document.getElementById('global-chat-input');
      const question = overrideQuestion || input.value.trim();
      if (!question) return;

      addGlobalChatMessage('user', question);
      input.value = '';

      const loadingId = 'global-loading-' + Date.now();
      const body = document.getElementById('global-chat-body');
      body.innerHTML += `<div id="${loadingId}" class="msg msg-ai" style="opacity:0.6 italic">CareGap is thinking...</div>`;
      body.scrollTop = body.scrollHeight;

      try {
        // Use the coordinator ask endpoint as it's general purpose
        // If we have a current patient, use their context. otherwise search for general.
        const payload = { question: question, history: globalChatHistory };
        if (currentPatientId) payload.patient_id = currentPatientId;

        const endpoint = currentPatientId ? '/api/rag/ask/' : '/api/rag/ask-analytics/';

        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        document.getElementById(loadingId).remove();
        const aiResponse = data.answer || data.explanation || data.error;
        addGlobalChatMessage('ai', aiResponse);
        
        // Push to memory
        globalChatHistory.push({ isUser: true, text: question });
        globalChatHistory.push({ isUser: false, text: aiResponse });
      } catch (e) {
        document.getElementById(loadingId).innerHTML = `Connection lost. Fallback to local simulation mode enabled.`;
      }
    }

    // Add listener for global chat input
    document.addEventListener('DOMContentLoaded', () => {
      const globalInput = document.getElementById('global-chat-input');
      if (globalInput) {
        globalInput.addEventListener('keypress', (e) => {
          if (e.key === 'Enter') sendGlobalChat();
        });
      }
    });

    // ─── Multi-Cohort Prediction Extensions ───
    function _renderAtRiskOnset(d) {
      const prob = Math.round(d.onset_probability * 100);
      const color = prob >= 60 ? 'var(--red)' : (prob >= 30 ? 'var(--amber)' : 'var(--green)');

      return `
        <div style="background:var(--bg3); padding:14px 16px; border-radius:8px; border:1px solid var(--border2); margin-bottom:16px">
          <div style="display:flex; justify-content:space-between; align-items:center">
            <div>
              <div style="font-size:.95rem; font-weight:600;">${d.patient_name}</div>
              <div style="font-size:.72rem; color:var(--text3);">Adult At-Risk · No Chronic History</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:1.8rem; font-weight:700; color:${color}">${prob}%</div>
              <div style="font-size:.65rem; color:var(--text3); text-transform:uppercase;">Onset Risk</div>
            </div>
          </div>
        </div>
        <div class="panel" style="padding:16px; border-top: 3px solid ${color}">
          <div style="font-size:.85rem; color:var(--text2); line-height:1.6;">${d.recommendation}</div>
          <div style="margin-top:12px; display:flex; gap:10px;">
             <button class="btn btn-primary" onclick='explainPrediction("${d.patient_id}", ${JSON.stringify(d)})'>Explain This AI Result</button>
          </div>
        </div>
      `;
    }

    function _renderPediatricBMI(d) {
      const color = d.risk_tier === 'Obesity' || d.risk_tier === 'Overweight' ? 'var(--red)' : (d.risk_tier === 'Healthy weight' ? 'var(--green)' : 'var(--amber)');
      
      let gapsHtml = '';
      if (d.care_gaps && d.care_gaps.length > 0) {
          gapsHtml = `
            <div style="margin-bottom:16px;">
                <div style="font-size:0.75rem; font-weight:700; color:var(--text); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Pediatric Care Gaps</div>
                <ul style="margin:0; padding-left:20px; font-size:0.85rem; color:var(--text2); line-height:1.5;">
                    ${d.care_gaps.map(gap => `<li style="margin-bottom:4px; color:var(--red);">${gap}</li>`).join('')}
                </ul>
            </div>
          `;
      } else {
          gapsHtml = `
            <div style="margin-bottom:16px;">
                <div style="font-size:0.75rem; font-weight:700; color:var(--text); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Pediatric Care Gaps</div>
                <div style="font-size:0.85rem; color:var(--text3); display:flex; align-items:center; gap:6px;">
                   <span style="color:var(--green)">✓</span> Up-to-date on Well-Child Visits
                </div>
            </div>
          `;
      }

      return `
        <div style="background:var(--bg3); padding:14px 16px; border-radius:8px; border:1px solid var(--border2); margin-bottom:16px">
          <div style="font-size:.95rem; font-weight:600;">${d.patient_name} (${d.age}y ${d.gender})</div>
          <div style="font-size:.72rem; color:var(--text3);">Pediatric Cohort · CDC Growth Chart Assessment</div>
        </div>
        
        <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; margin-bottom:16px;">
           <div class="vital"><div class="label">BMI</div><div class="value">${d.bmi.toFixed(1)}</div></div>
           <div class="vital"><div class="label">Percentile (CDC)</div><div class="value">${d.percentile || 50}<sup>th</sup></div></div>
           <div class="vital"><div class="label">Tier</div><div class="value" style="color:${color}">${d.risk_tier}</div></div>
        </div>
        
        ${gapsHtml}
        
        <div style="padding:14px; background:var(--bg2); border-radius:8px; border:1px solid var(--border); font-size:.85rem; margin-bottom:12px;">
           <strong>Pediatric Guidance:</strong> ${d.recommendation}
        </div>
        
        <div style="margin-top:12px; display:flex; flex-direction:column; gap:12px;">
           <div>
             <button class="btn btn-primary" onclick='explainPrediction("${d.patient_id}", ${JSON.stringify(d).replace(/'/g, "&apos;")})'>Explain This AI Result</button>
           </div>
           
           <div style="background:var(--bg2); padding:14px; border-radius:8px; border:1px solid var(--border);">
             <div style="font-size:0.75rem; font-weight:600; color:var(--text); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:10px;">💬 Quick AI Prompts (Pediatric)</div>
             <div style="display:flex; flex-wrap:wrap; gap:8px;">
               <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('Does this child\\'s BMI percentile indicate a long-term risk of early-onset Type 2 Diabetes?')">BMI Risk Trajectory</button>
               <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('What are the standard CDC immunization requirements for a child of this age?')">Immunization Schedule</button>
               <button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface);" onclick="sendGlobalChat('What lifestyle interventions or nutritional guidelines are most effective for improving BMI percentiles in children?')">Nutritional Interventions</button>
               ${d.has_asthma ? `<button class="btn btn-outline" style="font-size:0.7rem; padding:6px 10px; background:var(--surface); border-color:var(--red);" onclick="sendGlobalChat('What are the standard pediatric guidelines for managing active asthma and preventing exacerbations?')">Asthma Protocol</button>` : ''}
             </div>
           </div>
        </div>
      `;
    }
