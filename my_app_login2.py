import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import requests
from openai import OpenAI
import datetime
import uuid

# --- 初期設定 ---
st.set_page_config(page_title="Dify Chatbot", layout="centered")
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# --- 認証機能 (シンプルなサンプル) ---
def check_password():
    def password_entered():
        if (
            st.session_state["username"] == "admin"
            and st.session_state["password"] == "password123"
        ):
            st.session_state["password_correct"] = True
        else:
            st.session_state["password_correct"] = False

    if not st.session_state["password_correct"]:
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", key="password")
        st.button("Login", on_click=password_entered)
        return False

    return True

# --- セッション状態の初期化（最優先） ---
if "username" not in st.session_state:
    st.session_state["username"] = ""
if "password" not in st.session_state:
    st.session_state["password"] = ""
if "password_correct" not in st.session_state:
    st.session_state["password_correct"] = False

if not check_password():
    st.stop()

# --- セッション状態の初期化 ---
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = ""
if "messages" not in st.session_state:
    st.session_state.messages = []
if "user_uuid" not in st.session_state:
    st.session_state.user_uuid = str(uuid.uuid4())

conn = st.connection("gsheets", type=GSheetsConnection)

# --- ヘルパー関数 ---
def save_log(user_input, ai_response):
    try:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        new_row = {
            "date": now,
            "user_id": st.session_state["username"],
            "user_input": user_input,
            "ai_response": ai_response,
            "conversation_id": st.session_state.conversation_id
        }
        # 既存データ読み込みと更新
        existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
        updated_df = pd.concat([existing_data, pd.DataFrame([new_row])], ignore_index=True)
        conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
    except Exception as e:
        st.error(f"ログ保存失敗: {e}")

def transcribe_audio(audio_file):
    """Whisper APIで文字起こし"""
    transcript = client.audio.transcriptions.create(
        model="whisper-1", 
        file=audio_file
    )
    return transcript.text

def text_to_speech(text):
    """OpenAI TTSで音声生成"""
    response = client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=text
    )
    return response.content

def call_dify(query):
    url = "https://api.dify.ai/v1/chat-messages"
    headers = {
        "Authorization": f"Bearer {st.secrets['DIFY_API_KEY']}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",
        "conversation_id": st.session_state.conversation_id,
        "user": st.session_state.user_uuid
    }

    response = requests.post(url, headers=headers, json=payload)

    # 👇 追加（超重要）
    st.write("Dify raw response:", response.json())

    if response.status_code != 200:
        return f"HTTPエラー: {response.status_code}"

    data = response.json()
    st.session_state.conversation_id = data.get("conversation_id", "")

    return data.get("answer", "エラーが発生しました。")


# --- UI レイアウト ---
st.title("Dify AI Assistant 🎙️")

# チャット履歴の表示
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 音声入力
audio_value = st.audio_input("マイクに向かって話してください")

# テキスト入力
user_input = st.chat_input("メッセージを入力...")

# 入力処理
if audio_value or user_input:
    # 音声入力がある場合は文字起こしを優先
    actual_input = transcribe_audio(audio_value) if audio_value else user_input
    
    # ユーザーメッセージの表示
    st.session_state.messages.append({"role": "user", "content": actual_input})
    with st.chat_message("user"):
        st.markdown(actual_input)

    # Dify API 呼び出し
    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            response_text = call_dify(actual_input)
            st.markdown(response_text)
            
            # 音声出力 (自動再生)
            audio_bytes = text_to_speech(response_text)
            st.audio(audio_bytes, format="audio/mp3", autoplay=True)

    # 履歴保存とログ記録
    st.session_state.messages.append({"role": "assistant", "content": response_text})
    save_log(actual_input, response_text)
