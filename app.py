"""둘이 쓰는 하루 — Streamlit / GitHub / Supabase

배포 안내 (이 파일과 requirements.txt만 GitHub에 올리세요)
1. Supabase 프로젝트 생성 → SQL Editor에서 아래 SQL 실행.
2. Streamlit Community Cloud에서 저장소와 app.py 선택 후 배포.
3. 앱 Settings → Secrets에 아래 TOML 입력. 비밀번호/키는 GitHub에 올리지 마세요.
4. 배포 주소를 public_url에 넣으면 앱의 '접속 QR'에서 QR을 받을 수 있습니다.

[SQL]
create table public.couple_diary (
  couple_id text not null,
  author text not null check (author in ('p1','p2')),
  entry_date date not null,
  mood text not null,
  body text not null check (char_length(body) <= 500),
  updated_at timestamptz not null,
  read_at timestamptz,
  primary key (couple_id, author, entry_date)
);
alter table public.couple_diary enable row level security;
revoke all on table public.couple_diary from anon, authenticated;
grant all on table public.couple_diary to service_role;

[Streamlit Secrets: TOML 예시]
supabase_url = "https://YOUR_PROJECT.supabase.co"
supabase_service_role_key = "YOUR_SERVICE_ROLE_KEY"
couple_id = "둘만의-임의의-긴-고유문자열-변경하지마세요"
p1_name = "성겸"
p1_password = "충분히-긴-첫번째-비밀번호"
p2_name = "짝꿍"
p2_password = "충분히-긴-두번째-비밀번호"
public_url = "https://YOUR_APP.streamlit.app"

로컬: pip install -r requirements.txt && streamlit run app.py
로컬 설정: .streamlit/secrets.toml (절대로 Git에 커밋하지 마세요.)

설계: 한 커플 전용 / 1인 1일 1편 / 수정 시 읽음 초기화 / 한국시간 기준.
QR은 앱 주소를 여는 링크이며 비밀번호나 비밀키를 담지 않습니다.
각 날짜의 일기는 작성자만 수정, 읽음 표시는 상대방만 변경합니다.
로그인은 앱 서버에서 처리하며 Supabase 비밀키는 브라우저에 전달하지 않습니다.
공용 공개 서비스가 아닌 두 사람 전용 구성입니다. 강한 비밀번호를 쓰세요.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import qrcode
import requests
import streamlit as st

st.set_page_config(page_title="둘이 쓰는 하루", page_icon="💌", layout="centered", initial_sidebar_state="collapsed")
st.markdown("""<style>
#MainMenu,footer,[data-testid="stToolbar"] {visibility:hidden;}
.block-container {max-width:480px;padding:1rem .85rem .5rem;}
[data-testid="stAppViewContainer"] {background:#fff9f7;}
h1 {font-size:1.55rem!important;letter-spacing:-.06em;padding:0!important;}
p {margin-bottom:.35rem;}
[data-testid="stVerticalBlock"] {gap:.45rem;}
[data-testid="stCaptionContainer"] {font-size:.76rem;}
.stButton>button,[data-testid="stFormSubmitButton"]>button {border-radius:12px;min-height:2.3rem;}
textarea {border-radius:12px!important;font-size:16px!important;}
[data-testid="stTextInput"] input {font-size:16px!important;}
[data-testid="stExpander"] {background:#fff; border-radius:12px;}
@media(max-width:600px){.block-container{padding-top:.65rem;}h1{font-size:1.4rem!important;}}
</style>""", unsafe_allow_html=True)

REQUIRED = ('supabase_url', 'supabase_service_role_key', 'couple_id', 'p1_name', 'p1_password', 'p2_name', 'p2_password')
MOODS = ['😊 행복', '🥰 설렘', '😌 평온', '😢 슬픔', '😮‍💨 지침', '😠 속상']
KST = ZoneInfo('Asia/Seoul')


def config():
    try:
        return dict(st.secrets)
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return {}


CFG = config()
if any(not str(CFG.get(k, '')).strip() for k in REQUIRED):
    st.title('💌 둘이 쓰는 하루')
    st.info('첫 설정이 필요해요. app.py 맨 위의 SQL과 Secrets 예시를 따라 설정해 주세요.')
    st.caption('GitHub에는 app.py와 requirements.txt만 올리세요. 비밀번호와 Supabase 키는 Streamlit Secrets에 입력합니다.')
    with st.expander('설정 안내 보기', expanded=True):
        st.code(__doc__, language='text')
    st.stop()


def db(method, params=None, payload=None, prefer=None):
    """서비스 키는 서버 내부 요청에만 사용. 모든 요청은 커플 범위를 고정."""
    headers = {'apikey': CFG['supabase_service_role_key'],
               'Authorization': 'Bearer ' + CFG['supabase_service_role_key']}
    if prefer:
        headers['Prefer'] = prefer
    response = requests.request(method,
        str(CFG['supabase_url']).rstrip('/') + '/rest/v1/couple_diary',
        headers=headers, params=params, json=payload, timeout=15)
    response.raise_for_status()
    return response.json() if response.content else []


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def stamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(KST).strftime('%m/%d %H:%M')
    except (ValueError, AttributeError):
        return ''


def error():
    st.error('저장소에 연결하지 못했어요. 잠시 후 다시 시도해 주세요. 계속되면 Supabase 설정과 테이블 생성 여부를 확인해 주세요.')


# 설정 변경 시 기존 로그인 세션도 무효화합니다.
AUTH_REV = hashlib.sha256(('\x00'.join(str(CFG[k]) for k in REQUIRED)).encode()).hexdigest()
if st.session_state.get('auth_rev') != AUTH_REV:
    st.session_state.pop('who', None)

st.title('💌 둘이 쓰는 하루')
if 'who' not in st.session_state:
    st.caption('오늘의 마음을 남기고, 서로의 하루를 읽어요.')
    with st.form('login'):
        who = st.selectbox('나는', ['p1', 'p2'], format_func=lambda v: CFG[v + '_name'])
        password = st.text_input('내 비밀번호', type='password', max_chars=256)
        enter = st.form_submit_button('우리 다이어리 열기', use_container_width=True)
    if enter:
        if time.time() < st.session_state.get('retry_after', 0):
            st.warning('잠시 기다린 뒤 다시 시도해 주세요.')
        elif hmac.compare_digest(password.encode(), str(CFG[who + '_password']).encode()):
            st.session_state['who'] = who
            st.session_state['auth_rev'] = AUTH_REV
            st.session_state['failures'] = 0
            st.rerun()
        else:
            failures = st.session_state.get('failures', 0) + 1
            st.session_state['failures'] = failures
            st.session_state['retry_after'] = time.time() + min(60, 2 ** min(failures, 6))
            st.error('비밀번호를 확인해 주세요.')
    st.caption('🔒 QR로 접속해도 각자의 비밀번호가 필요해요.')
    st.stop()

me = st.session_state['who']
other = 'p2' if me == 'p1' else 'p1'
st.caption(f"{CFG[me + '_name']} ↔ {CFG[other + '_name']} · 둘만의 작은 기록")
selected = st.date_input('기록 날짜', value=datetime.now(KST).date(), max_value=datetime.now(KST).date(), label_visibility='collapsed')
day = selected.isoformat()
params = {'couple_id': 'eq.' + str(CFG['couple_id']), 'entry_date': 'eq.' + day, 'select': '*'}
try:
    records = db('GET', params)
except (requests.RequestException, ValueError):
    error()
    st.stop()
entries = {row['author']: row for row in records}
mine = entries.get(me, {})
theirs = entries.get(other, {})

if st.session_state.pop('saved_notice', False):
    st.toast('마음을 저장했어요 💌')

# 폼은 입력 중 서버 재실행을 막아 모바일 타이핑과 초안을 유지합니다.
status = ('💗 상대가 확인했어요 · ' + stamp(mine['read_at'])) if mine.get('read_at') else ('💌 아직 확인 전이에요' if mine else '✏️ 오늘의 첫 기록을 남겨요')
st.caption('나의 하루 · ' + status)
with st.form('diary_' + me + '_' + day, clear_on_submit=False):
    mood = st.selectbox('오늘 기분', MOODS, index=MOODS.index(mine['mood']) if mine.get('mood') in MOODS else 0, label_visibility='collapsed')
    body = st.text_area('오늘의 일기', value=mine.get('body', ''), placeholder='오늘 어떤 하루였어? 너에게 들려주고 싶어…', height=90, max_chars=500, label_visibility='collapsed')
    save = st.form_submit_button('마음 저장하기', use_container_width=True)
if save:
    if not body.strip():
        st.warning('한 줄이라도 마음을 적어 주세요.')
    elif mood == mine.get('mood') and body.strip() == mine.get('body'):
        st.toast('이미 저장된 내용이에요.')
    else:
        payload = {'couple_id': str(CFG['couple_id']), 'author': me, 'entry_date': day,
                   'mood': mood, 'body': body.strip(), 'updated_at': now_iso(), 'read_at': None}
        try:
            # 기존 일기는 조회한 버전과 같을 때만 변경: 다른 기기의 수정 덮어쓰기 방지.
            if mine:
                condition = dict(params, author='eq.' + me, updated_at='eq.' + mine['updated_at'])
                changed = db('PATCH', condition, payload, 'return=representation')
                if not changed:
                    st.warning('다른 기기에서 내용이 바뀌었어요. 아래 새로고침 후 다시 작성해 주세요.')
                    st.stop()
            else:
                db('POST', payload=payload, prefer='return=minimal')
            st.session_state['saved_notice'] = True
            st.rerun()
        except (requests.RequestException, ValueError):
            error()

st.caption(f"{CFG[other + '_name']}의 하루" + (f" · {theirs['mood']}" if theirs else ''))
# 고정 높이: 긴 일기는 입력 상자 안에서 스크롤, 페이지 자체는 컴팩트하게 유지.
st.text_area('상대의 일기', value=theirs.get('body', '아직 일기가 없어요. 조금만 기다려 주세요 🤍'), height=90, disabled=True, label_visibility='collapsed', key='partner_' + day + '_' + str(theirs.get('updated_at', 'empty')))
if theirs:
    seen = bool(theirs.get('read_at'))
    checked = st.checkbox('읽었어요 💗' + (' · ' + stamp(theirs['read_at']) if seen else ''), value=seen,
        key='read_' + day + '_' + theirs['updated_at'] + '_' + str(theirs.get('read_at')))
    if checked != seen:
        condition = dict(params, author='eq.' + other, updated_at='eq.' + theirs['updated_at'])
        try:
            changed = db('PATCH', condition, {'read_at': now_iso() if checked else None}, 'return=representation')
            if not changed:
                st.toast('상대가 일기를 수정했어요. 새 내용을 확인해 주세요.')
            st.rerun()
        except (requests.RequestException, ValueError):
            error()

# 자동 폴링 대신 명시적 새로고침: 작성 중인 글과 모바일 키보드 보호.
with st.expander('↻ 새로고침 · 접속 QR · 로그아웃'):
    st.caption('상대의 최신 기록과 읽음 상태는 새로고침하면 반영돼요. 저장하지 않은 초안은 화면을 닫거나 날짜를 바꾸기 전에 저장해 주세요.')
    if st.button('최신 내용 새로고침', use_container_width=True):
        st.rerun()
    url = str(CFG.get('public_url', '')).strip()
    parsed = urlparse(url)
    if parsed.scheme == 'https' and parsed.netloc and not parsed.username and not parsed.password:
        qr = qrcode.make(url)
        output = io.BytesIO()
        qr.save(output, format='PNG')
        st.image(output.getvalue(), width=160, caption='카메라로 찍으면 앱이 열려요')
        st.download_button('QR 이미지 다운로드', data=output.getvalue(), file_name='couple_diary_qr.png', mime='image/png', use_container_width=True)
    else:
        st.caption('Secrets의 public_url에 배포된 앱의 HTTPS 주소를 입력하면 접속 QR이 표시돼요.')
    if st.button('로그아웃', use_container_width=True):
        st.session_state.clear()
        st.rerun()
"""
참고: 약 700px 이상 높이의 일반 스마트폰 세로 화면을 목표로 구성했습니다.
브라우저 배율·접근성 글꼴·가상 키보드·오류 메시지·설정 패널을 펼친 상태에선
페이지 스크롤이 생길 수 있습니다. 500자 일기는 상자 내부 스크롤로 확인합니다.
로그인 시도 제한은 세션 단위이며, 공개 다중 사용자 서비스 수준의 인증은 아닙니다.
"""
