import { test } from 'node:test';
import assert from 'node:assert/strict';
import { consumeChatStream } from '../static/stream.mjs';

function response(text) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({
    start(controller) {
      // Force les caractères UTF-8 et lignes à traverser plusieurs lectures.
      for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    }
  }));
}

test('reconstitue lignes et caractères UTF-8 fragmentés', async () => {
  const events = [];
  await consumeChatStream(response('{"type":"sources","sources":[]}\n{"type":"chunk","text":"réponse à Noël"}\n{"type":"done"}'), event => events.push(event));
  assert.equal(events[1].text, 'réponse à Noël');
  assert.equal(events.at(-1).type, 'done');
});

test('une fermeture sans done reste une interruption', async () => {
  const events = [];
  await assert.rejects(consumeChatStream(response('{"type":"chunk","text":"Début"}\n'), event => events.push(event)), /interrompu/i);
  assert.equal(events[0].text, 'Début');
});

test('une erreur serveur conserve le texte et donne une erreur', async () => {
  const events = [];
  await assert.rejects(consumeChatStream(response('{"type":"chunk","text":"Début"}\n{"type":"error","message":"Serveur arrêté"}\n'), event => events.push(event)), /Serveur arrêté/);
  assert.equal(events[0].text, 'Début');
});

test('un flux malformé ne devient pas une réponse valide', async () => {
  await assert.rejects(consumeChatStream(response('not-json\n'), () => {}), /flux/i);
});
