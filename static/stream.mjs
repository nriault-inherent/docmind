/** Décode un flux NDJSON même lorsque lignes et caractères sont fragmentés. */
export async function consumeChatStream(response, onEvent) {
  if (!response.body) throw new Error('Le flux de réponse est indisponible.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let completed = false;
  const consume = line => {
    if (!line.trim()) return;
    let event;
    try { event = JSON.parse(line); }
    catch { throw new Error('Le flux de réponse est invalide.'); }
    if (!['sources', 'chunk', 'error', 'done'].includes(event.type)) {
      throw new Error('Le flux de réponse est invalide.');
    }
    if (event.type === 'error') throw new Error(event.message || 'La réponse a été interrompue.');
    if (event.type === 'chunk' && typeof event.text !== 'string') throw new Error('Le flux de réponse est invalide.');
    if (event.type === 'sources' && !Array.isArray(event.sources)) throw new Error('Les sources reçues sont invalides.');
    if (event.type === 'done') completed = true;
    onEvent(event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf('\n')) !== -1) {
        consume(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
      }
      if (done) break;
    }
    if (buffer.trim()) consume(buffer);
    if (!completed) throw new Error('Le flux a été interrompu. Vous pouvez réessayer.');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
