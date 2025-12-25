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
names = ["田中 太郎", "佐藤 花子", "工大 太郎"]
usernames = ["tanaka", "sato", "kodai"]
passwords = ["pass123", "pass456", "password"]

authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]},
        usernames[2]: {'name': names[2], 'password': passwords[2]}
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

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント (ログ収集)")

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # --- ★追加: 初回ログイン時に Dify の「会話の開始」を取得する ---
    # メッセージ履歴が空の場合、Difyに空のクエリ（またはダミー）を送って最初の挨拶を取得
    if len(st.session_state.messages) == 0:
        DIFY_KEY = st.secrets["DIFY_API_KEY"]
        headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
        
        # Difyの「会話の開始」をトリガーするために空文字または特定の入力を送信
        # ※Dify側の設定により、queryが必須の場合は "こんにちは" 等をダミーで送る手法もあります
        data = {
            "inputs": {},
            "query": "hello", # 会話を開始させるためのトリガー
            "response_mode": "blocking", # 最初はストリーミングなしの方が制御しやすい
            "user": username,
            "conversation_id": ""
        }
        
        try:
            response = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data)
            res_json = response.json()
            if "answer" in res_json:
                st.session_state.messages.append({"role": "assistant", "content": res_json["answer"]})
                st.session_state.conversation_id = res_json.get("conversation_id", "")
                # 再描画して表示させる
                st.rerun()
        except Exception as e:
            st.error(f"初期メッセージ取得エラー: {e}")

    # --- UI: 過去のメッセージ表示 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- 音声・テキスト入力処理 ---
    st.write("話しかけてください：")
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')
    user_input = None

    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
            user_input = transcript.text
    
    chat_input = st.chat_input("またはメッセージを入力...")
    if chat_input:
        user_input = chat_input

    # --- メイン処理 (Dify送信 & ログ保存) ---
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

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

            # --- ログ書き込み処理 (省略なし) ---
            try:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                new_row = {
                    "date": now, "user_id": username, "user_input": user_input,
                    "ai_response": full_response, "conversation_id": st.session_state.conversation_id
                }
                new_row_df = pd.DataFrame([new_row])
                updated_df = pd.concat([existing_data, new_row_df], ignore_index=True) if not existing_data.empty else new_row_df
                conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
            except Exception as e:
                st.error(f"ログ保存エラー: {e}")

            # --- 音声出力 (OpenAI TTS) ---
            if full_response.strip(): 
                with st.spinner('音声を生成中...'):
                    try:
                        tts_response = client.audio.speech.create(
                            model="tts-1", voice="alloy", input=full_response
                        )
                        st.audio(io.BytesIO(tts_response.content), format="audio/mp3", autoplay=True)
                    except Exception as e:
                        st.error(f"音声生成エラー: {e}")
