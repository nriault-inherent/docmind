import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { consumeChatStream } from '../static/stream.mjs';

// Petit DOM de test. Les fonctions de production restent exécutées telles quelles.
class Node {
  constructor() {
    this.children = []; this.value = ''; this.textContent = ''; this.className = '';
    this.dataset = {}; this.scrollHeight = 100; this.scrollTop = 0; this.clientHeight = 100;
    this.classList = {
      add: name => { this.className += ` ${name}`; },
      remove: name => { this.className = this.className.split(' ').filter(item => item !== name).join(' '); },
      toggle: (name, active) => { if (active) this.classList.add(name); else this.classList.remove(name); },
    };
  }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  get lastElementChild() { return this.children.at(-1); }
  querySelector(selector) {
    for (const child of this.children) {
      if (child.className?.split(' ').includes(selector.slice(1))) return child;
      const found = child.querySelector?.(selector);
      if (found) return found;
    }
    return null;
  }
  setAttribute() {}
  addEventListener() {}
  focus() {}
}

async function harness(fetch) {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const document = { getElementById: node, createElement: () => new Node(), createElementNS: () => new Node(), querySelectorAll: () => [], addEventListener() {} };
  const source = (await readFile(new URL('../static/app.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\n/m, '')
    .replace(/enterWorkspace\(\)\.catch\([^\n]+\);\s*$/, '');
  const context = vm.createContext({ document, fetch, consumeChatStream, Headers, FormData, AbortController, console });
  vm.runInContext(source, context);
  vm.runInContext('state.identity = "test"; state.projectId = "general"; state.conversationId = "test-conversation"; state.settings = {protocol:"openai", base_url:"http://local/v1", embed_model:"A", llm_model:"chat", project_id:"general"};', context);
  return { context, node, run: code => vm.runInContext(code, context) };
}

test('les réglages Ollama conservent l’adresse et les modèles saisis', async () => {
  const h = await harness(async () => Response.json({}));
  h.run('state.ollama = {base_url:"http://old:11434", llm_model:"old", embed_model:"old-embed"}');
  h.node('protocol').value = 'ollama';
  h.node('base-url').value = 'http://chosen:11434';
  h.node('llm-model').value = 'chosen';
  h.node('embed-model').value = 'chosen-embed';
  h.node('max-tokens').value = '2048';
  assert.equal(h.run('settingsFromForm().base_url'), 'http://chosen:11434');
  assert.equal(h.run('settingsFromForm().embed_model'), 'chosen-embed');
});

test('un catalogue indisponible conserve les identifiants manuels', async () => {
  const h = await harness(async () => Response.json({detail:'Catalogue indisponible'}, {status:404}));
  h.node('protocol').value = 'openai';
  h.node('base-url').value = 'https://gateway.example/v1';
  h.node('llm-model').value = 'manual-chat';
  h.node('embed-model').value = 'manual-embed';
  h.node('max-tokens').value = '2048';
  await h.run('refreshModels(true)');
  assert.equal(h.run('state.settings.llm_model'), 'manual-chat');
  assert.equal(h.node('model-label').textContent, 'manual-chat');
});

test('des options JSON invalides sont signalées sans appliquer les réglages', async () => {
  const h = await harness(async () => Response.json({documents:[]}));
  h.node('llm-options').value = '{invalid';
  await h.node('settings-form').onsubmit({preventDefault(){}});
  assert.match(h.node('models-status').textContent, /JSON/);
  assert.equal(h.run('state.settings.llm_model'), 'chat');
});

test('la fin de réponse conserve un nouveau brouillon saisi pendant la génération', async () => {
  let stream;
  const h = await harness(async () => new Response(new ReadableStream({ start(controller) { stream = controller; } })));
  h.node('question').value = 'Question A';
  const pending = h.run('sendQuestion({preventDefault(){}})');
  await new Promise(resolve => setImmediate(resolve));
  h.node('question').value = 'Question B';
  stream.enqueue(new TextEncoder().encode('{"type":"chunk","text":"Réponse A"}\n{"type":"done"}\n'));
  stream.close();
  await pending;
  assert.equal(h.node('question').value, 'Question B');
  assert.equal(h.run('state.messages.at(-1).content'), 'Réponse A');
});

test('une réponse de bibliothèque périmée ne remplace pas le nouvel index', async () => {
  const resolvers = [];
  const h = await harness(() => new Promise(resolve => resolvers.push(resolve)));
  const first = h.run('loadDocuments()');
  h.run('state.settings = {...state.settings, embed_model:"B"}');
  const second = h.run('loadDocuments()');
  resolvers[1](Response.json({ documents: [{ source: 'B.txt', chunk_count: 2 }] }));
  await second;
  resolvers[0](Response.json({ documents: [{ source: 'A.txt', chunk_count: 1 }] }));
  await first;
  assert.equal(h.node('document-list').children[0].querySelector('.document-name').textContent, 'B.txt');
  assert.equal(h.run('state.documents[0].source'), 'B.txt');
});

test('changer d’index retire les anciennes lignes même si le nouveau chargement échoue', async () => {
  const h = await harness(async () => Response.json({ detail: 'Index indisponible' }, { status: 502 }));
  h.run('state.documents = [{source:"A.txt", chunk_count:1}]; renderDocuments(state.documents);');
  h.node('protocol').value = 'openai';
  h.node('base-url').value = 'http://local/v1';
  h.node('llm-model').value = 'chat';
  h.node('embed-model').value = 'B';
  h.node('max-tokens').value = '2048';
  await h.node('settings-form').onsubmit({ preventDefault() {} });
  assert.equal(h.node('document-list').children.length, 0);
  assert.equal(h.run('state.documents.length'), 0);
  assert.match(h.node('notice').textContent, /Index indisponible/);
});

test('une conversation tardive d’un autre projet ne remplace ni les messages ni les sources', async () => {
  const resolvers = [];
  const h = await harness(() => new Promise(resolve => resolvers.push(resolve)));
  h.run('state.projectId = "A"');
  const first = h.run('selectConversation("conversation-A")');
  h.run('state.projectId = "B"');
  const second = h.run('selectConversation("conversation-B")');
  resolvers[1](Response.json({ messages: [{role:'assistant', content:'Réponse B', status:'complete', sources:[]}] }));
  await second;
  resolvers[0](Response.json({ messages: [{role:'assistant', content:'Secret A', status:'complete', sources:[{document:'A.txt'}]}] }));
  await first;
  assert.equal(h.run('state.messages[0].content'), 'Réponse B');
  assert.equal(h.node('source-list').children.length, 0);
});

test('une nouvelle discussion conserve la précédente dans la liste', async () => {
  const h = await harness(async (path, options) => {
    if (options?.method === 'POST') return Response.json({id:'new', title:'Nouvelle discussion'});
    return Response.json({messages:[]});
  });
  h.run('state.conversations = [{id:"old", title:"Discussion conservée"}]; state.messages = [{role:"user", content:"Ancien échange"}];');
  await h.run('newConversation()');
  assert.equal(h.run('state.conversationId'), 'new');
  assert.equal(h.run('state.conversations.some(c => c.id === "old")'), true);
  assert.equal(h.run('state.messages.length'), 0);
});

test('le chat transmet son projet et sa conversation, sans historique fourni par le navigateur', async () => {
  let payload;
  const h = await harness(async (path, options) => {
    payload = JSON.parse(options.body);
    return new Response('{"type":"chunk","text":"Réponse"}\n{"type":"done"}\n');
  });
  h.node('question').value = 'Question';
  await h.run('sendQuestion({preventDefault(){}})');
  assert.equal(payload.settings.project_id, 'general');
  assert.equal(payload.conversation_id, 'test-conversation');
  assert.equal('history' in payload, false);
});

test('changer de modèle conserve les messages de la discussion persistante', async () => {
  const h = await harness(async () => Response.json({documents:[]}));
  h.run('state.messages = [{role:"user", content:"Question conservée", complete:true}]; renderMessages(state.messages);');
  h.node('protocol').value = 'openai';
  h.node('base-url').value = 'http://local/v1';
  h.node('llm-model').value = 'autre-modele';
  h.node('embed-model').value = 'A';
  h.node('max-tokens').value = '2048';
  await h.node('settings-form').onsubmit({ preventDefault() {} });
  assert.equal(h.run('state.messages[0].content'), 'Question conservée');
  assert.equal(h.run('state.conversationId'), 'test-conversation');
});

test('un import de la session précédente ne bloque ni ne contamine la nouvelle session', async () => {
  let resolveUpload;
  const paths = [];
  const h = await harness(async (path) => {
    paths.push(path);
    if (path === '/api/documents/upload') return new Promise(resolve => { resolveUpload = resolve; });
    if (path === '/api/session') return Response.json({username:'new-user', name:'Nouvel utilisateur', csrf_token:'new', settings:{protocol:'openai', base_url:'http://local/v1', llm_model:'chat', embed_model:'A', max_tokens:2048}});
    if (path === '/api/projects') return Response.json({projects:[], general:{id:'general', name:'Général'}});
    if (path === '/api/models') return Response.json({chat:['chat'], embedding:['A']});
    if (path.endsWith('/conversations')) return Response.json({conversations:[{id:'saved', title:'Discussion sauvegardée'}]});
    if (path.endsWith('/conversations/saved')) return Response.json({messages:[]});
    if (path.endsWith('/audio')) return Response.json({summaries:[]});
    return Response.json({documents:[]});
  });
  h.context.files = [new File(['Secret'], 'old-project-secret.txt')];
  const importing = h.run('uploadFiles(files)');
  await new Promise(resolve => setImmediate(resolve));
  h.run('showLogin()');
  await h.run('enterWorkspace()');
  resolveUpload(Response.json({results:[{name:'old-project-secret.txt', success:true, fragments:1}]}));
  await importing;
  assert.equal(h.run('state.conversationId'), 'saved');
  assert.ok(paths.includes('/api/projects/general/audio'));
  assert.equal(h.node('upload-results').children.length, 0);
  assert.equal(h.run('state.importing'), false);
});

test('les opérations de l’ancienne session libèrent leurs contrôles sans toucher une nouvelle opération', async () => {
  let resolveUpload;
  const h = await harness(() => new Promise(resolve => { resolveUpload = resolve; }));
  h.context.files = [new File(['Secret'], 'old.txt')];
  const importing = h.run('uploadFiles(files)');
  await new Promise(resolve => setImmediate(resolve));
  h.run('state.audioBusy=true;state.busy=true;state.managing=true;showLogin()');
  assert.equal(h.run('state.audioBusy || state.busy || state.managing || state.importing'), false);
  h.run('state.identity="new-user";state.projectId="new-project";state.importing=true;');
  resolveUpload(Response.json({results:[{name:'old.txt', success:true, fragments:1}]}));
  await importing;
  assert.equal(h.run('state.importing'), true);
  assert.equal(h.node('upload-results').children.length, 0);
});

test('changer de projet retire documents, conversation et anciens lecteurs audio', async () => {
  const h = await harness(async path => path.includes('/audio') ? Response.json({summaries: []}) : Response.json({documents: []}));
  h.run('state.documents = [{source:"A.txt", chunk_count:1}]; state.messages = [{role:"user", content:"Question"}];');
  h.node('audio-history').append(new Node());
  h.node('question').value = 'Ancien brouillon';
  await h.run('selectProject("bbbb")');
  assert.equal(h.run('state.settings.project_id'), 'bbbb');
  assert.equal(h.run('state.messages.length'), 0);
  assert.equal(h.node('audio-history').children.length, 0);
  assert.equal(h.node('question').value, '');
});

test('un historique audio périmé ne traverse pas un changement de projet', async () => {
  const resolvers = [];
  const h = await harness(() => new Promise(resolve => resolvers.push(resolve)));
  h.run('state.projectId = "A";');
  const first = h.run('loadAudioHistory()');
  h.run('state.projectId = "B";');
  const second = h.run('loadAudioHistory()');
  resolvers[1](Response.json({summaries: [{detail:'brief', created_at:'2026-09-30', transcript:'B', sources:['B.txt'], audio_url:'/B.wav'}]}));
  await second;
  resolvers[0](Response.json({summaries: [{detail:'brief', created_at:'2026-09-30', transcript:'A', sources:['A.txt'], audio_url:'/A.wav'}]}));
  await first;
  assert.equal(h.node('audio-history').children[0].children[1].src, '/B.wav');
});

test('la génération audio bloque le changement de projet et réactive les contrôles après une erreur', async () => {
  let finish;
  const h = await harness(() => new Promise(resolve => { finish = resolve; }));
  h.run('state.projectId = "A"; state.documents = [{source:"A.txt"}];');
  const pending = h.run('generateAudio({preventDefault(){}})');
  assert.equal(h.node('project-select').disabled, true);
  await h.run('selectProject("B")');
  assert.equal(h.run('state.projectId'), 'A');
  finish(Response.json({detail:'Modèle indisponible'}, {status:502}));
  await pending;
  assert.equal(h.node('project-select').disabled, false);
  assert.match(h.node('audio-status').textContent, /Modèle indisponible/);
});

test('passer à l’audio puis revenir conserve le brouillon et la discussion', async () => {
  const h = await harness(async () => Response.json({}));
  h.node('question').value = 'Mon brouillon';
  h.run('state.messages = [{role:"user", content:"Question conservée"}]; setView("audio")');
  assert.equal(h.node('chat-content').hidden, true);
  assert.equal(h.node('audio-panel').hidden, false);
  assert.equal(h.node('workspace').dataset.mode, 'audio');
  h.run('setView("chat")');
  assert.equal(h.node('audio-panel').hidden, true);
  assert.equal(h.node('chat-content').hidden, false);
  assert.equal(h.node('question').value, 'Mon brouillon');
  assert.equal(h.run('state.messages[0].content'), 'Question conservée');
});

test('ouvrir les sources depuis l’audio rétablit la discussion associée', async () => {
  const h = await harness(async () => Response.json({}));
  h.run('setView("audio"); setView("sources")');
  assert.equal(h.node('workspace').dataset.mode, 'chat');
  assert.equal(h.node('audio-panel').hidden, true);
  assert.equal(h.node('workspace').dataset.view, 'sources');
});
