# -*- coding: utf-8 -*-
"""The bot catalog of the «Characters» module (app/module_builtins/characters/catalog.js).

The pure helpers (Chub / Pygmalion / SpicyChat search URLs, response
parsing, token counts, the "Hide Low Quality Bots" filter, sorting, tag
filter) run in Node against fixture answers shaped like the real
responses. The Pygmalion and SpicyChat fixtures are real answers captured
live with curl on 2026-11-03 (Pygmalion: server.pygmalion.chat Connect
search + listing; SpicyChat: Typesense GET on ts-lb.nd-api.com), trimmed
only by dropping search-metadata noise (SpicyChat hit highlights) — the
document/character objects are verbatim. The live endpoints themselves
cannot be reached through the module transport from this VM (the VM DNS
resolves public hosts to CGNAT, which the SSRF guard rejects, and Chub
geo-blocks the datacentre egress IP), so the fixtures — not a live call —
are the source of truth here; the fixlog records the live curl results
separately.

A second test makes sure the part is actually inlined into the built
module JS (load_package) and that the built JS parses.
"""
from __future__ import annotations

PYG_FIXTURE = r"""{"totalItems": "55", "characters": [{"id": "8e6fb05b-52e9-4688-a8cd-e4bca5baf351", "displayName": "A DDLC Scenario", "avatarUrl": "https://assets.pygmalion.chat/436cccb4-bac2-43d4-a104-ffdca34ab187", "description": "DDLC Full and complete scenario with all the Dokis included!\n-On this scenario, it's your first day of HS, and Sayori wants to take you \nto the club!.\n-More greetings to be added!.\nTested on Magnum Free-Tier", "source": "A game by: Team Salvato, Serenity Forge", "stars": 4, "views": 419, "downloads": 5, "isPublic": true, "tags": ["Female", "Scenario", "Video Game", "Human"], "createdAt": "1765816251", "updatedAt": "1765816507", "owner": {"id": "6e07e2c1-eec1-48db-b1fa-1a2b139c50bb", "displayName": "Hori", "username": "lilmonix3", "avatarUrl": "https://assets.pygmalion.chat/2a720d34-f5a9-44c0-81ac-81832284d2c1"}, "chatCount": 28, "versionId": "ceb259bb-f1de-49b7-919e-0b6f2ee8276b", "personalityTokenCount": 0, "approvedAt": "1766399240"}, {"id": "695f9f24-d28a-4150-bbbd-2f62266dd5c6", "displayName": "Agnes Tachyon", "avatarUrl": "https://assets.pygmalion.chat/9e1eb289-5380-4c9a-8070-c0231f361141", "description": "Agnes Tachyon is a brilliant and deeply eccentric umamusume whose life is entirely devoted to the search for \"Truth\" about speed and the limits of physics.", "source": "Uma Musume: Pretty Derby", "stars": 7, "views": 565, "downloads": 12, "isPublic": true, "tags": ["Female", "Anime", "Video Game", "Non-Human", "Ali:Chat"], "createdAt": "1764834963", "updatedAt": "1764834963", "owner": {"id": "eab1584a-af41-49df-a3ed-ad19a408a811", "displayName": "Emilia", "username": "emilie228x", "avatarUrl": "https://assets.pygmalion.chat/2ca941a1-8ae6-4f06-b97b-6d4f933941eb"}, "chatCount": 67, "versionId": "70afd135-5675-4049-8b4b-47f45f8f7031", "personalityTokenCount": 0, "approvedAt": "1765015496"}, {"id": "5f9e33ad-2bf6-44bb-934f-c763c8c86fa3", "displayName": "Mikasa", "avatarUrl": "https://assets.pygmalion.chat/cb169184-30f5-4347-b251-0ddef8ae333d", "description": "Mikasa from Attack on Titan.", "source": "Attack on Titan", "stars": 3, "views": 40334, "downloads": 1313, "isPublic": true, "tags": ["Female", "Anime"], "createdAt": "1706427614", "updatedAt": "1722520534", "owner": {"id": "e6681af2-8ae0-4662-9106-5984f2615638", "displayName": "Makima", "username": "makima", "avatarUrl": "https://assets.pygmalion.chat/2baa841e-233a-41d0-b322-acf1ea3bee01"}, "chatCount": 41, "versionId": "3db5657b-ce2b-4b6e-8b12-12b8590bb89f", "personalityTokenCount": 1106, "approvedAt": "1706431260"}]}"""

SPICY_FIXTURE = r"""{"found": 8506, "hits": [{"document": {"application_ids": ["spicychat"], "avatar_is_nsfw": false, "avatar_url": "avatars/c18b811639bc006f7c23e15cd46884d2.gif", "character_id": "6be99c6f-a6ce-4626-a777-0538ff7770c5", "createdAt": 1715147475344, "creator_user_id": "kp:2cf550d6bf8248b08d4393802b24220b", "creator_username": "ruske", "definition_size_category": "mid", "definition_visible": false, "features": [], "features_with_sfw_lorebooks": [], "greeting": "*I blick slowly while looking up at you* \"do you wanna know your fate today?\"", "group_size_category": "regular", "id": "6be99c6f-a6ce-4626-a777-0538ff7770c5", "is_nsfw": true, "language": "en", "moderation_flags": [], "moderation_keywords": [], "moderation_status": "reviewed", "name": "Cat", "num_messages": 4733, "num_messages_24h": 0, "tags": ["Anal", "Futanari", "NSFW", "Omegaverse", "Male", "Magic", "Femboy", "Anthro", "Horror", "Comedy"], "title": "Cat", "token_count": 962, "type": "STANDARD", "updatedAt": 1793719471986, "visibility": "public"}}, {"document": {"application_ids": ["spicychat", "pixelchat"], "avatar_is_nsfw": false, "avatar_url": "avatars/a09de00bea6f348b9528ee19fc2783a1.png", "character_id": "bfbd197b-4194-41d6-b9b2-d398df6fd011", "createdAt": 1729745313897, "creator_user_id": "kp:a8db69e823234a3d898c7f88b6d96bc7", "creator_username": "longlivetbc", "definition_size_category": "minimal", "definition_visible": true, "features": [], "features_with_sfw_lorebooks": [], "greeting": "*you were walking until you came across a cat base and a cat came out of it*-Meow- *There is a cat in front of you, it is at level 29, be careful*", "group_size_category": "regular", "id": "bfbd197b-4194-41d6-b9b2-d398df6fd011", "is_nsfw": false, "language": "en", "moderation_flags": [], "moderation_keywords": [], "moderation_status": "reviewed", "name": "Cat", "num_messages": 3691, "num_messages_24h": 0, "tags": ["Game", "Comedy"], "title": "First cat of all The Battle Cats", "token_count": 149, "type": "STANDARD", "updatedAt": 1793543854094, "visibility": "public"}}], "page": 1, "out_of": 1459921}"""

CHUB_FIXTURE = r"""{"data":{"count":2620,"nodes":[{"id":7900350,"name":"Testa","fullPath":"clear_appointment_1805/testa-a6ad0525d3f2","description":" ","starCount":0,"lastActivityAt":"2026-09-30T04:48:11Z","createdAt":"2026-09-30T04:48:11Z","labels":[{"title":"TOKEN_COUNTS","description":"{\"description\":0,\"personality\":0,\"scenario\":0,\"mes_example\":0,\"first_mes\":0,\"system_prompt\":0,\"post_history_instructions\":0,\"total\":1978}"}],"topics":[],"forksCount":0,"rating":0,"ratingCount":0,"projectSpace":"characters","creatorId":10845232,"nTokens":1978,"tagline":" ","primaryFormat":null,"related_characters":[],"related_lorebooks":[],"related_prompts":[],"related_extensions":[],"hasGallery":false,"nChats":1,"nMessages":4,"definition":null,"permissions":"read","is_public":true,"is_favorite":false,"nsfw_image":false,"n_public_chats":0,"n_favorites":0,"is_unlisted":false,"avatar_url":"https://avatars.charhub.io/avatars/clear_appointment_1805/testa-a6ad0525d3f2/avatar.webp","max_res_url":"https://avatars.charhub.io/avatars/clear_appointment_1805/testa-a6ad0525d3f2/chara_card_v2.png","bound_preset":null,"project_uuid":null,"voice_id":null,"verified":false,"recommended":false,"ratings_disabled":false,"lang_id":24,"badges":[]},{"id":7655751,"name":"test","fullPath":"Jones1/test-fac24dc48794","description":" ","starCount":0,"lastActivityAt":"2026-08-22T20:08:14Z","createdAt":"2026-08-22T20:08:14Z","labels":[{"title":"TOKEN_COUNTS","description":"{\"description\":0,\"personality\":0,\"scenario\":0,\"mes_example\":0,\"first_mes\":0,\"system_prompt\":0,\"post_history_instructions\":0,\"total\":398}"}],"topics":[],"forksCount":0,"rating":0,"ratingCount":0,"projectSpace":"characters","creatorId":10375530,"nTokens":398,"tagline":" ","primaryFormat":null,"related_characters":[],"related_lorebooks":[],"related_prompts":[],"related_extensions":[],"hasGallery":false,"nChats":0,"nMessages":0,"definition":null,"permissions":"read","is_public":true,"is_favorite":false,"nsfw_image":false,"n_public_chats":0,"n_favorites":0,"is_unlisted":false,"avatar_url":"https://avatars.charhub.io/avatars/Jones1/test-fac24dc48794/avatar.webp","max_res_url":"https://avatars.charhub.io/avatars/Jones1/test-fac24dc48794/chara_card_v2.png","bound_preset":null,"project_uuid":null,"voice_id":null,"verified":false,"recommended":false,"ratings_disabled":false,"lang_id":24,"badges":[]}],"page":1,"previous_cursor":null,"cursor":"eyJwcm9qZWN0X25hbWVfbGlrZSI6eyJfdmFsdWUiOiJ0ZXN0IiwiX29yZGVyIjoiZGVzYyJ9LCJsYXN0X3VwZGF0ZWQiOnsiX3ZhbHVlIjoiMjAyNi0wOC0yMlQyMDowODoxNCIsIl9vcmRlciI6ImRlc2MiLCJfcHJvamVjdF9pZCI6NzY1NTc1MX0sIm9yZGVyIjp7Il92YWx1ZSI6WyJwcm9qZWN0X25hbWVfbGlrZSIsImxhc3RfdXBkYXRlZCJdfX0="}}"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE = ROOT / "app" / "module_builtins" / "characters"

SCRIPT = r"""
const assert = require('assert');
const fs = require('fs');
// Stubs for the module globals the catalog part touches (S, Memu, t).
// The real module provides them; the pure helpers only need these shapes.
var S = { settings: {} };
var Memu = { fetch: function () { return Promise.resolve({ text: '{}' }); },
             open: function () { return Promise.resolve({ ok: true }); } };
function t(k, params) {
  if (!params) return k;
  return Object.keys(params).reduce(function (s, key) {
    return s.split('{' + key + '}').join(String(params[key]));
  }, k);
}
eval(fs.readFileSync(process.argv[2], 'utf8'));
const PYG = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const SPICY = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const CHUB_LIVE = JSON.parse(fs.readFileSync(process.argv[5], 'utf8'));

// ---------- token label parsing
assert.strictEqual(catalogParseTokenLabel('1.2k'), 1200);
assert.strictEqual(catalogParseTokenLabel('2.5K'), 2500);
assert.strictEqual(catalogParseTokenLabel('3.4M'), 3400000);
assert.strictEqual(catalogParseTokenLabel('850'), 850);
assert.strictEqual(catalogParseTokenLabel('1,234'), 1234);
assert.strictEqual(catalogParseTokenLabel(''), null);
assert.strictEqual(catalogParseTokenLabel('abc'), null);
assert.strictEqual(catalogParseTokenLabel(null), null);
assert.strictEqual(catalogParseTokenLabel('0'), 0);

// null/empty counts stay "unknown": Number(null) is 0, and a confident zero
// would make Hide Low Quality hide every live Chub row (tokenCount: null)
assert.strictEqual(catalogNum(null), null);
assert.strictEqual(catalogNum(undefined), null);
assert.strictEqual(catalogNum(''), null);
assert.strictEqual(catalogNum('55'), 55);
assert.strictEqual(catalogNum(0), 0);

// ---------- normalisation of one Chub node
const full = {
  id: 'n1', name: 'Aqua', fullPath: 'ddd/aqua-the-useless-goddess',
  tagline: 'Useless goddess', description: 'Long description',
  topics: ['anime', 'comedy', 'goddess'],
  avatar_url: 'https://avatars.charhub.io/a.png', max_res_url: 'https://avatars.charhub.io/a-big.png',
  tokenCount: 1840, tokenCountLabel: '1.8k', nChats: 12345, starCount: 42, rating: 4.7,
  creator: { username: 'ddd' }, createdAt: '2024-01-02', lastActivityAt: '2026-10-01'
};
const row = catalogNormalizeChub(full);
assert.strictEqual(row.name, 'Aqua');
assert.strictEqual(row.creator, 'ddd');
assert.strictEqual(row.pageUrl, 'https://chub.ai/characters/ddd/aqua-the-useless-goddess');
assert.strictEqual(row.tokens, 1840);
assert.strictEqual(row.tokensEstimated, false);
assert.strictEqual(row.description, 'Useless goddess');
assert.deepStrictEqual(row.tags, ['anime', 'comedy', 'goddess']);
assert.strictEqual(row.chats, 12345);
assert.strictEqual(row.stars, 42);

// tokenCountLabel is only a fallback, and it is flagged as estimated
const labelled = catalogNormalizeChub({ name: 'L', fullPath: 'x/l', tokenCountLabel: '1.2k', topics: [] });
assert.strictEqual(labelled.tokens, 1200);
assert.strictEqual(labelled.tokensEstimated, true);
// live search rows carry the count in nTokens (tokenCount is null there)
assert.strictEqual(catalogChubTokens({ nTokens: 1978 }).value, 1978);
assert.strictEqual(catalogChubTokens({ tokenCount: null, tokenCountLabel: null, nTokens: 398 }).value, 398);
assert.strictEqual(catalogChubTokens({ nTokens: 0 }).value, 0);
// a {node: {...}} wrapper (seen in the wild) unwraps instead of dropping the row
const wrapped = catalogNormalizeChub({ node: full });
assert.strictEqual(wrapped.name, 'Aqua');
assert.strictEqual(wrapped.avatarUrl, 'https://avatars.charhub.io/a-big.png');
assert.strictEqual(wrapped.avatarFallback, 'https://avatars.charhub.io/a.png');
assert.strictEqual(wrapped.tokens, 1840);
// whitespace-only tagline/description is no description
assert.strictEqual(catalogNormalizeChub({ name: 'W', fullPath: 'x/w', tagline: ' ', description: ' ' }).description, '');
// no token data at all: unknown, never invented
const unknown = catalogNormalizeChub({ name: 'U', fullPath: 'x/u', topics: ['tag'] });
assert.strictEqual(unknown.tokens, null);
assert.strictEqual(unknown.tokensEstimated, false);
// creator falls back to the first fullPath segment
assert.strictEqual(catalogNormalizeChub({ name: 'C', fullPath: 'someone/c' }).creator, 'someone');
// junk is dropped, not turned into a card
assert.strictEqual(catalogNormalizeChub(null), null);
assert.strictEqual(catalogNormalizeChub({}), null);
assert.strictEqual(catalogNormalizeChub({ name: '' }), null);

// ---------- parsing both envelope shapes
const parsed = catalogParseChub({ data: { nodes: [full, labelled, unknown, null, 'junk'], count: 500 } });
assert.strictEqual(parsed.rows.length, 3);
assert.strictEqual(parsed.total, 500);
const bare = catalogParseChub({ nodes: [full] });
assert.strictEqual(bare.rows.length, 1);
assert.strictEqual(bare.total, 1);

// ---------- parsing the REAL api.chub.ai/search answer (captured live
// 2026-11-04 with curl; verbatim, only the two nodes are kept)
const live = catalogParseChub(CHUB_LIVE);
assert.strictEqual(live.total, 2620);
assert.strictEqual(live.rows.length, 2);
assert.strictEqual(live.rows[0].name, 'Testa');
assert.strictEqual(live.rows[0].creator, 'clear_appointment_1805');
assert.strictEqual(live.rows[0].pageUrl, 'https://chub.ai/characters/clear_appointment_1805/testa-a6ad0525d3f2');
assert.strictEqual(live.rows[0].avatarUrl, 'https://avatars.charhub.io/avatars/clear_appointment_1805/testa-a6ad0525d3f2/chara_card_v2.png');
assert.strictEqual(live.rows[0].avatarFallback, 'https://avatars.charhub.io/avatars/clear_appointment_1805/testa-a6ad0525d3f2/avatar.webp');
assert.strictEqual(live.rows[0].tokens, 1978);
assert.strictEqual(live.rows[0].tokensEstimated, false);
assert.strictEqual(live.rows[0].chats, 1);
assert.strictEqual(live.rows[0].nsfw, false);
assert.strictEqual(live.rows[0].description, '');
assert.strictEqual(live.rows[1].tokens, 398);
// Hide Low Quality hides the 398-token live row and keeps the 1978-token one
assert.deepStrictEqual(catalogFilterRows(live.rows, { hideLow: true }).map(r => r.name), ['Testa']);
assert.deepStrictEqual(catalogParseChub(null), { rows: [], total: 0 });
assert.deepStrictEqual(catalogParseChub({ data: { nodes: 'nope' } }), { rows: [], total: 0 });
assert.deepStrictEqual(catalogParseChub('not json'), { rows: [], total: 0 });

// ---------- search URL
let url = catalogChubSearchUrl({ query: 'goddess', page: 2, sort: 'default', nsfw: false, hideLow: false, tag: '' });
assert.ok(url.startsWith('https://gateway.chub.ai/search?'), url);
assert.ok(url.includes('search=goddess'), url);
assert.ok(url.includes('first=24'), url);
assert.ok(url.includes('page=2'), url);
assert.ok(url.includes('namespace=characters'), url);
assert.ok(url.includes('nsfw=false'), url);
assert.ok(url.includes('nsfl=false'), url);
assert.ok(!url.includes('min_tokens'), url);
assert.ok(!url.includes('sort='), url);
url = catalogChubSearchUrl({ query: '', page: 1, sort: 'new', nsfw: true, hideLow: true, tag: 'anime' });
assert.ok(url.includes('sort=created_at'), url);
assert.ok(url.includes('asc=false'), url);
assert.ok(url.includes('nsfw=true'), url);
assert.ok(url.includes('nsfl=true'), url);
assert.ok(url.includes('min_tokens=500'), url);
assert.ok(url.includes('topics=anime'), url);
url = catalogChubSearchUrl({ sort: 'active' });
assert.ok(url.includes('sort=last_activity_at'), url);

// ---------- Hide Low Quality Bots + tag filter + sorting
const rows = [
  { name: 'Tiny', tokens: 499, tags: ['anime'], chats: 10 },
  { name: 'Edge', tokens: 500, tags: ['anime'], chats: 5 },
  { name: 'Big', tokens: 4000, tags: ['scifi'], chats: 900 },
  { name: 'Mystery', tokens: null, tags: ['anime'], chats: 1 },
];
let kept = catalogFilterRows(rows, { hideLow: true });
assert.deepStrictEqual(kept.map(r => r.name), ['Edge', 'Big', 'Mystery']);  // unknown tokens are kept
kept = catalogFilterRows(rows, { hideLow: false });
assert.strictEqual(kept.length, 4);
kept = catalogFilterRows(rows, { hideLow: true, tag: 'anime' });
assert.deepStrictEqual(kept.map(r => r.name), ['Edge', 'Mystery']);
kept = catalogFilterRows(rows, { hideLow: false, sort: 'tokens' });
assert.deepStrictEqual(kept.map(r => r.name), ['Big', 'Edge', 'Tiny', 'Mystery']);
kept = catalogFilterRows(rows, { hideLow: false, sort: 'chats' });
assert.deepStrictEqual(kept.map(r => r.name), ['Big', 'Tiny', 'Edge', 'Mystery']);
kept = catalogFilterRows(rows, { hideLow: false, sort: 'name' });
assert.deepStrictEqual(kept.map(r => r.name), ['Big', 'Edge', 'Mystery', 'Tiny']);

// ---------- top tags
assert.deepStrictEqual(catalogTopTags(rows, 2), ['anime', 'scifi']);
assert.deepStrictEqual(catalogTopTags([], 5), []);

// ---------- sources: Janitor is gone, three working sources
assert.deepStrictEqual(Object.keys(CATALOG_SOURCES), ['chub', 'pygmalion', 'spicychat']);
assert.strictEqual(CATALOG_SOURCES.janitor, undefined);
assert.strictEqual(CATALOG_SOURCES.pygmalion.searchBase,
  'https://server.pygmalion.chat/galatea.v1.PublicCharacterService/CharacterSearch');
// a saved "janitor" source from an older version falls back to Chub
S.settings.catalog = { source: 'janitor' };
assert.strictEqual(catalogSettings().source, 'chub');
S.settings.catalog = {};

// ---------- Pygmalion search URL (Connect GET, message is JSON)
let purl = catalogPygmalionSearchUrl({ query: 'goddess', page: 2, sort: 'tokens', nsfw: false, hideLow: true, tag: '' });
assert.ok(purl.startsWith('https://server.pygmalion.chat/galatea.v1.PublicCharacterService/CharacterSearch?connect=v1&encoding=json&message='), purl);
let pmsg = JSON.parse(decodeURIComponent(purl.split('message=')[1]));
assert.deepStrictEqual(pmsg, { query: 'goddess', page: 2, pageSize: 24, orderBy: 'token_count', orderDescending: true, includeSensitive: false });
purl = catalogPygmalionSearchUrl({ query: '', page: 1, sort: 'new', nsfw: true, tag: 'anime' });
pmsg = JSON.parse(decodeURIComponent(purl.split('message=')[1]));
assert.strictEqual(pmsg.orderBy, 'created_at');
assert.strictEqual(pmsg.includeSensitive, true);
assert.deepStrictEqual(pmsg.tagsNamesInclude, ['anime']);
assert.strictEqual(pmsg.query, '');
assert.strictEqual(JSON.parse(decodeURIComponent(catalogPygmalionSearchUrl({ sort: 'active' }).split('message=')[1])).orderBy, 'updated_at');
assert.strictEqual(JSON.parse(decodeURIComponent(catalogPygmalionSearchUrl({ sort: 'chats' }).split('message=')[1])).orderBy, 'downloads');

// ---------- Pygmalion parsing of the real captured answer
const pyg = catalogParsePygmalion(PYG);
assert.strictEqual(pyg.total, 55);
assert.strictEqual(pyg.rows.length, 3);
assert.strictEqual(pyg.rows[0].name, 'A DDLC Scenario');
assert.strictEqual(pyg.rows[0].creator, 'Hori');
assert.strictEqual(pyg.rows[0].pageUrl, 'https://pygmalion.chat/character/8e6fb05b-52e9-4688-a8cd-e4bca5baf351');
assert.strictEqual(pyg.rows[0].tokens, null);  // personalityTokenCount 0 = unknown, never 0
assert.strictEqual(pyg.rows[0].chats, 28);
assert.strictEqual(pyg.rows[0].stars, 4);
assert.deepStrictEqual(pyg.rows[0].tags, ['Female', 'Scenario', 'Video Game', 'Human']);
assert.strictEqual(pyg.rows[0].importable, true);
assert.strictEqual(pyg.rows[2].name, 'Mikasa');
assert.strictEqual(pyg.rows[2].tokens, 1106);
assert.deepStrictEqual(catalogParsePygmalion(null), { rows: [], total: 0 });
assert.deepStrictEqual(catalogParsePygmalion({ characters: 'nope' }), { rows: [], total: 0 });
// hide-low keeps unknown-token Pygmalion rows (search mostly returns 0)
assert.deepStrictEqual(catalogFilterRows(pyg.rows, { hideLow: true }).map(r => r.name),
  ['A DDLC Scenario', 'Agnes Tachyon', 'Mikasa']);

// ---------- SpicyChat search URL (Typesense GET, config passed in)
const SPICY_CFG = { collection: 'public_characters_alias', apiKey: 'TESTKEY' };
let surl = catalogSpicySearchUrl(SPICY_CFG, { query: 'cat', page: 1, sort: 'default', nsfw: false, hideLow: false, tag: '' });
assert.ok(surl.startsWith('https://ts-lb.nd-api.com/collections/public_characters_alias/documents/search?'), surl);
assert.ok(surl.includes('q=cat'), surl);
assert.ok(surl.includes('query_by=' + encodeURIComponent(CATALOG_SPICY_QUERY_BY)), surl);
assert.ok(surl.includes('per_page=24'), surl);
assert.ok(surl.includes('sort_by=num_messages%3Adesc'), surl);
assert.ok(surl.includes('filter_by=is_nsfw%3Afalse'), surl);
assert.ok(surl.includes('x-typesense-api-key=TESTKEY'), surl);
surl = catalogSpicySearchUrl(SPICY_CFG, { query: '', page: 3, sort: 'tokens', nsfw: true, tag: 'Anime' });
assert.ok(surl.includes('q=*'), surl);
assert.ok(surl.includes('page=3'), surl);
assert.ok(surl.includes('sort_by=num_messages%3Adesc'), surl);  // token_count is not sortable server-side; the client re-sorts the page
assert.ok(!surl.includes('is_nsfw'), surl);
assert.ok(surl.includes('filter_by=' + encodeURIComponent('tags:=Anime')), surl);
assert.ok(catalogSpicySearchUrl(SPICY_CFG, { sort: 'new' }).includes('sort_by=createdAt%3Adesc'));
assert.ok(catalogSpicySearchUrl(SPICY_CFG, { sort: 'active' }).includes('sort_by=num_messages_24h%3Adesc'));

// ---------- SpicyChat parsing of the real captured answer
const spicy = catalogParseSpicy(SPICY);
assert.strictEqual(spicy.total, 8506);
assert.strictEqual(spicy.rows.length, 2);
assert.strictEqual(spicy.rows[0].name, 'Cat');
assert.strictEqual(spicy.rows[0].description, 'Cat');
assert.ok(spicy.rows[0].greeting.indexOf('do you wanna know your fate') >= 0);
assert.strictEqual(spicy.rows[0].creator, 'ruske');
assert.strictEqual(spicy.rows[0].avatarUrl, 'https://cdn.nd-api.com/avatars/c18b811639bc006f7c23e15cd46884d2.gif');
assert.strictEqual(spicy.rows[0].pageUrl, 'https://spicychat.ai/chatbot/6be99c6f-a6ce-4626-a777-0538ff7770c5');
assert.strictEqual(spicy.rows[0].tokens, 962);
assert.strictEqual(spicy.rows[0].chats, 4733);
assert.strictEqual(spicy.rows[0].importable, false);
assert.strictEqual(spicy.rows[1].tokens, 149);
assert.strictEqual(spicy.rows[1].description, 'First cat of all The Battle Cats');
assert.deepStrictEqual(catalogParseSpicy(null), { rows: [], total: 0 });
assert.deepStrictEqual(catalogParseSpicy({ hits: 'nope' }), { rows: [], total: 0 });
// hide-low hides the 149-token SpicyChat row, keeps the 962-token one
const ske = catalogFilterRows(spicy.rows, { hideLow: true });
assert.deepStrictEqual(ske.map(r => r.name), ['Cat']);
assert.strictEqual(ske[0].tokens, 962);

// ---------- source-aware fetch errors
assert.strictEqual(catalogFetchError(new Error('HTTP 403 Forbidden'), 'chub'), 'catalog_err_403');
assert.strictEqual(catalogFetchError(new Error('HTTP 403 Forbidden'), 'pygmalion'), 'catalog_err_403_other');
assert.strictEqual(catalogFetchError(new Error('HTTP 404 Not Found'), 'spicychat'), 'catalog_err_http');  // stub t() returns the key; the real t() interpolates {message}

// ---------- avatar failure reasons are human-readable, never silent
assert.strictEqual(catalogAvatarErrorText('avatar_http_403'), 'catalog_avatar_reason_blocked');
assert.strictEqual(catalogAvatarErrorText('avatar_blocked'), 'catalog_avatar_reason_blocked');
assert.strictEqual(catalogAvatarErrorText('avatar_http_404'), 'catalog_avatar_reason_gone');
assert.strictEqual(catalogAvatarErrorText('avatar_dns'), 'catalog_avatar_reason_network');
assert.strictEqual(catalogAvatarErrorText('avatar_not_image'), 'catalog_avatar_reason_bad');
assert.strictEqual(catalogAvatarErrorText('avatar_too_large'), 'catalog_avatar_reason_big');
assert.strictEqual(catalogAvatarErrorText('avatar_http_500'), 'catalog_avatar_reason_http');
assert.strictEqual(catalogAvatarErrorText(''), 'catalog_avatar_reason_missing');
assert.strictEqual(catalogAvatarErrorText('avatar_something_new'), 'avatar_something_new');

// ---------- dispatch: each source resolves to {url, parse}; the SpicyChat
// config is fetched once (from the site's public config endpoint) and cached
(async function () {
  CAT.query = 'test'; CAT.page = 1; CAT.tag = '';
  const reqPyg = await catalogSearchRequest({ source: 'pygmalion', sort: 'tokens', nsfw: true, hideLow: false });
  assert.ok(reqPyg.url.indexOf('https://server.pygmalion.chat/') === 0, reqPyg.url);
  assert.strictEqual(reqPyg.parse, catalogParsePygmalion);
  const reqChub = await catalogSearchRequest({ source: 'chub', sort: 'default', nsfw: false, hideLow: false });
  assert.ok(reqChub.url.indexOf('https://gateway.chub.ai/search?') === 0, reqChub.url);
  assert.strictEqual(reqChub.parse, catalogParseChub);
  Memu.fetch = function (opts) {
    assert.ok(opts.url === 'https://prod.nd-api.com/v2/applications/spicychat', opts.url);
    return Promise.resolve({ text: JSON.stringify({ typesenseConfig: { collectionNamePublicCharacter: 'public_characters_alias', apiKeyPublicCharacter: 'TESTKEY' } }) });
  };
  const reqSpicy = await catalogSearchRequest({ source: 'spicychat', sort: 'default', nsfw: false, hideLow: false });
  assert.ok(reqSpicy.url.indexOf('https://ts-lb.nd-api.com/collections/public_characters_alias/documents/search?') === 0, reqSpicy.url);
  assert.ok(reqSpicy.url.indexOf('x-typesense-api-key=TESTKEY') >= 0, reqSpicy.url);
  assert.strictEqual(reqSpicy.parse, catalogParseSpicy);
  let calls = 0;
  Memu.fetch = function () { calls++; return Promise.resolve({ text: '{}' }); };
  await catalogSearchRequest({ source: 'spicychat', sort: 'default', nsfw: false, hideLow: false });
  assert.strictEqual(calls, 0);  // config cached, no second fetch
  console.log('catalog dispatch OK');
})().catch(function (e) { console.error(e); process.exit(1); });

console.log('catalog pure helpers OK');
"""


@unittest.skipUnless(shutil.which("node"), "node is required")
class CharactersCatalogTest(unittest.TestCase):
    def test_pure_helpers(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run.js"
            run.write_text(SCRIPT, encoding="utf-8")
            pyg = Path(temp) / "pyg.json"
            pyg.write_text(PYG_FIXTURE, encoding="utf-8")
            spicy = Path(temp) / "spicy.json"
            spicy.write_text(SPICY_FIXTURE, encoding="utf-8")
            chub = Path(temp) / "chub.json"
            chub.write_text(CHUB_FIXTURE, encoding="utf-8")
            done = subprocess.run(
                ["node", str(run), str(MODULE / "catalog.js"), str(pyg), str(spicy), str(chub)],
                capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("catalog pure helpers OK", done.stdout)
        self.assertIn("catalog dispatch OK", done.stdout)

    def test_part_is_inlined_into_built_module(self):
        from app import modules_host
        row = modules_host.load_package(ROOT, "characters")
        self.assertIn("/* @memu-inlined-part:catalog.js */", row["js"])
        self.assertIn("catalogParseChub", row["js"])
        self.assertIn("catalogParsePygmalion", row["js"])
        self.assertIn("catalogParseSpicy", row["js"])
        self.assertIn("catalogSpicyConfig", row["js"])
        self.assertIn("renderChatSidebar", row["js"])
        self.assertIn("catalog", row["js"])
        with tempfile.TemporaryDirectory() as temp:
            built = Path(temp) / "built.js"
            built.write_text(row["js"], encoding="utf-8")
            done = subprocess.run(["node", "--check", str(built)],
                                  capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_catalog_ui_smoke(self):
        """The real catalog view in jsdom (see the .cjs file for details).

        Exit code 2 from the script means jsdom is not installed in this
        checkout — the smoke is skipped then, like the Playwright tests.
        """
        done = subprocess.run(
            ["node", str(ROOT / "tests" / "round29_characters_catalog_ui_test.cjs")],
            capture_output=True, text=True, timeout=180)
        if done.returncode == 2 and "jsdom not available" in done.stdout:
            self.skipTest("jsdom is not installed in this checkout")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("catalog UI smoke OK", done.stdout)

    def test_i18n_parity(self):
        i18n = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))
        self.assertEqual(set(i18n["ru"]), set(i18n["en"]))
        for key in ("catalog", "catalog_title", "catalog_hide_low",
                    "catalog_hide_low_hint", "catalog_err_403",
                    "catalog_err_403_other", "catalog_import_chat",
                    "catalog_open_site", "catalog_spicy_hint",
                    "catalog_spicy_noimport", "catalog_greeting",
                    "side_chats_with", "side_switch_char", "catalog_found",
                    "catalog_avatar_missing", "catalog_avatar_linked",
                    "catalog_clear_filters", "catalog_error_title",
                    "catalog_rating", "catalog_avatar_reason_blocked"):
            self.assertIn(key, i18n["ru"])
            self.assertIn(key, i18n["en"])
        # Janitor is out of the selector: its strings must be gone too.
        for key in ("catalog_janitor_unavailable",
                    "catalog_source_unavailable_short"):
            self.assertNotIn(key, i18n["ru"])
            self.assertNotIn(key, i18n["en"])


if __name__ == "__main__":
    unittest.main()
