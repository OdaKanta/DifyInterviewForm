import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
import yaml
from streamlit_gsheets import GSheetsConnection
import datetime
import pandas as pd

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子", "工大 太郎", "工大 花子"]
usernames = ["tanaka", "sato", "kodai", "hanako"]
passwords = ["pass123", "pass456", "password", "password"]

authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]},
        usernames[2]: {'name': names[2], 'password': passwords[2]},
        usernames[3]: {'name': names[3], 'password': passwords[3]}
    }},
    "dify_app_cookie", "signature_key", cookie_expiry_days=30
)

# --- 2. ログイン画面の表示 ---
authenticator.login('main')

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    conn = st.connection("gsheets", type=GSheetsConnection)

    # セッション状態の初期化
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""
    if "first_run" not in st.session_state:
        st.session_state.first_run = True

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント")

    # --- ★修正1: 初回メッセージの取得ロジックの改善 ---
    if st.session_state.first_run and len(st.session_state.messages) == 0:
        DIFY_KEY = st.secrets["DIFY_API_KEY"]
        headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
        try:
            params_res = requests.get(f"https://api.dify.ai/v1/parameters?user={username}", headers=headers)
            opening_message = params_res.json().get("opening_statement", "")
            if opening_message:
                st.session_state.messages.append({"role": "assistant", "content": opening_message})
                # rerunする前にfirst_runをオフにする
                st.session_state.first_run = False
                
                # 初回の音声生成
                tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=opening_message)
                st.session_state.pending_audio = tts_res.content
                st.rerun()
        except Exception as e:
            st.error(f"初期メッセージ取得エラー: {e}")
            st.session_state.first_run = False

    # --- 過去のメッセージ表示 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- ★修正2: 音声の自動再生（rerun後も確実に実行） ---
    if "pending_audio" in st.session_state:
        st.audio(st.session_state.pending_audio, format="audio/mp3", autoplay=True)
        del st.session_state.pending_audio

    # --- 入力インターフェース ---
    st.write("---")
    audio_data = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')
    chat_input = st.chat_input("またはメッセージを入力...")

    user_input = None
    if audio_data:
        audio_bio = io.BytesIO(audio_data['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
            user_input = transcript.text
    elif chat_input:
        user_input = chat_input

    # --- メイン処理 (Dify送信 & ログ保存) ---
    if user_input:
        # ユーザーの発言を表示・保存
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # AIの返答処理
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            DIFY_KEY = st.secrets["DIFY_API_KEY"]
            headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
            data = {
                "inputs": {}, "query": user_input, "response_mode": "streaming",
                "user": username, "conversation_id": st.session_state.conversation_id
            }
            
            response = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data, stream=True)
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        chunk = json.loads(decoded_line[6:])
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                            response_placeholder.markdown(full_response + "▌")
            
            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})

            # ログ保存 (GSheets)
            try:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                new_row = pd.DataFrame([{
                    "date": now, "user_id": username, "user_input": user_input,
                    "ai_response": full_response, "conversation_id": st.session_state.conversation_id
                }])
                updated_df = pd.concat([existing_data, new_row], ignore_index=True) if not existing_data.empty else new_row
                conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
            except Exception as e:
                st.error(f"ログ保存エラー: {e}")

            # AI音声生成
            if full_response.strip():
                with st.spinner('音声を生成中...'):
                    tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
                    st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)
