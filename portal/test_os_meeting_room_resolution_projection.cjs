'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const contract = require('./static/os_meeting_readonly_contract.js');
const project = contract.buildRoomResolutionProjection;
const script = fs.readFileSync(path.join(__dirname, 'static', 'os_meeting_readonly_contract.js'), 'utf8');
const browser = { window: {} };
vm.runInNewContext(script, browser);
assert.equal(typeof browser.window.OSMeetingReadonlyContract.buildRoomResolutionProjection, 'function');
assert.equal(typeof project, 'function');
assert.doesNotMatch(script, /\bfetch\s*\(|XMLHttpRequest|sendBeacon|\bPOST\b|innerHTML|eval\s*\(/);

const id = 'res-123';
const room = {
  room_id: 'room-a',
  resolutions: [{ resolution_id: id, room_id: 'room-a', card_id: 'card-1',
    text: '仅供讨论的决议正文', content_hash: 'fingerprint', status: 'proposed' }],
  board: [{ card_id: 'card-1', kind: 'decision', text: '仅供讨论的决议正文',
    content_hash: 'fingerprint', status: 'resolved' }]
};
const response = { ok: true, resolution: { ...room.resolutions[0] },
  integrity: { resolution_id: id, integrity: 'valid', approval_status: 'not_verified', actionable: false } };
function clone(value) { return structuredClone(value); }
function checkInvalid(roomInput, responseInput, expectedId = id) {
  const result = project(roomInput, responseInput, expectedId);
  assert.equal(result.status, 'invalid');
  assert.equal(result.text, '');
  assert.equal(result.approval_status, 'not_verified');
  assert.equal(result.actionable, false);
  assert.ok(Object.isFrozen(result));
}

const before = JSON.stringify({ room, response });
assert.deepEqual(project(room, response, id), { status: 'valid', text: '仅供讨论的决议正文',
  approval_status: 'not_verified', actionable: false });
assert.deepEqual(project({ ok: true, room }, response, id), project(room, response, id));
assert.equal(JSON.stringify({ room, response }), before, '不修改输入');
assert.ok(Object.isFrozen(project(room, response, id)));
for (const missing of [undefined, null]) {
  const result = project(room, missing, id);
  assert.equal(result.status, 'unknown');
  assert.equal(result.text, '');
  assert.equal(result.actionable, false);
}
for (const failure of [{ ok: false }, { ok: true, resolution: null, integrity: response.integrity },
  { ok: true, resolution: response.resolution }, { ok: true, integrity: response.integrity }, {}]) {
  checkInvalid(room, failure);
}
checkInvalid(room, response, 'res-other');
checkInvalid({ ...room, room_id: 'room-b' }, response);
checkInvalid({ ...room, resolutions: [] }, response);
checkInvalid({ ...room, resolutions: [...room.resolutions, room.resolutions[0]] }, response);
checkInvalid({ ...room, board: [] }, response);
checkInvalid({ ...room, board: [...room.board, room.board[0]] }, response);
for (const field of ['resolution', 'integrity']) {
  const changed = clone(response);
  changed[field].resolution_id = 'res-other';
  checkInvalid(room, changed);
}
for (const [field, value] of [['room_id', 'room-b'], ['card_id', 'card-other'],
  ['text', '篡改正文'], ['content_hash', 'tampered'], ['status', 'approved']]) {
  const changed = clone(response);
  changed.resolution[field] = value;
  checkInvalid(room, changed);
}
for (const [field, value] of [['integrity', 'invalid'], ['approval_status', 'approved'],
  ['actionable', true]]) {
  const changed = clone(response);
  changed.integrity[field] = value;
  checkInvalid(room, changed);
}
for (const [key, replacement] of [['text', '被改写的卡片'], ['content_hash', 'tampered']]) {
  const changed = clone(room);
  changed.board[0][key] = replacement;
  checkInvalid(changed, response);
}
checkInvalid({ ...room, resolutions: [{ ...room.resolutions[0], text: '篡改房间决议' }] }, response);
console.log('PASS 决议只读投影：有效、非法、跨房间、伪造审批/可执行、缺响应、篡改响应与输入不变');
