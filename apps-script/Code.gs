/**
 * 뱅크샐러드 내보내기 메일을 가계부 서버로 보내고, 주 1회 암호화 백업을 Drive에 저장한다.
 *
 * Gmail 권한은 이 스크립트 안에만 있고 서버에는 없다. 서버가 뚫려도 메일함으로 번지지 않는다.
 * 메일은 읽기만 하며 수정하거나 지우지 않는다.
 *
 * 처음 한 번:
 *   1. 프로젝트 설정 > 스크립트 속성에 LEDGER_URL과 LEDGER_HMAC_KEY를 넣는다.
 *   2. 편집기에서 setup을 실행하고 권한을 승인한다.
 */

const SCRIPT_VERSION = '2';
const MAIL_QUERY = 'from:export-noreply@banksalad.com has:attachment filename:zip';
const RECENT_WINDOW = 'newer_than:7d';
const INITIAL_WINDOW = 'newer_than:365d';
const PAGE_SIZE = 20;
const MAX_PAGES = 10;
const MAX_SEND_PER_RUN = 20;
const MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024; // base64로 늘어나도 서버 한도 안에 들어야 한다
const PROCESSED_KEY = 'PROCESSED_IDS';
const PROCESSED_KEEP = 400; // 스크립트 속성 값은 9KB까지다
const HEARTBEAT_KEY = 'LAST_HEARTBEAT';
const HEARTBEAT_INTERVAL_MS = 23 * 60 * 60 * 1000;
const BACKUP_FOLDER_KEY = 'BACKUP_FOLDER_ID';
const BACKUP_FOLDER_NAME = 'Ledger Backups';
const BACKUP_KEEP = 12;
const TRIGGER_HANDLERS = ['runIngest', 'runBackup'];

/** 처음 한 번 실행한다. 권한을 받고 트리거를 만든 뒤 연결을 확인한다. */
function setup() {
  config_();
  ScriptApp.getProjectTriggers()
    .filter(function (trigger) {
      return TRIGGER_HANDLERS.indexOf(trigger.getHandlerFunction()) !== -1;
    })
    .forEach(function (trigger) {
      ScriptApp.deleteTrigger(trigger);
    });
  ScriptApp.newTrigger('runIngest').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('runBackup').timeBased().onWeekDay(ScriptApp.WeekDay.SUNDAY).atHour(4).create();
  sendHeartbeat_();
  console.log('설정 완료: 5분마다 메일 확인, 매주 일요일 새벽 백업');
}

/** 5분마다 실행된다. */
function runIngest() {
  withLock_(function () {
    try {
      processMail_(RECENT_WINDOW);
    } finally {
      heartbeatIfDue_(); // 메일 처리가 실패해도 수신 경로가 살아 있다는 신호는 보낸다
    }
  });
}

/** 예전에 받은 메일까지 한 번에 가져올 때 편집기에서 직접 실행한다. */
function runInitialImport() {
  withLock_(function () {
    processMail_(INITIAL_WINDOW);
  });
}

/** 매주 실행된다. 서버가 암호화한 백업을 받아 Drive에 저장한다. */
function runBackup() {
  const response = post_('/api/backup/export', { script_version: SCRIPT_VERSION });
  const code = response.getResponseCode();
  if (code !== 200) {
    throw new Error('백업 실패: HTTP ' + code);
  }
  const folderId = backupFolderId_();
  const name = 'ledger-' + Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyyMMdd-HHmm') + '.bak';
  const blob = Utilities.newBlob(response.getContent(), 'application/octet-stream', name);
  Drive.Files.create({ name: name, parents: [folderId], mimeType: 'application/octet-stream' }, blob, { fields: 'id' });
  pruneBackups_(folderId);
  console.log('백업 저장 완료: ' + name);
}

/** 서버 연결만 확인한다. 설정이 맞는지 볼 때 편집기에서 실행한다. */
function checkConnection() {
  sendHeartbeat_();
  console.log('서버 연결 정상');
}

function withLock_(work) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) {
    return; // 이전 실행이 아직 도는 중이면 이번에는 건너뛴다
  }
  try {
    work();
  } finally {
    lock.releaseLock();
  }
}

function processMail_(window) {
  const properties = PropertiesService.getScriptProperties();
  const processed = JSON.parse(properties.getProperty(PROCESSED_KEY) || '[]');
  const waiting = listMessageIds_(window)
    .filter(function (id) {
      return processed.indexOf(id) === -1;
    })
    .slice(0, MAX_SEND_PER_RUN);

  const failures = [];
  waiting.forEach(function (id) {
    try {
      const result = sendMessage_(id);
      processed.push(id);
      console.log('메일 처리: ' + result.status + (result.reason ? ' (' + result.reason + ')' : ''));
      if (result.permanentFailure) {
        failures.push(result.reason); // 다시 보내도 소용없으니 한 번만 알린다
      }
    } catch (error) {
      failures.push(error.message); // 처리 목록에 넣지 않았으므로 다음 실행에서 다시 시도한다
    }
  });
  properties.setProperty(PROCESSED_KEY, JSON.stringify(processed.slice(-PROCESSED_KEEP)));

  if (failures.length > 0) {
    throw new Error('메일 전송 실패 ' + failures.length + '건: ' + failures[0]);
  }
}

/** 오래된 것부터 돌려준다. 위조 메일이 많이 와도 정상 메일이 목록에서 밀려나지 않게 페이지를 끝까지 읽는다. */
function listMessageIds_(window) {
  const ids = [];
  let pageToken = null;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const options = {
      q: MAIL_QUERY + ' ' + window,
      maxResults: PAGE_SIZE,
      includeSpamTrash: true, // 처리한 메일을 자동 삭제하도록 설정해도 읽을 수 있어야 한다
    };
    if (pageToken) {
      options.pageToken = pageToken;
    }
    const listed = Gmail.Users.Messages.list('me', options);
    (listed.messages || []).forEach(function (item) {
      ids.push(item.id);
    });
    pageToken = listed.nextPageToken;
    if (!pageToken) {
      break;
    }
  }
  return ids.reverse();
}

function sendMessage_(messageId) {
  const message = Gmail.Users.Messages.get('me', messageId, { format: 'full' });
  const part = findZipPart_(message.payload);
  if (!part) {
    return { status: 'skipped', reason: 'ZIP 첨부 없음' };
  }

  const payload = {
    message_id: messageId,
    auth_results: topAuthenticationResults_(message.payload),
    to_headers: headerValues_(message.payload, 'to'),
    from_headers: headerValues_(message.payload, 'from'),
  };
  // 위조가 의심되거나 큰 첨부는 올리지 않는다. 최종 판단은 서버가 하지만, 올리다 실패하면 재시도만 반복한다.
  if (looksAuthentic_(payload.auth_results) && (part.body.size || 0) <= MAX_ATTACHMENT_BYTES) {
    const attachment = Gmail.Users.Messages.Attachments.get('me', messageId, part.body.attachmentId);
    payload.attachment = attachmentToBase64_(attachment);
  }

  const response = post_('/api/ingest/banksalad', payload);
  const code = response.getResponseCode();
  if (code === 200) {
    return JSON.parse(response.getContentText());
  }
  if (isPermanentFailure_(code)) {
    return { status: 'failed', reason: 'HTTP ' + code, permanentFailure: true };
  }
  throw new Error('HTTP ' + code);
}

/** 설정 오류(401)와 일시적 오류는 다시 시도하고, 요청 자체가 잘못된 경우는 포기한다. */
function isPermanentFailure_(code) {
  return code >= 400 && code < 500 && code !== 401 && code !== 408 && code !== 429;
}

function findZipPart_(part) {
  if (part.filename && /\.zip$/i.test(part.filename) && part.body && part.body.attachmentId) {
    return part;
  }
  const children = part.parts || [];
  for (let index = 0; index < children.length; index += 1) {
    const found = findZipPart_(children[index]);
    if (found) {
      return found;
    }
  }
  return null;
}

function headerValues_(payload, lowerCaseName) {
  return (payload.headers || [])
    .filter(function (header) {
      return header.name.toLowerCase() === lowerCaseName;
    })
    .map(function (header) {
      return header.value;
    });
}

/** 맨 위 헤더만 Gmail이 직접 쓴 것이다. 그 아래는 보낸 쪽이 넣었을 수 있다. */
function topAuthenticationResults_(payload) {
  const values = headerValues_(payload, 'authentication-results');
  return values.length > 0 ? values[0] : '';
}

/** 첨부를 올릴지 정하는 간단한 사전 확인이다. 위조 판단의 기준은 서버 쪽 검사다. */
function looksAuthentic_(authenticationResults) {
  return /dkim=pass[^;]*header\.i=@banksalad\.com/i.test(authenticationResults);
}

/** 고급 서비스는 첨부를 문자열 또는 바이트 배열로 돌려준다. 어느 쪽이든 받는다. */
function attachmentToBase64_(attachment) {
  const data = attachment.data;
  return typeof data === 'string' ? data : Utilities.base64EncodeWebSafe(data);
}

function heartbeatIfDue_() {
  const properties = PropertiesService.getScriptProperties();
  const last = Number(properties.getProperty(HEARTBEAT_KEY) || 0);
  if (Date.now() - last >= HEARTBEAT_INTERVAL_MS) {
    sendHeartbeat_();
  }
}

function sendHeartbeat_() {
  const response = post_('/api/ingest/heartbeat', { script_version: SCRIPT_VERSION });
  const code = response.getResponseCode();
  if (code !== 200) {
    throw new Error('서버 연결 실패: HTTP ' + code);
  }
  PropertiesService.getScriptProperties().setProperty(HEARTBEAT_KEY, String(Date.now()));
}

function backupFolderId_() {
  const properties = PropertiesService.getScriptProperties();
  const existing = properties.getProperty(BACKUP_FOLDER_KEY);
  if (existing) {
    return existing;
  }
  const folder = Drive.Files.create({ name: BACKUP_FOLDER_NAME, mimeType: 'application/vnd.google-apps.folder' });
  properties.setProperty(BACKUP_FOLDER_KEY, folder.id);
  return folder.id;
}

/** 최근 백업만 남긴다. 휴지통으로 보내므로 30일 안에는 되살릴 수 있다. */
function pruneBackups_(folderId) {
  const listed = Drive.Files.list({
    q: "'" + folderId + "' in parents and trashed = false",
    orderBy: 'createdTime desc',
    fields: 'files(id, name)',
    pageSize: 100,
  });
  (listed.files || []).slice(BACKUP_KEEP).forEach(function (file) {
    Drive.Files.update({ trashed: true }, file.id);
  });
}

function config_() {
  const properties = PropertiesService.getScriptProperties();
  const url = properties.getProperty('LEDGER_URL');
  const key = properties.getProperty('LEDGER_HMAC_KEY');
  if (!url || !key) {
    throw new Error('스크립트 속성에 LEDGER_URL과 LEDGER_HMAC_KEY를 넣어 주세요.');
  }
  return { url: url.replace(/\/+$/, ''), key: key };
}

function post_(path, payload) {
  const config = config_();
  const body = toAsciiJson_(payload);
  const timestamp = String(Math.floor(Date.now() / 1000));
  return UrlFetchApp.fetch(config.url + path, {
    method: 'post',
    contentType: 'application/json',
    payload: body,
    headers: {
      'X-Ledger-Timestamp': timestamp,
      'X-Ledger-Signature': sign_(timestamp + '.' + path + '.' + body, config.key),
    },
    muteHttpExceptions: true,
    followRedirects: false,
  });
}

/** 서명한 바이트와 보낸 바이트가 같도록 본문을 ASCII로만 만든다. */
function toAsciiJson_(value) {
  return JSON.stringify(value).replace(/[\u0080-\uffff]/g, function (character) {
    return '\\u' + ('0000' + character.charCodeAt(0).toString(16)).slice(-4);
  });
}

function sign_(text, key) {
  const bytes = Utilities.computeHmacSha256Signature(text, key, Utilities.Charset.UTF_8);
  return bytes
    .map(function (value) {
      const unsigned = value < 0 ? value + 256 : value;
      return ('0' + unsigned.toString(16)).slice(-2);
    })
    .join('');
}
