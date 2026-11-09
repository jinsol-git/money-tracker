# 설치 매뉴얼

뱅크샐러드 내보내기 메일을 자동으로 받아 쌓는 개인 가계부를 **내 계정에 직접 설치**하는 안내입니다. 개발을 몰라도 위에서부터 순서대로 따라 하면 됩니다. 터미널 명령은 복사해서 붙여 넣기만 하면 됩니다.

- **걸리는 시간:** 처음이면 1시간쯤 잡으세요.
- **비용:** 모두 무료이고 카드 등록이 필요 없습니다. 결제 계정을 연결하라는 안내가 나와도 연결하지 마세요.
- **쉬어도 되는 곳:** 단계 사이에 쉬어도 됩니다. 단, **5단계와 6단계는 이어서** 하세요.

설치가 끝나면 메일로 받은 데이터가 앱에 자동으로 쌓이고, 매주 Google Drive에 암호화 백업이 저장됩니다. 앱에서 월별 지출과 카테고리별 분석, 거래 내역, 자산을 볼 수 있습니다.

## 전체 순서

| 단계 | 하는 일 | 하는 곳 | 끝나면 생기는 것 |
|---|---|---|---|
| 0 | 금고, 코드, 도구, 내보내기 준비 | Bitwarden, GitHub, 터미널, 뱅크샐러드 앱 | 값을 모아 둘 메모, 코드, 내보내기 메일 |
| 1 | 비밀 키 2개 만들기 | 터미널 | `MASTER_KEY`, `INGEST_HMAC_KEY` |
| 2 | 데이터베이스 만들기 | Neon, 터미널 | Pooled 주소, Direct 주소 |
| 3 | 앱 올리기 | Vercel | 내 앱 주소 |
| 4 | Google 로그인 연결 | Google Cloud | 클라이언트 ID, 보안 비밀번호 |
| 5 | 앱에 값 7개 넣기 | Vercel | 실행되는 앱 |
| 6 | 첫 로그인과 OTP 등록 | 내 앱, 휴대폰 | 복구 코드 10개 |
| 7 | 메일 자동 수신 켜기 | Apps Script | 자동 수신, 첫 데이터, 첫 백업 |
| 8 | 마무리 | Gmail, 내 앱 | 설치 완료 |

## 어떻게 동작하나요

```mermaid
flowchart LR
  A[뱅크샐러드 앱] -->|내보내기 메일| B[내 Gmail]
  B -->|5분마다 확인| C[Apps Script]
  C -->|서명해서 전송| D[내 앱 · Vercel]
  D <--> E[(데이터베이스 · Neon)]
  D -->|매주 암호화 백업| C
  C --> F[내 Google Drive]
```

## 가입할 곳

| 서비스 | 하는 일 | 가입 방법 |
|---|---|---|
| Bitwarden | 비밀값을 보관하는 금고 | 이메일로 가입 |
| GitHub | 코드 보관 | 이메일로 가입 |
| Neon | 데이터베이스 | GitHub로 가입 |
| Vercel | 앱 실행 | GitHub로 가입 |
| Google Cloud | "Google로 로그인" 허가 | 가입 없이 내 Gmail로 사용 |
| Apps Script | 메일을 앱으로 전달 | 가입 없이 내 Gmail로 사용 |
| Google OTP | 로그인할 때 쓰는 6자리 코드 | 휴대폰 앱, 계정 없이 사용 |

**내 Gmail은 하나로 통일하세요.** 이 문서의 "내 Gmail"은 뱅크샐러드 내보내기를 받는 주소입니다. 앱 로그인과 Apps Script도 같은 계정이어야 합니다. 서버가 받는 사람 주소를 그대로 비교하므로 `my.name@gmail.com`과 `myname@gmail.com`도 다른 주소로 봅니다.

---

## 0단계: 준비

### 0-1. Bitwarden에 값 메모 만들기

앞으로 나오는 값을 모아 두는 곳입니다. Gmail이나 Chrome 비밀번호 관리자는 Google 계정이 뚫리면 함께 노출되니 쓰지 마세요.

1. [bitwarden.com](https://bitwarden.com)에서 가입합니다. **마스터 비밀번호는 잊으면 Bitwarden도 복구해 줄 수 없습니다.**
2. [웹 보관함](https://vault.bitwarden.com)에서 오른쪽 위 **New > Note**를 누릅니다.
3. **Name**에 `money-tracker`를 넣고, **Notes**에 아래 내용을 붙여 넣은 뒤 **Save**를 누릅니다.

```text
내 Gmail:
내 앱 주소:
MASTER_KEY:
INGEST_HMAC_KEY:
Neon Pooled 주소:
Neon Direct 주소:
GOOGLE_CLIENT_ID:
GOOGLE_CLIENT_SECRET:
뱅크샐러드 파일 비밀번호:
복구 코드:
```

값이 나올 때마다 이 메모를 열어 채우고 저장하세요. 5단계에서 그대로 복사하면 됩니다.

### 0-2. GitHub에 내 코드 만들기

1. [github.com](https://github.com)에서 가입합니다.
2. [프로젝트 저장소](https://github.com/jinsol-git/money-tracker)에서 **Use this template > Create a new repository**를 누릅니다.
3. 아래처럼 고르고 **Create repository**를 누릅니다.
   - **Owner:** 내 개인 계정. Vercel 무료 요금제는 조직 소유 저장소를 연결할 수 없습니다.
   - **Repository name:** `my-ledger`
   - **공개 범위:** **Private**
4. 만들어진 내 저장소에서 **Code > Download ZIP**을 눌러 받고 압축을 풉니다.

### 0-3. 터미널 도구 설치

터미널은 명령을 입력하는 창입니다.

- **Windows:** 시작 메뉴에서 `PowerShell`을 검색해 엽니다.
- **Mac:** `Cmd+Space`를 누르고 `터미널`을 검색해 엽니다.

아래 명령을 붙여 넣고 Enter를 누릅니다. Python은 이 도구가 알아서 받으니 따로 설치하지 않습니다.

Windows:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Mac:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치가 끝나면 **터미널을 닫았다가 다시 열고** 아래를 실행합니다. 버전 번호가 나오면 성공입니다.

```bash
uv --version
```

### 0-4. 뱅크샐러드에서 내보내기

1. 뱅크샐러드 앱 홈의 오른쪽 위 메뉴에서 **설정 > 데이터 내보내기 > 파일로 받기**로 갑니다. 메뉴 이름은 앱 버전에 따라 조금 다를 수 있습니다.
2. 이메일에 **내 Gmail**을 넣고, 파일 비밀번호를 새로 정합니다. 4자 이상으로 정하세요. 로그인 비밀번호와 다른 값입니다.
3. 비밀번호를 Bitwarden의 `뱅크샐러드 파일 비밀번호`에 적습니다.
4. Gmail에 `export-noreply@banksalad.com`이 보낸 메일이 왔는지 확인합니다.

---

## 1단계: 비밀 키 2개 만들기

**압축을 푼 폴더에서 터미널을 엽니다.** `pyproject.toml` 파일이 보이는 폴더입니다.

- **Windows:** 탐색기에서 그 폴더를 열고, 위쪽 주소창을 클릭해 `powershell`을 입력한 뒤 Enter를 누릅니다.
- **Mac:** 터미널에 `cd `(뒤에 공백 한 칸)를 입력하고, 폴더를 터미널 창으로 끌어다 놓은 뒤 Enter를 누릅니다.

아래 명령을 실행합니다. 처음에는 필요한 파일을 받느라 몇 분 걸릴 수 있습니다.

```bash
uv run --locked python scripts/generate_secrets.py
```

안내 문구 아래 **맨 끝 두 줄**을 씁니다. Vercel에 넣으라는 문구도 함께 나오는데, 그건 5단계에서 합니다. 아래 값은 가짜 예시이고, 실제 값은 실행할 때마다 달라집니다.

```text
MASTER_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
INGEST_HMAC_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCdEfGhIjKlMnOpQrStUvWxYz
```

`=` **뒤의 값만** Bitwarden의 같은 이름 칸에 저장합니다. 여러 번 실행했다면 마지막 값을 쓰세요.

> **주의:** `MASTER_KEY`를 잃으면 백업을 복원할 수 없습니다. Vercel에 넣은 뒤에는 다시 읽을 수 없으니 Bitwarden의 값이 유일한 사본입니다.

**성공 확인:** Bitwarden 메모를 다시 열었을 때 두 값이 들어 있습니다.

---

## 2단계: 데이터베이스 만들기 (Neon)

### 2-1. 가입하고 프로젝트 만들기

[neon.com](https://neon.com)에서 **GitHub로 가입**합니다. Google 로그인은 쓰지 마세요. Gmail이 뚫려도 데이터베이스는 지키기 위해서입니다.

가입 중에 아래 화면이 나오면 이렇게 고릅니다.

| 화면 문구 | 고를 것 |
|---|---|
| `Let's install the Neon skill` | 아무것도 설치하지 않고 `It's installed, let's continue` |
| `what else does your app need?` | 아무것도 체크하지 않고 `Just Postgres for now` |
| `Paste this prompt into Claude Code` | 프롬프트는 쓰지 않고 `Go to project` |

지역을 고르는 칸이 나오면 **AWS Asia Pacific (Singapore)** 를 고릅니다.

### 2-2. 주소 2개 복사하기

프로젝트 화면 위쪽의 **Connect**를 누릅니다. **Connection pooling** 스위치에 따라 주소가 바뀝니다. 설정이 바뀌는 게 아니라 보이는 주소만 바뀝니다.

| 스위치 | 복사해서 저장할 칸 |
|---|---|
| 켜짐 | `Neon Pooled 주소` |
| 꺼짐 | `Neon Direct 주소` |

복사한 주소는 아래처럼 생겼습니다. 가짜 예시이고, 다른 부분은 `-pooler` 유무뿐입니다.

```text
postgresql://neondb_owner:npg_AbCd1234EfGh@ep-cool-river-a1b2c3d4-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
postgresql://neondb_owner:npg_AbCd1234EfGh@ep-cool-river-a1b2c3d4.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

- `postgresql://`부터 끝까지 한 줄 전체를 복사합니다. 앞에 `psql`이 붙어 있으면 따옴표 안의 주소만 씁니다.
- 주소에 `****`가 있으면 비밀번호가 가려진 것입니다. 복사 버튼을 쓰세요.
- 주소에 `ap-southeast-1`이 없으면 싱가포르가 아닙니다. 앱이 느려질 수 있으니 프로젝트를 지우고 싱가포르로 다시 만드세요.

### 2-3. 데이터베이스에 표 만들기

1단계처럼 코드 폴더에서 터미널을 열고, 아래 명령을 **먼저** 실행합니다. `Direct 주소:`가 나오면 **Direct 주소**를 붙여 넣고 Enter를 누릅니다.

Windows:

```powershell
$env:DATABASE_URL = Read-Host "Direct 주소"; uv run --locked alembic upgrade head; Remove-Item Env:DATABASE_URL
```

Mac:

```bash
printf 'Direct 주소: '; read -r DATABASE_URL; export DATABASE_URL; uv run --locked alembic upgrade head; unset DATABASE_URL
```

**성공하면 아무것도 출력되지 않고** 입력 대기 상태로 돌아옵니다. 빨간 오류가 없으면 정상입니다.

**성공 확인:** Neon 왼쪽 메뉴의 **Postgres database > SQL Editor**에서 아래를 실행합니다. `0002` 같은 번호가 나오면 성공입니다.

```sql
SELECT version_num FROM alembic_version;
```

`relation "alembic_version" does not exist`가 나오면 표가 만들어지지 않은 것입니다. 2-3을 다시 하세요.

| 터미널에서 막히면 | 해결 |
|---|---|
| `uv`를 찾을 수 없다는 오류 | 터미널을 닫았다가 다시 엽니다 |
| `Failed to spawn: alembic` 또는 `can't open file` | 다른 폴더에서 실행한 것입니다. `pyproject.toml`이 보이는 폴더에서 터미널을 다시 엽니다 |
| `password authentication failed` 또는 주소를 찾을 수 없다는 오류 | Neon의 복사 버튼으로 Direct 주소를 다시 복사해 한 줄 전체를 넣습니다 |

---

## 3단계: 앱 올리기 (Vercel)

1. [vercel.com](https://vercel.com)에서 **GitHub로 가입**하고 **Hobby** 요금제를 고릅니다.
2. **Add New > Project**에서 `my-ledger` 저장소를 **Import**합니다. 저장소가 안 보이면 GitHub 권한 화면에서 이 저장소를 선택해 허용합니다.
3. **Project Name**을 정합니다. 이 이름이 주소가 되니 추측하기 어렵게 만드세요. 예: `ledger-7f3k`
   - 소문자, 숫자, `.`, `_`, `-`만 쓸 수 있습니다. `!`와 대문자는 안 됩니다.
4. Framework는 `FastAPI` 그대로 두고, 환경변수는 비워 둔 채 **Deploy**를 누릅니다.
5. 끝나면 화면에 나온 주소를 Bitwarden의 `내 앱 주소`에 저장합니다. 예: `https://ledger-7f3k.vercel.app`

**성공 확인:** 배포가 완료로 표시됩니다. **지금 주소를 열면 500 오류(`FUNCTION_INVOCATION_FAILED`)가 나는데 정상입니다.** 값을 넣는 5단계 전이기 때문입니다.

| 막히면 | 해결 |
|---|---|
| 배포가 `Build Failed`로 끝남 | 그 배포의 로그에서 빨간 줄을 복사해 [막혔을 때](#막혔을-때)대로 합니다 |
| 주소를 열면 Vercel 로그인 화면이 나옴 | 프로젝트의 **Settings > Deployment Protection**을 **Standard Protection**으로 바꿉니다 |

---

## 4단계: Google 로그인 연결 (Google Cloud)

**내 Gmail 계정**으로 진행합니다. 오른쪽 위 프로필에서 계정을 확인하세요.

### 4-1. 프로젝트 만들기

[Google Cloud 콘솔](https://console.cloud.google.com)에서 **새 프로젝트**를 만듭니다. 이름은 `Ledger`, 조직은 **조직 없음** 그대로 두고, 만들어진 프로젝트를 선택합니다.

### 4-2. 초기 설정

위쪽 검색창에 `Google Auth Platform`을 입력해 열고 **시작하기**를 누릅니다.

| 항목 | 입력값 |
|---|---|
| 앱 이름 | `Ledger` |
| 사용자 지원 이메일 | 내 Gmail |
| 대상 | **외부** |
| 연락처 정보 | 내 Gmail |

약관에 동의하고 **만들기**를 누릅니다.

### 4-3. 데이터 액세스

1. 왼쪽 **데이터 액세스**에서 **범위 추가 또는 삭제**를 누릅니다.
2. `openid`와 `.../auth/userinfo.email` **두 개만** 체크합니다. 안 보이면 필터에 `userinfo.email`을 입력합니다.
3. **업데이트**를 누르고, 페이지 맨 아래 **저장**까지 누릅니다.

### 4-4. 테스트 사용자

왼쪽 **대상**의 **테스트 사용자**에서 **Add users**를 누르고 내 Gmail을 추가합니다.

> **주의:** **앱 게시는 누르지 마세요.** 누르면 홈페이지와 개인정보처리방침 URL을 요구합니다. 이 앱은 혼자 쓰고 이메일 확인에만 Google을 쓰므로 "테스트" 상태로 충분합니다.

### 4-5. 클라이언트 만들기

왼쪽 **클라이언트**에서 **클라이언트 만들기**를 누릅니다.

| 항목 | 입력값 |
|---|---|
| 애플리케이션 유형 | **웹 애플리케이션** |
| 이름 | `Ledger` |
| 승인된 JavaScript 원본 | 비워 둠 |
| 승인된 리디렉션 URI | **URI 추가**를 누르고 아래 주소 입력 |

`내 앱 주소` 뒤에 `/auth/callback`을 붙인 주소입니다. 예:

```text
https://ledger-7f3k.vercel.app/auth/callback
```

**만들기**를 누르고, 창을 닫기 전에 두 값을 Bitwarden에 저장합니다.

- **클라이언트 ID** → `GOOGLE_CLIENT_ID`
- **클라이언트 보안 비밀번호** → `GOOGLE_CLIENT_SECRET`

**성공 확인:** 클라이언트 목록에 `Ledger`가 있고, Bitwarden의 두 칸이 채워져 있습니다.

---

## 5단계: 앱에 값 7개 넣기 (Vercel)

> **주의:** 이 단계를 마치면 **바로 6단계**를 하세요. OTP를 등록하기 전까지는 내 Google 계정으로 로그인한 사람이 OTP를 처음 등록할 수 있습니다.

1. Vercel에서 내 프로젝트를 열고 **왼쪽 사이드바의 Environment Variables**를 누릅니다. **Create Environment**는 다른 기능이니 누르지 마세요.
2. 아래 값을 하나씩 넣고 **Save**를 누릅니다. **Type은 Secret, Environments는 Production만** 선택합니다.

| Name | Value |
|---|---|
| `DATABASE_URL` | `Neon Pooled 주소` 전체 (`-pooler`가 있는 쪽) |
| `MASTER_KEY` | Bitwarden의 값 |
| `INGEST_HMAC_KEY` | Bitwarden의 값 |
| `GOOGLE_CLIENT_ID` | Bitwarden의 값 |
| `GOOGLE_CLIENT_SECRET` | Bitwarden의 값 |
| `OWNER_EMAIL` | 내 Gmail 주소 |
| `BASE_URL` | 내 앱 주소. 예: `https://ledger-7f3k.vercel.app` (끝에 `/` 없이) |

Value 칸에는 **값만** 넣습니다. `MASTER_KEY=` 같은 이름과 앞뒤 공백이 들어가면 안 됩니다. Secret은 저장 후 다시 볼 수 없으니, 잘못 넣었으면 값을 새로 입력합니다.

3. **Deployments**에서 맨 위 배포의 **⋯ > Redeploy**를 누릅니다. 값은 새 배포부터 적용되므로 저장만으로는 바뀌지 않습니다.

**성공 확인:** 재배포가 끝난 뒤 브라우저에서 `내 앱 주소/healthz`를 엽니다. 예: `https://ledger-7f3k.vercel.app/healthz`. 아래처럼 나오면 성공입니다.

```json
{"ok":true}
```

이 확인은 앱이 켜졌는지만 봅니다. 데이터베이스 주소와 Google 값이 맞는지는 6단계 로그인에서 확인됩니다.

**500 오류가 계속되면** Vercel 왼쪽 **Logs**에서 빨간 500 항목을 눌러 오류의 마지막 줄을 봅니다. 값을 고친 뒤에는 Redeploy를 다시 눌러야 합니다.

| 마지막 줄 | 해결 |
|---|---|
| `환경변수가 없습니다: ...` | 표시된 이름을 추가합니다. 7개가 전부 표시되면 Production을 체크했는지, 값을 저장한 **뒤에** Redeploy했는지 봅니다 |
| `MASTER_KEY는 16진수 문자열이어야 합니다.` | `MASTER_KEY=`를 빼고 값만 넣습니다 |
| `MASTER_KEY는 32바이트(16진수 64자)여야 합니다.` | 64자를 빠짐없이 다시 넣습니다 |
| `INGEST_HMAC_KEY는 32자 이상이어야 합니다.` | 값 전체를 다시 넣습니다 |
| `BASE_URL은 https여야 합니다.` | `https://`로 시작하는 주소를 넣습니다 |
| `Could not parse` | `DATABASE_URL`에 `postgresql://`로 시작하는 주소만 넣습니다 |

---

## 6단계: 첫 로그인과 OTP 등록

> **주의: Google 로그인 후 5분 안에 등록까지 끝내야 합니다.** 새로고침해도 시간은 늘어나지 않습니다. 아래 준비를 먼저 하고, 시작하기 전에 순서를 끝까지 읽으세요.

**준비**

- 휴대폰에 **Google OTP** 앱(영문 이름 Google Authenticator)을 설치하고 **계정 없이 사용**을 고릅니다. 클라우드 동기화를 켜면 Google 계정이 뚫릴 때 OTP도 함께 넘어갑니다.
- PC에서 Bitwarden 메모를 열어 둡니다.
- **브라우저 하나에서만** 진행합니다. QR은 브라우저마다 다릅니다.

**순서**

1. 내 앱 주소를 열고 **Google로 로그인**을 눌러 내 Gmail로 로그인합니다. 확인되지 않은 앱이라는 경고가 나오면 **계속**을 누릅니다.
2. Google OTP 앱에서 **+ > QR 코드 스캔**으로 화면의 QR을 찍습니다.
3. 앱에 나온 6자리를 화면에 입력하고 **등록**을 누릅니다. **QR만 찍으면 등록되지 않습니다.**
4. **복구 코드 10개**를 Bitwarden의 `복구 코드`에 저장합니다. **이 화면에서만 볼 수 있습니다.** 저장을 확인한 뒤 **저장했습니다**를 누릅니다. 놓쳤다면 **설정**의 **복구 코드 새로 발급**으로 다시 받습니다.
5. 위쪽 **설정**을 누르고, **뱅크샐러드 파일 비밀번호**의 **새 비밀번호**에 0-4에서 정한 값을 넣어 **저장**합니다. **OTP 코드** 칸이 보이면 6자리도 함께 입력합니다.

**성공 확인:** 설정 화면의 파일 비밀번호 **상태**가 `설정됨`입니다. 이제 다른 브라우저나 휴대폰에서는 QR 없이 Google 로그인과 6자리 입력만 하면 됩니다.

이때 화면 위쪽에 **데이터, 신호, 백업이 아직 없다는 경고 3개**가 보이는 건 정상입니다. 7단계를 마치면 사라집니다.

QR과 설정 키는 OTP 비밀값입니다. 이 화면을 찍어서 다른 사람에게 보내지 마세요.

| 막히면 | 원인과 해결 |
|---|---|
| QR 화면에서 등록을 눌렀더니 다시 로그인 화면이 나옴 | 5분이 지났습니다. 다시 로그인하세요. QR이 바뀌므로 OTP 앱의 옛 항목을 지우고 새 QR을 찍습니다 |
| 등록을 마친 뒤 같은 문구가 나옴 | 오래 열어 둔 화면의 로그인이 끝난 것입니다. **OTP 항목은 지우지 말고** 앱 주소를 다시 열어 로그인만 합니다 |
| 다른 브라우저에서 QR이 또 나옴 | 아직 등록이 끝나지 않았습니다. 지금 브라우저에서 QR부터 다시 찍고 **등록**까지 누릅니다 |
| `코드가 맞지 않습니다` | 같은 이름의 항목이 여러 개면 마지막에 추가한 항목을 씁니다. 휴대폰 시간이 자동 설정인지도 봅니다. 두 번까지는 바로 다시 할 수 있고, 그다음부터는 틀릴 때마다 30초, 1분, 5분, 15분, 1시간, 6시간을 기다립니다 |
| `이 계정으로는 사용할 수 없습니다.` | Vercel의 `OWNER_EMAIL`이 로그인한 주소와 글자까지 같은지 봅니다. 고쳤으면 Redeploy합니다 |
| `로그인을 완료하지 못했습니다` | 다시 해도 같으면 `GOOGLE_CLIENT_SECRET`이 틀린 것입니다. 값을 다시 넣고 Redeploy합니다 |
| `일시적인 오류가 발생했습니다.` | Vercel **Logs**의 빨간 항목을 봅니다. `type=OperationalError`면 `DATABASE_URL`을 다시 넣고 Redeploy합니다. `type=ProgrammingError`면 2-3을 합니다 |
| Google 화면의 `redirect_uri_mismatch` | 4-5의 리디렉션 URI가 `내 앱 주소/auth/callback`과 글자까지 같은지 봅니다 |
| Google 화면의 `invalid_client` | `GOOGLE_CLIENT_ID`를 다시 넣고 Redeploy합니다 |
| Google 화면의 `access_denied` | 4-4의 테스트 사용자에 내 Gmail이 있는지 봅니다 |

**OTP 앱의 옛 항목 지우기:** Android는 항목을 오른쪽으로 밀어 **삭제**합니다. iPhone은 공식 안내에 메뉴가 나와 있지 않아 앱 화면에서 확인하세요.

---

## 7단계: 메일 자동 수신 켜기 (Apps Script)

**크롬 시크릿 창(`Ctrl+Shift+N`, Mac은 `Cmd+Shift+N`)에서 내 Gmail로만 로그인하고 진행하세요.** Apps Script는 여러 계정 동시 로그인을 지원하지 않고, 다른 계정으로 만들면 그 계정의 메일함을 읽게 됩니다.

### 7-1. 프로젝트 만들기

1. [script.google.com](https://script.google.com)에서 **새 프로젝트**를 누릅니다. 코드 창에 `function myFunction()`이 보이는데 정상입니다.
2. 왼쪽 톱니바퀴 **프로젝트 설정**에서 `"appsscript.json" 매니페스트 파일 표시`를 체크합니다.
3. 왼쪽 `< >` **편집기**로 돌아옵니다. 파일 목록에 `Code.gs`와 `appsscript.json`이 보입니다.

### 7-2. 코드 붙여 넣기

내 GitHub 저장소에서 파일을 열고 코드 오른쪽 위의 복사 아이콘(**Copy raw file**)을 누른 뒤, 편집기의 **같은 이름 파일**을 눌러 `Ctrl+A`, `Ctrl+V`로 덮어씁니다.

| 편집기 파일 | 복사할 파일 | 첫 줄 |
|---|---|---|
| `Code.gs` | `apps-script/Code.gs` | `/**` |
| `appsscript.json` | `apps-script/appsscript.json` | `{` |

두 파일을 **바꿔 넣으면 오류**가 납니다. 다 넣었으면 `Ctrl+S`로 저장합니다.

### 7-3. 스크립트 속성 넣기

**프로젝트 설정** 맨 아래 **스크립트 속성**에서 **스크립트 속성 추가**를 눌러 2개를 넣고 **스크립트 속성 저장**을 누릅니다.

| 속성 | 값 |
|---|---|
| `LEDGER_URL` | 내 앱 주소. 예: `https://ledger-7f3k.vercel.app` |
| `LEDGER_HMAC_KEY` | Bitwarden의 `INGEST_HMAC_KEY` 값 |

### 7-4. 함수 3개 실행하기

**편집기** 위쪽 함수 목록에서 아래 순서로 하나씩 고르고 **실행**을 누릅니다. 앞 함수가 끝난 뒤 다음 함수를 실행합니다.

| 순서 | 함수 | 하는 일 | 성공하면 실행 로그에 나오는 내용 |
|---|---|---|---|
| 1 | `setup` | 자동 실행 등록, 연결 확인 | `설정 완료: 5분마다 메일 확인, 매주 일요일 새벽 백업` |
| 2 | `runInitialImport` | 최근 1년 메일 가져오기 | 메일마다 `메일 처리: applied` 한 줄 |
| 3 | `runBackup` | 첫 백업 저장 | `백업 저장 완료: ledger-20261105-2231.bak` 형식의 줄 |

**처음 `setup`을 실행하면 권한을 묻습니다.**

1. **권한 검토**를 누르고 내 Gmail을 고릅니다.
2. Google에서 확인하지 않은 앱이라는 경고가 나오면 **고급 > (프로젝트 이름)(으)로 이동**을 누릅니다. 내가 만든 스크립트라서 나오는 경고입니다.
3. 체크박스가 나오면 **모두 선택**하고 **계속**을 누릅니다. 일부만 허용하면 실행이 실패할 수 있습니다.

허용할 권한은 아래 4개입니다. 메일 발송이나 삭제, Drive 전체 접근처럼 다른 권한이 보이면 허용하지 마세요.

- Gmail 읽기
- 이 스크립트가 만든 Drive 파일 관리
- 외부 서비스 연결
- 트리거 관리

**성공 확인:** 앱의 **설정** 화면에서 아래 3개를 봅니다. `runInitialImport`는 메일이 없어도 "완료"로 끝나므로 여기서 확인해야 합니다.

| 항목 | 정상 |
|---|---|
| 수신 스크립트 신호 | 방금 시간 |
| 마지막 백업 | 방금 시간 |
| 최근 가져오기 | 방금 시간과 데이터 기준일 |

| 막히면 | 원인과 해결 |
|---|---|
| 저장할 때 문법 오류 | 7-2의 두 파일이 바뀌었습니다. 다시 붙여 넣습니다 |
| `스크립트 속성에 LEDGER_URL과 LEDGER_HMAC_KEY를 넣어 주세요.` | 7-3을 다시 합니다. 저장 버튼까지 눌러야 합니다 |
| `서버 연결 실패: HTTP 401` | `LEDGER_HMAC_KEY`가 Vercel의 `INGEST_HMAC_KEY`와 다릅니다. 앞뒤 공백을 지우고 다시 넣습니다 |
| `서버 연결 실패: HTTP` 뒤에 401이 아닌 번호 | `LEDGER_URL`이 `내 앱 주소`와 같은지, `내 앱 주소/healthz`가 열리는지 봅니다 |
| `메일 처리` 줄이 없고 `최근 가져오기`에 시간이 있음 | 정상입니다. 자동 실행이 먼저 처리한 것입니다 |
| `메일 처리` 줄이 없고 `최근 가져오기`가 `없음` | 최근 1년 안에 받은 내보내기 메일이 없습니다. 0-4를 하고 `runInitialImport`를 다시 실행합니다 |
| `메일 처리: rejected (recipient)` | 메일을 받은 주소가 `OWNER_EMAIL`과 다릅니다. 내 Gmail로 다시 내보냅니다 |
| `메일 처리: rejected (sender)` 또는 `(signature)` | 뱅크샐러드가 보낸 원본 메일이 아닙니다. 전달한 메일은 쓸 수 없으니 내 Gmail로 다시 내보냅니다 |
| `메일 처리: quarantined (wrong_password)` | 앱에 저장한 파일 비밀번호가 다릅니다. **설정**에서 다시 저장하면 자동으로 다시 처리합니다. 예전에 다른 비밀번호로 내보낸 메일이면 **처리 대기 파일**에서 **지우기**를 누릅니다 |
| 그 밖의 `메일 처리: quarantined (...)` | 뱅크샐러드에서 새로 내보내 봅니다. 같은 사유가 반복되면 [막혔을 때](#막혔을-때)대로 합니다 |
| `메일 처리` 줄이 20개 | 한 번에 20통까지 처리합니다. `runInitialImport`를 한 번 더 실행합니다 |

---

## 8단계: 마무리

### 8-1. 처리한 메일 자동 삭제 (권장)

내보내기 ZIP은 암호화가 약해서 Gmail이 뚫리면 내용도 사실상 노출됩니다. 메일이 30일 뒤 지워지도록 필터를 겁니다. 스크립트는 휴지통도 읽으므로 수신에는 지장이 없습니다.

1. Gmail의 톱니바퀴에서 **모든 설정 보기**를 누릅니다. 왼쪽 **라벨 관리**를 눌러도 같은 화면입니다.
2. 위쪽 **필터 및 차단된 주소** 탭에서 **새 필터 만들기**를 누릅니다.
3. **보낸사람**에만 `export-noreply@banksalad.com`을 넣고 **필터 만들기**를 누릅니다.
4. **삭제**를 체크하고 **필터 만들기**를 누릅니다.

**일치하는 대화에도 필터 적용**을 체크하면 이미 받은 내보내기 메일도 휴지통으로 갑니다.

### 8-2. 계정 잠그기

**Gmail, GitHub, Bitwarden에 2단계 인증을 켭니다.** Neon과 Vercel은 GitHub로 로그인하므로 GitHub가 특히 중요합니다. 각 서비스의 복구 코드는 Gmail 밖에 보관하세요.

### 8-3. 확인하기

- [ ] 시크릿 창에서 앱 주소를 열면 로그인 화면이 나온다.
- [ ] 다른 Google 계정으로 로그인하면 거부된다. Google의 `access_denied`나 앱의 `이 계정으로는 사용할 수 없습니다.`가 나옵니다. 다른 계정이 없으면 건너뜁니다.
- [ ] Google 로그인 후 OTP를 입력하기 전에는 `내 앱 주소/settings`가 열리지 않는다.
- [ ] 휴대폰과 PC에서 모두 로그인되고, **설정**의 기기 목록에 둘 다 보인다.
- [ ] **설정** 위쪽의 경고 문구가 없다. OTP를 잘못 입력했거나 메일이 거부된 적이 있으면 그 경고는 7일 동안 남습니다. 설치 중에 직접 겪은 일이면 신경 쓰지 않아도 됩니다.

### 8-4. 백업이 열리는지 확인하기 (필수)

Vercel에 넣은 `MASTER_KEY`와 Bitwarden의 값이 다르면 백업을 복원할 수 없는데, 평소에는 오류가 나지 않아 알 수 없습니다. 지금 한 번 확인하세요. 데이터베이스는 건드리지 않습니다.

1. 내 Google Drive의 `Ledger Backups` 폴더에서 백업 파일을 받습니다.
2. 코드 폴더에서 터미널을 열고 아래 명령을 입력합니다. `백업파일경로`를 지우고, 받은 파일을 터미널 창으로 끌어다 놓으면 경로가 들어갑니다.

   ```bash
   uv run --locked python scripts/restore_backup.py 백업파일경로 --dry-run
   ```

3. `MASTER_KEY`를 물으면 Bitwarden의 값을 붙여 넣고 Enter를 누릅니다. 입력해도 화면에 표시되지 않습니다.

**성공 확인:** `백업 시각:`과 표별 행 수가 나옵니다.

| 막히면 | 해결 |
|---|---|
| `FileNotFoundError` | 파일 경로가 틀렸습니다. 파일을 다시 끌어다 놓습니다 |
| `오류:`로 시작하는 줄 | `MASTER_KEY`를 다시 붙여 넣어 봅니다. 그래도 같으면 두 값이 다른 것이니 [막혔을 때](#막혔을-때)대로 합니다 |

### 8-5. 8일 뒤에 한 번 더 보기

달력에 적어 두세요. **8일 뒤 설정 화면의 `수신 스크립트 신호`가 24시간 안쪽이면** 설치가 완전히 끝난 것입니다. 일반 Gmail 계정에서 스크립트 권한이 언제 만료되는지 공식 문서로 확인되지 않아 실제로 봐야 합니다. 신호가 멈췄으면 7-4의 `setup`을 다시 실행해 권한을 승인합니다.

압축을 푼 폴더는 8-4까지 끝낸 뒤 지워도 됩니다. 복원할 때는 내 저장소에서 다시 받으면 됩니다. **Bitwarden 메모는 지우지 마세요.**

---

## 평소 사용

**뱅크샐러드 앱에서 내보내기만 하면 됩니다.** 5분 안팎으로 앱에 자동으로 들어옵니다. 내보내기는 자동이 아니므로 주기적으로 직접 실행하세요.

앱이 문제를 찾으면 화면 위쪽에 경고를 띄웁니다.

| 경고 | 조건 | 할 일 |
|---|---|---|
| 마지막 내보내기 후 N일이 지났습니다 | 8일 초과 | 뱅크샐러드에서 다시 내보냅니다 |
| 메일 수신 스크립트의 신호가 N일째 없습니다 | 3일 초과 | Apps Script의 `setup`을 다시 실행합니다 |
| 마지막 백업 후 N일이 지났습니다 | 15일 초과 | Apps Script의 **실행** 기록에서 `runBackup` 오류를 봅니다 |
| 처리하지 못한 파일이 N건 있습니다 | 처리 실패 | **설정**의 처리 대기 파일에서 사유를 봅니다 |
| 인증되지 않은 메일 N통을 거부했습니다 | 최근 7일 | 내가 전달한 메일이면 **확인했어요**를 누릅니다. 아니면 위조 메일일 수 있으니 열지 마세요 |
| OTP 입력 실패가 N회 있었습니다 | 최근 7일 | 내 실수면 **확인했어요**를 누릅니다. 새로 실패하면 다시 알립니다. 내가 아니면 아래 복구 방법을 봅니다 |

스크립트 실행이 실패하면 Google이 기본 설정으로 하루 한 번 메일을 보냅니다. 메일 수신이 멈췄을 때는 **설정**의 **직접 올리기**로 ZIP을 올릴 수 있습니다.

---

## 업데이트할 때

새 버전에 **DB 변경**이 있으면 코드를 합치기 **전에** DB부터 올립니다. 순서가 바뀌면 앱이 `일시적인 오류`를 보여 줍니다. 추가만 하는 변경이라 먼저 올려도 지금 앱은 그대로 동작합니다.

`uv`와 저장소 폴더가 있는 컴퓨터에서 **Direct 연결 문자열**을 입력합니다(주소에 `-pooler`가 없는 쪽).

Windows PowerShell:

```powershell
$env:DATABASE_URL = Read-Host "Direct 주소"; uv run --locked alembic upgrade head; Remove-Item Env:DATABASE_URL
```

Mac 터미널:

```bash
printf 'Direct 주소: '; read -r DATABASE_URL; export DATABASE_URL; uv run --locked alembic upgrade head; unset DATABASE_URL
```

아무것도 출력되지 않으면 성공입니다. 그다음 코드를 합치면 Vercel이 자동으로 배포합니다.

---

## 복구 방법

**OTP를 여러 번 틀려 오래 기다려야 할 때:** 기다리면 다시 시도할 수 있습니다. 급하면 Neon의 **SQL Editor**에서 아래를 실행합니다.

```sql
UPDATE owner SET failed_attempts = 0, locked_until = NULL;
```

**휴대폰을 잃어버렸을 때:** OTP 화면의 **휴대폰을 쓸 수 없나요?** 에 복구 코드를 입력하고 OTP를 다시 등록합니다. 새 코드를 등록하기 전까지는 기존 OTP가 그대로 유효합니다. 등록이 끝나면 옛 복구 코드는 모두 못 쓰게 되니, 새 코드 10개로 Bitwarden을 덮어씁니다.

**"OTP 입력 실패" 경고가 떴는데 내가 아닐 때:** 누군가 Google 로그인까지 통과했다는 뜻입니다. Google 비밀번호를 바꾸고 Google 계정의 로그인 기기를 확인한 뒤, **설정**에서 **다른 기기 모두 로그아웃**을 누릅니다.

**복구 코드도 없을 때:** Neon의 **Postgres database > SQL Editor**에서 아래를 실행하면 다음 로그인 때 OTP를 새로 등록합니다. Neon 접근 권한이 곧 최종 복구 수단이라 GitHub 계정이 중요합니다.

```sql
UPDATE owner SET totp_enabled_at = NULL, totp_last_step = NULL, failed_attempts = 0, locked_until = NULL;
DELETE FROM secret_value WHERE name = 'totp_seed';
UPDATE auth_session SET revoked_at = now() WHERE revoked_at IS NULL;
```

**데이터베이스를 잃었을 때:** 새 Neon 프로젝트를 만들고 Drive의 최신 백업을 복원합니다. 표는 자동으로 만들어집니다. 코드 폴더가 없으면 0-2의 4번과 0-3을 먼저 하고, 1단계처럼 터미널을 엽니다.

아래 명령에서 `백업파일경로`를 지우고 받은 파일을 터미널 창으로 끌어다 놓은 뒤 실행합니다. 주소를 물으면 새 프로젝트의 **Direct 주소**를, `MASTER_KEY`를 물으면 Bitwarden의 값을 붙여 넣습니다.

Windows:

```powershell
$env:DATABASE_URL = Read-Host "새 Direct 주소"; uv run --locked python scripts/restore_backup.py 백업파일경로; Remove-Item Env:DATABASE_URL
```

Mac:

```bash
printf '새 Direct 주소: '; read -r DATABASE_URL; export DATABASE_URL; uv run --locked python scripts/restore_backup.py 백업파일경로; unset DATABASE_URL
```

끝나면 Vercel의 `DATABASE_URL`을 새 **Pooled 주소**로 바꾸고 Redeploy합니다.

---

## 막혔을 때

표에 없는 오류가 나오거나 화면이 이 문서와 다르면 이렇게 하세요. 서비스 화면은 바뀔 수 있어서, 메뉴 이름이 조금 달라도 비슷한 이름을 찾으면 대부분 해결됩니다.

1. 지금 몇 단계인지와 오류의 마지막 줄을 복사합니다.
2. 그 내용으로 검색하거나 AI 도우미에게 물어봅니다.

**아래 값은 절대 그대로 붙여 넣지 마세요.** 데이터베이스 주소의 비밀번호, `MASTER_KEY`, `INGEST_HMAC_KEY`, `GOOGLE_CLIENT_SECRET`, 파일 비밀번호, OTP 코드, QR과 설정 키, 복구 코드입니다. Vercel 로그의 설정 오류는 값 없이 변수 이름만 보여 주니 그대로 복사해도 됩니다.
