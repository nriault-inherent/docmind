import { consumeChatStream } from './stream.mjs';

const $ = id => document.getElementById(id);
const state = {
  csrf: '', identity: null, settings: {}, ollama: {}, documents: [], messages: [],
  busy: false, importing: false, selectedSources: [], catalog: null, modelRequest: 0, documentRequest: 0,
  topK: 5, cutoff: .2, controller: null,
  projectId: '', projects: [], audioBusy: false, audioRequest: 0, ttsModel: 'fishaudio-s2-pro-8bit-mlx',
  conversationId: null, conversations: [], conversationRequest: 0, messageRequest: 0,
  projectRequest: 0, workspaceRequest: 0, loadingMessages: false, loadingConversations: false, managing: false,
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

function announce(message) { $('announcement').textContent = message; }
function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').hidden = !message;
  $('notice').classList.toggle('is-error', error);
}

async function api(path, options = {}) {
  const workspace = state.workspaceRequest;
  const headers = new Headers(options.headers);
  if (state.csrf) headers.set('X-CSRF-Token', state.csrf);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response;
  try { response = await fetch(path, { ...options, headers, credentials: 'same-origin' }); }
  catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('La connexion à DocMind est interrompue. Vérifiez que l’application est lancée.');
  }
  if (!response.ok) {
    let detail = 'Cette opération a échoué. Réessayez.';
    try { detail = (await response.json()).detail || detail; } catch { /* Réponse proxy sans JSON. */ }
    if (response.status === 401 && path !== '/api/login' && workspace === state.workspaceRequest) showLogin(detail);
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response;
}

function showLogin(message = '') {
  state.workspaceRequest++;
  state.projectRequest++;
  state.conversationRequest++;
  state.messageRequest++;
  state.conversationId = null;
  state.conversations = [];
  state.loadingMessages = false;
  state.loadingConversations = false;
  state.controller?.abort();
  state.controller = null;
  state.busy = false;
  state.importing = false;
  state.audioBusy = false;
  state.managing = false;
  state.modelRequest++;
  state.documentRequest++;
  state.audioRequest++;
  state.projectId = '';
  state.projects = [];
  renderAudioHistory([]);
  $('audio-status').hidden = true;
  $('project-select').replaceChildren();
  $('upload-results').replaceChildren();
  $('upload-status').hidden = true;
  state.csrf = '';
  state.identity = null;
  state.settings = {};
  state.providerDrafts = {};
  state.formProtocol = '';
  state.messages = [];
  state.documents = [];
  state.selectedSources = [];
  $('api-key').value = '';
  $('embed-api-key').value = '';
  $('workspace').hidden = true;
  $('login-view').hidden = false;
  $('settings-panel').hidden = true;
  $('settings-open').hidden = true;
  $('account-button').hidden = true;
  $('login-error').textContent = message;
  $('login-error').hidden = !message;
  renderMessages([]);
  renderSources([]);
}

function option(select, value, label = value) {
  const node = element('option', '', label);
  node.value = value;
  select.append(node);
}

function fillModels(select, models, preferred) {
  select.replaceChildren();
  for (const model of models) if (model) option(select, model);
  if (preferred && !models.includes(preferred)) option(select, preferred, `${preferred} · identifiant saisi`);
  if (!models.length && !preferred) option(select, '', 'Saisissez un identifiant manuellement');
  select.value = preferred || models[0] || '';
}

function settingsFromForm() {
  const protocol = $('protocol').value;
  const settings = {
    protocol, base_url: $('base-url').value.trim(), llm_model: $('llm-model-manual').value.trim() || $('llm-model').value,
    embed_model: $('embed-model-manual').value.trim() || $('embed-model').value, max_tokens: Number($('max-tokens').value),
  };
  settings.embed_base_url = $('embed-base-url').value.trim();
  settings.embed_protocol = $('embed-protocol').value || '';
  const options = $('llm-options').value.trim();
  if (options) {
    try {
      settings.llm_options = JSON.parse(options);
      if (!settings.llm_options || Array.isArray(settings.llm_options) || typeof settings.llm_options !== 'object') throw new Error();
    } catch { throw new Error('Les options de génération doivent être un objet JSON valide.'); }
  }
  // Un champ vide laisse le serveur utiliser sa clé d'environnement sans la divulguer.
  if ($('api-key').value) settings.api_key = $('api-key').value;
  if ($('embed-api-key').value) settings.embed_api_key = $('embed-api-key').value;
  settings.project_id = state.projectId || null;
  return settings;
}

function setBusyControls() {
  const locked = state.busy || state.importing || state.audioBusy || state.managing;
  $('send-question').disabled = state.busy || state.importing || !$('question').value.trim();
  $('clear-chat').disabled = state.busy;
  $('add-documents').disabled = state.importing || state.busy;
  $('dropzone').disabled = state.importing || state.busy;
  $('apply-settings').disabled = state.busy || state.importing;
  document.querySelectorAll('.delete-action').forEach(button => { button.disabled = state.importing || state.busy; });
  document.querySelectorAll('.retry-button').forEach(button => { button.disabled = state.busy; });
  if (state.audioBusy) {
    ['send-question', 'clear-chat', 'add-documents', 'dropzone', 'apply-settings'].forEach(id => { $(id).disabled = true; });
    document.querySelectorAll('.delete-action, .retry-button').forEach(button => { button.disabled = true; });
  }
  $('project-select').disabled = locked;
  $('create-project').disabled = locked;
  const unavailable = !state.projectId || state.projects.find(project => project.id === state.projectId)?.deleting;
  const loading = state.loadingMessages || state.loadingConversations;
  ['send-question', 'clear-chat', 'add-documents', 'dropzone', 'apply-settings'].forEach(id => {
    $(id).disabled ||= locked || Boolean(unavailable) || (['send-question', 'clear-chat'].includes(id) && loading);
  });
  $('rename-project').disabled = locked || Boolean(unavailable);
  $('delete-project-confirm').disabled = locked || !state.projectId;
  $('conversation-select').disabled = locked || state.loadingConversations || !state.conversations.length;
  document.querySelectorAll('.delete-action, .retry-button').forEach(button => { button.disabled ||= locked || Boolean(unavailable); });
  $('generate-audio').disabled = locked || !state.projectId || !state.documents.length || state.settings.protocol === 'ollama';
  $('audio-detail').disabled = locked;
  $('audio-hint').textContent = !state.projectId ? 'Créez ou sélectionnez un projet pour écouter ses documents.' : state.settings.protocol === 'ollama' ? 'Sélectionnez oMLX dans les réglages pour générer l’audio.' : !state.documents.length ? 'Ajoutez des documents à ce projet pour générer un résumé.' : 'Tous les documents du projet seront résumés. La génération peut prendre plusieurs minutes.';
}

async function enterWorkspace() {
  state.workspaceRequest++;
  const session = await (await api('/api/session')).json();
  state.csrf = session.csrf_token;
  state.identity = session.username;
  state.settings = { ...session.settings };
  delete state.settings.has_api_key;
  delete state.settings.has_embed_api_key;
  state.ollama = session.ollama_settings || {};
  state.formProtocol = state.settings.protocol;
  state.providerDrafts = {};
  $('llm-model-manual').value = '';
  $('embed-model-manual').value = '';
  $('protocol').value = state.settings.protocol;
  $('base-url').value = state.settings.base_url;
  $('embed-base-url').value = state.settings.embed_base_url || '';
  $('embed-protocol').value = state.settings.embed_protocol || '';
  $('llm-options').value = JSON.stringify(state.settings.llm_options || {});
  $('api-key').placeholder = session.settings.has_api_key ? 'Clé du serveur configurée, remplacement facultatif' : 'Si votre serveur en demande une';
  $('max-tokens').value = state.settings.max_tokens;
  fillModels($('llm-model'), [state.settings.llm_model], state.settings.llm_model);
  fillModels($('embed-model'), [state.settings.embed_model], state.settings.embed_model);
  $('account-button').textContent = session.name.split(/\s+/).map(word => word[0]).slice(0, 2).join('').toUpperCase();
  $('account-button').title = `${session.name} · Se déconnecter`;
  $('account-button').setAttribute('aria-label', `${session.name}, se déconnecter`);
  $('account-button').hidden = false;
  $('settings-open').hidden = false;
  $('login-view').hidden = true;
  $('workspace').hidden = false;
  updateOutputs();
  setView('chat');
  await loadProjects();
  await refreshModels(true);
  if (state.identity) await selectProject(state.projectId);
}

async function changeProtocol() {
  const protocol = $('protocol').value;
  const previous = state.formProtocol || state.settings.protocol;
  const kind = value => value === 'ollama' ? 'ollama' : 'api';
  state.providerDrafts ||= {};
  if (kind(previous) !== kind(protocol)) {
    const fields = ['base-url', 'api-key', 'llm-model', 'embed-model', 'llm-model-manual', 'embed-model-manual', 'embed-base-url', 'embed-protocol', 'embed-api-key', 'llm-options'];
    state.providerDrafts[kind(previous)] = Object.fromEntries(fields.map(id => [id, $(id).value]));
    const defaults = protocol === 'ollama' ? state.ollama : {};
    const draft = state.providerDrafts[kind(protocol)] || {
      'base-url': defaults.base_url || 'http://127.0.0.1:11435/v1',
      'llm-model': defaults.llm_model || '', 'embed-model': defaults.embed_model || '',
      'llm-options': '{}',
    };
    for (const id of fields) if (!['llm-model', 'embed-model'].includes(id)) $(id).value = draft[id] || '';
    fillModels($('llm-model'), [], draft['llm-model']);
    fillModels($('embed-model'), [], draft['embed-model']);
    state.catalog = {chat: [], embedding: [], tts: []};
  }
  state.formProtocol = protocol;
  await refreshModels();
}

async function refreshModels(applyToActive = false) {
  const generation = ++state.modelRequest;
  let draft;
  try { draft = settingsFromForm(); }
  catch (error) { $('models-status').textContent = error.message; return; }
  $('omlx-settings').hidden = false;
  $('max-tokens').disabled = false;
  updateOutputs();
  $('base-url').required = true;
  $('refresh-models').disabled = true;
  $('models-status').classList.remove('is-error');
  $('models-status').textContent = 'Lecture des modèles disponibles…';
  try {
    const catalog = await (await api('/api/models', { method: 'POST', body: JSON.stringify(draft) })).json();
    if (generation !== state.modelRequest || !state.identity) return;
    state.catalog = catalog;
    $('tts-models').replaceChildren();
    for (const model of catalog.tts || []) option($('tts-models'), model);
    fillModels($('llm-model'), catalog.chat, draft.llm_model);
    fillModels($('embed-model'), catalog.embedding, draft.embed_model);
    if (!catalog.chat.length || !catalog.embedding.length) {
      $('models-status').textContent = 'Saisissez les identifiants des modèles si le catalogue ne décrit pas leurs capacités.';
    } else {
      $('models-status').textContent = 'Le catalogue est prêt. Vous pouvez aussi saisir un identifiant de modèle.';
    }
    if (applyToActive) {
      state.settings = settingsFromForm();
      $('model-label').textContent = state.settings.llm_model;
    }
  } catch (error) {
    if (generation !== state.modelRequest) return;
    $('models-status').textContent = error.message;
    $('models-status').classList.add('is-error');
    if (applyToActive && state.identity) {
      if (draft.llm_model && draft.embed_model) {
        state.settings = draft;
        $('model-label').textContent = draft.llm_model;
      } else {
        notice('Catalogue indisponible : vérifiez l’adresse ou saisissez les identifiants des modèles dans les réglages.', true);
        $('model-label').textContent = 'Modèles à sélectionner';
      }
    }
  } finally {
    if (generation === state.modelRequest) $('refresh-models').disabled = false;
  }
}

async function loadDocuments() {
  const generation = ++state.documentRequest;
  const project = state.projectId;
  const settings = JSON.stringify({ ...state.settings, project_id: project || null });
  if (!project) { renderDocuments([]); return; }
  $('refresh-documents').disabled = true;
  try {
    const result = await (await api('/api/documents/list', { method: 'POST', body: settings })).json();
    if (!state.identity || generation !== state.documentRequest || project !== state.projectId || settings !== JSON.stringify({ ...state.settings, project_id: state.projectId || null })) return;
    state.documents = result.documents;
    renderDocuments(state.documents);
  } catch (error) {
    if (state.identity && generation === state.documentRequest) notice(error.message, true);
  } finally {
    if (generation === state.documentRequest) $('refresh-documents').disabled = false;
  }
}

function renderDocuments(documents) {
  $('document-list').replaceChildren();
  $('document-count').textContent = documents.length;
  $('library-empty').hidden = documents.length > 0;
  $('document-list').hidden = !documents.length;
  $('context-count').textContent = documents.length ? `${documents.length} document${documents.length > 1 ? 's' : ''} dans la bibliothèque` : 'Vos documents comme point de départ';
  $('welcome-hint').textContent = documents.length ? 'Tout est prêt. La prochaine question vous appartient.' : 'Commencez par ajouter vos documents à la bibliothèque.';
  for (const doc of documents) {
    const row = element('div', 'document-row');
    const main = element('div', 'document-main');
    const extension = doc.source.split('.').at(-1).toLowerCase();
    const type = element('span', 'file-type', extension.slice(0, 4).toUpperCase());
    if (['pdf', 'md', 'markdown'].includes(extension)) type.classList.add(`type-${extension}`);
    const info = element('div', 'document-info');
    const name = element('div', 'document-name', doc.source);
    info.append(name, element('div', 'document-meta', `${doc.chunk_count} fragments${doc.page_count ? ` · ${doc.page_count} pages` : ''}`));
    if (doc.added_at) {
      const date = new Date(doc.added_at);
      if (!Number.isNaN(date.getTime())) info.append(element('div', 'document-meta', date.toLocaleDateString('fr-FR')));
    }
    const remove = element('button', 'icon-button delete-action');
    remove.setAttribute('aria-label', `Supprimer ${doc.source}`);
    remove.append(icon('trash'));
    main.append(type, info, remove);
    row.append(main);
    remove.addEventListener('click', () => {
      remove.hidden = true;
      const confirm = element('div', 'delete-confirm');
      const yes = element('button', 'button danger delete-action', 'Supprimer');
      const no = element('button', 'button quiet', 'Annuler');
      confirm.append(element('p', '', 'Supprimer ce document de l’index ?'), yes, no);
      row.append(confirm);
      yes.focus();
      no.onclick = () => { confirm.remove(); remove.hidden = false; remove.focus(); };
      yes.onclick = async () => {
        yes.disabled = true;
        no.disabled = true;
        try {
          await api('/api/documents/delete', { method: 'POST', body: JSON.stringify({ source: doc.source, settings: state.settings }) });
          announce(`${doc.source} a été supprimé.`);
          await loadDocuments();
          $('add-documents').focus();
        } catch (error) {
          notice(error.message, true);
          confirm.remove(); remove.hidden = false; remove.focus();
        }
      };
    });
    $('document-list').append(row);
  }
  setBusyControls();
}

async function uploadFiles(files) {
  if (!files.length || !state.projectId || state.importing || state.busy || state.audioBusy || state.managing) return;
  const project = state.projectId;
  const workspace = state.workspaceRequest;
  const active = () => state.identity && workspace === state.workspaceRequest && project === state.projectId;
  const settings = { ...state.settings, project_id: project };
  state.importing = true;
  setBusyControls();
  $('upload-results').replaceChildren();
  $('upload-status').hidden = false;
  let succeeded = 0;
  try {
    for (const [index, file] of [...files].entries()) {
      if (!active()) break;
      $('upload-status').textContent = `Indexation ${index + 1}/${files.length} · ${file.name}`;
      const form = new FormData();
      form.append('files', file);
      form.append('settings', JSON.stringify(settings));
      try {
        const result = await (await api('/api/documents/upload', { method: 'POST', body: form })).json();
        if (!active()) break;
        const row = result.results[0];
        if (row.success) succeeded++;
        $('upload-results').append(element('li', row.success ? '' : 'failed', row.success ? `✓ ${row.name} · ${row.fragments} fragments` : `${row.name} : ${row.error}`));
      } catch (error) {
        if (!active()) break;
        $('upload-results').append(element('li', 'failed', `${file.name} : ${error.message}`));
      }
    }
    if (active()) {
      $('upload-status').textContent = `${succeeded}/${files.length} document${files.length > 1 ? 's' : ''} indexé${files.length > 1 ? 's' : ''}.`;
      announce($('upload-status').textContent);
      await loadDocuments();
    }
  } finally {
    if (workspace === state.workspaceRequest) {
      state.importing = false;
      $('file-input').value = '';
      setBusyControls();
    }
  }
}

function renderSources(sources) {
  state.selectedSources = sources;
  $('source-count').textContent = sources.length;
  $('sources-empty').hidden = sources.length > 0;
  $('source-list').hidden = !sources.length;
  $('source-list').replaceChildren();
  for (const [index, source] of sources.entries()) {
    const item = element('li', 'source-item');
    const heading = element('div', 'source-heading');
    heading.append(element('span', 'source-number', String(index + 1).padStart(2, '0')), element('span', 'source-document', source.document));
    item.append(heading, element('p', 'source-excerpt', source.excerpt));
    const meta = [];
    if (source.page !== null && source.page !== undefined) meta.push(`Page ${source.page}`);
    if (typeof source.score === 'number') meta.push(`Pertinence ${source.score.toLocaleString('fr-FR', { maximumFractionDigits: 2 })}`);
    if (meta.length) item.append(element('span', 'source-meta', meta.join(' · ')));
    $('source-list').append(item);
  }
}

function messageElement(message) {
  const node = element('article', `message ${message.role}`);
  const label = element('div', 'message-label');
  if (message.role === 'assistant') label.append(icon('spark'));
  label.append(element('span', '', message.role === 'user' ? 'Vous' : 'DocMind'));
  const body = element('div', 'message-body', message.content);
  node.append(label, body);
  if (message.role === 'assistant') {
    const status = element('div', 'message-state');
    if (message.pending) status.textContent = 'Recherche des passages utiles…';
    else if (message.error) { status.textContent = message.error; status.classList.add('interrupted'); }
    node.append(status);
    if (message.sources?.length) {
      const button = element('button', 'button source-button', `${message.sources.length} source${message.sources.length > 1 ? 's' : ''}`);
      button.prepend(icon('file'));
      button.onclick = () => { renderSources(message.sources); setView('sources'); $('sources-title').tabIndex = -1; $('sources-title').focus(); };
      node.append(button);
    }
    if (message.error && message.question) {
      const retry = element('button', 'button quiet retry-button', 'Réessayer');
      retry.onclick = () => { $('question').value = message.question; setView('chat'); $('question').focus(); setBusyControls(); };
      node.append(retry);
    }
  }
  return node;
}

function renderMessages(messages) {
  $('messages').replaceChildren(...messages.map(messageElement));
  $('welcome').hidden = messages.length > 0;
}

function scrollConversation() { $('conversation').scrollTop = $('conversation').scrollHeight; }

async function sendQuestion(event) {
  event.preventDefault();
  const submittedValue = $('question').value;
  const question = submittedValue.trim();
  if (!question || !state.projectId || state.busy || state.importing || state.audioBusy || state.managing || state.loadingMessages || state.loadingConversations) return;
  const project = state.projectId;
  const workspace = state.workspaceRequest;
  let conversation = state.conversationId;
  const user = { role: 'user', content: question, complete: false };
  const assistant = { role: 'assistant', content: '', sources: [], pending: true, question, complete: false };
  state.messages.push(user, assistant);
  state.busy = true;
  state.controller = new AbortController();
  notice('');
  renderSources([]);
  renderMessages(state.messages);
  setBusyControls();
  scrollConversation();
  announce('Recherche des passages utiles.');
  try {
    if (!conversation) {
      const created = await (await api(`/api/projects/${project}/conversations`, { method: 'POST' })).json();
      if (workspace !== state.workspaceRequest || !state.identity || project !== state.projectId) return;
      conversation = created.id;
      state.conversationId = conversation;
      state.conversations.unshift(created);
      renderConversations();
    }
    const active = () => state.identity && workspace === state.workspaceRequest && project === state.projectId && conversation === state.conversationId;
    const response = await api('/api/chat', {
      method: 'POST', signal: state.controller.signal,
      body: JSON.stringify({ question, conversation_id: conversation, settings: { ...state.settings, project_id: project }, top_k: state.topK, similarity_cutoff: state.cutoff }),
    });
    if (!active()) return;
    const article = $('messages').lastElementChild;
    const body = article.querySelector('.message-body');
    const status = article.querySelector('.message-state');
    await consumeChatStream(response, event => {
      if (!active()) return;
      if (event.type === 'sources') {
        assistant.sources = event.sources;
        renderSources(event.sources);
        status.textContent = 'Rédaction de la réponse…';
        announce('Rédaction de la réponse.');
      } else if (event.type === 'chunk') {
        const nearBottom = $('conversation').scrollHeight - $('conversation').scrollTop - $('conversation').clientHeight < 100;
        assistant.content += event.text;
        body.textContent = assistant.content;
        if (nearBottom) scrollConversation();
      } else if (event.type === 'done') {
        user.complete = true;
        assistant.complete = true;
      }
    });
    if (!active()) return;
    if ($('question').value === submittedValue) $('question').value = '';
    const row = state.conversations.find(item => item.id === conversation);
    if (row?.title === 'Nouvelle discussion') row.title = question.slice(0, 80);
    renderConversations();
    announce('Réponse terminée. Les sources sont disponibles.');
  } catch (error) {
    assistant.error = error.name === 'AbortError' ? 'Réponse interrompue.' : error.message;
    if (state.identity && workspace === state.workspaceRequest && project === state.projectId) announce(assistant.error);
  } finally {
    assistant.pending = false;
    if (workspace !== state.workspaceRequest) return;
    state.busy = false;
    state.controller = null;
    if (state.identity && workspace === state.workspaceRequest && project === state.projectId && conversation === state.conversationId) {
      renderMessages(state.messages);
      if (assistant.complete) scrollConversation();
    }
    setBusyControls();
  }
}

function setView(view) {
  const mode = view === 'audio' ? 'audio' : view === 'library' ? ($('workspace').dataset.mode || 'chat') : 'chat';
  $('workspace').dataset.mode = mode;
  $('workspace').dataset.view = view;
  $('chat-content').hidden = mode === 'audio';
  $('audio-panel').hidden = mode !== 'audio';
  $('chat-panel').setAttribute('aria-labelledby', mode === 'audio' ? 'audio-title' : 'chat-title');
  const destinations = { chat: ['question', 'Aller à la question'], audio: ['audio-title', 'Aller aux résumés audio'], library: ['library-title', 'Aller aux documents'], sources: ['sources-title', 'Aller aux sources'] };
  const [target, label] = destinations[view] || destinations.chat;
  $('skip-link').href = `#${target}`;
  $('skip-link').textContent = label;
  if (view !== 'audio') document.querySelectorAll('#audio-history audio').forEach(player => player.pause());
  document.querySelectorAll('.mode-button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
  document.querySelectorAll('.view-button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === view)));
}

function toggleSettings(open) {
  $('settings-panel').hidden = !open;
  $('settings-open').setAttribute('aria-expanded', String(open));
  if (open) $('settings-close').focus();
  else $('settings-open').focus();
}

function updateOutputs() {
  $('tokens-output').textContent = $('protocol').value === 'ollama' ? 'Gérée par Ollama' : `${$('max-tokens').value} tokens`;
  $('k-output').textContent = $('top-k').value;
  $('cutoff-output').textContent = Number($('cutoff').value).toLocaleString('fr-FR', { minimumFractionDigits: 2 });
}

async function loadProjects() {
  const identity = state.identity;
  const workspace = state.workspaceRequest;
  const generation = ++state.projectRequest;
  const result = await (await api('/api/projects')).json();
  if (!state.identity || identity !== state.identity || workspace !== state.workspaceRequest || generation !== state.projectRequest) return;
  state.projects = [...(result.general ? [result.general] : []), ...result.projects];
  if (!state.projects.some(project => project.id === state.projectId)) state.projectId = state.projects[0]?.id || '';
  $('project-select').replaceChildren();
  if (!state.projects.length) option($('project-select'), '', 'Créez votre premier projet');
  for (const project of state.projects) option($('project-select'), project.id, project.deleting ? `${project.name} · suppression à terminer` : project.name);
  $('project-select').value = state.projectId;
  setBusyControls();
}

async function selectProject(projectId) {
  if (state.busy || state.importing || state.audioBusy || state.managing) return;
  state.projectId = projectId;
  state.settings = { ...state.settings, project_id: projectId || null };
  $('project-select').value = projectId;
  state.documentRequest++;
  state.audioRequest++;
  state.conversationRequest++;
  state.messageRequest++;
  state.conversationId = null;
  state.conversations = [];
  state.loadingMessages = false;
  state.loadingConversations = false;
  state.documents = [];
  state.messages = [];
  $('question').value = '';
  $('upload-results').replaceChildren();
  $('upload-status').hidden = true;
  renderAudioHistory([]);
  $('audio-status').hidden = true;
  renderDocuments([]);
  renderMessages([]);
  renderSources([]);
  notice('');
  const project = state.projects.find(project => project.id === projectId);
  $('chat-title').textContent = 'Discussion';
  $('rename-project-name').value = project?.name || '';
  $('delete-project-name').textContent = project?.name || '';
  $('manage-project').open = false;
  $('delete-project').open = false;
  renderConversations();
  setBusyControls();
  if (!projectId) {
    notice('Créez un projet pour importer des documents et discuter.');
    return;
  }
  if (project?.deleting) {
    notice('La suppression de ce projet est incomplète. Ouvrez « Gérer le projet » et réessayez sa suppression.', true);
    return;
  }
  await Promise.all([loadDocuments(), loadAudioHistory(), loadConversations()]);
}

function renderConversations() {
  const select = $('conversation-select');
  select.replaceChildren();
  if (!state.conversations.length) option(select, '', 'Votre première discussion');
  for (const conversation of state.conversations) option(select, conversation.id, conversation.title);
  select.value = state.conversationId || '';
}

async function loadConversations() {
  const project = state.projectId;
  const workspace = state.workspaceRequest;
  const generation = ++state.conversationRequest;
  if (!project) return;
  state.loadingConversations = true;
  setBusyControls();
  try {
    const result = await (await api(`/api/projects/${project}/conversations`)).json();
    if (!state.identity || project !== state.projectId || workspace !== state.workspaceRequest || generation !== state.conversationRequest) return;
    state.conversations = result.conversations || [];
    renderConversations();
    if (state.conversations.length) await selectConversation(state.conversations[0].id);
  } catch (error) {
    if (state.identity && project === state.projectId && generation === state.conversationRequest) notice(error.message, true);
  } finally {
    if (generation === state.conversationRequest) state.loadingConversations = false;
    setBusyControls();
  }
}

async function selectConversation(conversationId) {
  if (state.busy || state.importing || state.audioBusy || !state.projectId) return;
  const project = state.projectId;
  const workspace = state.workspaceRequest;
  const generation = ++state.messageRequest;
  state.conversationId = conversationId;
  state.messages = [];
  $('question').value = '';
  renderMessages([]);
  renderSources([]);
  renderConversations();
  state.loadingMessages = true;
  setBusyControls();
  try {
    const result = await (await api(`/api/projects/${project}/conversations/${conversationId}`)).json();
    if (!state.identity || workspace !== state.workspaceRequest || project !== state.projectId || conversationId !== state.conversationId || generation !== state.messageRequest) return;
    state.messages = result.messages.map(message => ({
      ...message, complete: message.status === 'complete', pending: false,
      error: message.role === 'assistant' && message.status !== 'complete' ? message.error || 'Réponse non terminée.' : null,
    }));
    for (let index = 1; index < state.messages.length; index++) {
      if (state.messages[index].role === 'assistant') state.messages[index].question = state.messages[index - 1].content;
    }
    renderMessages(state.messages);
    renderSources(state.messages.filter(message => message.role === 'assistant').at(-1)?.sources || []);
    scrollConversation();
  } catch (error) {
    if (state.identity && generation === state.messageRequest && project === state.projectId) notice(error.message, true);
  } finally {
    if (generation === state.messageRequest) state.loadingMessages = false;
    setBusyControls();
  }
}

async function newConversation() {
  if (!state.projectId || state.busy || state.importing || state.audioBusy || state.managing || state.loadingMessages || state.loadingConversations) return;
  const project = state.projectId;
  const workspace = state.workspaceRequest;
  state.managing = true;
  setBusyControls();
  try {
    const conversation = await (await api(`/api/projects/${project}/conversations`, { method: 'POST' })).json();
    if (!state.identity || project !== state.projectId || workspace !== state.workspaceRequest) return;
    state.conversations.unshift(conversation);
    await selectConversation(conversation.id);
    notice('Nouvelle discussion. Les précédentes restent disponibles dans la liste.');
    setView('chat');
    $('question').focus();
  } catch (error) { if (state.identity && project === state.projectId) notice(error.message, true); }
  finally { state.managing = false; setBusyControls(); }
}

function renderAudioHistory(summaries) {
  $('audio-history').replaceChildren();
  $('audio-count').textContent = summaries.length;
  $('audio-empty').hidden = summaries.length > 0;
  const labels = { brief: 'Bref', standard: 'Standard', detailed: 'Détaillé' };
  for (const summary of summaries) {
    const row = element('article', 'audio-summary');
    row.append(element('h3', '', `${labels[summary.detail] || summary.detail} · ${new Date(summary.created_at).toLocaleString('fr-FR')}`));
    const player = element('audio');
    player.controls = true;
    player.preload = 'none';
    player.src = summary.audio_url;
    player.setAttribute('aria-label', `Écouter le résumé ${labels[summary.detail] || ''}`);
    const download = element('a', 'audio-download', 'Télécharger le WAV');
    download.href = summary.audio_url;
    download.download = `resume-${summary.detail}.wav`;
    const transcript = element('details', 'audio-transcript');
    transcript.append(element('summary', '', 'Transcription et documents utilisés'));
    transcript.append(element('p', '', summary.transcript));
    transcript.append(element('p', 'field-help', `Documents : ${summary.sources.join(', ')}`));
    row.append(player, download, transcript);
    $('audio-history').append(row);
  }
}

async function loadAudioHistory() {
  const generation = ++state.audioRequest;
  const project = state.projectId;
  if (!project) return;
  try {
    const result = await (await api(`/api/projects/${project}/audio`)).json();
    if (state.identity && project === state.projectId && generation === state.audioRequest) renderAudioHistory(result.summaries);
  } catch (error) {
    if (state.identity && generation === state.audioRequest) {
      $('audio-status').hidden = false;
      $('audio-status').textContent = error.message;
    }
  }
}

async function generateAudio(event) {
  event.preventDefault();
  if (!state.projectId || state.audioBusy || state.busy || state.importing || !state.documents.length) return;
  const project = state.projectId;
  const workspace = state.workspaceRequest;
  const generation = ++state.audioRequest;
  state.audioBusy = true;
  setBusyControls();
  setView('audio');
  $('audio-panel').setAttribute('aria-busy', 'true');
  $('audio-status').hidden = false;
  $('audio-status').classList.remove('is-error');
  $('audio-status').textContent = 'Préparation du résumé et de la voix… Cela peut prendre plusieurs minutes.';
  $('generate-audio').textContent = 'Génération en cours…';
  try {
    await api(`/api/projects/${project}/audio`, { method: 'POST', body: JSON.stringify({ detail: $('audio-detail').value, settings: state.settings, tts_model: state.ttsModel }) });
    if (state.identity && generation === state.audioRequest && project === state.projectId) {
      await loadAudioHistory();
      $('audio-status').textContent = 'Le résumé est prêt à écouter.';
      announce('Le résumé audio est prêt.');
    }
  } catch (error) {
    if (state.identity && generation === state.audioRequest) {
      $('audio-status').textContent = error.message;
      $('audio-status').classList.add('is-error');
    }
  } finally {
    if (workspace !== state.workspaceRequest) return;
    state.audioBusy = false;
    $('audio-panel').setAttribute('aria-busy', 'false');
    $('generate-audio').textContent = 'Créer un résumé audio';
    setBusyControls();
  }
}

$('project-select').onchange = () => selectProject($('project-select').value);
$('conversation-select').onchange = () => selectConversation($('conversation-select').value);
$('rename-project-form').onsubmit = async event => {
  event.preventDefault();
  if (!state.projectId || state.busy || state.importing || state.audioBusy || state.managing) return;
  state.managing = true;
  const project = state.projectId;
  setBusyControls();
  try {
    const result = await (await api(`/api/projects/${project}`, { method: 'PATCH', body: JSON.stringify({ name: $('rename-project-name').value.trim() }) })).json();
    if (!state.identity || state.projectId !== project) return;
    await loadProjects();
    $('rename-project-name').value = result.name;
    $('delete-project-name').textContent = result.name;
    $('manage-project').open = false;
    announce('Projet renommé.');
  } catch (error) { if (state.identity) notice(error.message, true); }
  finally { state.managing = false; setBusyControls(); }
};
$('delete-project-confirm').onclick = async () => {
  if (!state.projectId || state.busy || state.importing || state.audioBusy || state.managing) return;
  const project = state.projectId;
  state.managing = true;
  setBusyControls();
  try {
    await api(`/api/projects/${project}`, { method: 'DELETE' });
    if (!state.identity) return;
    state.projectId = '';
    await loadProjects();
    state.managing = false;
    await selectProject(state.projectId);
    announce('Projet supprimé avec ses documents et ses discussions.');
  } catch (error) {
    if (state.identity) {
      await loadProjects().catch(() => {});
      notice(error.message, true);
    }
  } finally { state.managing = false; setBusyControls(); }
};
$('project-form').onsubmit = async event => {
  event.preventDefault();
  if (state.busy || state.importing || state.audioBusy) return;
  $('create-project').disabled = true;
  $('project-status').textContent = 'Création du projet…';
  try {
    const result = await (await api('/api/projects', { method: 'POST', body: JSON.stringify({ name: $('project-name').value.trim() }) })).json();
    if (!state.identity) return;
    await loadProjects();
    await selectProject(result.id);
    $('new-project').open = false;
    $('project-name').value = '';
    $('project-status').textContent = 'Projet créé. Ajoutez ses documents.';
  } catch (error) { $('project-status').textContent = error.message; }
  finally { setBusyControls(); }
};
$('audio-form').onsubmit = generateAudio;

$('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('login-submit').disabled = true;
  $('login-error').hidden = true;
  try {
    const result = await (await api('/api/login', { method: 'POST', body: JSON.stringify({ username: $('username').value.trim(), password: $('password').value }) })).json();
    state.csrf = result.csrf_token;
    $('password').value = '';
    await enterWorkspace();
  } catch (error) { $('login-error').textContent = error.message; $('login-error').hidden = false; }
  finally { $('login-submit').disabled = false; }
});
$('account-button').onclick = async () => {
  try { await api('/api/logout', { method: 'POST' }); showLogin(); $('username').focus(); }
  catch (error) { notice(error.message, true); }
};
$('settings-open').onclick = () => toggleSettings($('settings-panel').hidden);
$('settings-close').onclick = () => toggleSettings(false);
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  if (!$('settings-panel').hidden) toggleSettings(false);
  document.querySelectorAll('.project-menu[open]').forEach(menu => {
    menu.open = false;
    menu.querySelector('summary').focus();
  });
});
document.querySelectorAll('.project-menu').forEach(menu => menu.addEventListener('toggle', () => {
  if (menu.open) document.querySelectorAll('.project-menu').forEach(other => { if (other !== menu) other.open = false; });
}));
document.addEventListener('click', event => {
  document.querySelectorAll('.project-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
});
$('refresh-models').onclick = () => refreshModels();
$('protocol').onchange = changeProtocol;
for (const [id, category] of [['llm-model', 'chat'], ['embed-model', 'embedding']]) {
  $(id).onchange = () => { $(`${id}-manual`).value = ''; };
  $(`${id}-manual`).oninput = () => fillModels($(id), state.catalog?.[category] || [], $(`${id}-manual`).value.trim());
}
$('settings-form').onsubmit = async event => {
  event.preventDefault();
  if (state.busy || state.importing || state.audioBusy) return;
  let next;
  try { next = settingsFromForm(); }
  catch (error) { $('models-status').textContent = error.message; return; }
  const identity = settings => [settings.embed_base_url || settings.base_url, settings.embed_protocol || (settings.protocol === 'ollama' ? 'ollama' : 'openai'), settings.embed_model];
  const indexChanged = JSON.stringify(identity(next)) !== JSON.stringify(identity(state.settings));
  if (indexChanged) {
    state.documentRequest++;
    state.documents = [];
    renderDocuments([]);
  }
  state.settings = next;
  state.ttsModel = $('tts-model').value.trim() || 'fishaudio-s2-pro-8bit-mlx';
  state.topK = Number($('top-k').value);
  state.cutoff = Number($('cutoff').value);
  $('model-label').textContent = next.llm_model || 'Modèle à sélectionner';
  toggleSettings(false);
  notice(indexChanged ? 'Bibliothèque du modèle d’indexation sélectionné. Réimportez vos documents si elle est vide.' : 'Réglages appliqués.');
  await loadDocuments();
};
['max-tokens', 'top-k', 'cutoff'].forEach(id => $(id).oninput = updateOutputs);
$('add-documents').onclick = $('dropzone').onclick = () => $('file-input').click();
$('file-input').onchange = () => uploadFiles($('file-input').files);
$('refresh-documents').onclick = loadDocuments;
['dragenter', 'dragover'].forEach(name => $('dropzone').addEventListener(name, event => { event.preventDefault(); $('dropzone').classList.add('drag-over'); }));
['dragleave', 'drop'].forEach(name => $('dropzone').addEventListener(name, event => { event.preventDefault(); $('dropzone').classList.remove('drag-over'); }));
$('dropzone').addEventListener('drop', event => uploadFiles(event.dataTransfer.files));
document.querySelectorAll('.view-button').forEach(button => button.onclick = () => setView(button.dataset.view));
document.querySelectorAll('.mode-button').forEach(button => button.onclick = () => setView(button.dataset.mode));
document.querySelectorAll('[data-question]').forEach(button => button.onclick = () => { $('question').value = button.dataset.question; $('question').focus(); setBusyControls(); });
$('question').oninput = setBusyControls;
$('question').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); } };
$('chat-form').onsubmit = sendQuestion;
$('clear-chat').onclick = newConversation;

enterWorkspace().catch(error => showLogin(error.status === 401 ? '' : error.message));
