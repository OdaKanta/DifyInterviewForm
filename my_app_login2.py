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
names = ["田中 太郎", "佐藤 花子"]
usernames = ["tanaka", "sato"]
passwords = ["pass123", "pass456"]

# ログイン部品の準備
authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]}
    }},
    "dify_app_cookie",
    "signature_key",
    cookie_expiry_days=30
)

# --- 2. ログイン画面の表示 ---
authenticator.login('main')

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

    # --- スプレッドシート接続 ---
    conn = st.connection("gsheets", type=GSheetsConnection)

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント (ログ収集付)GPT")

    # --- セッション初期化 ---
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # =========================================================
    # ★追加：ログイン直後に Dify の「会話開始」メッセージを取得
    # =========================================================
    if (
        st.session_state.conversation_id == ""
        and len(st.session_state.messages) == 0
    ):
        with st.chat_message("assistant"):
            placeholder = st.empty()
            full_response = ""

            DIFY_KEY = st.secrets["DIFY_API_KEY"]
            headers = {
                "Authorization": f"Bearer {DIFY_KEY}",
                "Content-Type": "application/json"
            }

            data = {
                "inputs": {},
                "query": "",  # 会話開始トリガー
                "response_mode": "streaming",
                "user": username
            }

            response = requests.post(
                "https://api.dify.ai/v1/chat-messages",
                headers=headers,
                json=data,
                stream=True
            )

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode("utf-8")
                    if decoded_line.startswith("data: "):
                        chunk = json.loads(decoded_line[6:])

                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]

                        if "answer" in chunk:
                            full_response += chunk["answer"]
                            placeholder.markdown(full_response + "▌")

            placeholder.markdown(full_response)

            st.session_state.messages.append({
                "role": "assistant",
                "content": full_response
            })

    # --- 入力UI ---
    st.write("話しかけてください：")
    audio = mic_recorder(
        start_prompt="⏺️ 録音開始",
        stop_prompt="⏹️ 停止",
        key='recorder'
    )

    user_input = None

    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_bio
            )
            user_input = transcript.text

    chat_input = st.chat_input("またはメッセージを入力...")
    if chat_input:
        user_input = chat_input

    # --- 過去メッセージ表示 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # =========================================================
    # --- ユーザー入力があった場合のメイン処理 ---
    # =========================================================
    if user_input:
        st.session_state.messages.append({
            "role": "user",
            "content": user_input
        })

        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            DIFY_KEY = st.secrets["DIFY_API_KEY"]
            headers = {
                "Authorization": f"Bearer {DIFY_KEY}",
                "Content-Type": "application/json"
            }

            data = {
                "inputs": {},
                "query": user_input,
                "response_mode": "streaming",
                "user": username,
                "conversation_id": st.session_state.conversation_id
            }

            response = requests.post(
                "https://api.dify.ai/v1/chat-messages",
                headers=headers,
                json=data,
                stream=True
            )

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode("utf-8")
                    if decoded_line.startswith("data: "):
                        chunk = json.loads(decoded_line[6:])

                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]

                        if "answer" in chunk:
                            full_response += chunk["answer"]
                            response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)

            st.session_state.messages.append({
                "role": "assistant",
                "content": full_response
            })

        # --- ログ保存 ---
        try:
            now = datetime.datetime.now(
                datetime.timezone(datetime.timedelta(hours=9))
            ).strftime('%Y-%m-%d %H:%M:%S')

            existing_data = conn.read(
                spreadsheet=st.secrets["spreadsheet_url"],
                ttl=0
            )

            new_row = {
                "date": now,
                "user_id": username,
                "user_input": user_input,
                "ai_response": full_response,
                "conversation_id": st.session_state.conversation_id
            }

            new_row_df = pd.DataFrame([new_row])

            if existing_data.empty:
                updated_df = new_row_df
            else:
                updated_df = pd.concat(
                    [existing_data, new_row_df],
                    ignore_index=True
                )

            conn.update(
                spreadsheet=st.secrets["spreadsheet_url"],
                data=updated_df
            )

        except Exception as e:
            st.error(f"ログ保存エラー: {e}")

        # --- 音声出力 ---
        if full_response.strip():  # ★空文字防止
            with st.spinner('音声を生成中...'):
                tts_response = client.audio.speech.create(
                    model="tts-1",
                    voice="alloy",
                    input=full_response
                )
                st.audio(
                    io.BytesIO(tts_response.content),
                    format="audio/mp3",
                    autoplay=True
                )

