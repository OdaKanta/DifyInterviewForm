import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
from streamlit_gsheets import GSheetsConnection
import datetime
import pandas as pd

# ==========================================
# ★ 設定エリア：ここを書き換えてください
# ==========================================
# Difyの入力フィールド（変数名）とファイルID
FILE_VARIABLE_NAME = "material" # Difyで設定した変数名
UPLOAD_FILE_ID = "ee477849-b192-4035-a6b7-aae8b111a328" # Difyにアップ済みのファイルID
# ==========================================

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子"]
usernames = ["tanaka", "sato"]
passwords = ["pass123", "pass456"]

authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]}
    }},
    "dify_app_cookie", "signature_key", cookie_expiry_days=30
)

# --- 2. ログイン画面 ---
authenticator.login('main')

if st.session_state["authentication_status"] == False:
    st.error('ユーザー名またはパスワードが間違っています')
elif st.session_state["authentication_status"] == None:
    st.warning('ユーザー名とパスワードを入力してください')
elif st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]
    
    # クライアント・接続の初期化
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    conn = st.connection("gsheets", type=GSheetsConnection)

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント")

    # セッション状態の初期化
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # Difyへの共通送信関数
    def send_to_dify(query_text):
        DIFY_KEY = st.secrets["DIFY_API_KEY"]
        headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
        
        # 入力フィールド（ファイル）の設定
        inputs_data = {
            FILE_VARIABLE_NAME: {
                "transfer_method": "local_file",
                "upload_file_id": UPLOAD_FILE_ID,
                "type": "document"
            }
        }
        
        data = {
            "inputs": inputs_data,
            "query": query_text,
            "response_mode": "streaming",
            "user": username,
            "conversation_id": st.session_state.conversation_id
        }
        return requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data, stream=True)

    # --- ★追加：ログイン直後の「最初の挨拶」自動取得 ---
    if len(st.session_state.messages) == 0:
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            # 初回トリガーを送信
            response = send_to_dify("こんにちは（初回挨拶開始）")

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        chunk = json.loads(decoded_line[6:])
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        
                        # チャットフロー対応の文字抽出
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                        elif "event" in chunk and chunk["event"] == "text_chunk":
                            full_response += chunk["data"].get("text", "")
                        
                        response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            # 挨拶の音声再生
            if full_response.strip():
                tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
                st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)

    # 過去の会話表示
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- 入力セクション ---
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')
    prompt = st.chat_input("メッセージを入力...")
    
    user_input = None
    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
            user_input = transcript.text
    elif prompt:
        user_input = prompt

    # --- メイン対話処理 ---
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            
            response = send_to_dify(user_input)

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        chunk = json.loads(decoded_line[6:])
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                        elif "event" in chunk and chunk["event"] == "text_chunk":
                            full_response += chunk["data"].get("text", "")
                            
                        response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})

            # ログ書き込み
            try:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                new_row = {"date": now, "user_id": username, "user_input": user_input, "ai_response": full_response, "conversation_id": st.session_state.conversation_id}
                updated_df = pd.concat([existing_data, pd.DataFrame([new_row])], ignore_index=True)
                conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
            except Exception as e:
                st.error(f"ログ保存エラー: {e}")

            # 音声再生
            if full_response.strip():
                tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
                st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)
