// Code.gs를 Google 서비스 흉내 위에서 실행한다. macOS의 osascript -l JavaScript로 돌린다.
// 결과는 JSON 문자열 하나로 표준 출력에 나온다.

function readFile(path) {
  return $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null).js;
}

function run(argv) {
  (0, eval)(readFile(argv[0]));
  var results = [];
  Object.keys(SCENARIOS).forEach(function (name) {
    try {
      resetWorld();
      SCENARIOS[name]();
      results.push({ name: name, ok: true });
    } catch (error) {
      results.push({ name: name, ok: false, error: String(error && error.message ? error.message : error) });
    }
  });
  return JSON.stringify(results);
}

var world;

function assertEqual(actual, expected, label) {
  var left = JSON.stringify(actual);
  var right = JSON.stringify(expected);
  if (left !== right) {
    throw new Error((label || '값') + ': ' + left + ' != ' + right);
  }
}

function assertThrows(work, pattern) {
  try {
    work();
  } catch (error) {
    if (!pattern.test(error.message)) {
      throw new Error('다른 오류: ' + error.message);
    }
    return;
  }
  throw new Error('오류가 나야 합니다');
}

function response(code, body) {
  return {
    getResponseCode: function () { return code; },
    getContentText: function () { return body || '{}'; },
    getContent: function () { return [1, 2, 3]; },
  };
}

function mail(id, overrides) {
  var message = {
    id: id,
    payload: {
      headers: [
        { name: 'Authentication-Results', value: 'mx.google.com; dkim=pass header.i=@banksalad.com top-' + id },
        { name: 'From', value: 'banksalad <export-noreply@banksalad.com>' },
        { name: 'To', value: 'owner@example.com' },
        { name: 'Authentication-Results', value: 'forged-by-sender' },
      ],
      parts: [
        { filename: '', body: {}, parts: [{ filename: '', body: { data: 'text' } }] },
        { filename: '김철수님_2025-11-04~2026-11-04.zip', body: { attachmentId: 'att-' + id, size: 95511 } },
      ],
    },
  };
  return Object.assign(message, overrides || {});
}

function resetWorld() {
  world = {
    properties: { LEDGER_URL: 'https://ledger.example.com/', LEDGER_HMAC_KEY: 'hmac-key' },
    requests: [],
    responder: function () { return response(200, '{"status":"applied"}'); },
    messages: [],
    listCalls: [],
    attachmentData: 'YWJj',
    triggers: [],
    deleted: [],
    created: [],
    driveFiles: [],
    trashed: [],
    lockFree: true,
    lockReleased: false,
    logs: [],
  };

  globalThis.console = globalThis.console || {};
  console.log = function (text) { world.logs.push(String(text)); };

  globalThis.PropertiesService = {
    getScriptProperties: function () {
      return {
        getProperty: function (key) { return world.properties[key] === undefined ? null : world.properties[key]; },
        setProperty: function (key, value) { world.properties[key] = value; },
      };
    },
  };
  globalThis.LockService = {
    getScriptLock: function () {
      return {
        tryLock: function () { return world.lockFree; },
        releaseLock: function () { world.lockReleased = true; },
      };
    },
  };
  globalThis.Utilities = {
    Charset: { UTF_8: 'UTF-8' },
    computeHmacSha256Signature: function (text, key, charset) {
      world.signed = { text: text, key: key, charset: charset };
      return [-1, 0, 127, -128, 15];
    },
    base64EncodeWebSafe: function (bytes) { return 'encoded:' + bytes.join(','); },
    formatDate: function () { return '20261105-0400'; },
    newBlob: function (content, type, name) { return { content: content, type: type, name: name }; },
  };
  globalThis.UrlFetchApp = {
    fetch: function (url, options) {
      world.requests.push({ url: url, options: options });
      return world.responder(url, options);
    },
  };
  globalThis.Gmail = {
    Users: {
      Messages: {
        list: function (user, options) {
          world.listCalls.push({ user: user, options: options });
          var start = options.pageToken ? Number(options.pageToken) : 0;
          var size = options.maxResults;
          var page = world.messages.slice(start, start + size).map(function (m) { return { id: m.id }; });
          var next = start + size < world.messages.length ? String(start + size) : undefined;
          return { messages: page, nextPageToken: next };
        },
        get: function (user, id) {
          return world.messages.filter(function (m) { return m.id === id; })[0];
        },
        Attachments: {
          get: function () { return { data: world.attachmentData }; },
        },
      },
    },
  };
  globalThis.Drive = {
    Files: {
      create: function (metadata, blob) {
        var file = { id: 'file-' + world.driveFiles.length, metadata: metadata, blob: blob };
        world.driveFiles.push(file);
        return file;
      },
      list: function (options) {
        world.driveListQuery = options.q;
        return { files: world.backupListing || [] };
      },
      update: function (metadata, id) { world.trashed.push({ metadata: metadata, id: id }); },
    },
  };
  function triggerBuilder(handler) {
    var entry = { handler: handler, chain: [] };
    var builder = {
      timeBased: function () { entry.chain.push('timeBased'); return builder; },
      everyMinutes: function (n) { entry.chain.push('every' + n); return builder; },
      onWeekDay: function (day) { entry.chain.push(day); return builder; },
      atHour: function (hour) { entry.chain.push('at' + hour); return builder; },
      create: function () { world.created.push(entry); return entry; },
    };
    return builder;
  }
  globalThis.ScriptApp = {
    WeekDay: { SUNDAY: 'SUNDAY' },
    getProjectTriggers: function () { return world.triggers; },
    deleteTrigger: function (trigger) { world.deleted.push(trigger.getHandlerFunction()); },
    newTrigger: triggerBuilder,
  };
}

function bodyOf(request) {
  return JSON.parse(request.options.payload);
}

var SCENARIOS = {
  'new mail is sent with top authentication header and attachment': function () {
    world.messages = [mail('m1')];
    runIngest();
    var ingest = world.requests.filter(function (r) { return /banksalad$/.test(r.url); })[0];
    assertEqual(ingest.url, 'https://ledger.example.com/api/ingest/banksalad', '주소');
    assertEqual(bodyOf(ingest).message_id, 'm1');
    assertEqual(bodyOf(ingest).auth_results, 'mx.google.com; dkim=pass header.i=@banksalad.com top-m1', '맨 위 헤더');
    assertEqual(bodyOf(ingest).to_headers, ['owner@example.com'], 'To 헤더');
    assertEqual(bodyOf(ingest).from_headers, ['banksalad <export-noreply@banksalad.com>'], 'From 헤더');
    assertEqual(bodyOf(ingest).attachment, 'YWJj');
    assertEqual(world.lockReleased, true, '잠금 해제');
  },
  'query reads only banksalad mail including trash': function () {
    runIngest();
    var options = world.listCalls[0].options;
    assertEqual(options.q, 'from:export-noreply@banksalad.com has:attachment filename:zip newer_than:7d');
    assertEqual(options.includeSpamTrash, true);
  },
  'request is signed over timestamp and body': function () {
    world.messages = [mail('m1')];
    runIngest();
    var ingest = world.requests[0];
    var stamp = ingest.options.headers['X-Ledger-Timestamp'];
    assertEqual(ingest.options.headers['X-Ledger-Signature'], 'ff007f800f', '서명 16진수');
    assertEqual(/^\d+$/.test(stamp), true, '타임스탬프');
    assertEqual(ingest.options.muteHttpExceptions, true);
    assertEqual(ingest.options.followRedirects, false);
    assertEqual(ingest.options.contentType, 'application/json');
  },
  'signed text equals timestamp dot body exactly': function () {
    world.properties.LAST_HEARTBEAT = String(Date.now()); // 하트비트가 끼어들지 않게 한다
    world.messages = [mail('m1')];
    runIngest();
    var ingest = world.requests[0];
    assertEqual(
      world.signed.text,
      ingest.options.headers['X-Ledger-Timestamp'] + '.' + '/api/ingest/banksalad' + '.' + ingest.options.payload
    );
    assertEqual(world.signed.key, 'hmac-key');
    assertEqual(world.signed.charset, 'UTF-8');
  },
  'body is ascii only': function () {
    world.messages = [mail('m1')];
    var value = JSON.parse(toAsciiJson_({ text: '한글 é' }));
    assertEqual(value.text, '한글 é', '되돌림');
    assertEqual(/^[\x00-\x7f]*$/.test(toAsciiJson_({ text: '한글 é' })), true, 'ASCII');
  },
  'byte array attachment is encoded': function () {
    world.attachmentData = [1, 2, 3];
    world.messages = [mail('m1')];
    runIngest();
    assertEqual(bodyOf(world.requests[0]).attachment, 'encoded:1,2,3');
  },
  'processed mail is not sent again': function () {
    world.messages = [mail('m1')];
    runIngest();
    runIngest();
    var ingests = world.requests.filter(function (r) { return /banksalad$/.test(r.url); });
    assertEqual(ingests.length, 1, '전송 횟수');
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['m1']);
  },
  'oldest mail goes first': function () {
    world.messages = [mail('new'), mail('old')];
    runIngest();
    var ids = world.requests
      .filter(function (r) { return /banksalad$/.test(r.url); })
      .map(function (r) { return bodyOf(r).message_id; });
    assertEqual(ids, ['old', 'new']);
  },
  'server error keeps mail for retry and surfaces failure': function () {
    world.messages = [mail('m1')];
    world.responder = function (url) { return /banksalad$/.test(url) ? response(500) : response(200); };
    assertThrows(runIngest, /메일 전송 실패 1건: HTTP 500/);
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS || '[]'), []);
    assertEqual(world.lockReleased, true, '실패해도 잠금 해제');

    world.responder = function () { return response(200, '{"status":"applied"}'); };
    runIngest();
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['m1']);
  },
  'one failure does not block other mail': function () {
    world.messages = [mail('good'), mail('bad')];
    world.responder = function (url, options) {
      if (!/banksalad$/.test(url)) { return response(200); }
      return JSON.parse(options.payload).message_id === 'bad' ? response(500) : response(200, '{"status":"applied"}');
    };
    assertThrows(runIngest, /실패 1건/);
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['good']);
  },
  'rejected and quarantined mail is not retried forever': function () {
    world.messages = [mail('m1')];
    world.responder = function () { return response(200, '{"status":"rejected","reason":"mail_auth_failed"}'); };
    runIngest();
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['m1']);
  },
  'mail without zip is skipped': function () {
    var plain = mail('m1');
    plain.payload.parts = [{ filename: 'note.txt', body: { attachmentId: 'a' } }];
    world.messages = [plain];
    runIngest();
    assertEqual(world.requests.filter(function (r) { return /banksalad$/.test(r.url); }).length, 0);
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['m1']);
  },
  'mail without headers sends empty authentication': function () {
    var bare = mail('m1');
    bare.payload.headers = [];
    world.messages = [bare];
    runIngest();
    assertEqual(bodyOf(world.requests[0]).auth_results, '');
  },
  'processed list stays bounded': function () {
    var many = [];
    for (var index = 0; index < 450; index += 1) { many.push('old-' + index); }
    world.properties.PROCESSED_IDS = JSON.stringify(many);
    world.messages = [mail('new')];
    runIngest();
    var stored = JSON.parse(world.properties.PROCESSED_IDS);
    assertEqual(stored.length, 400);
    assertEqual(stored[399], 'new');
  },
  'concurrent run is skipped': function () {
    world.lockFree = false;
    world.messages = [mail('m1')];
    runIngest();
    assertEqual(world.requests.length, 0);
  },
  'heartbeat is sent at most once a day': function () {
    runIngest();
    var beats = function () {
      return world.requests.filter(function (r) { return /heartbeat$/.test(r.url); }).length;
    };
    assertEqual(beats(), 1, '첫 신호');
    runIngest();
    assertEqual(beats(), 1, '같은 날');
    world.properties.LAST_HEARTBEAT = String(Date.now() - 24 * 60 * 60 * 1000);
    runIngest();
    assertEqual(beats(), 2, '다음 날');
  },
  'heartbeat failure is reported': function () {
    world.responder = function () { return response(401); };
    assertThrows(runIngest, /서버 연결 실패: HTTP 401/);
  },
  'initial import looks back a year': function () {
    world.messages = [mail('m1')];
    runInitialImport();
    assertEqual(world.listCalls[0].options.q.indexOf('newer_than:365d') !== -1, true);
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['m1']);
  },
  'missing settings give a clear error': function () {
    delete world.properties.LEDGER_HMAC_KEY;
    assertThrows(setup, /스크립트 속성/);
    assertEqual(world.created.length, 0);
  },
  'setup replaces only our triggers': function () {
    function trigger(name) { return { getHandlerFunction: function () { return name; } }; }
    world.triggers = [trigger('runIngest'), trigger('someOtherJob'), trigger('runBackup')];
    setup();
    assertEqual(world.deleted, ['runIngest', 'runBackup']);
    assertEqual(world.created.map(function (t) { return t.handler; }), ['runIngest', 'runBackup']);
    assertEqual(world.created[0].chain, ['timeBased', 'every5']);
    assertEqual(world.created[1].chain, ['timeBased', 'SUNDAY', 'at4']);
    assertEqual(world.requests.filter(function (r) { return /heartbeat$/.test(r.url); }).length, 1);
  },
  'backup is stored in its folder and old ones are trashed': function () {
    world.backupListing = [];
    for (var index = 0; index < 14; index += 1) { world.backupListing.push({ id: 'b' + index, name: 'n' }); }
    runBackup();
    assertEqual(world.requests[0].url, 'https://ledger.example.com/api/backup/export');
    assertEqual(world.driveFiles[0].metadata.mimeType, 'application/vnd.google-apps.folder', '폴더');
    assertEqual(world.driveFiles[1].metadata.name, 'ledger-20261105-0400.bak');
    assertEqual(world.driveFiles[1].metadata.parents, ['file-0']);
    assertEqual(world.driveFiles[1].blob.content, [1, 2, 3]);
    assertEqual(world.trashed.map(function (t) { return t.id; }), ['b12', 'b13']);
    assertEqual(world.trashed[0].metadata, { trashed: true });
    assertEqual(world.driveListQuery, "'file-0' in parents and trashed = false");
  },
  'second backup reuses the folder': function () {
    runBackup();
    runBackup();
    var folders = world.driveFiles.filter(function (f) {
      return f.metadata.mimeType === 'application/vnd.google-apps.folder';
    });
    assertEqual(folders.length, 1);
  },
  'heartbeat is sent even when mail fails': function () {
    world.messages = [mail('m1')];
    world.responder = function (url) { return /banksalad$/.test(url) ? response(500) : response(200); };
    assertThrows(runIngest, /메일 전송 실패/);
    assertEqual(world.requests.filter(function (r) { return /heartbeat$/.test(r.url); }).length, 1, '신호');
  },
  'mail failing the quick check is reported without its attachment': function () {
    var forged = mail('m1');
    forged.payload.headers[0].value = 'mx.google.com; dkim=fail header.i=@banksalad.com';
    world.messages = [forged];
    world.responder = function () { return response(200, '{"status":"rejected","reason":"signature"}'); };
    runIngest();
    var body = bodyOf(world.requests[0]);
    assertEqual(body.attachment === undefined, true, '첨부 없음');
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['m1']);
  },
  'oversized attachment is reported without uploading': function () {
    var big = mail('m1');
    big.payload.parts[1].body.size = 4 * 1024 * 1024;
    world.messages = [big];
    runIngest();
    assertEqual(bodyOf(world.requests[0]).attachment === undefined, true);
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['m1']);
  },
  'bad request is not retried forever but is surfaced once': function () {
    world.messages = [mail('m1')];
    world.responder = function (url) { return /banksalad$/.test(url) ? response(413) : response(200); };
    assertThrows(runIngest, /메일 전송 실패 1건: HTTP 413/);
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), ['m1']);
    runIngest();
    assertEqual(world.requests.filter(function (r) { return /banksalad$/.test(r.url); }).length, 1, '재시도 없음');
  },
  'wrong key is retried because it is a setup problem': function () {
    world.messages = [mail('m1')];
    world.responder = function (url) { return /banksalad$/.test(url) ? response(401) : response(200); };
    assertThrows(runIngest, /HTTP 401/);
    assertEqual(JSON.parse(world.properties.PROCESSED_IDS), []);
  },
  'mail flood does not hide the real mail': function () {
    var flood = [mail('real')];
    for (var index = 0; index < 45; index += 1) { flood.unshift(mail('forged-' + index)); }
    world.messages = flood; // 최신순이므로 위조 메일이 앞쪽 페이지를 채운다
    world.responder = function () { return response(200, '{"status":"rejected"}'); };
    runIngest();
    var sent = world.requests
      .filter(function (r) { return /banksalad$/.test(r.url); })
      .map(function (r) { return bodyOf(r).message_id; });
    assertEqual(world.listCalls.length, 3, '페이지 수');
    assertEqual(sent[0], 'real', '가장 오래된 메일부터');
    assertEqual(sent.length, 20, '한 번에 보내는 수 제한');
  },
  'backup failure stores nothing': function () {
    world.responder = function () { return response(507); };
    assertThrows(runBackup, /백업 실패: HTTP 507/);
    assertEqual(world.driveFiles.length, 0);
  },
};
