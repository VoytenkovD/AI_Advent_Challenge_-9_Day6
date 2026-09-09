(function () {
  'use strict';
  let CATALOG = {};
  let busy = false;
  
  // Состояние единственного агента
  let AGENT = {
    history: [], 
    total_prompt: 0, 
    total_comp: 0,
    config: { 
      provider: 'ai-public', 
      model: 'deepseek-ai/deepseek-v4-pro-0813', 
      systemPromptPreset: 'assistant', 
      temperature: 0.7, 
      topP: 1.0, 
      frequencyPenalty: 0.0, 
      presencePenalty: 0.0, 
      maxTokens: 4000, 
      responseFormat: 'text', 
      maxWords: 0, 
      historyDepth: 5, 
      maxInputChars: 2000 
    }
  };

  const els = {
    note: document.getElementById('app-note'),
    history: document.getElementById('chat-history'),
    input: document.getElementById('input'),
    send: document.getElementById('send'),
    sidebar: document.getElementById('sidebar'),
    btnSettings: document.getElementById('btn-settings'),
    btnStats: document.getElementById('btn-stats'),
    btnClear: document.getElementById('btn-clear'),
    modalStats: document.getElementById('modal-stats'),
    btnCloseStats: document.getElementById('btn-stats-close'),
    
    // Настройки
    selModel: document.getElementById('set-model'),
    preset: document.getElementById('set-preset'),
    temp: document.getElementById('set-temp'),
    topp: document.getElementById('set-topp'),
    freq: document.getElementById('set-freq'),
    pres: document.getElementById('set-pres'),
    maxt: document.getElementById('set-maxt'),
    format: document.getElementById('set-format'),
    words: document.getElementById('set-words'),
    hist: document.getElementById('set-hist'),
    chars: document.getElementById('set-chars'),
  };

  function el(tag, cls, txt) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (txt !== undefined) node.textContent = txt;
    return node;
  }

  // Сохранение/загрузка истории на сервер
  function saveHistoryToServer() {
    fetch('/api/history', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ history: AGENT.history }) }).catch(function() {});
  }
  function clearHistoryOnServer() {
    fetch('/api/history', { method: 'DELETE' }).catch(function() {});
  }
  function loadHistoryFromServer() {
    return fetch('/api/history').then(function(r) { return r.json(); }).then(function(data) {
      if (Array.isArray(data.history) && data.history.length > 0) {
        AGENT.history = data.history;
        els.history.innerHTML = '';
        data.history.forEach(function(msg) { appendMessage(msg.role, msg.content); });
      }
    }).catch(function() {});
  }

  function initUI() {
    els.btnSettings.addEventListener('click', () => {
      els.sidebar.style.display = els.sidebar.style.display === 'none' ? 'block' : 'none';
    });
    els.btnStats.addEventListener('click', updateStats);
    els.btnCloseStats.addEventListener('click', () => els.modalStats.style.display = 'none');
    els.btnClear.addEventListener('click', () => {
      AGENT.history = [];
      els.history.innerHTML = '<div class="msg-bot"><div class="md"><p>История очищена. Я готов к новому разговору!</p></div></div>';
      clearHistoryOnServer();
    });
  }

  function initSettings() {
    for (const [provider, models] of Object.entries(CATALOG)) {
      const optgroup = el('optgroup');
      optgroup.label = provider;
      models.forEach(m => {
        const opt = el('option', null, m);
        opt.value = `${provider}:${m}`;
        optgroup.appendChild(opt);
      });
      els.selModel.appendChild(optgroup);
    }

    function loadSettings() {
      // Пытаемся выставить дефолтную модель. Если её нет — берём первую из списка.
      let defaultVal = `${AGENT.config.provider}:${AGENT.config.model}`;
      if (!Array.from(els.selModel.options).some(o => o.value === defaultVal)) {
          defaultVal = els.selModel.options[0].value;
          const [p, m] = defaultVal.split(':');
          AGENT.config.provider = p;
          AGENT.config.model = m;
      }
      els.selModel.value = defaultVal;

      els.preset.value = AGENT.config.systemPromptPreset;
      els.temp.value = AGENT.config.temperature;
      els.topp.value = AGENT.config.topP;
      els.freq.value = AGENT.config.frequencyPenalty;
      els.pres.value = AGENT.config.presencePenalty;
      els.maxt.value = AGENT.config.maxTokens;
      els.format.value = AGENT.config.responseFormat;
      els.words.value = AGENT.config.maxWords;
      els.hist.value = AGENT.config.historyDepth;
      els.chars.value = AGENT.config.maxInputChars;

      document.getElementById('val-temp').textContent = AGENT.config.temperature;
      document.getElementById('val-topp').textContent = AGENT.config.topP;
      document.getElementById('val-freq').textContent = AGENT.config.frequencyPenalty;
      document.getElementById('val-pres').textContent = AGENT.config.presencePenalty;
    }

    function saveSettings() {
      const [prov, mod] = els.selModel.value.split(':');
      AGENT.config.provider = prov;
      AGENT.config.model = mod;
      AGENT.config.systemPromptPreset = els.preset.value;
      AGENT.config.temperature = parseFloat(els.temp.value);
      AGENT.config.topP = parseFloat(els.topp.value);
      AGENT.config.frequencyPenalty = parseFloat(els.freq.value);
      AGENT.config.presencePenalty = parseFloat(els.pres.value);
      AGENT.config.maxTokens = parseInt(els.maxt.value, 10);
      AGENT.config.responseFormat = els.format.value;
      AGENT.config.maxWords = parseInt(els.words.value, 10);
      AGENT.config.historyDepth = parseInt(els.hist.value, 10);
      AGENT.config.maxInputChars = parseInt(els.chars.value, 10);

      document.getElementById('val-temp').textContent = AGENT.config.temperature;
      document.getElementById('val-topp').textContent = AGENT.config.topP;
      document.getElementById('val-freq').textContent = AGENT.config.frequencyPenalty;
      document.getElementById('val-pres').textContent = AGENT.config.presencePenalty;
    }

    els.selModel.addEventListener('change', saveSettings);
    ['preset', 'temp', 'topp', 'freq', 'pres', 'maxt', 'format', 'words', 'hist', 'chars'].forEach(f => {
      els[f].addEventListener('input', saveSettings);
    });

    loadSettings();
  }

  function appendMessage(role, content, metaData = null) {
    const msgDiv = el('div', role === 'user' ? 'msg-user' : 'msg-bot');
    
    if (role === 'user') {
      msgDiv.textContent = content;
    } else {
      if (metaData && metaData.status === 'error') {
        msgDiv.appendChild(el('div', 'answer--empty', content));
      } else {
        msgDiv.appendChild(window.MD.render(content));
        if (metaData) {
          const m = el('div', 'meta');
          m.appendChild(el('span', null, (metaData.latency_ms / 1000).toFixed(1) + ' с'));
          const u = metaData.usage || {};
          m.appendChild(el('span', null, (u.total_tokens || 0) + ' tok'));
          if (metaData.finish_reason && metaData.finish_reason !== 'stop') {
            m.appendChild(el('span', 'meta__warn', 'finish: ' + metaData.finish_reason));
          }
          msgDiv.appendChild(m);
        }
      }
    }
    els.history.appendChild(msgDiv);
    els.history.scrollTop = els.history.scrollHeight;
    return msgDiv;
  }

  function updateStats() {
    document.getElementById('t-p').textContent = AGENT.total_prompt;
    document.getElementById('t-c').textContent = AGENT.total_comp;
    document.getElementById('t-t').textContent = AGENT.total_prompt + AGENT.total_comp;
    els.modalStats.style.display = 'flex';
  }

  function setBusy(val) {
    busy = val;
    els.input.disabled = val;
    els.send.disabled = val || !els.input.value.trim();
    els.send.textContent = val ? 'Работаю…' : 'Отправить';
  }

  async function send() {
    const question = els.input.value.trim();
    if (!question || busy) return;

    els.input.value = '';
    setBusy(true);

    appendMessage('user', question);
    
    const loadDiv = el('div', 'msg-bot');
    loadDiv.id = 'load-indicator';
    const s = el('span', 'muted', 'думаю');
    s.appendChild(el('span', 'cursor'));
    loadDiv.appendChild(s);
    els.history.appendChild(loadDiv);
    els.history.scrollTop = els.history.scrollHeight;

    const reqData = { 
      question: question, 
      agent: { config: AGENT.config, history: AGENT.history } 
    };

    try {
      const response = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reqData),
      });
      const data = await response.json();
      
      const loadNode = document.getElementById('load-indicator');
      if (loadNode) loadNode.remove();

      const res = data.result;
      if (res && res.status === 'done') {
        AGENT.history.push({ role: 'user', content: question });
        AGENT.history.push({ role: 'assistant', content: res.text });
        
        const u = res.usage || {};
        AGENT.total_prompt += u.prompt_tokens || 0;
        AGENT.total_comp += u.completion_tokens || 0;
        
        appendMessage('assistant', res.text, res);
        saveHistoryToServer();
      } else {
        appendMessage('assistant', res ? res.error : 'Ошибка связи', { status: 'error' });
      }
    } catch (e) {
      const loadNode = document.getElementById('load-indicator');
      if (loadNode) loadNode.remove();
      appendMessage('assistant', String(e.message || e), { status: 'error' });
    } finally {
      setBusy(false);
    }
  }

  els.send.addEventListener('click', send);
  els.input.addEventListener('input', () => { els.send.disabled = busy || !els.input.value.trim(); });
  els.input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  fetch('/api/config').then(r => r.json()).then(config => {
    CATALOG = config.catalog;
    initUI();
    initSettings();
    loadHistoryFromServer().then(function() {
      els.note.textContent = 'настройки загружены';
    });
  }).catch(e => {
    els.note.textContent = 'Ошибка загрузки конфигурации: ' + e.message;
  });
})();
