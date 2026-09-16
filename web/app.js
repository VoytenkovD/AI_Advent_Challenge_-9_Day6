(function () {
  'use strict';
  var CATALOG = {};
  var busy = false;

  var AGENT = {
    history: [],
    facts: [],
    factsUpTo: 0,
    branches: {},
    activeBranch: null,
    memory: { working: [], long_term: [] },
    total_prompt: 0,
    total_comp: 0,
    config: {
      provider: 'ai-public', model: 'deepseek-ai/deepseek-v4-pro-0813',
      systemPromptPreset: 'assistant', temperature: 0.7, topP: 1.0,
      frequencyPenalty: 0.0, presencePenalty: 0.0, maxTokens: 4000,
      responseFormat: 'text', maxWords: 0, maxInputChars: 2000,
      contextMode: 'sliding', keepRecent: 6, summarizeEvery: 10, factsUpdateEvery: 1
    }
  };

  var els = {};

  function el(tag, cls, txt) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (txt !== undefined) node.textContent = txt;
    return node;
  }

  function cacheElements() {
    var ids = ['note','history','input','send','sidebar','btnSettings','btnStats','btnClear',
      'btnStratBar','btnMemory','modalStats','btnCloseStats','selModel','preset','strategy','keeprecent',
      'sumevery','factsEvery','temp','topp','freq','pres','maxt','format','words','chars',
      'factsList','btnFactsUpdate','btnFactsClear','branchesList','btnBranchNew','btnBranchSwitch'];
    var map = {
      note:'app-note', history:'chat-history', input:'input', send:'send',
      sidebar:'sidebar', btnSettings:'btn-settings', btnStats:'btn-stats',
      btnClear:'btn-clear', btnStratBar:'btn-strat-bar', btnMemory:'btn-memory', modalStats:'modal-stats',
      btnCloseStats:'btn-stats-close', selModel:'set-model', preset:'set-preset',
      strategy:'set-strategy', keeprecent:'set-keeprecent', sumevery:'set-sumevery',
      factsEvery:'set-facts-every', temp:'set-temp', topp:'set-topp',
      freq:'set-freq', pres:'set-pres', maxt:'set-maxt', format:'set-format',
      words:'set-words', chars:'set-chars', factsList:'facts-list',
      btnFactsUpdate:'btn-facts-update', btnFactsClear:'btn-facts-clear',
      branchesList:'branches-list', btnBranchNew:'btn-branch-new',
      btnBranchSwitch:'btn-branch-switch'
    };
    ids.forEach(function(k){ els[k] = document.getElementById(map[k]) || {}; });
  }

  function getActiveHistory() {
    return AGENT.activeBranch ? (AGENT.branches[AGENT.activeBranch] || []) : AGENT.history;
  }

  // --- Сохранение/загрузка ---
  function saveStateToServer() {
    fetch('/api/history', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        history: AGENT.history, facts: AGENT.facts, factsUpTo: AGENT.factsUpTo,
        branches: AGENT.branches, activeBranch: AGENT.activeBranch,
        strategy: AGENT.config.contextMode
      })
    }).catch(function(){});
  }

  function clearHistoryOnServer() {
    fetch('/api/history', { method: 'DELETE' }).catch(function(){});
  }

  function saveMemoryToServer() {
    fetch('/api/memory', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ memory: AGENT.memory })
    }).catch(function(){});
  }

  function renderMemoryPanel() {
    // Краткосрочная
    var doc = document.getElementById('mem-short');
    if (doc) doc.textContent = 'Сообщений в диалоге: ' + (getActiveHistory().length || 0);

    // Рабочая
    var wDiv = document.getElementById('mem-working');
    if (wDiv) {
      wDiv.innerHTML = '';
      var wm = AGENT.memory.working || [];
      if (!wm.length) wDiv.textContent = '(пусто)';
      else wm.forEach(function(entry, i){
        var d = document.createElement('div');
        d.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:3px;';
        d.innerHTML = '<span style="color:#ffd426;">'+esc(entry.key)+'</span> = <span>'+esc(entry.value)+'</span> ' +
          '<button data-del="working:'+i+'" style="color:var(--err);background:none;border:none;cursor:pointer;font-size:11px;">×</button>';
        wDiv.appendChild(d);
      });
      wDiv.querySelectorAll('[data-del]').forEach(function(btn){
        btn.addEventListener('click', function(){
          var parts = this.getAttribute('data-del').split(':');
          var type = parts[0], idx = parseInt(parts[1]);
          AGENT.memory[type].splice(idx, 1);
          renderMemoryPanel(); saveMemoryToServer();
        });
      });
    }

    // Долговременная
    var lDiv = document.getElementById('mem-long');
    if (lDiv) {
      lDiv.innerHTML = '';
      var lm = AGENT.memory.long_term || [];
      if (!lm.length) lDiv.textContent = '(пусто)';
      else lm.forEach(function(entry, i){
        var d = document.createElement('div');
        d.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:3px;';
        d.innerHTML = '<span style="color:#3ddc84;">'+esc(entry.key)+'</span> = <span>'+esc(entry.value)+'</span> ' +
          '<button data-del="long_term:'+i+'" style="color:var(--err);background:none;border:none;cursor:pointer;font-size:11px;">×</button>';
        lDiv.appendChild(d);
      });
      lDiv.querySelectorAll('[data-del]').forEach(function(btn){
        btn.addEventListener('click', function(){
          var parts = this.getAttribute('data-del').split(':');
          var type = parts[0], idx = parseInt(parts[1]);
          AGENT.memory[type].splice(idx, 1);
          renderMemoryPanel(); saveMemoryToServer();
        });
      });
    }
  }

  function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  function clearWorkingMemory() {
    AGENT.memory.working = [];
    renderMemoryPanel(); saveMemoryToServer();
  }

  function clearLongTermMemory() {
    AGENT.memory.long_term = [];
    renderMemoryPanel(); saveMemoryToServer();
  }

  function loadStateFromServer() {
    return fetch('/api/history').then(function(r){ return r.json(); }).then(function(data){
      AGENT.history = Array.isArray(data.history) ? data.history : [];
      AGENT.facts = Array.isArray(data.facts) ? data.facts : [];
      AGENT.factsUpTo = Number(data.factsUpTo || 0);
      AGENT.branches = data.branches || {};
      AGENT.activeBranch = data.activeBranch || null;
      AGENT.memory = (data.memory && typeof data.memory === 'object') ? data.memory : { working: [], long_term: [] };
      renderHistory();
    }).catch(function(){});
  }

  function renderHistory() {
    els.history.innerHTML = '';
    var h = getActiveHistory();
    h.forEach(function(msg){ appendMessage(msg.role, msg.content); });
    updateBranchUI();
    updateFactsUI();
  }

  // --- UI ---
  function initUI() {
    els.btnSettings.addEventListener('click', function(){
      els.sidebar.style.display = els.sidebar.style.display === 'none' ? 'block' : 'none';
    });
    els.btnStats.addEventListener('click', renderStats);
    els.btnCloseStats.addEventListener('click', function(){ els.modalStats.style.display = 'none'; });

    els.btnClear.addEventListener('click', function(){
      AGENT.history = []; AGENT.facts = []; AGENT.factsUpTo = 0;
      AGENT.branches = {}; AGENT.activeBranch = null;
      AGENT.total_prompt = 0; AGENT.total_comp = 0;
      els.history.innerHTML = '<div class="msg-bot"><div class="md"><p>Очищено.</p></div></div>';
      clearHistoryOnServer();
      updateBranchUI(); updateFactsUI();
    });

    // Стратегия в шапке
    els.btnStratBar.addEventListener('click', function(){ els.sidebar.style.display = 'block';
      document.getElementById('set-strategy').focus(); });

    // Факты
    if (els.btnFactsUpdate && els.btnFactsUpdate.addEventListener) els.btnFactsUpdate.addEventListener('click', function(){ AGENT.factsUpTo = 0; });
    els.btnFactsClear.addEventListener('click', function(){ AGENT.facts = []; AGENT.factsUpTo = 0;
      updateFactsUI(); saveStateToServer(); });

    // Ветки
    els.btnBranchNew.addEventListener('click', createBranch);
    els.btnBranchSwitch.addEventListener('click', function(){
      AGENT.activeBranch = null; renderHistory(); saveStateToServer();
    });

    // Память
    els.btnMemory.addEventListener('click', function(){
      renderMemoryPanel();
      document.getElementById('modal-memory').style.display = 'flex';
    });
    document.getElementById('mem-add-working').addEventListener('click', function(){
      var k = document.getElementById('mem-new-key').value.trim();
      var v = document.getElementById('mem-new-val').value.trim();
      if (k && v) { AGENT.memory.working.push({key:k, value:v});
        document.getElementById('mem-new-key').value = '';
        document.getElementById('mem-new-val').value = '';
        renderMemoryPanel(); saveMemoryToServer(); }
    });
    document.getElementById('mem-add-long').addEventListener('click', function(){
      var k = document.getElementById('meml-new-key').value.trim();
      var v = document.getElementById('meml-new-val').value.trim();
      if (k && v) { AGENT.memory.long_term.push({key:k, value:v});
        document.getElementById('meml-new-key').value = '';
        document.getElementById('meml-new-val').value = '';
        renderMemoryPanel(); saveMemoryToServer(); }
    });
    document.getElementById('btn-work-clear').addEventListener('click', clearWorkingMemory);
    document.getElementById('btn-long-clear').addEventListener('click', clearLongTermMemory);
    document.getElementById('btn-memory-close').addEventListener('click', function(){
      document.getElementById('modal-memory').style.display = 'none';
    });
  }

  function syncStrategyVisibility() {
    var s = els.strategy.value;
    document.getElementById('grp-keep').style.display = (s === 'compressed' || s === 'sliding' || s === 'facts') ? '' : 'none';
    document.getElementById('grp-every').style.display = s === 'compressed' ? '' : 'none';
    document.getElementById('grp-facts-every').style.display = s === 'facts' ? '' : 'none';
    document.getElementById('grp-facts').style.display = s === 'facts' ? '' : 'none';
    document.getElementById('grp-branches').style.display = s === 'branching' ? '' : 'none';

    AGENT.config.contextMode = s;
    els.btnStratBar.textContent = s;
    saveStateToServer();
  }

  function updateBranchUI() {
    if (!els.branchesList) return;
    els.branchesList.innerHTML = '';
    var keys = Object.keys(AGENT.branches);
    if (!AGENT.activeBranch && keys.length === 0) {
      els.branchesList.textContent = 'Веток нет. Нажмите «Новая ветка».';
      els.btnBranchSwitch.textContent = 'Основная';
      return;
    }
    var active = (AGENT.activeBranch || 'main');
    if (AGENT.activeBranch) {
      els.branchesList.appendChild(el('div', null, 'Активна: ' + AGENT.activeBranch + ' (' + getActiveHistory().length + ' сообщ.)'));
      els.btnBranchSwitch.textContent = 'Основная';
    } else {
      els.branchesList.appendChild(el('div', null, 'Основная ветка (' + AGENT.history.length + ' сообщ.)'));
      els.btnBranchSwitch.textContent = 'К основной';
    }
    keys.forEach(function(id){
      if (id === active) return;
      var d = el('div', null, '' + id + ' (' + (AGENT.branches[id]||[]).length + ' сообщ.)');
      d.style.cursor = 'pointer'; d.style.color = 'var(--accent)';
      d.addEventListener('click', function(){
        AGENT.activeBranch = id; renderHistory(); saveStateToServer();
      });
      els.branchesList.appendChild(d);
    });
  }

  function createBranch() {
    var name = prompt('Имя ветки (напр. «вариант А»):', 'branch-' + (Object.keys(AGENT.branches).length + 1));
    if (!name) return;
    var history = getActiveHistory();
    var checkpoint = history.length;
    AGENT.branches[name] = history.slice(0, checkpoint);
    AGENT.activeBranch = name;
    renderHistory(); saveStateToServer();
  }

  function updateFactsUI() {
    if (!els.factsList) return;
    if (!AGENT.facts || AGENT.facts.length === 0) {
      els.factsList.textContent = 'Факты пока не извлечены.';
      return;
    }
    els.factsList.innerHTML = '';
    AGENT.facts.forEach(function(f){
      els.factsList.appendChild(el('div', null, f.key + ' = ' + f.value));
    });
  }

  function initSettings() {
    Object.keys(CATALOG).forEach(function(provider){
      var optgroup = el('optgroup');
      optgroup.label = provider;
      CATALOG[provider].forEach(function(m){
        var opt = el('option', null, m);
        opt.value = provider + ':' + m;
        optgroup.appendChild(opt);
      });
      els.selModel.appendChild(optgroup);
    });

    function loadSettings() {
      var def = AGENT.config.provider + ':' + AGENT.config.model;
      var m = false;
      Array.from(els.selModel.options).forEach(function(o){ if (o.value === def) m = true; });
      if (!m && els.selModel.options.length) {
        var p = els.selModel.options[0].value.split(':');
        AGENT.config.provider = p[0]; AGENT.config.model = p[1];
      }
      els.selModel.value = AGENT.config.provider + ':' + AGENT.config.model;
      els.preset.value = AGENT.config.systemPromptPreset;
      els.temp.value = AGENT.config.temperature;
      els.topp.value = AGENT.config.topP;
      els.freq.value = AGENT.config.frequencyPenalty;
      els.pres.value = AGENT.config.presencePenalty;
      els.maxt.value = AGENT.config.maxTokens;
      els.format.value = AGENT.config.responseFormat;
      els.words.value = AGENT.config.maxWords;
      els.chars.value = AGENT.config.maxInputChars;
      els.strategy.value = AGENT.config.contextMode;
      els.keeprecent.value = AGENT.config.keepRecent;
      els.sumevery.value = AGENT.config.summarizeEvery;
      els.factsEvery.value = AGENT.config.factsUpdateEvery || 1;
      syncStrategyVisibility();
      updateFactsUI();
      updateBranchUI();

      document.getElementById('val-temp').textContent = AGENT.config.temperature;
      document.getElementById('val-topp').textContent = AGENT.config.topP;
      document.getElementById('val-freq').textContent = AGENT.config.frequencyPenalty;
      document.getElementById('val-pres').textContent = AGENT.config.presencePenalty;
    }

    function saveSettings() {
      var p = els.selModel.value.split(':');
      AGENT.config.provider = p[0]; AGENT.config.model = p[1];
      AGENT.config.systemPromptPreset = els.preset.value;
      AGENT.config.temperature = parseFloat(els.temp.value);
      AGENT.config.topP = parseFloat(els.topp.value);
      AGENT.config.frequencyPenalty = parseFloat(els.freq.value);
      AGENT.config.presencePenalty = parseFloat(els.pres.value);
      AGENT.config.maxTokens = parseInt(els.maxt.value, 10);
      AGENT.config.responseFormat = els.format.value;
      AGENT.config.maxWords = parseInt(els.words.value, 10);
      AGENT.config.maxInputChars = parseInt(els.chars.value, 10);
      AGENT.config.contextMode = els.strategy.value;
      AGENT.config.keepRecent = parseInt(els.keeprecent.value, 10);
      AGENT.config.summarizeEvery = parseInt(els.sumevery.value, 10);
      AGENT.config.factsUpdateEvery = parseInt(els.factsEvery.value, 10);
      syncStrategyVisibility();

      document.getElementById('val-temp').textContent = AGENT.config.temperature;
      document.getElementById('val-topp').textContent = AGENT.config.topP;
      document.getElementById('val-freq').textContent = AGENT.config.frequencyPenalty;
      document.getElementById('val-pres').textContent = AGENT.config.presencePenalty;
    }

    els.selModel.addEventListener('change', saveSettings);
    ['preset','temp','topp','freq','pres','maxt','format','words','chars','strategy',
     'keeprecent','sumevery','factsEvery'].forEach(function(f){
      els[f].addEventListener('input', saveSettings);
    });
    els.strategy.addEventListener('change', saveSettings);
    loadSettings();
  }

  function appendMessage(role, content, metaData) {
    metaData = metaData || null;
    var msgDiv = el('div', role === 'user' ? 'msg-user' : 'msg-bot');
    if (role === 'user') { msgDiv.textContent = content; }
    else {
      if (metaData && metaData.status === 'error') msgDiv.appendChild(el('div', 'answer--empty', content));
      else {
        msgDiv.appendChild(window.MD.render(content));
        if (metaData) {
          var m = el('div', 'meta');
          m.appendChild(el('span', null, (metaData.latency_ms / 1000).toFixed(1) + ' с'));
          var u = metaData.usage || {};
          m.appendChild(el('span', null, (u.total_tokens || 0) + ' tok'));
          if (metaData.facts && metaData.facts.length) {
            m.appendChild(el('span', 'meta__cmp', '+facts: ' + metaData.facts.length));
          }
          msgDiv.appendChild(m);
        }
      }
    }
    els.history.appendChild(msgDiv);
    els.history.scrollTop = els.history.scrollHeight;
  }

  function renderStats() {
    document.getElementById('t-p').textContent = AGENT.total_prompt;
    document.getElementById('t-c').textContent = AGENT.total_comp;
    document.getElementById('t-t').textContent = AGENT.total_prompt + AGENT.total_comp;
    els.modalStats.style.display = 'flex';
  }

  function setBusy(v) {
    busy = v;
    els.input.disabled = v;
    els.send.disabled = v || !els.input.value.trim();
    els.send.textContent = v ? 'Работаю...' : 'Отправить';
  }

  function send() {
    var question = els.input.value.trim();
    if (!question || busy) return;
    els.input.value = '';
    setBusy(true);
    appendMessage('user', question);

    var loadDiv = el('div', 'msg-bot'); loadDiv.id = 'load-indicator';
    var span = el('span', 'muted', 'думаю'); span.appendChild(el('span', 'cursor'));
    loadDiv.appendChild(span); els.history.appendChild(loadDiv);
    els.history.scrollTop = els.history.scrollHeight;

    var history = getActiveHistory();
    var reqData = { question: question, agent: {
      config: AGENT.config, history: history,
      facts: AGENT.facts, factsUpTo: AGENT.factsUpTo,
      branches: AGENT.branches, activeBranch: AGENT.activeBranch
    }};

    fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqData)
    }).then(function(r){ return r.json(); }).then(function(data){
      var loadNode = document.getElementById('load-indicator');
      if (loadNode) loadNode.remove();
      var res = data.result;

      if (res && res.status === 'done') {
        history.push({ role: 'user', content: question });
        history.push({ role: 'assistant', content: res.text });

        var u = res.usage || {};
        AGENT.total_prompt += u.prompt_tokens || 0;
        AGENT.total_comp += u.completion_tokens || 0;

        if (res.facts) { AGENT.facts = res.facts; AGENT.factsUpTo = res.factsUpTo; }
        else { AGENT.factsUpTo = history.length; }

        // Предложения для памяти
        if (res.memory_suggestions) {
          var sugg = res.memory_suggestions;
          var div = document.getElementById('mem-suggestions');
          if (div) {
            div.innerHTML = '<h4 style="color:var(--accent);margin:0 0 6px;">Предложения памяти</h4>';
            if ((sugg.working||[]).length + (sugg.long_term||[]).length === 0) {
              div.innerHTML += '<span style="font-size:12px;color:var(--muted);">Нет новых предложений.</span>';
            } else {
              if (sugg.working && sugg.working.length) {
                div.innerHTML += '<div style="font-size:12px;margin-bottom:4px;"><b>Рабочая:</b><br>' +
                  sugg.working.map(function(e){return esc(e.key)+' = '+esc(e.value)}).join('<br>') + '</div>';
              }
              if (sugg.long_term && sugg.long_term.length) {
                div.innerHTML += '<div style="font-size:12px;margin-bottom:4px;"><b>Долговременная:</b><br>' +
                  sugg.long_term.map(function(e){return esc(e.key)+' = '+esc(e.value)}).join('<br>') + '</div>';
              }
              div.innerHTML += '<div style="display:flex;gap:6px;margin-top:6px;">' +
                '<button id="btn-accept-mem" class="btn-outline" style="flex:1;font-size:11px;">Принять все</button>' +
                '<button id="btn-reject-mem" class="btn-outline" style="flex:1;font-size:11px;">Отклонить</button></div>';
              document.getElementById('btn-accept-mem').addEventListener('click', function(){
                if (sugg.working) sugg.working.forEach(function(e){ AGENT.memory.working.push(e); });
                if (sugg.long_term) sugg.long_term.forEach(function(e){ AGENT.memory.long_term.push(e); });
                renderMemoryPanel(); saveMemoryToServer();
                div.innerHTML = '<span style="font-size:12px;color:#3ddc84;">Принято.</span>';
              });
              document.getElementById('btn-reject-mem').addEventListener('click', function(){
                div.innerHTML = '<span style="font-size:12px;color:var(--muted);">Отклонено.</span>';
              });
            }
          }
        }

        appendMessage('assistant', res.text, res);
        updateFactsUI();
        updateBranchUI();
        saveStateToServer();
      } else {
        appendMessage('assistant', res ? res.error : 'Ошибка', { status: 'error' });
      }
    }).catch(function(e){
      var ln = document.getElementById('load-indicator'); if (ln) ln.remove();
      appendMessage('assistant', String(e.message || e), { status: 'error' });
    }).finally(function(){ setBusy(false); });
  }

  function boot() {
    cacheElements();
    els.send.addEventListener('click', send);
    els.input.addEventListener('input', function(){ els.send.disabled = busy || !els.input.value.trim(); });
    els.input.addEventListener('keydown', function(e){
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    fetch('/api/config').then(function(r){ return r.json(); }).then(function(config){
      CATALOG = config.catalog || {};
      initUI();
      initSettings();
      loadStateFromServer().then(function(){ els.note.textContent = 'готово'; });
    }).catch(function(e){ els.note.textContent = 'Ошибка: ' + e.message; });
  }
  boot();
})();