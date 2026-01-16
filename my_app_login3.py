import streamlit as st
import requests
import os
import datetime
import pandas as pd
from streamlit_gsheets import GSheetsConnection
import json
import base64
import io

# --- 追加ライブラリ ---
from streamlit_mic_recorder import mic_recorder
from openai import OpenAI

# --- スプレッドシート接続 ---
conn = st.connection("gsheets", type=GSheetsConnection)

# --- 設定 ---
DIFY_API_KEY = st.secrets["DIFY_API_KEY"]
openai_client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

BASE_URL = "https://api.dify.ai/v1"
FILE_VARIABLE_KEY = "material"
FIXED_FILE_PATH = "NLP11.pdf"

headers = {
    "Authorization": f"Bearer {DIFY_API_KEY}"
}

# --- ログイン機能 ---
def login():
    if "username" not in st.session_state:
        st.session_state.username = None
    if not st.session_state.username:
        with st.form("login_form"):
            st.write("学習を開始するにはID（氏名または学籍番号）を入力してください")
            username_input = st.text_input("ユーザーID")
            submitted = st.form_submit_button("開始")
            if submitted and username_input:
                st.session_state.username = username_input
                st.rerun()
        st.stop()

# --- Dify連携関数群 ---
def upload_local_file_to_dify(file_path, user_id):
    if not os.path.exists(file_path):
        st.error(f"ファイルが見つかりません: {file_path}")
        return None
    url = f"{BASE_URL}/files/upload"
    with open(file_path, "rb") as f:
        files = {'file': (os.path.basename(file_path), f, 'application/pdf')}
        data = {'user': user_id}
        try:
            response = requests.post(url, headers=headers, files=files, data=data)
            response.raise_for_status()
            return response.json().get('id')
        except Exception as e:
            st.error(f"内部アップロードエラー: {e}")
            return None

def send_chat_message(query, conversation_id, file_id_to_send, user_id):
    url = f"{BASE_URL}/chat-messages"
    inputs = {}
    if file_id_to_send:
        inputs[FILE_VARIABLE_KEY] = {
            "type": "document", 
            "transfer_method": "local_file",
            "upload_file_id": file_id_to_send
        }
    payload = {
        "inputs": inputs,
        "query": query,
        "response_mode": "blocking",
        "conversation_id": conversation_id,
        "user": user_id,
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"通信エラー: {e}")
        return None

# --- ログ保存機能 ---
def save_log_to_sheet(username, user_input, bot_question, conversation_id):
    try:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
        new_row = pd.DataFrame([{
            "date": now,
            "user_id": username,
            "user_input": user_input,
            "ai_response": bot_question,
            "conversation_id": conversation_id
        }])
        if existing_data.empty:
            updated_df = new_row
        else:
            updated_df = pd.concat([existing_data, new_row], ignore_index=True)
        conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
    except Exception as e:
        st.error(f"ログ保存エラー: {e}")

# --- 音声処理関数 ---
def transcribe_audio(audio_bytes):
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "input.wav"
        transcript = openai_client.audio.transcriptions.create(
            model="whisper-1", file=audio_file, language="ja"
        )
        return transcript.text
    except Exception as e:
        st.error(f"音声認識エラー: {e}")
        return ""

def text_to_speech_autoplay(text):
    try:
        response = openai_client.audio.speech.create(
            model="tts-1-hd", voice="nova", input=text
        )
        audio_bytes = response.content
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        audio_tag = f'<audio autoplay="true" style="display:none"><source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3"></audio>'
        return audio_tag
    except Exception as e:
        st.error(f"音声合成エラー: {e}")
        return None

# ==========================================
# メイン処理
# ==========================================
st.set_page_config(page_title="講義の復習", page_icon="🤖")
st.title("🤖 講義振り返りインタビュアー改良")

login()
current_user = st.session_state.username
st.sidebar.write(f"ログイン中: {current_user}")

# セッション変数
if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = ""
if "current_file_id" not in st.session_state:
    st.session_state.current_file_id = None
if "last_bot_message" not in st.session_state:
    st.session_state.last_bot_message = ""
if "audio_html" not in st.session_state:
    st.session_state.audio_html = None
if "prev_audio_bytes" not in st.session_state:
    st.session_state.prev_audio_bytes = None

# --- 緊急リセット ---
if st.sidebar.button("⚠️ 会話をリセット"):
    st.session_state.conversation_id = ""
    st.session_state.messages = []
    st.session_state.current_file_id = None
    st.session_state.last_bot_message = ""
    st.session_state.audio_html = None
    st.session_state.prev_audio_bytes = None
    st.rerun()

# 3. 自動初期化
if not st.session_state.conversation_id:
    with st.spinner("インタビュアーを準備中..."):
        if not st.session_state.current_file_id:
            file_id = upload_local_file_to_dify(FIXED_FILE_PATH, current_user)
            if file_id:
                st.session_state.current_file_id = file_id
            else:
                st.error("ファイルのアップロードに失敗しました。")
                st.stop()
        
        initial_res = send_chat_message(
            query="授業内容について学んだことを教えてください。", 
            conversation_id="",
            file_id_to_send=st.session_state.current_file_id,
            user_id=current_user
        )
        
        if initial_res:
            st.session_state.conversation_id = initial_res.get('conversation_id')
            welcome_msg = initial_res.get('answer', '')
            st.session_state.messages.append({"role": "assistant", "content": welcome_msg})
            st.session_state.last_bot_message = welcome_msg
            st.session_state.audio_html = text_to_speech_autoplay(welcome_msg)
            st.rerun()

# 4. チャット履歴の表示（ここが変わりました！）
# 固定の高さを指定したコンテナの中にメッセージを表示します。
# これにより、メッセージが増えてもコンテナ内でスクロールされるだけで、
# その下のマイクボタン等は位置が固定されたままになります。
chat_container = st.container(height=500) # 高さは調整してください

with chat_container:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
    
    # 音声再生用の隠し要素もここに入れておきます（邪魔にならないので）
    if st.session_state.audio_html:
        st.markdown(st.session_state.audio_html, unsafe_allow_html=True)

# 5. 入力エリア（コンテナの外に書くことで固定表示される）
st.divider()
col1, col2 = st.columns([1, 4])

with col1:
    # ここが常に定位置になります！
    st.write("音声入力:")
    audio = mic_recorder(start_prompt="●", stop_prompt="■", key='recorder', format="wav")

user_input_text = st.chat_input("テキストで入力...")

# 6. 入力処理ロジック
final_prompt = None

if audio:
    if audio['bytes'] != st.session_state.prev_audio_bytes:
        st.session_state.prev_audio_bytes = audio['bytes']
        with st.spinner("音声認識中..."):
            transcribed_text = transcribe_audio(audio['bytes'])
            if transcribed_text:
                final_prompt = transcribed_text
                st.session_state.audio_html = None
    else:
        pass

elif user_input_text:
    final_prompt = user_input_text
    st.session_state.audio_html = None

if final_prompt:
    st.session_state.messages.append({"role": "user", "content": final_prompt})
    
    # ここでは「コンテナの中」に書き込みたいので、context managerを使います
    with chat_container:
        with st.chat_message("user"):
            st.write(final_prompt)

    with st.spinner("思考中..."):
        response = send_chat_message(
            query=final_prompt,
            conversation_id=st.session_state.conversation_id,
            file_id_to_send=st.session_state.current_file_id,
            user_id=current_user
        )
        
        if response:
            answer_text = response.get('answer', '')
            st.session_state.messages.append({"role": "assistant", "content": answer_text})
            
            save_log_to_sheet(
                username=current_user,
                user_input=final_prompt,
                bot_question=st.session_state.last_bot_message, 
                conversation_id=st.session_state.conversation_id
            )
            
            st.session_state.last_bot_message = answer_text
            st.session_state.audio_html = text_to_speech_autoplay(answer_text)

            st.rerun()
